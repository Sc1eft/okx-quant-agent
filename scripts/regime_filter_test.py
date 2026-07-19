"""regime 过滤 × 费率 × 做空 组合实验（3 年 1h 缓存数据）

  A: 基准 + 市价(taker)      —— 已知的 -37.5% 基线，重跑保证同口径
  B: 基准 + 限价(maker)
  C: 过滤 + 市价(taker)
  D: 过滤 + 限价(maker)
  E: 过滤 + 限价 + 做空      —— 顺日线趋势双向交易的候选组合
  F: 基准 + 限价 + 做空      —— 隔离做空本身的贡献（无 regime 门）

依赖 scripts/robustness_check.py 生成的缓存 data/cache/ETH-USDT_1h.csv。
用法: python scripts/regime_filter_test.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from config import Config
from backtest.engine import BacktestEngine

STRATEGY = "macd_agent"
FILTER = {"trend_filter": "ema50"}

RUNS = {
    "A 基准+市价": dict(params=None, order_type="market"),
    "B 基准+限价": dict(params=None, order_type="limit"),
    "C 过滤+市价": dict(params=FILTER, order_type="market"),
    "D 过滤+限价": dict(params=FILTER, order_type="limit"),
    "E 过滤+限价+空": dict(params={**FILTER, "allow_short": True}, order_type="limit"),
    "F 基准+限价+空": dict(params={"allow_short": True}, order_type="limit"),
}


def load_cache(cfg: Config) -> pd.DataFrame:
    cache = PROJECT_ROOT / "data" / "cache" / f"{cfg.trading.symbol}_1h.csv"
    if not cache.exists():
        raise SystemExit("缓存不存在，先跑: python scripts/robustness_check.py --years 3")
    df = pd.read_csv(cache, index_col="timestamp")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Shanghai")
    print(f"缓存: {len(df)} 根  {df.index[0]} ~ {df.index[-1]}")
    return df


def main():
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    cfg = Config.load(str(cfg_path)) if cfg_path.exists() else Config()
    df = load_cache(cfg)
    engine = BacktestEngine(cfg)

    # ── 全样本 2×2 ──
    print("\n全样本（3 年）:")
    print(f"  {'组合':<12}{'收益率%':>9}{'回撤%':>8}{'Sharpe':>8}{'胜率%':>8}{'交易数':>7}{'费用$':>9}")
    yearly: dict[str, dict[int, float]] = {}
    for name, kw in RUNS.items():
        r = engine.run(df, strategy_name=STRATEGY, **kw)
        m = r.metrics
        fee = sum(t.fee for t in r.trades)
        print(f"  {name:<12}{m['total_return_pct']:>9}{m['max_drawdown_pct']:>8}"
              f"{m['sharpe']:>8}{m['win_rate']:>8}{m['total_trades']:>7}{fee:>9.0f}")
        yearly[name] = {}
        for year, g in df.groupby(df.index.year):
            if len(g) < 200:
                continue
            rm = engine.run(g, strategy_name=STRATEGY, **kw).metrics
            yearly[name][year] = rm["total_return_pct"]

    # ── 分年度对比 ──
    years = sorted(next(iter(yearly.values())).keys())
    print("\n分年度收益率%:")
    print(f"  {'组合':<12}" + "".join(f"{y:>9}" for y in years))
    for name in RUNS:
        print(f"  {name:<12}" + "".join(f"{yearly[name][y]:>9.1f}" for y in years))
    bh = {}
    for y in years:
        g = df[df.index.year == y]
        bh[y] = round((g["close"].iloc[-1] / g["close"].iloc[0] - 1) * 100, 1)
    print(f"  {'基准(持有)':<12}" + "".join(f"{bh[y]:>9}" for y in years))


if __name__ == "__main__":
    main()
