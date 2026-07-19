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


# ── 最新值摘要型指标（供 Agent 1 实时信号与前端 AI 分析共用） ──
# 与上方的 Series 型指标不同，这三个函数只返回最新一根 K 线的摘要 dict，
# 数据不足时返回 None。原位于 frontend/utils/eth_ai_analysis.py，
# 下沉到本模块作为唯一实现来源。


def calc_macd_summary(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict | None:
    """计算 MACD 指标，返回最新值摘要。

    Returns:
        {"macd": float, "signal": float, "histogram": float,
         "hist_direction": "rising"|"falling", "crossover": "bullish"|"bearish"|None}
         数据不足时返回 None。
    """
    if df is None or df.empty or len(df) < slow:
        return None
    close = df["close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    # 最新值和前值判断方向
    macd_val = float(macd_line.iloc[-1])
    sig_val = float(signal_line.iloc[-1])
    hist_val = float(histogram.iloc[-1])

    hist_direction = "rising" if len(histogram) >= 2 and histogram.iloc[-1] > histogram.iloc[-2] else "falling"

    # 金叉/死叉判断：macd 穿越 signal
    crossover = None
    if len(macd_line) >= 2:
        prev_macd = macd_line.iloc[-2]
        prev_sig = signal_line.iloc[-2]
        if prev_macd <= prev_sig and macd_val > sig_val:
            crossover = "bullish"  # 金叉
        elif prev_macd >= prev_sig and macd_val < sig_val:
            crossover = "bearish"  # 死叉

    return {
        "macd": round(macd_val, 4),
        "signal": round(sig_val, 4),
        "histogram": round(hist_val, 4),
        "hist_direction": hist_direction,
        "crossover": crossover,
    }


def calc_kdj_summary(
    df: pd.DataFrame,
    n: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> dict | None:
    """计算 KDJ 随机指标，返回最新值摘要。

    Returns:
        {"k": float, "d": float, "j": float,
         "k_cross_d": "bullish"|"bearish"|None,
         "zone": "overbought"|"oversold"|"normal"}
         数据不足时返回 None。
    """
    if df is None or df.empty or len(df) < n:
        return None

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n + 1e-10) * 100

    k = rsv.ewm(span=k_period, adjust=False).mean()
    d = k.ewm(span=d_period, adjust=False).mean()
    j = 3 * k - 2 * d

    k_val = float(k.iloc[-1])
    d_val = float(d.iloc[-1])
    j_val = float(j.iloc[-1])

    # K 穿越 D 判断
    k_cross = None
    if len(k) >= 2:
        if k.iloc[-2] <= d.iloc[-2] and k_val > d_val:
            k_cross = "bullish"
        elif k.iloc[-2] >= d.iloc[-2] and k_val < d_val:
            k_cross = "bearish"

    zone = "normal"
    if j_val >= 100:
        zone = "overbought"
    elif j_val <= 0:
        zone = "oversold"

    return {
        "k": round(k_val, 2),
        "d": round(d_val, 2),
        "j": round(j_val, 2),
        "k_cross_d": k_cross,
        "zone": zone,
    }


def calc_boll_summary(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
) -> dict | None:
    """计算布林带（Bollinger Bands）指标，返回最新值摘要。

    Returns:
        {"upper": float, "middle": float, "lower": float,
         "bandwidth": float, "position": float,
         "position_label": "above"|"below"|"inside"|"touch_upper"|"touch_lower",
         "squeeze": bool}
         数据不足时返回 None。
    """
    if df is None or df.empty or len(df) < period:
        return None

    close = df["close"].astype(float)
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()

    upper = middle + std_dev * std
    lower = middle - std_dev * std

    mid_val = float(middle.iloc[-1])
    up_val = float(upper.iloc[-1])
    low_val = float(lower.iloc[-1])
    price = float(close.iloc[-1])

    # 带宽缩小（squeeze）判断：带宽低于近期中位数的 70%
    bw_series = (upper - lower) / middle
    bw = float(bw_series.iloc[-1]) if len(bw_series) > 0 else 0.0
    squeeze = False
    if len(bw_series) >= period:
        hist_bw = float(bw_series.iloc[-period:].median())
        squeeze = bw < hist_bw * 0.7

    # 价格在布林带中的位置
    if price >= up_val:
        position_label = "touch_upper"
    elif price <= low_val:
        position_label = "touch_lower"
    else:
        position_label = "inside"

    # 计算价格在带宽中的百分比位置 (0~100)
    if up_val - low_val > 1e-10:
        pos_pct = (price - low_val) / (up_val - low_val) * 100
    else:
        pos_pct = 50.0

    return {
        "upper": round(up_val, 2),
        "middle": round(mid_val, 2),
        "lower": round(low_val, 2),
        "bandwidth": round(bw, 4),
        "position_pct": round(pos_pct, 1),
        "position_label": position_label,
        "squeeze": squeeze,
    }
