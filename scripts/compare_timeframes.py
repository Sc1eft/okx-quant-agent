"""三周期对比回测：macd_agent @ 15m / 1h / 4h

同一日历窗口、同一策略默认参数、同一回测引擎（taker 手续费 + 滑点），
只换基础 K 线周期，回答「ETH 信号用哪个级别更合适」。

策略内部始终会合成 1h/1d 高周期（4h 基础时跳过 1h），
三个周期跑的都是「实盘 RuleDecider 同源」的完整信号链。

用法: python scripts/compare_timeframes.py [--days 180]
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from backtest.engine import BacktestEngine
from frontend.utils.data_provider import fetch_okx_data

TF_BARS_PER_DAY = {"15m": 96, "1h": 24, "4h": 6}


def load_cfg() -> Config:
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    return Config.load(str(cfg_path)) if cfg_path.exists() else Config()


def fetch_aligned(cfg: Config, days: int) -> dict[str, "pd.DataFrame"]:
    """按同一日历窗口拉取三个周期，对齐起止并丢弃未收盘的末根 K 线"""
    frames = {}
    for tf, per_day in TF_BARS_PER_DAY.items():
        limit = days * per_day + per_day  # 多拉一天余量，对齐时裁掉
        df = fetch_okx_data(cfg, limit=limit, timeframe=tf)
        frames[tf] = df
        print(f"  {tf}: {len(df)} 根  {df.index[0]} ~ {df.index[-1]}")

    start = max(df.index[0] for df in frames.values())
    end = min(df.index[-2] for df in frames.values())  # 末根可能未收盘，弃用
    for tf in frames:
        frames[tf] = frames[tf].loc[start:end]
        print(f"  {tf} 对齐后: {len(frames[tf])} 根  [{start} ~ {end}]")
    return frames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=180, help="对比窗口天数（默认 180）")
    args = parser.parse_args()

    cfg = load_cfg()
    print(f"标的: {cfg.trading.symbol}  窗口: {args.days} 天")
    print(f"费用: taker {cfg.trading.taker_fee*100:.2f}%/边  滑点 {cfg.trading.slippage_pct:.2f}%  "
          f"单笔仓位 {cfg.risk.max_single_order_pct*100:.0f}%  止损 {cfg.strategy.stop_loss_pct}%  "
          f"止盈 {cfg.strategy.take_profit_pct}%")
    print("拉取 K 线...")

    frames = fetch_aligned(cfg, args.days)

    rows = []
    for tf, df in frames.items():
        engine = BacktestEngine(cfg)
        result = engine.run(df, strategy_name="macd_agent")
        m = result.metrics
        total_fee = sum(t.fee for t in result.trades)
        rows.append({
            "周期": tf,
            "收益率%": m["total_return_pct"],
            "年化%": m["annual_return_pct"],
            "最大回撤%": m["max_drawdown_pct"],
            "Sharpe": m["sharpe"],
            "PSR": m.get("psr", 0),
            "胜率%": m["win_rate"],
            "盈亏比": m["profit_factor"],
            "交易数": m["total_trades"],
            "平均持仓h": m["avg_hold_hours"],
            "总手续费$": round(total_fee, 2),
        })

    print("\n" + "=" * 96)
    print(f"{'周期':<6}{'收益率%':>9}{'年化%':>9}{'回撤%':>8}{'Sharpe':>8}{'PSR':>7}"
          f"{'胜率%':>8}{'盈亏比':>8}{'交易数':>7}{'持仓h':>8}{'费用$':>9}")
    print("-" * 96)
    for r in rows:
        print(f"{r['周期']:<6}{r['收益率%']:>9}{r['年化%']:>9}{r['最大回撤%']:>8}"
              f"{r['Sharpe']:>8}{r['PSR']:>7}{r['胜率%']:>8}{r['盈亏比']:>8}"
              f"{r['交易数']:>7}{r['平均持仓h']:>8}{r['总手续费$']:>9}")
    print("-" * 96)
    bh = result.metrics["benchmark_return_pct"]
    print(f"同期买入持有: {bh:+.2f}%   （PSR<0.95 说明 Sharpe 统计上不显著，min_trades≥20 才可靠）")
    print("=" * 96)


if __name__ == "__main__":
    main()
