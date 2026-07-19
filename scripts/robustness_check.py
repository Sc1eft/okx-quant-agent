"""macd_agent 稳健性检验：多年回测 + 分年度切片 + Walk-Forward + 参数扫描

回答「策略的 edge 是真的，还是最近这半年碰巧」：
  1. 多年全样本回测（默认 3 年 1h，数据落本地缓存）
  2. 分年度切片 —— 牛/熊/震荡 regime 覆盖检查
  3. Walk-Forward 滚动窗口 —— 反过拟合（train/test 滚动）
  4. 蒙特卡洛参数扫描 —— 参数高原 vs 尖峰

用法: python scripts/robustness_check.py [--years 3] [--sweep 80] [--refresh]
"""

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from config import Config
from backtest.engine import BacktestEngine
from backtest.analyzer import WalkForwardAnalyzer
from frontend.utils.data_provider import fetch_okx_data

CACHE_DIR = PROJECT_ROOT / "data" / "cache"
STRATEGY = "macd_agent"


def load_cfg() -> Config:
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    return Config.load(str(cfg_path)) if cfg_path.exists() else Config()


def load_or_fetch(cfg: Config, years: int, refresh: bool) -> pd.DataFrame:
    """1h 全量数据：优先本地缓存，缺了才走 OKX 深度分页"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{cfg.trading.symbol}_1h.csv"
    need_start = pd.Timestamp.now(tz="Asia/Shanghai") - pd.Timedelta(days=years * 365)

    if cache.exists() and not refresh:
        df = pd.read_csv(cache, index_col="timestamp")
        df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Shanghai")
        if df.index[0] <= need_start:
            print(f"缓存命中: {cache.name}  {len(df)} 根  {df.index[0]} ~ {df.index[-1]}")
            return df
        print("缓存覆盖不足，重新拉取...")

    limit = years * 365 * 24 + 48
    print(f"从 OKX 拉取 {years} 年 1h K 线（约 {limit} 根，需几分钟）...")
    t0 = time.time()
    df = fetch_okx_data(cfg, limit=limit, timeframe="1h")
    print(f"  拉取完成: {len(df)} 根  {df.index[0]} ~ {df.index[-1]}  耗时 {time.time()-t0:.0f}s")
    df.to_csv(cache)
    print(f"  已缓存到 {cache}")
    return df


def run_slice(engine: BacktestEngine, df: pd.DataFrame) -> dict:
    r = engine.run(df, strategy_name=STRATEGY)
    m = r.metrics
    return {
        "收益率%": m["total_return_pct"], "回撤%": m["max_drawdown_pct"],
        "Sharpe": m["sharpe"], "胜率%": m["win_rate"],
        "交易数": m["total_trades"], "基准%": m["benchmark_return_pct"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--sweep", type=int, default=80, help="参数扫描次数")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新拉数据")
    args = parser.parse_args()

    cfg = load_cfg()
    df = load_or_fetch(cfg, args.years, args.refresh)
    engine = BacktestEngine(cfg)

    # ── 1. 全样本 ──
    print("\n[1/4] 全样本回测...")
    t0 = time.time()
    full = run_slice(engine, df)
    print(f"  {full}  ({time.time()-t0:.0f}s)")

    # ── 2. 分年度切片 ──
    print("\n[2/4] 分年度切片...")
    print(f"  {'年份':<8}{'收益率%':>9}{'回撤%':>8}{'Sharpe':>8}{'胜率%':>8}{'交易数':>7}{'基准%':>9}")
    for year, g in df.groupby(df.index.year):
        if len(g) < 200:
            continue
        s = run_slice(engine, g)
        print(f"  {year:<8}{s['收益率%']:>9}{s['回撤%']:>8}{s['Sharpe']:>8}"
              f"{s['胜率%']:>8}{s['交易数']:>7}{s['基准%']:>9}")

    # ── 3. Walk-Forward ──
    print("\n[3/4] Walk-Forward 滚动验证（6 窗口）...")
    analyzer = WalkForwardAnalyzer(cfg)
    wf = analyzer.run(df, strategy_name=STRATEGY, n_windows=6)

    # ── 4. 参数扫描 ──
    print(f"\n[4/4] 蒙特卡洛参数扫描（{args.sweep} 次，前 70% 扫描 / 后 30% 复验）...")
    t0 = time.time()
    sweep = analyzer.parameter_sweep(df, strategy_name=STRATEGY, n_iterations=args.sweep)
    print(f"  扫描耗时 {time.time()-t0:.0f}s")

    # ── 汇总 ──
    print("\n" + "=" * 70)
    print("稳健性汇总")
    print("=" * 70)
    print(f"全样本:       {full['收益率%']:+.2f}%  Sharpe {full['Sharpe']}  "
          f"交易 {full['交易数']} 笔  (基准 {full['基准%']:+.2f}%)")
    print(f"Walk-Forward: {wf.verdict} — {wf.details}")
    print(f"  训练 {wf.avg_train_return:+.2f}% → 测试 {wf.avg_test_return:+.2f}%  "
          f"Sharpe 下降 {wf.sharpe_drop_pct}%  盈利窗口 {wf.stable_window_ratio:.0%}")
    print(f"参数扫描:     {sweep.verdict} — {sweep.details}")
    print(f"  最佳 {sweep.best_return:+.2f}% / 中位 {sweep.median_return:+.2f}% / "
          f"最差 {sweep.worst_return:+.2f}%  CV={sweep.param_stability}")
    print("=" * 70)


if __name__ == "__main__":
    main()
