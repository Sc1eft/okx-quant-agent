"""data_provider 深度分页测试：突破 /market/candles 1440 根窗口上限"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from frontend.utils import data_provider


def _klines_page(start_ts_ms: int, count: int, step_ms: int = 3_600_000) -> list[dict]:
    """构造一页 K 线（OKX 风格：newest-first）"""
    return [
        {
            "timestamp": start_ts_ms - i * step_ms,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "vol": 10.0, "vol_ccy": 1005.0,
        }
        for i in range(count)
    ]


def _patched_client(phase1_pages: list[list[dict]], phase2_pages: list[list[dict]]):
    client = MagicMock()
    client.get_klines.side_effect = phase1_pages
    client.get_history_klines.side_effect = phase2_pages
    # fetch_okx_data 在函数体内 import OKXClient，须 patch 源模块
    return patch("okx_client.OKXClient", MagicMock(return_value=client)), client


def test_fetch_okx_data_crosses_1440_boundary():
    """/candles 返回短页（1440 窗口边界）后，自动用 /history-candles 补足"""
    cfg = Config()
    page1 = _klines_page(1_720_000_000_000, 300)   # /candles 满页 300
    page2 = _klines_page(page1[-1]["timestamp"] - 3_600_000, 144)  # 窗口边界短页
    remaining = 500 - 300 - 144
    page3 = _klines_page(page2[-1]["timestamp"] - 3_600_000, remaining)

    patcher, client = _patched_client([page1, page2], [page3])
    with patcher:
        df = data_provider.fetch_okx_data(cfg, limit=500, timeframe="1h", symbol="ETH-USDT")

    assert len(df) == 500
    assert df.index.is_monotonic_increasing
    assert not df.index.duplicated().any()
    # phase2 用 /history-candles 接续：limit 截到 100 以内，after=当前最旧 ts
    client.get_history_klines.assert_called_once()
    _, kwargs = client.get_history_klines.call_args
    assert kwargs["limit"] == remaining
    assert kwargs["after"] == page2[-1]["timestamp"]


def test_fetch_okx_data_short_limit_stays_on_candles():
    """小 limit 在 /candles 内拿满，不触碰 /history-candles"""
    cfg = Config()
    page1 = _klines_page(1_720_000_000_000, 200)

    patcher, client = _patched_client([page1], [])
    with patcher:
        df = data_provider.fetch_okx_data(cfg, limit=200, timeframe="1h", symbol="ETH-USDT")

    assert len(df) == 200
    client.get_history_klines.assert_not_called()


def _http_429() -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://www.okx.com/api/v5/market/candles")
    return httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))


def test_retry_429_recovers():
    """429 重试：前两次限频、第三次成功"""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_429()
        return "ok"

    with patch.object(data_provider.time, "sleep"):
        assert data_provider._retry_429(flaky) == "ok"
    assert calls["n"] == 3


def test_retry_429_passes_other_http_errors():
    """非 429 的 HTTP 错误不重试，直接抛出"""
    req = httpx.Request("GET", "https://www.okx.com")
    err = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))

    def boom():
        raise err

    with patch.object(data_provider.time, "sleep"):
        with pytest.raises(httpx.HTTPStatusError):
            data_provider._retry_429(boom)
