"""
回测引擎测试
"""

from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from config import Config
from backtest.engine import BacktestEngine, Trade


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
