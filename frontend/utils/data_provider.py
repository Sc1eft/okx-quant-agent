"""Real OKX data provider — no Streamlit dependency.

Handles fetching K-line data from OKX public API (no API key required).
"""

import sys
from pathlib import Path
from typing import Optional
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config


def fetch_okx_data(
    cfg: Config,
    limit: int = 500,
    timeframe: str = "",
    symbol: str = "",
) -> pd.DataFrame:
    """从 OKX API 获取真实 K 线数据（公开接口，无需 API Key）。

    Args:
        cfg: Config 实例
        limit: K 线数量（最多 300）
        timeframe: 时间周期，为空则使用 cfg.trading.primary_timeframe
        symbol: 交易对，为空则使用 cfg.trading.symbol

    返回: OHLCV DataFrame (index=datetime, columns=[open, high, low, close, volume])
    """
    try:
        from okx_client import OKXClient
    except ImportError:
        raise RuntimeError("无法导入 OKXClient，请检查 okx_client.py")

    tf = timeframe or cfg.trading.primary_timeframe
    sym = symbol or cfg.trading.symbol
    client = OKXClient(cfg.exchange)

    try:
        raw = client.get_klines(sym, tf, limit=min(limit, 300))
    except Exception as e:
        client.close()
        raise RuntimeError(f"网络波动 - 从 OKX 获取数据失败 ({sym} {tf}): {e}")

    client.close()

    if not raw:
        raise RuntimeError("OKX 返回了空数据")

    df = pd.DataFrame(raw)
    # OKX 返回的时间戳是 UTC 毫秒 → 转为 Asia/Shanghai 时区
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        .tz_convert("Asia/Shanghai")
    )
    df = df.set_index("timestamp")
    df = df.rename(columns={"vol": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df.index.name = "timestamp"
    return df


def fetch_latest_klines(
    cfg: Config,
    limit: int = 100,
    timeframe: str = "",
    symbol: str = "",
) -> pd.DataFrame:
    """获取最新 K 线数据。"""
    return fetch_okx_data(cfg, limit=limit, timeframe=timeframe, symbol=symbol)


def fetch_klines_with_agg(
    cfg: Config,
    limit: int = 100,
    timeframe: str = "1h",
    symbol: str = "",
) -> pd.DataFrame:
    """获取 K 线数据，支持聚合非标准周期（2m, 15d 等）。

    对于 OKX 不直接支持的周期（如 2m, 15d），
    从最近的底层周期 fetch 并 pandas resample 聚合。
    """
    AGG_MAP = {
        "2m": {"base": "1m", "rule": "2T"},
        "15d": {"base": "1d", "rule": "15D"},
    }

    if timeframe in AGG_MAP:
        agg = AGG_MAP[timeframe]
        # Fetch more base candles to cover the aggregation window
        base_limit = min(limit * (2 if timeframe == "2m" else 15), 300)
        df = fetch_latest_klines(
            cfg, limit=base_limit,
            timeframe=agg["base"], symbol=symbol,
        )
        if df.empty:
            return df
        # resample 对时区感知的 DatetimeIndex 兼容性不一，先剥离时区（墙钟不变）
        _idx = df.index
        if _idx.tz is not None:
            df.index = _idx.tz_localize(None)
        df_agg = df.resample(agg["rule"]).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()
        return df_agg.tail(limit)

    return fetch_latest_klines(
        cfg, limit=limit, timeframe=timeframe, symbol=symbol,
    )


def fetch_ticker(cfg: Config, symbol: str = "") -> dict:
    """获取 OKX 实时 ticker 行情（公开接口，无需 API Key）。

    Args:
        cfg: Config 实例
        symbol: 交易对，为空则使用 cfg.trading.symbol

    返回: ticker dict (timestamp, last, bid, ask, volume_24h, change_24h)
    """
    from okx_client import OKXClient

    sym = symbol or cfg.trading.symbol
    client = OKXClient(cfg.exchange)
    try:
        ticker = client.get_ticker(sym)
    except Exception as e:
        client.close()
        raise RuntimeError(f"网络波动 - 获取 ticker 失败 ({sym}): {e}")
    client.close()
    return ticker
