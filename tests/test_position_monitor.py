"""测试持仓监控器——止盈、止损、移动止损"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.position_monitor import PositionMonitor
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig(
        position_monitor_interval=0.05,  # 50ms 方便测试
        trailing_stop_activation_pct=3.0,
        trailing_stop_distance_pct=1.5,
    )


@pytest.fixture
def mock_risk_manager():
    rm = MagicMock()
    rm._current_position_eth = 0.01
    rm._current_position_side = "long"
    return rm


@pytest.fixture
def mock_executor():
    ex = AsyncMock()
    ex.symbol = "ETH-USDT"
    # Mock execute_market to return success
    ex.execute_market.return_value = {
        "success": True, "order_id": "sl123", "fill_price": 3400.0,
    }
    return ex


@pytest.fixture
def mock_okx_client():
    client = MagicMock()
    client.get_ticker.return_value = {"last": 3500.0}
    return client


class TestPositionMonitor:
    @pytest.mark.asyncio
    async def test_stop_loss_triggered(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """价格跌破止损 → 触发止损卖出"""
        config.trailing_stop_activation_pct = 3.0
        config.trailing_stop_distance_pct = 1.5

        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 模拟初始持仓：long @ 3500，止损 2% = 3430
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3430.0, take_profit=3700.0)

        # 价格跌到 3420 < 3430 → 触发止损
        mock_okx_client.get_ticker.return_value = {"last": 3420.0}
        triggered = await monitor._check_once()
        assert triggered is True
        assert monitor._stats["stop_loss_triggered"] == 1

    @pytest.mark.asyncio
    async def test_take_profit_triggered(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """价格涨到止盈 → 触发止盈卖出"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3600.0)

        # 价格涨到 3650 > 3600 → 触发止盈
        mock_okx_client.get_ticker.return_value = {"last": 3650.0}
        triggered = await monitor._check_once()
        assert triggered is True
        assert monitor._stats["take_profit_triggered"] == 1

    @pytest.mark.asyncio
    async def test_trailing_stop_activates(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """浮盈达到 3% 后激活移动止损，止损位上移"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 初始：long @ 3500，止损 2% = 3430
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3430.0, take_profit=3700.0)

        # 价格涨到 3650 (浮盈 4.3% > 3%) → 激活移动止损
        # 移动止损位 = 3650 * (1 - 1.5%) = 3595.25
        mock_okx_client.get_ticker.return_value = {"last": 3650.0}
        triggered = await monitor._check_once()
        assert triggered is False  # 还未触发卖出

        # 验证止损位上移了
        assert monitor._current_stop_loss > 3430.0
        assert monitor._stats["trailing_stop_activated"] == 1

    @pytest.mark.asyncio
    async def test_trailing_stop_triggers(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """移动止损激活后，价格回落到新止损位 → 触发卖出"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3430.0, take_profit=3700.0)
        monitor._trailing_high = 3650.0
        monitor._trailing_stop_active = True
        # 移动止损位 = 3650 * (1 - 1.5%) = 3595.25
        monitor._current_stop_loss = 3595.25

        # 价格回落到 3580 < 3595.25 → 触发
        mock_okx_client.get_ticker.return_value = {"last": 3580.0}
        triggered = await monitor._check_once()
        assert triggered is True

    @pytest.mark.asyncio
    async def test_no_position_no_action(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """无持仓时不做任何操作"""
        mock_risk_manager._current_position_eth = 0
        mock_risk_manager._current_position_side = None

        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        triggered = await monitor._check_once()
        assert triggered is False
        mock_executor.execute_market.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_position_take_profit_and_stop(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """空头仓位：止盈（价格跌）和止损（价格涨）方向正确"""
        mock_risk_manager._current_position_side = "short"

        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 空头：entry=3500, stop=3570(涨2%), take=3400(跌2.86%)
        monitor.update_position(side="short", size=0.01, entry_price=3500.0,
                                stop_loss=3570.0, take_profit=3400.0)

        # 价格跌到 3380 < 3400 → 止盈触发（买回平仓）
        mock_okx_client.get_ticker.return_value = {"last": 3380.0}
        triggered = await monitor._check_once()
        assert triggered is True
        assert monitor._stats["take_profit_triggered"] == 1

    @pytest.mark.asyncio
    async def test_status_report(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """get_status 返回正确统计"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3600.0)

        status = monitor.get_status()
        assert status["running"] is True
        assert status["position_side"] == "long"
        assert status["entry_price"] == 3500.0
        assert status["stop_loss"] == 3400.0
        assert status["take_profit"] == 3600.0
        assert "stop_loss_triggered" in status
