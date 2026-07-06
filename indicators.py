"""
共享技术指标计算 — 纯 pandas 函数，无外部依赖

从 execution/ai_executor.py 和 frontend/utils/backtest_engine.py 提取，
消除两处完全相同的指标计算函数。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI 指标"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均"""
    return series.rolling(window=period, min_periods=1).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, pd.Series]:
    """MACD 指标，返回 {macd, signal, histogram}"""
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def calc_bollinger(
    series: pd.Series, period: int = 20, std: float = 2.0
) -> dict[str, pd.Series]:
    """布林带，返回 {middle, upper, lower}"""
    sma = calc_sma(series, period)
    std_dev = series.rolling(window=period, min_periods=1).std()
    return {
        "middle": sma,
        "upper": sma + std * std_dev,
        "lower": sma - std * std_dev,
    }


def calc_price_change(series: pd.Series, period: int = 1) -> pd.Series:
    """价格变动百分比"""
    return series.pct_change(period) * 100


def calc_indicators(df: pd.DataFrame) -> dict[str, pd.Series]:
    """对 DataFrame 计算所有常见指标

    包括 RSI(6/14/20), SMA(5/10/20/50/200), EMA(5/10/20/50/200),
    MACD, 布林带, 价格变动百分比, K线实体波动率。
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    ind: dict[str, pd.Series] = {
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
    }

    # RSI 多个周期
    for p in [6, 14, 20]:
        if len(close) >= p:
            ind[f"rsi_{p}"] = calc_rsi(close, p)

    # SMA / EMA 多个周期
    for p in [5, 10, 20, 50, 200]:
        if len(close) >= p:
            ind[f"sma_{p}"] = calc_sma(close, p)
            ind[f"ema_{p}"] = calc_ema(close, p)

    # MACD
    if len(close) >= 26:
        macd = calc_macd(close)
        ind["macd"] = macd["macd"]
        ind["macd_signal"] = macd["signal"]
        ind["macd_histogram"] = macd["histogram"]

    # 布林带
    if len(close) >= 20:
        bb = calc_bollinger(close)
        ind["bb_middle"] = bb["middle"]
        ind["bb_upper"] = bb["upper"]
        ind["bb_lower"] = bb["lower"]

    # 价格变动百分比
    ind["price_change_pct"] = calc_price_change(close, 1)
    ind["price_change_5"] = calc_price_change(close, 5)

    # K线实体波动率（用于波动率触发策略）
    body_size = (close - df["open"]).abs()
    ind["body_size"] = body_size
    ind["body_sum_2"] = body_size.rolling(window=2, min_periods=1).sum()
    # body_direction: 1=阴线(close<open), -1=阳线(close>open), 0=平盘
    direction = pd.Series(0, index=close.index)
    direction[close < df["open"]] = 1
    direction[close > df["open"]] = -1
    ind["body_direction"] = direction

    return ind


def resolve_indicator(indicator_name: str, indicators: dict) -> Optional[pd.Series]:
    """将条件中的 indicator 名映射到实际计算出的 Series"""
    if indicator_name in indicators:
        return indicators[indicator_name]

    aliases = {
        "rsi": "rsi_14",
        "sma": "sma_20",
        "ema": "ema_20",
    }
    alias = aliases.get(indicator_name)
    if alias:
        return indicators.get(alias)

    # BB / MACD 等已经是全名，直接返回
    for prefix in ("bb_", "macd_"):
        if indicator_name.startswith(prefix):
            return indicators.get(indicator_name)

    return None
