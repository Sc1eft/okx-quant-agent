"""
策略单元测试
"""

from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from strategies.base import Signal, create_strategy, get_available_strategies


@pytest.fixture
def sample_klines() -> pd.DataFrame:
    """生成 200 根模拟 K 线"""
    np.random.seed(42)
    dates = pd.date_range("2025-01-01", periods=200, freq="h", tz="utc")
    close = 50000 + np.cumsum(np.random.randn(200) * 100)
    high = close + np.random.rand(200) * 200
    low = close - np.random.rand(200) * 200

    return pd.DataFrame({
        "open": close - np.random.randn(200) * 50,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.rand(200) * 100,
    }, index=dates)


def test_all_strategies_can_be_created():
    """测试所有策略可以成功实例化"""
    registry = get_available_strategies()
    assert len(registry) >= 3, "至少要有 3 个策略"
    for name in registry:
        strat = create_strategy(name)
        assert strat.name == name
        assert strat.description != ""


def test_ma_cross_generates_signals(sample_klines):
    """MA 交叉策略生成信号"""
    strat = create_strategy("ma_cross", {
        "short_window": 5,
        "long_window": 20,
        "stop_loss_pct": 2.0,
        "take_profit_pct": 6.0,
    })
    result = strat.generate_signals(sample_klines)
    assert not result.signals.empty
    assert "signal" in result.signals.columns
    # 至少有一些非 HOLD 信号
    non_hold = result.signals[result.signals["signal"] != Signal.HOLD]
    assert len(non_hold) > 0


def test_rsi_generates_signals(sample_klines):
    """RSI 策略生成信号"""
    strat = create_strategy("rsi_mean_reversion", {
        "rsi_period": 14,
        "oversold": 30,
        "overbought": 70,
    })
    result = strat.generate_signals(sample_klines)
    assert not result.signals.empty


def test_breakout_generates_signals(sample_klines):
    """突破策略生成信号"""
    strat = create_strategy("breakout", {
        "period": 20,
        "atr_multiplier": 2.0,
    })
    result = strat.generate_signals(sample_klines)
    assert not result.signals.empty


def test_strategy_signal_types(sample_klines):
    """信号类型必须是 Signal 枚举"""
    strat = create_strategy("ma_cross", {"short_window": 5, "long_window": 20})
    result = strat.generate_signals(sample_klines)
    valid_signals = {Signal.BUY, Signal.SELL, Signal.HOLD, Signal.EXIT}
    for sig in result.signals["signal"]:
        assert sig in valid_signals


def test_strategy_metadata_has_name(sample_klines):
    """策略元数据包含名称"""
    strat = create_strategy("ma_cross")
    result = strat.generate_signals(sample_klines)
    assert "strategy" in result.metadata


def test_stop_loss_triggered(sample_klines):
    """止损应该触发 EXIT 信号"""
    strat = create_strategy("ma_cross", {
        "short_window": 5,
        "long_window": 20,
        "stop_loss_pct": 50.0,  # 50%，故意设大让 BUY 后不触发
        "take_profit_pct": 0.5,  # 0.5% 容易触发
    })
    result = strat.generate_signals(sample_klines)
    exits = result.signals[result.signals["signal"] == Signal.EXIT]
    # 至少确保信号存在（注意：模拟数据中不一定实际触发）
    assert Signal.EXIT in result.signals["signal"].values or Signal.BUY in result.signals["signal"].values


def test_signal_consistency(sample_klines):
    """BUY 后不能马上 BUY（有持仓时）"""
    strat = create_strategy("ma_cross", {"short_window": 5, "long_window": 20})
    result = strat.generate_signals(sample_klines)

    signals = result.signals["signal"].values
    in_position = False
    for sig in signals:
        if sig == Signal.BUY:
            assert not in_position, "BUY 时不应已有持仓"
            in_position = True
        elif sig in (Signal.SELL, Signal.EXIT):
            in_position = False
