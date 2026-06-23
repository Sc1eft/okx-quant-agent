"""测试 OKX API 追加的三个方法（使用 mock 避免真实网络请求）"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from okx_client import OKXClient
from config import ExchangeConfig


@pytest.fixture
def client():
    cfg = ExchangeConfig(api_key="test", secret_key="test", passphrase="test")
    client = OKXClient(cfg)
    # Mock _request 避免真实 HTTP 调用
    client._request = MagicMock()
    return client


def _mock_response(data: list):
    """构造 OKX 标准响应格式"""
    mock = MagicMock()
    mock.json.return_value = {"code": "0", "msg": "", "data": data}
    return mock


class TestCancelOrder:
    def test_cancel_order_success(self, client):
        """测试成功撤单"""
        client._request.return_value = _mock_response([{"ordId": "12345"}])
        result = client.cancel_order("ETH-USDT", "12345")
        assert result["ordId"] == "12345"
        # 验证签名和请求参数
        call_kwargs = client._request.call_args
        assert call_kwargs[0][0] == "POST"
        assert "/api/v5/trade/cancel-order" in call_kwargs[0][1]

    def test_cancel_order_api_error(self, client):
        """测试撤单 API 返回错误"""
        mock = MagicMock()
        mock.json.return_value = {"code": "51001", "msg": "订单不存在", "data": []}
        client._request.return_value = mock
        with pytest.raises(RuntimeError, match="OKX API error"):
            client.cancel_order("ETH-USDT", "99999")


class TestGetOrder:
    def test_get_order_filled(self, client):
        """测试查询已成交订单"""
        mock_data = [{
            "ordId": "12345", "state": "filled", "fillPx": "3450.50",
            "fillSz": "0.01", "accFillSz": "0.01", "side": "buy",
            "instId": "ETH-USDT",
        }]
        client._request.return_value = _mock_response(mock_data)
        result = client.get_order("ETH-USDT", "12345")
        assert result["state"] == "filled"
        assert float(result["fillPx"]) == 3450.50

    def test_get_order_partial_fill(self, client):
        """测试查询部分成交订单"""
        mock_data = [{
            "ordId": "12345", "state": "partially_filled", "fillPx": "3450.00",
            "fillSz": "0.005", "accFillSz": "0.005", "side": "buy",
            "instId": "ETH-USDT",
        }]
        client._request.return_value = _mock_response(mock_data)
        result = client.get_order("ETH-USDT", "12345")
        assert result["state"] == "partially_filled"

    def test_get_order_cancelled(self, client):
        """测试查询已取消订单"""
        mock_data = [{
            "ordId": "12345", "state": "canceled", "fillPx": "",
            "fillSz": "0", "accFillSz": "0", "side": "buy",
            "instId": "ETH-USDT",
        }]
        client._request.return_value = _mock_response(mock_data)
        result = client.get_order("ETH-USDT", "12345")
        assert result["state"] == "canceled"


class TestGetOrderBook:
    def test_get_order_book(self, client):
        """测试获取订单簿"""
        mock_data = {
            "asks": [["3451.0", "12.5", "0", "1"], ["3452.0", "8.3", "0", "2"]],
            "bids": [["3449.5", "15.2", "0", "1"], ["3448.0", "10.1", "0", "2"]],
            "ts": "1719200000000",
        }
        mock = MagicMock()
        mock.json.return_value = {"code": "0", "msg": "", "data": [mock_data]}
        client._request.return_value = mock
        result = client.get_order_book("ETH-USDT", depth=5)
        assert len(result["asks"]) == 2
        assert float(result["asks"][0][0]) == 3451.0
        assert float(result["bids"][0][0]) == 3449.5
        # 验证请求参数
        call_kwargs = client._request.call_args
        assert call_kwargs[0][0] == "GET"
        assert "/api/v5/market/books" in call_kwargs[0][1]
        # Verify params were passed (keyword args, second arg is params dict)
        kwargs = call_kwargs[1] if len(call_kwargs) > 1 else {}
        params = kwargs.get("params", {})
        assert params.get("instId") == "ETH-USDT"
        assert params.get("sz") == "5"
