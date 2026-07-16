"""
回测引擎测试
"""

from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from config import Config
from backtest.engine import BacktestEngine, Trade
from backtest.metrics import compute_metrics
from strategies.base import Signal, StrategyResult


@pytest.fixture
def test_config():
    """简化的测试配置"""
    cfg = Config()
    cfg.trading.symbol = "BTC-USDT"
    cfg.trading.primary_timeframe = "1h"
    cfg.strategy.enabled_strategies = ["ma_cross"]
    cfg.strategy.stop_loss_pct = 2.0
    cfg.strategy.take_profit_pct = 6.0
    return cfg


@pytest.fixture
def price_data() -> pd.DataFrame:
    """生成带趋势的模拟 K 线数据"""
    np.random.seed(42)
    n = 300
    dates = pd.date_range("2025-01-01", periods=n, freq="h", tz="utc")

    # 先涨后跌再涨的趋势
    t = np.linspace(0, 4 * np.pi, n)
    trend = np.sin(t) * 2000 + np.linspace(0, 3000, n)
    noise = np.random.randn(n) * 200
    close = 50000 + trend + noise

    return pd.DataFrame({
        "open": close - np.random.randn(n) * 100,
        "high": close + np.abs(np.random.randn(n)) * 150,
        "low": close - np.abs(np.random.randn(n)) * 150,
        "close": close,
        "volume": np.random.rand(n) * 100,
    }, index=dates)


def test_backtest_engine_initializes(test_config):
    engine = BacktestEngine(test_config)
    assert engine.initial_capital == 10000.0


def test_backtest_returns_result(test_config, price_data):
    engine = BacktestEngine(test_config)
    result = engine.run(price_data, strategy_name="ma_cross")
    assert result is not None
    assert result.strategy_name == "ma_cross"
    assert result.symbol == "BTC-USDT"
    assert isinstance(result.trades, list)
    assert isinstance(result.equity_curve, pd.Series)


def test_backtest_has_metrics(test_config, price_data):
    engine = BacktestEngine(test_config)
    result = engine.run(price_data, strategy_name="ma_cross")
    metrics = result.metrics
    assert "total_return_pct" in metrics
    assert "sharpe" in metrics
    assert "max_drawdown_pct" in metrics
    assert "win_rate" in metrics
    assert "total_trades" in metrics


def test_backtest_metrics_are_reasonable(test_config, price_data):
    """指标应该在合理范围内"""
    engine = BacktestEngine(test_config)
    result = engine.run(price_data, strategy_name="ma_cross")
    m = result.metrics

    assert m["total_trades"] >= 0
    assert m["win_rate"] >= 0
    assert m["win_rate"] <= 100
    assert m["max_drawdown_pct"] >= 0
    # Sharpe 可能为负（策略亏损时）
    assert isinstance(m["sharpe"], float)


def test_backtest_trades_have_required_fields(test_config, price_data):
    engine = BacktestEngine(test_config)
    result = engine.run(price_data, strategy_name="ma_cross")
    for trade in result.trades:
        assert isinstance(trade.entry_time, pd.Timestamp)
        assert isinstance(trade.exit_time, pd.Timestamp)
        assert trade.entry_price > 0
        assert trade.exit_price > 0
        assert isinstance(trade.pnl, float)
        assert isinstance(trade.pnl_pct, float)


def test_backtest_includes_benchmark(test_config, price_data):
    engine = BacktestEngine(test_config)
    result = engine.run(price_data, strategy_name="ma_cross")
    assert "benchmark_return_pct" in result.metrics


def test_multiple_strategies(test_config, price_data):
    """测试多策略回测"""
    test_config.strategy.enabled_strategies = ["ma_cross", "rsi_mean_reversion"]
    test_config.strategy.rsi_period = 14
    engine = BacktestEngine(test_config)
    result = engine.run(price_data, strategy_name="ma_cross")
    assert result is not None


def test_order_type_comparison(test_config, price_data):
    """订单类型对比测试"""
    engine = BacktestEngine(test_config)
    comparison = engine.run_order_type_comparison(price_data, "ma_cross")
    assert "market" in comparison
    assert "limit" in comparison or len(comparison) == 1


def test_backtest_reproducible(test_config, price_data):
    """相同数据应该产生相同结果"""
    engine = BacktestEngine(test_config)
    r1 = engine.run(price_data, strategy_name="ma_cross")
    r2 = engine.run(price_data, strategy_name="ma_cross")
    assert r1.metrics["total_return_pct"] == r2.metrics["total_return_pct"]
    assert r1.metrics["total_trades"] == r2.metrics["total_trades"]


class ScriptedStrategy:
    def __init__(self, name, signals):
        self.name = name
        self._signals = signals

    def generate_signals(self, df):
        result = df.copy()
        result["signal"] = self._signals
        result["reason"] = "scripted"
        return StrategyResult(signals=result)


def _scripted_engine(monkeypatch, cfg, signals_by_name):
    def factory(name, params=None):
        return ScriptedStrategy(name, signals_by_name[name])

    monkeypatch.setattr("backtest.engine.create_strategy", factory)
    cfg.risk.max_single_order_pct = 1.0
    cfg.trading.taker_fee = 0.0
    cfg.trading.maker_fee = 0.0
    cfg.trading.slippage_pct = 0.0
    cfg.strategy.stop_loss_pct = 50.0
    cfg.strategy.take_profit_pct = 500.0
    cfg.strategy.trailing_stop_activation = 0.0
    return BacktestEngine(cfg)


def _ohlc_frame(rows):
    index = pd.date_range("2025-01-01", periods=len(rows), freq="h", tz="utc")
    return pd.DataFrame(rows, index=index)


def test_signal_executes_at_next_bar_open(monkeypatch, test_config):
    df = _ohlc_frame([
        {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"open": 110, "high": 111, "low": 109, "close": 110, "volume": 1},
        {"open": 120, "high": 121, "low": 119, "close": 120, "volume": 1},
        {"open": 130, "high": 131, "low": 129, "close": 130, "volume": 1},
    ])
    engine = _scripted_engine(monkeypatch, test_config, {"scripted": [Signal.BUY, Signal.HOLD, Signal.SELL, Signal.HOLD]})

    result = engine.run(df, strategy_name="scripted")

    assert result.trades[0].entry_price == 110
    assert result.trades[0].exit_price == 130
    assert result.trades[0].entry_time == df.index[1]
    assert result.trades[0].exit_time == df.index[3]


def test_open_position_is_liquidated_at_end_of_data(monkeypatch, test_config):
    df = _ohlc_frame([
        {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"open": 110, "high": 111, "low": 109, "close": 110, "volume": 1},
        {"open": 120, "high": 121, "low": 119, "close": 120, "volume": 1},
    ])
    engine = _scripted_engine(monkeypatch, test_config, {"scripted": [Signal.BUY, Signal.HOLD, Signal.HOLD]})

    result = engine.run(df, strategy_name="scripted")

    assert len(result.trades) == 1
    assert result.trades[0].reason == "end_of_data"
    assert result.trades[0].exit_price == 120


def test_intrabar_stop_loss_uses_kline_low(monkeypatch, test_config):
    df = _ohlc_frame([
        {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"open": 100, "high": 110, "low": 94, "close": 108, "volume": 1},
        {"open": 108, "high": 109, "low": 107, "close": 108, "volume": 1},
    ])
    engine = _scripted_engine(monkeypatch, test_config, {"scripted": [Signal.BUY, Signal.HOLD, Signal.HOLD]})
    engine.cfg.strategy.stop_loss_pct = 5.0

    result = engine.run(df, strategy_name="scripted")

    assert result.trades[0].reason == "stop_loss"
    assert result.trades[0].exit_price == 95


def test_unfilled_limit_order_does_not_become_market_order(monkeypatch, test_config):
    df = _ohlc_frame([
        {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"open": 101, "high": 102, "low": 100, "close": 101, "volume": 1},
    ])
    engine = _scripted_engine(monkeypatch, test_config, {"scripted": [Signal.BUY, Signal.HOLD]})

    result = engine.run(df, strategy_name="scripted", order_type="limit")

    assert result.trades == []


def test_multiple_strategies_use_weighted_signal_votes(monkeypatch, test_config):
    df = _ohlc_frame([
        {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"open": 101, "high": 102, "low": 100, "close": 101, "volume": 1},
    ])
    test_config.strategy.enabled_strategies = ["first", "second"]
    test_config.strategy.strategy_weights = {"first": 0.7, "second": 0.3}
    engine = _scripted_engine(monkeypatch, test_config, {
        "first": [Signal.BUY, Signal.HOLD],
        "second": [Signal.SELL, Signal.HOLD],
    })

    result = engine.run(df)

    assert result.signals_df.iloc[0]["signal"] == Signal.BUY


def test_sharpe_uses_equity_curve_frequency():
    values = [100.0, 101.0, 100.0, 103.0]
    hourly_index = pd.date_range("2025-01-01", periods=4, freq="h", tz="utc")
    daily_index = pd.date_range("2025-01-01", periods=4, freq="D", tz="utc")
    prices = pd.DataFrame({"close": values}, index=hourly_index)

    hourly = compute_metrics(pd.Series(values, index=hourly_index), [], 100.0, prices)
    daily = compute_metrics(pd.Series(values, index=daily_index), [], 100.0, prices)

    assert hourly["sharpe"] > daily["sharpe"]
