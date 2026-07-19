"""daily_trend 稳健性验证：Walk-Forward + 留出段 OOS + 参数网格敏感性

回答一个问题：trigger+市价 的 +109% 是策略真本事还是参数运气？
  - WF：4 窗口滚动训练/测试，看样本外一致性
    （日线策略 3 年只有 ~26 笔交易，每窗口测试段仅 1~3 笔，统计偏薄，仅作参考）
  - OOS：最后 30% 数据留出复验收益保留率
  - 网格：trend_span{40,50,60} × lookback{2,3,5}，看收益对参数是否敏感

用法: python scripts/validate_daily_trend.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from backtest.analyzer import WalkForwardAnalyzer
from backtest.engine import BacktestEngine
from config import Config

SPANS = [40, 50, 60]
LOOKBACKS = [2, 3, 5]


def main():
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    cfg = Config.load(str(cfg_path)) if cfg_path.exists() else Config()
    cfg.risk.max_single_order_pct = 1.0  # 与基线同口径：全额本金

    cache = PROJECT_ROOT / "data" / "cache" / f"{cfg.trading.symbol}_1h.csv"
    df = pd.read_csv(cache, index_col="timestamp")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Shanghai")
    print(f"数据: {len(df)} 根 1h  {df.index[0]} ~ {df.index[-1]}", flush=True)

    analyzer = WalkForwardAnalyzer(cfg)

    print("\n── 1. Walk-Forward（默认 trigger 参数）──", flush=True)
    wf = analyzer.run(df, "daily_trend", n_windows=4)
    print(f"判定: {wf.verdict} — {wf.details}", flush=True)

    print("\n── 2. 留出段 OOS（最后 30% 未参与任何调参）──", flush=True)
    oos = analyzer.out_of_sample_test(df, "daily_trend")
    print(f"IS {oos['in_sample_return']:+.1f}% → OOS {oos['out_of_sample_return']:+.1f}%  "
          f"保留率 {oos['retention_ratio']}%  判定: {oos['verdict']}", flush=True)

    print("\n── 3. 参数网格（trigger+市价，全额本金）──", flush=True)
    engine = BacktestEngine(cfg)
    rows = []
    for span in SPANS:
        for lb in LOOKBACKS:
            res = engine.run(df, strategy_name="daily_trend",
                             params={"trend_span": span, "trigger_lookback_days": lb})
            m = res.metrics
            rows.append({
                "span": span, "lookback": lb,
                "总收益%": round(m["total_return_pct"], 1),
                "最大回撤%": round(m["max_drawdown_pct"], 1),
                "夏普": round(m["sharpe"], 2),
                "交易数": len(res.trades),
            })
            print(f"  span={span} lookback={lb} 完成", flush=True)

    grid = pd.DataFrame(rows)
    print("\n" + grid.to_string(index=False))
    ret = grid["总收益%"]
    print(f"\n网格收益分布: min {ret.min():+.1f}% / 中位 {ret.median():+.1f}% / max {ret.max():+.1f}%")
    print("判读: 全部组合为正且中位接近最优 → 参数不敏感；若只有 (50,3) 孤零零为正 → 运气成分大")


if __name__ == "__main__":
    main()
