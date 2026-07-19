"""DailyTrendStrategy 测试

合成 1h K 线（日收盘序列线性插值），覆盖：
  - 多头 regime 入场/持有、跌破离场、空头 regime 不交易
  - 无前视（截断一致性）
  - trigger 模式等待突破（与 regime 模式的入场日区分）
  - 增量 on_bar 与批处理 generate_signals 决策一致
  - 工厂注册与默认参数

注：线性合成数据上没有布林突破/KDJ 金叉，trigger 模式不会入场；
测「机制」（regime 进出/截断/增量一致）的用例显式指定 regime 模式。
"""

import numpy as np
import pandas as pd
import pytest

from strategies.base import Signal, create_strategy
from strategies.daily_trend import DailyTrendStrategy

REGIME = {"entry_mode": "regime"}


def _mk_1h_df(daily_closes, start="2024-01-01") -> pd.DataFrame:
    """日收盘序列 → 1h K 线：每天 24 根，从上一日收盘线性过渡到当日收盘"""
    dc = np.asarray(daily_closes, dtype=float)
    hourly = []
    prev = dc[0]
    for c in dc:
        hourly.extend(np.linspace(prev, c, 25)[1:])  # 24 根，末根收盘=当日收盘
        prev = c
    hourly = np.array(hourly)
    idx = pd.date_range(start, periods=len(hourly), freq="1h", name="timestamp")
    opens = np.roll(hourly, 1)
    opens[0] = hourly[0]
    return pd.DataFrame({
        "open": opens,
        "high": np.maximum(opens, hourly) * 1.0005,
        "low": np.minimum(opens, hourly) * 0.9995,
        "close": hourly,
        "volume": 100.0,
    }, index=idx)


def _decisions(result) -> dict:
    """批处理信号 → {决策日: Signal}（信号落在决策日最后一根 1h bar）"""
    sig = result.signals
    fired = sig[sig["signal"] != Signal.HOLD]
    return {t.normalize(): row["signal"] for t, row in fired.iterrows()}


def _up_down_closes():
    """100 天 100→300，再 120 天 300→90"""
    return np.concatenate([
        np.linspace(100, 300, 100),
        np.linspace(300, 90, 120)[1:],
    ])


# ── 行为测试（regime 模式） ──

def test_uptrend_single_buy_no_sell():
    df = _mk_1h_df(np.linspace(100, 260, 200))
    res = create_strategy("daily_trend", REGIME).generate_signals(df)
    dec = _decisions(res)
    buys = [d for d, s in dec.items() if s == Signal.BUY]
    sells = [d for d, s in dec.items() if s == Signal.SELL]
    assert len(buys) == 1
    assert buys[0] == pd.Timestamp("2024-01-01") + pd.Timedelta(days=50)  # warmup 结束即入场
    assert sells == []


def test_regime_flip_exit():
    df = _mk_1h_df(_up_down_closes())
    res = create_strategy("daily_trend", REGIME).generate_signals(df)
    dec = _decisions(res)
    buys = [d for d, s in dec.items() if s == Signal.BUY]
    sells = [d for d, s in dec.items() if s == Signal.SELL]
    peak_day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=99)
    assert len(buys) == 1
    assert len(sells) == 1
    assert sells[0] > peak_day  # 跌破发生在见顶之后
    assert buys[0] < sells[0]   # 先买后卖


def test_downtrend_no_trades():
    df = _mk_1h_df(np.linspace(300, 100, 200))
    res = create_strategy("daily_trend", REGIME).generate_signals(df)
    assert _decisions(res) == {}
    assert res.metadata["decisions"] == 0


def test_short_data_no_crash():
    df = _mk_1h_df(np.linspace(100, 110, 30))  # 不足 warmup(50 天)
    res = create_strategy("daily_trend").generate_signals(df)
    assert (res.signals["signal"] == Signal.HOLD).all()


# ── 无前视 ──

def test_no_lookahead_truncation():
    df = _mk_1h_df(_up_down_closes())
    t_bars = 130 * 24  # 截断在第 130 天第一根 bar
    full = create_strategy("daily_trend", REGIME).generate_signals(df)
    trunc = create_strategy("daily_trend", REGIME).generate_signals(df.iloc[:t_bars])
    assert (full.signals["signal"].iloc[:t_bars] == trunc.signals["signal"]).all()
    assert (full.signals["reason"].iloc[:t_bars] == trunc.signals["reason"]).all()


# ── trigger 模式 ──

def test_trigger_mode_waits_for_breakout():
    closes = np.concatenate([
        np.linspace(100, 105, 80),    # 平缓上行：regime 多头但无突破
        np.linspace(105, 160, 25)[1:],  # 急涨：布林上轨突破
        np.linspace(160, 162, 25)[1:],  # 高位企稳
    ])
    df = _mk_1h_df(closes)
    day0 = pd.Timestamp("2024-01-01")

    regime_dec = _decisions(create_strategy("daily_trend", REGIME).generate_signals(df))
    trigger_dec = _decisions(create_strategy("daily_trend").generate_signals(df))  # 默认 trigger
    regime_buy = [d for d, s in regime_dec.items() if s == Signal.BUY]
    trigger_buy = [d for d, s in trigger_dec.items() if s == Signal.BUY]
    assert regime_buy == [day0 + pd.Timedelta(days=50)]       # warmup 即入场
    assert len(trigger_buy) == 1
    assert trigger_buy[0] >= day0 + pd.Timedelta(days=80)     # 等到突破才入场


def test_trigger_mode_no_trigger_no_entry():
    """纯线性上涨无任何触发 → trigger 模式整段不入场（与 regime 模式的区别）"""
    df = _mk_1h_df(np.linspace(100, 260, 200))
    res = create_strategy("daily_trend").generate_signals(df)  # 默认 trigger
    assert _decisions(res) == {}


# ── 增量与批处理一致 ──

def test_on_bar_matches_batch():
    df = _mk_1h_df(_up_down_closes())
    batch_dec = _decisions(create_strategy("daily_trend", REGIME).generate_signals(df))

    strat = create_strategy("daily_trend", REGIME)
    inc_dec = {}
    for j in range(len(df)):
        bar = df.iloc[j]
        sig = strat.on_bar(bar)
        if sig != Signal.HOLD:
            # on_bar 在日界第一根 bar 返回信号 → 决策日 = 前一天（刚收盘日）
            inc_dec[(bar.name - pd.Timedelta(days=1)).normalize()] = sig

    # 决策序列必须一致；EMA 在截断缓冲上重算与全量历史有微小差异，
    # 边界日（close≈ema 处）允许 ±1 天的落点漂移
    assert len(inc_dec) == len(batch_dec)
    inc_items = sorted(inc_dec.items())
    batch_items = sorted(batch_dec.items())
    assert [s for _, s in inc_items] == [s for _, s in batch_items]
    for (d_inc, _), (d_batch, _) in zip(inc_items, batch_items):
        assert abs((d_inc - d_batch).days) <= 1


def test_on_bar_dedup_same_day():
    """同一天内的后续 bar 不重复触发"""
    df = _mk_1h_df(np.linspace(100, 260, 80))
    strat = create_strategy("daily_trend", REGIME)
    fired = [strat.on_bar(df.iloc[j]) for j in range(len(df))]
    assert fired.count(Signal.BUY) == 1
    assert strat.position is not None


# ── 注册 ──

def test_registered_in_factory():
    strat = create_strategy("daily_trend")
    assert isinstance(strat, DailyTrendStrategy)
    assert strat._mode == "trigger"  # 注册表默认（3 年回测 trigger 优于 regime）
    assert create_strategy("daily_trend", REGIME)._mode == "regime"
    with pytest.raises(ValueError):
        create_strategy("daily_trend", {"entry_mode": "bogus"})
