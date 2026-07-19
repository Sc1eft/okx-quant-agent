# tests/test_macd_agent.py
"""MACDAgentStrategy 增量模式（on_bar）与批处理模式（generate_signals）一致性"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.base import Signal
from strategies.macd_agent import MACDAgentStrategy


def _make_klines(n: int = 400, tf: str = "1h", seed: int = 42) -> pd.DataFrame:
    """合成带趋势的 K 线（随机游走 + 正弦，确保产生交叉信号）"""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 3000 + 80 * np.sin(t / 15) + np.cumsum(rng.normal(0, 8, n))
    close = np.maximum(close, 100)
    idx = pd.date_range("2026-01-01", periods=n, freq=tf, tz="UTC")
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


@pytest.fixture
def strategy():
    return MACDAgentStrategy("macd_agent", {"higher_tfs": ["1h"]})  # 单周期，便于一致性断言


def test_on_bar_matches_batch(strategy):
    """逐根 on_bar 的 BUY/SELL 序列应与 generate_signals 完全一致"""
    df = _make_klines()

    batch = strategy.generate_signals(df)
    batch_sigs = batch.signals["signal"]

    inc_strategy = MACDAgentStrategy("macd_agent", {"higher_tfs": ["1h"]})
    inc_sigs = [inc_strategy.on_bar(row) for _, row in df.iterrows()]

    assert len(inc_sigs) == len(batch_sigs)
    for i, (got, want) in enumerate(zip(inc_sigs, batch_sigs)):
        assert got == want, f"bar {i}: 增量 {got} != 批处理 {want}"


def test_on_bar_dedup_same_bar(strategy):
    """重复喂入同一根 K 线不重复触发信号"""
    df = _make_klines()
    sigs = [strategy.on_bar(row) for _, row in df.iterrows()]
    # 重喂最后一根（模拟页面刷新重复推送）
    again = strategy.on_bar(df.iloc[-1])
    assert again == Signal.HOLD
    # 至少产生过交易信号（数据设计应触发共振）
    assert any(s in (Signal.BUY, Signal.SELL) for s in sigs)


def test_on_bar_warmup(strategy):
    """预热期内只返回 HOLD"""
    df = _make_klines(n=20)
    for _, row in df.iterrows():
        assert strategy.on_bar(row) == Signal.HOLD


def test_on_bar_empty_events_no_crash():
    """缓冲区刚满 min_bars 且无任何事件时不得抛 KeyError（空 ts 列回归）

    完全平直的价格不会产生 MACD/KDJ/BOLL 事件，事件流为空 DataFrame（无 ts 列）。
    """
    idx = pd.date_range("2026-01-01", periods=40, freq="1h", tz="UTC")
    flat = pd.DataFrame(
        {"open": 3000.0, "high": 3000.0, "low": 3000.0, "close": 3000.0, "volume": 100.0},
        index=idx,
    )
    strategy = MACDAgentStrategy("macd_agent", {"higher_tfs": ["1h"]})
    for _, row in flat.iterrows():
        assert strategy.on_bar(row) == Signal.HOLD


def _trend_klines(n: int, start: float, slope: float, seed: int = 7) -> pd.DataFrame:
    """持续趋势 + 正弦波动的 K 线（趋势决定 regime，波动制造 MACD 交叉）"""
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


def test_daily_regime_mask_trend():
    """regime 掩码：单边下跌全程为 False，单边上涨在后段为 True"""
    from strategies.macd_agent import _daily_regime_mask

    n = 90 * 24
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    up = pd.DataFrame({"close": np.linspace(2000, 4000, n)}, index=idx)
    dn = pd.DataFrame({"close": np.linspace(4000, 2000, n)}, index=idx)
    up_mask = _daily_regime_mask(up, 50)
    assert not _daily_regime_mask(dn, 50).any()      # 下跌：从不允许开多
    assert up_mask[-30 * 24:].all()                  # 上涨：后段全程允许
    assert not up_mask[:2 * 24].any()                # 前 2 天（shift 预热）不允许


def test_trend_filter_blocks_downtrend_buys():
    """下跌趋势中，开启过滤后 BUY 信号应为 0（关闭时确有金叉开多）"""
    df = _trend_klines(2000, 5000, -1.2)
    params = {"higher_tfs": ["1h"], "score_threshold": 0.1, "min_confidence": 0.3}
    off = MACDAgentStrategy("macd_agent", dict(params))
    on = MACDAgentStrategy("macd_agent", dict(params, trend_filter="ema50"))
    buys_off = (off.generate_signals(df).signals["signal"] == Signal.BUY).sum()
    buys_on = (on.generate_signals(df).signals["signal"] == Signal.BUY).sum()
    assert buys_off > 0   # 确认数据本身会产生开多信号
    assert buys_on == 0   # 下跌 regime 全部被挡


def test_trend_filter_allows_uptrend_buys():
    """上涨趋势中，开启过滤后 BUY 信号不受影响"""
    df = _trend_klines(2000, 2000, 1.2)
    params = {
        "higher_tfs": ["1h"], "score_threshold": 0.1, "min_confidence": 0.3,
        "trend_filter": "ema50",
    }
    on = MACDAgentStrategy("macd_agent", params)
    buys = (on.generate_signals(df).signals["signal"] == Signal.BUY).sum()
    assert buys > 0
