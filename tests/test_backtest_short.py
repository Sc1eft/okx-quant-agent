# tests/test_backtest_short.py
"""回测引擎做空侧：信号、成交、出场、盈亏结算"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from backtest.engine import BacktestEngine
from strategies.base import Signal
from strategies.macd_agent import MACDAgentStrategy


def _trend_klines(n: int, start: float, slope: float, seed: int = 7) -> pd.DataFrame:
    """持续趋势 + 正弦波动（趋势定方向，波动制造 MACD 交叉）"""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = start + slope * t + 80 * np.sin(t / 12) + np.cumsum(rng.normal(0, 3, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": np.roll(close, 1),
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": rng.uniform(100, 500, n),
        },
        index=idx,
    )


SHORT_PARAMS = {
    "higher_tfs": ["1h"], "score_threshold": 0.1, "min_confidence": 0.3,
    "allow_short": True,
}


def test_strategy_emits_short_only_when_allowed():
    """下跌趋势：allow_short=True 出现 SHORT 信号，默认关闭时不出现"""
    df = _trend_klines(2000, 5000, -1.2)
    params = {k: v for k, v in SHORT_PARAMS.items() if k != "allow_short"}
    off = MACDAgentStrategy("macd_agent", dict(params))
    on = MACDAgentStrategy("macd_agent", dict(params, allow_short=True))
    sigs_off = off.generate_signals(df).signals["signal"]
    sigs_on = on.generate_signals(df).signals["signal"]
    assert not (sigs_off == Signal.SHORT).any()
    assert (sigs_on == Signal.SHORT).any()
    assert (sigs_on == Signal.COVER).any()  # 有空必有平空（或数据末强平）


def test_short_intrabar_exit_model():
    """空单 intrabar 出场：止损在上方、止盈在下方、移动止损跟踪最低价"""
    engine = BacktestEngine(Config())  # 默认止损 5% / 止盈 10% / 移动 6%+3%
    # 上破 5% 止损
    assert engine._intrabar_exit_price("short", 100, 106, 99, 100, 100) == (105.0, "stop_loss")
    # 跳空高开止损
    assert engine._intrabar_exit_price("short", 106, 106.5, 105, 100, 100) == (106, "stop_loss_gap")
    # 下破 10% 止盈（extreme=95 未触发移动止损激活线 94）
    assert engine._intrabar_exit_price("short", 100, 100.5, 89, 100, 95) == (90.0, "take_profit")
    # 移动止损：最低 93（≤94 激活），跟踪价 93×1.03=95.79，开盘 100 直接触发 gap
    assert engine._intrabar_exit_price("short", 100, 101, 96, 100, 93) == (100, "trailing_stop_gap")
    # 未触发任何出场
    assert engine._intrabar_exit_price("short", 100, 103, 98, 100, 98) is None
    # 多单路径保持原样
    assert engine._intrabar_exit_price("long", 100, 101, 94, 100, 101) == (95.0, "stop_loss")


def test_close_position_short_settlement():
    """空单平仓：价差结算盈亏，名义价值不进现金"""
    trades = []
    result = BacktestEngine._close_position(
        trades, equity=10000.0, position=2.0, entry_price=100.0,
        entry_time=pd.Timestamp("2026-01-01"), entry_fee=2.0,
        exit_time=pd.Timestamp("2026-01-02"), exit_price=90.0,
        fee_rate=0.001, reason="take_profit", side="short",
    )
    t = trades[0]
    assert t.side == "short"
    assert t.pnl == pytest.approx((100 - 90) * 2 - 2.0 - 90 * 2 * 0.001, abs=1e-6)
    assert result[0] == pytest.approx(10000 + (100 - 90) * 2 - 90 * 2 * 0.001, abs=1e-6)


def test_engine_short_trades_profit_in_downtrend():
    """集成：强下跌趋势中，开空组合整体应盈利，且不开 allow_short 时无空单"""
    df = _trend_klines(2000, 5000, -1.2)
    engine = BacktestEngine(Config())
    res = engine.run(df, "macd_agent", params=dict(SHORT_PARAMS))
    shorts = [t for t in res.trades if t.side == "short"]
    assert shorts, "下跌趋势应产生空单"
    assert sum(t.pnl for t in shorts) > 0, "下跌趋势做空整体应盈利"

    long_only = {k: v for k, v in SHORT_PARAMS.items() if k != "allow_short"}
    res2 = engine.run(df, "macd_agent", params=long_only)
    assert all(t.side == "long" for t in res2.trades)
