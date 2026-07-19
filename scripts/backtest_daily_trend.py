"""daily_trend 策略 3 年回测：两种入场模式 × 市价/限价，对照基线与买入持有

对照锚点（同一缓存数据）：
  - daily_trend_baseline.py 纯 regime 多/空仓向量基线：+62.0%，81 次调仓
  - macd_agent 1h 信号体系基线：-37.52%（862 笔）

引擎说明：daily_trend 声明 use_engine_stops=False，引擎自动跳过
止损/止盈/移动止损（出场完全由 regime 翻转负责）。

用法: python scripts/backtest_daily_trend.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from backtest.engine import BacktestEngine
from config import Config

VARIANTS = [
    ("regime+市价", {"entry_mode": "regime"}, "market"),
    ("regime+限价", {"entry_mode": "regime"}, "limit"),
    ("trigger+市价", {"entry_mode": "trigger"}, "market"),
    ("trigger+限价", {"entry_mode": "trigger"}, "limit"),
]


def yearly_returns(equity: pd.Series) -> dict:
    out = {}
    for y, seg in equity.groupby(equity.index.year):
        out[y] = (seg.iloc[-1] / seg.iloc[0] - 1) * 100
    return out


def main():
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    cfg = Config.load(str(cfg_path)) if cfg_path.exists() else Config()
    # 与向量基线同口径：全额本金（默认 max_single_order_pct=0.10 只下 10% 仓位，
    # 是实盘风控口径，回测对照时按基线的全额本金计）
    cfg.risk.max_single_order_pct = 1.0

    cache = PROJECT_ROOT / "data" / "cache" / f"{cfg.trading.symbol}_1h.csv"
    if not cache.exists():
        raise SystemExit("缓存不存在，先跑: python scripts/robustness_check.py --years 3")
    df = pd.read_csv(cache, index_col="timestamp")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Shanghai")
    print(f"数据: {len(df)} 根 1h  {df.index[0]} ~ {df.index[-1]}  "
          f"taker {cfg.trading.taker_fee*100:.2f}% / maker {cfg.trading.maker_fee*100:.2f}%")

    engine = BacktestEngine(cfg)
    rows = []
    for label, params, order_type in VARIANTS:
        res = engine.run(df, strategy_name="daily_trend", params=params, order_type=order_type)
        m = res.metrics
        yr = yearly_returns(res.equity_curve)
        rows.append({
            "变体": label,
            "总收益%": round(m["total_return_pct"], 1),
            "最大回撤%": round(m["max_drawdown_pct"], 1),
            "夏普": round(m["sharpe"], 2),
            "交易数": len(res.trades),
            "胜率%": round(m.get("win_rate", 0), 0),
            **{f"{y}": round(yr.get(y, 0), 1) for y in (2023, 2024, 2025, 2026)},
        })

    # 买入持有对照
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    bh_eq = (1 + df["close"].pct_change().fillna(0)).cumprod()
    bh_dd = abs((bh_eq / bh_eq.cummax() - 1).min()) * 100
    bh_yr = yearly_returns(bh_eq)
    rows.append({
        "变体": "买入持有",
        "总收益%": round(bh * 100, 1),
        "最大回撤%": round(bh_dd, 1),
        "夏普": None,
        "交易数": 1,
        "胜率%": None,
        **{f"{y}": round(bh_yr.get(y, 0), 1) for y in (2023, 2024, 2025, 2026)},
    })

    pd.set_option("display.width", 200)
    print("\n══ daily_trend 3 年回测 ══")
    print(pd.DataFrame(rows).to_string(index=False))
    print("\n锚点：向量基线（同规则无引擎）+62.0% / macd_agent 1h 体系 -37.52%")


if __name__ == "__main__":
    main()
