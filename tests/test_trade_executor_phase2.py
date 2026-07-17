"""测试 TradeExecutor 阶段二升级（限价单完整生命周期、滑点保护、部分成交）"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.trade_executor import TradeExecutor
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig()


@pytest.fixture
def okx_mock():
    """模拟 OKXClient"""
    mock = MagicMock()
    mock.place_order.return_value = [{"ordId": "12345"}]
    mock.get_order.return_value = {
        "ordId": "12345", "state": "filled", "fillPx": "3450.00",
        "fillSz": "0.01", "accFillSz": "0.01", "side": "buy",
        "instId": "ETH-USDT",
    }
    mock.cancel_order.return_value = {"ordId": "12345"}
    return mock


@pytest.fixture
def executor(okx_mock):
    return TradeExecutor(okx_client=okx_mock, symbol="ETH-USDT")


class TestExecuteLimit:
    @pytest.mark.asyncio
    async def test_limit_order_fills_normally(self, executor, okx_mock):
        """测试限价单正常成交流程"""
        okx_mock.get_order.return_value = {
            "ordId": "12345", "state": "filled", "fillPx": "3450.00",
            "fillSz": "0.01", "accFillSz": "0.01", "side": "sell",
            "instId": "ETH-USDT",
        }
        result = await executor.execute_limit("sell", "0.01", "3450.00")
        assert result["success"] is True
        assert result["order_id"] == "12345"
        assert result["fill_price"] == 3450.00
        # 验证下单调用
        okx_mock.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_limit_order_unfilled_cancel(self, executor, okx_mock):
        """测试限价单未成交→撤单→市价单兜底"""
        # 第1次查询: 未成交; 撤单后第2次: 已撤销; 之后市价兜底单查询: 已成交
        okx_mock.get_order.side_effect = [
            {"ordId": "12345", "state": "live", "fillPx": "",
             "fillSz": "0", "accFillSz": "0", "side": "sell", "instId": "ETH-USDT"},
            {"ordId": "12345", "state": "canceled", "fillPx": "",
             "fillSz": "0", "accFillSz": "0", "side": "sell", "instId": "ETH-USDT"},
            {"ordId": "12345", "state": "filled", "fillPx": "3450.00",
             "fillSz": "0.01", "accFillSz": "0.01", "side": "sell", "instId": "ETH-USDT"},
            {"ordId": "12345", "state": "filled", "fillPx": "3450.00",
             "fillSz": "0.01", "accFillSz": "0.01", "side": "sell", "instId": "ETH-USDT"},
        ]
        result = await executor.execute_limit("sell", "0.01", "3450.00", timeout_seconds=0.1)
        # 应该调用了 cancel_order
        okx_mock.cancel_order.assert_called_once()
        # 市价单兜底
        assert result["success"] is True
        assert result["note"] == "限价单未成交→市价单兜底"

    @pytest.mark.asyncio
    async def test_limit_order_unfilled_cancel_not_confirmed(self, executor, okx_mock):
        """撤单后订单仍为 live → 拒绝市价兜底（防双仓）"""
        okx_mock.get_order.return_value = {
            "ordId": "12345", "state": "live", "fillPx": "",
            "fillSz": "0", "accFillSz": "0", "side": "sell",
            "instId": "ETH-USDT",
        }
        result = await executor.execute_limit("sell", "0.01", "3450.00", timeout_seconds=0.1)
        okx_mock.cancel_order.assert_called_once()
        assert result["success"] is False
        assert "撤单未生效" in result["error"]

    @pytest.mark.asyncio
    async def test_limit_order_partial_fill_cancel_remainder(self, executor, okx_mock):
        """测试限价单部分成交→撤销剩余→报告实际成交"""
        okx_mock.get_order.return_value = {
            "ordId": "12345", "state": "partially_filled", "fillPx": "3450.00",
            "fillSz": "0.005", "accFillSz": "0.005", "side": "sell",
            "instId": "ETH-USDT",
        }
        result = await executor.execute_limit("sell", "0.01", "3450.00", timeout_seconds=0.1)
        assert result["success"] is True
        assert result["filled_size"] == 0.005  # 部分成交
        assert result["filled_pct"] == 50.0    # 50% 成交
        okx_mock.cancel_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_limit_order_slippage_recorded_not_rejected(self, executor, okx_mock):
        """限价单已成交后滑点只记录不拒绝（报失败会导致仓位失控）"""
        okx_mock.get_order.return_value = {
            "ordId": "12345", "state": "filled", "fillPx": "3500.00",
            "fillSz": "0.01", "accFillSz": "0.01", "side": "sell",
            "instId": "ETH-USDT",
        }
        # signal_price=3450, fill=3500 → 滑点 = |3500-3450|/3450 = 1.45% > 0.3%
        result = await executor.execute_limit(
            "sell", "0.01", "3450.00", timeout_seconds=0.1, signal_price=3450.00
        )
        assert result["success"] is True  # 已成交，必须如实上报
        assert result["fill_price"] == 3500.00
        assert result["slippage_pct"] > 0.3

    @pytest.mark.asyncio
    async def test_limit_order_place_order_fails(self, executor, okx_mock):
        """测试限价单下单失败→转市价单"""
        # 第一次调用（限价单）失败，第二次调用（市价单兜底）成功
        okx_mock.place_order.side_effect = [RuntimeError("API timeout"), [{"ordId": "67890"}]]
        result = await executor.execute_limit("buy", "0.01", "3450.00")
        assert result["success"] is True  # 兜底成功
        assert result["note"] == "限价单提交失败→市价单兜底"
