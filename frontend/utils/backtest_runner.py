"""Backtest runner wrapper for Streamlit frontend.

Runs backtests synchronously (wrapped in spinner), caches results in session_state.
"""

import sys
from pathlib import Path
from typing import Optional, Dict, Any
import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from strategies.base import Signal, create_strategy, get_available_strategies
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.analyzer import WalkForwardAnalyzer


def get_mock_data(cfg: Config, periods: int = 1000) -> pd.DataFrame:
    """Generate mock OHLCV data when OKX data is unavailable."""
    from datetime import datetime, timedelta
    import numpy as np

    periods = 1000
    now = datetime.now()
    time_idx = [now - timedelta(hours=i) for i in range(periods - 1, -1, -1)]

    np.random.seed(42)
    price = 60000.0
    opens, highs, lows, closes, volumes = [], [], [], [], []

    for i in range(periods):
        ret = np.random.normal(0.0001, 0.008)
        price *= (1 + ret)
        vol = np.random.uniform(0.002, 0.008) * price
        high = price + abs(np.random.normal(0, vol))
        low = price - abs(np.random.normal(0, vol))
        opens.append(price * (1 - ret * 0.3))
        highs.append(high)
        lows.append(low)
        closes.append(price)
        volumes.append(np.random.uniform(50, 500))

    df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes
    }, index=pd.DatetimeIndex(time_idx, name="timestamp"))
    return df


def run_backtest(
    strategy_name: str,
    cfg: Config,
    data: Optional[pd.DataFrame] = None,
) -> Optional[Dict[str, Any]]:
    """Run a single strategy backtest and return serializable results.

    Returns a dict with keys:
      - metrics: dict of computed metrics
      - trades: list of trade dicts (JSON-friendly)
      - equity_curve: list of {time, equity} dicts
      - signals: list of signal records
      - price_data: list of {time, close} dicts (回测真实 K 线收盘价)
      - strategy_name: str
    """
    try:
        cfg.strategy.enabled_strategies = [strategy_name]
        engine = BacktestEngine(cfg)

        if data is None:
            data = get_mock_data(cfg)

        result = engine.run(data, strategy_name)
        result_dict = _serialize_result(result)
        return result_dict
    except Exception as e:
        st.error(f"回测运行失败: {e}")
        return None


def run_all_strategies(
    cfg: Config,
    data: Optional[pd.DataFrame] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run backtests for all enabled strategies and return serialized results."""
    engine = BacktestEngine(cfg)
    if data is None:
        data = get_mock_data(cfg)

    results = {}
    strategies = get_available_strategies()
    for name in strategies:
        try:
            result = engine.run(data, name)
            results[name] = _serialize_result(result)
        except Exception as e:
            st.warning(f"策略 {name} 回测失败: {e}")
    return results


def run_comparison(
    strategy_name: str,
    cfg: Config,
    data: Optional[pd.DataFrame] = None,
) -> Optional[Dict[str, Any]]:
    """Run market vs limit order comparison."""
    try:
        cfg.strategy.enabled_strategies = [strategy_name]
        engine = BacktestEngine(cfg)
        if data is None:
            data = get_mock_data(cfg)
        comparison = engine.run_order_type_comparison(data, strategy_name)
        return comparison
    except Exception as e:
        st.error(f"订单类型对比失败: {e}")
        return None


def run_walk_forward(
    strategy_name: str,
    cfg: Config,
    n_windows: int = 4,
    data: Optional[pd.DataFrame] = None,
):
    """Run walk-forward analysis and return serialized result."""
    from dataclasses import asdict

    try:
        if data is None:
            data = get_mock_data(cfg)

        analyzer = WalkForwardAnalyzer(cfg, n_windows=n_windows)
        result = analyzer.run(data, strategy_name)
        # Convert dataclass to dict
        wf_dict = asdict(result)
        # Convert timestamps in windows
        for w in wf_dict.get("windows", []):
            for key in ["train_start", "train_end", "test_start", "test_end"]:
                if key in w and hasattr(w[key], "isoformat"):
                    w[key] = w[key].isoformat()
        return wf_dict
    except Exception as e:
        st.error(f"Walk-Forward 分析失败: {e}")
        return None


def run_param_sweep(
    strategy_name: str,
    cfg: Config,
    n_iterations: int = 200,
    data: Optional[pd.DataFrame] = None,
):
    """Run parameter sweep and return serialized result."""
    from dataclasses import asdict

    try:
        if data is None:
            data = get_mock_data(cfg)

        analyzer = WalkForwardAnalyzer(cfg)
        result = analyzer.parameter_sweep(data, strategy_name, n_iterations=n_iterations)
        return asdict(result)
    except Exception as e:
        st.error(f"参数扫描失败: {e}")
        return None


def run_oos_test(
    strategy_name: str,
    cfg: Config,
    data: Optional[pd.DataFrame] = None,
):
    """Run out-of-sample test and return serialized result."""
    try:
        if data is None:
            data = get_mock_data(cfg)

        analyzer = WalkForwardAnalyzer(cfg)
        result = analyzer.out_of_sample_test(data, strategy_name)
        # result is already a dict
        return result
    except Exception as e:
        st.error(f"样本外测试失败: {e}")
        return None


def _serialize_result(result) -> Dict[str, Any]:
    """Convert BacktestResult dataclass to JSON-friendly dict."""
    import numpy as np
    from dataclasses import asdict

    metrics = dict(result.metrics)

    trades = []
    for t in result.trades:
        t_dict = {
            "entry_time": t.entry_time.isoformat() if hasattr(t.entry_time, "isoformat") else str(t.entry_time),
            "exit_time": t.exit_time.isoformat() if hasattr(t.exit_time, "isoformat") else str(t.exit_time),
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price),
            "side": t.side,
            "size": float(t.size),
            "pnl": float(t.pnl),
            "pnl_pct": float(t.pnl_pct),
            "fee": float(t.fee),
            "reason": t.reason,
        }
        trades.append(t_dict)

    equity_curve = []
    if result.equity_curve is not None:
        for ts, val in result.equity_curve.items():
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            equity_curve.append({"time": ts_str, "equity": float(val)})

    signals = []
    price_data = []
    if result.signals_df is not None and not result.signals_df.empty:
        df = result.signals_df.reset_index()
        for _, row in df.iterrows():
            sig = row.get("signal", "")
            close = float(row.get("close", 0)) if pd.notna(row.get("close", 0)) else 0
            signals.append({
                "time": str(row.iloc[0]),
                "signal": sig.name if isinstance(sig, Signal) else str(sig).upper(),
                "price": close,
            })
            price_data.append({"time": str(row.iloc[0]), "close": close})

    return {
        "strategy_name": result.strategy_name,
        "metrics": metrics,
        "trades": trades,
        "equity_curve": equity_curve,
        "signals": signals,
        "price_data": price_data,
        "symbol": result.symbol,
        "fee_model": result.fee_model,
        "slippage_pct": result.slippage_pct,
    }
