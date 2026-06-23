# tests/test_agent3_phase2.py
"""测试 Agent 3 阶段二集成——风控注入、BTC检查、市场深度"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.agent3_trader import Agent3
from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig()


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def mock_deepseek():
    ds = MagicMock()
    ds.analyze.return_value = {
        "action": "hold",
        "confidence": 50,
        "entry_price_min": "",
        "entry_price_max": "",
        "position_size_pct": "",
        "stop_loss": "",
        "take_profit": "",
        "reason": "test",
    }
    return ds


@pytest.fixture
def mock_risk_manager():
    rm = MagicMock()
    rm.check_layer1.return_value = (True, "")
    rm.get_position_size_multiplier.return_value = 1.0
    rm.get_status.return_value = {
        "daily_trade_count": 2,
        "max_daily_trades": 10,
        "daily_loss_usdt": 10.0,
        "max_daily_loss_usdt": 100.0,
        "consecutive_losses": 0,
        "max_consecutive_losses": 3,
        "position_size_multiplier": 1.0,
        "position_eth": 0,
        "position_side": None,
    }
    return rm


@pytest.fixture
def mock_executor():
    ex = MagicMock()
    ex.symbol = "ETH-USDT"
    ex.execute_safe = AsyncMock(return_value={
        "success": True, "order_id": "test123", "fill_price": 3450.0,
    })
    return ex


@pytest.fixture
def mock_root_config():
    cfg = MagicMock()
    cfg.trading.symbol = "ETH-USDT"
    return cfg


@pytest.fixture
def agent3(config, event_bus, mock_deepseek, mock_risk_manager, mock_executor, mock_root_config):
    return Agent3(
        config=config,
        event_bus=event_bus,
        deepseek=mock_deepseek,
        risk_manager=mock_risk_manager,
        trade_executor=mock_executor,
        root_config=mock_root_config,
    )


class TestRiskStatusInjection:
    def test_build_context_includes_risk_status(self, agent3, mock_risk_manager):
        """验证 _build_context 注入风控状态"""
        mock_risk_manager.get_status.return_value = {
            "daily_trade_count": 3,
            "max_daily_trades": 10,
            "consecutive_losses": 1,
            "position_size_multiplier": 0.75,
            "daily_loss_usdt": 25.0,
            "max_daily_loss_usdt": 100.0,
            "position_eth": 0,
            "position_side": None,
        }
        context = agent3._build_context([])
        assert "risk_status" in context
        rs = context["risk_status"]
        assert rs["daily_trade_count"] == 3
        assert rs["consecutive_losses"] == 1
        assert rs["position_size_multiplier"] == 0.75

    def test_build_context_with_events(self, agent3):
        """验证上下文包含技术和新闻事件"""
        events = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "MACD金叉", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
            AgentEvent(type=AgentEventType.NEWS_EVENT, source="agent2",
                       data={"title": "ETH ETF获批", "source": "CoinDesk", "weight": 0.8},
                       timestamp=datetime.now(timezone.utc)),
        ]
        context = agent3._build_context(events)
        assert "MACD金叉" in context["agent1_summary"]
        assert "ETH ETF获批" in context["agent2_summary"]
        assert context["current_price"] == 3500.0

    def test_build_context_empty(self, agent3):
        """无事件时上下文包含默认值"""
        context = agent3._build_context([])
        assert "暂无技术面信号" in context["agent1_summary"]
        assert "暂无新闻数据" in context["agent2_summary"]
        assert "risk_status" in context


class TestDeepSeekPromptUpdate:
    def test_context_passed_to_deepseek(self, agent3, mock_deepseek):
        """验证上下文正确传递给 DeepSeek"""
        events = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "BOLL上轨突破", "timeframe": "15m", "price": 3510},
                       timestamp=datetime.now(timezone.utc)),
        ]
        context = agent3._build_context(events)

        # 模拟 _make_decision 中的 analyze 调用
        agent3.deepseek.analyze(context)
        mock_deepseek.analyze.assert_called_once()
        called_context = mock_deepseek.analyze.call_args[0][0]
        assert called_context["current_price"] == 3510.0
        assert "risk_status" in called_context


class TestBtcDepthChecks:
    @pytest.mark.asyncio
    async def test_btc_check_called(self, agent3, mock_risk_manager):
        """有 okx_client 时 BTC 波动检查被调用"""
        mock_client = MagicMock()
        mock_client.__bool__ = MagicMock(return_value=True)
        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(True, ""))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(True, "", False))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3.okx_client = mock_client
        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        # Mock analyze to return buy
        agent3.deepseek.analyze.return_value = {
            "action": "buy",
            "confidence": 80,
            "entry_price_min": "3490",
            "entry_price_max": "3510",
            "position_size_pct": "10",
            "stop_loss": "3450",
            "take_profit": "3600",
            "reason": "测试买入",
        }

        # Make executor execute_safe return success
        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        await agent3._make_decision()
        mock_risk_manager.check_btc_volatility_async.assert_called_once_with(mock_client)

    @pytest.mark.asyncio
    async def test_depth_check_called(self, agent3, mock_risk_manager):
        """有 okx_client 时市场深度检查被调用"""
        mock_client = MagicMock()
        mock_client.__bool__ = MagicMock(return_value=True)
        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(True, ""))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(True, "", False))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3.okx_client = mock_client
        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        # Mock analyze to return buy
        agent3.deepseek.analyze.return_value = {
            "action": "buy",
            "confidence": 80,
            "entry_price_min": "3490",
            "entry_price_max": "3510",
            "position_size_pct": "10",
            "stop_loss": "3450",
            "take_profit": "3600",
            "reason": "测试买入",
        }

        # Make executor execute_safe return success
        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        await agent3._make_decision()
        # check_market_depth_async should have been called with (client, side, size)
        mock_risk_manager.check_market_depth_async.assert_called_once()
        args = mock_risk_manager.check_market_depth_async.call_args[0]
        assert args[0] is mock_client  # client
        assert args[1] == "buy"  # side

    @pytest.mark.asyncio
    async def test_btc_check_blocks_trade(self, agent3, mock_risk_manager):
        """BTC 波动检查不通过时交易被阻止"""
        mock_client = MagicMock()
        mock_client.__bool__ = MagicMock(return_value=True)
        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(False, "BTC 波动过大"))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(True, "", False))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3.okx_client = mock_client
        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.deepseek.analyze.return_value = {
            "action": "buy", "confidence": 80,
            "entry_price_min": "", "entry_price_max": "",
            "position_size_pct": "", "stop_loss": "", "take_profit": "",
            "reason": "测试",
        }

        await agent3._make_decision()
        assert agent3._stats["trades_skipped"] == 1
        assert agent3._stats["trades_executed"] == 0

    @pytest.mark.asyncio
    async def test_depth_check_blocks_trade(self, agent3, mock_risk_manager):
        """市场深度不通过时交易被阻止"""
        mock_client = MagicMock()
        mock_client.__bool__ = MagicMock(return_value=True)
        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(True, ""))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(False, "深度不足", True))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3.okx_client = mock_client
        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.deepseek.analyze.return_value = {
            "action": "buy", "confidence": 80,
            "entry_price_min": "", "entry_price_max": "",
            "position_size_pct": "", "stop_loss": "", "take_profit": "",
            "reason": "测试",
        }

        await agent3._make_decision()
        assert agent3._stats["trades_skipped"] == 1
        assert agent3._stats["trades_executed"] == 0

    @pytest.mark.asyncio
    async def test_no_client_skips_checks(self, agent3, mock_risk_manager):
        """无 okx_client 时跳过 BTC/深度检查"""
        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(True, ""))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(True, "", False))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3.okx_client = None
        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.deepseek.analyze.return_value = {
            "action": "hold", "confidence": 50,
            "entry_price_min": "", "entry_price_max": "",
            "position_size_pct": "", "stop_loss": "", "take_profit": "",
            "reason": "无客户端跳过检查",
        }

        await agent3._make_decision()
        mock_risk_manager.check_btc_volatility_async.assert_not_called()
        mock_risk_manager.check_market_depth_async.assert_not_called()
        # Since action is hold, trades_skipped incremented
        assert agent3._stats["trades_skipped"] == 1

    @pytest.mark.asyncio
    async def test_prefer_limit_passed_to_executor(self, agent3, mock_risk_manager):
        """深度检查返回的 prefer_limit 被传递给执行器"""
        mock_client = MagicMock()
        mock_client.__bool__ = MagicMock(return_value=True)
        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(True, ""))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(True, "价差过大", True))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3.okx_client = mock_client
        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.deepseek.analyze.return_value = {
            "action": "buy", "confidence": 80,
            "entry_price_min": "", "entry_price_max": "",
            "position_size_pct": "10", "stop_loss": "3450", "take_profit": "3600",
            "reason": "测试",
        }

        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        await agent3._make_decision()
        agent3.executor.execute_safe.assert_called_once()
        _, kwargs = agent3.executor.execute_safe.call_args
        assert kwargs.get("prefer_limit") is True


class TestPositionMonitorNotify:
    @pytest.mark.asyncio
    async def test_position_monitor_notified(self, agent3, mock_risk_manager):
        """交易成功后通知 PositionMonitor"""
        mock_client = MagicMock()
        agent3.okx_client = mock_client

        mock_monitor = MagicMock()
        agent3.position_monitor = mock_monitor

        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(True, ""))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(True, "", False))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.deepseek.analyze.return_value = {
            "action": "buy", "confidence": 80,
            "entry_price_min": "3490", "entry_price_max": "3510",
            "position_size_pct": "10", "stop_loss": "3450", "take_profit": "3600",
            "reason": "测试买入",
        }

        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        await agent3._make_decision()
        mock_monitor.update_position.assert_called_once()
        args, kwargs = mock_monitor.update_position.call_args
        assert kwargs.get("side") == "buy"
        assert kwargs.get("size") == 0.01  # default suggested size
        assert kwargs.get("entry_price") == 3500.0
        assert kwargs.get("stop_loss") == 3450.0
        assert kwargs.get("take_profit") == 3600.0

    @pytest.mark.asyncio
    async def test_position_monitor_not_called_on_failure(self, agent3, mock_risk_manager):
        """交易失败时不通知 PositionMonitor"""
        mock_client = MagicMock()
        agent3.okx_client = mock_client

        mock_monitor = MagicMock()
        agent3.position_monitor = mock_monitor

        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(True, ""))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(True, "", False))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.deepseek.analyze.return_value = {
            "action": "buy", "confidence": 80,
            "entry_price_min": "3490", "entry_price_max": "3510",
            "position_size_pct": "10", "stop_loss": "3450", "take_profit": "3600",
            "reason": "测试买入",
        }

        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": False, "error": "订单被拒绝",
        })

        await agent3._make_decision()
        mock_monitor.update_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_monitor_no_error(self, agent3, mock_risk_manager):
        """没有 PositionMonitor 也不会报错"""
        mock_client = MagicMock()
        agent3.okx_client = mock_client
        agent3.position_monitor = None

        mock_risk_manager.check_btc_volatility_async = AsyncMock(return_value=(True, ""))
        mock_risk_manager.check_market_depth_async = AsyncMock(return_value=(True, "", False))
        mock_risk_manager.check_layer1.return_value = (True, "")

        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.deepseek.analyze.return_value = {
            "action": "buy", "confidence": 80,
            "entry_price_min": "", "entry_price_max": "",
            "position_size_pct": "", "stop_loss": "3450", "take_profit": "3600",
            "reason": "测试",
        }

        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        # Should not raise
        await agent3._make_decision()
        assert agent3._stats["trades_executed"] == 1
