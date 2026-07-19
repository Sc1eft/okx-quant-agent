# tests/test_agent3_phase2.py
"""测试 Agent 3 阶段二集成——风控注入、RuleEngine 两阶段检查、仓位通知"""
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
from agents.rule_engine.base import RuleResult


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
    rm.is_daily_limit_reached.return_value = False  # 防止进入每日上限暂停睡眠
    rm.get_position.return_value = (None, 0.0)
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
def mock_rule_decider():
    rd = MagicMock()
    rd.decide.return_value = {
        "action": "hold",
        "confidence": 50,
        "entry_price_min": "",
        "entry_price_max": "",
        "position_size_pct": "",
        "stop_loss": "",
        "take_profit": "",
        "add_to_position": None,
        "reason": "test",
    }
    return rd


@pytest.fixture
def mock_rule_engine():
    """RuleEngine mock：默认全部通过，market_depth 返回 prefer_limit=False"""
    engine = MagicMock()
    engine.check_pre_trade = AsyncMock(return_value=[])
    engine.check_execution = AsyncMock(return_value=[
        RuleResult(rule_name="market_depth", passed=True, reason="",
                   severity="info", data={"prefer_limit": False}),
    ])
    engine.all_pass.side_effect = lambda results: all(r.passed for r in results)
    engine.blocked_by.side_effect = lambda results: next(
        (r.rule_name for r in results if not r.passed), None
    )
    return engine


@pytest.fixture
def agent3(config, event_bus, mock_deepseek, mock_risk_manager, mock_executor,
           mock_root_config, mock_rule_decider, mock_rule_engine):
    return Agent3(
        config=config,
        event_bus=event_bus,
        deepseek=mock_deepseek,
        risk_manager=mock_risk_manager,
        trade_executor=mock_executor,
        root_config=mock_root_config,
        rule_decider=mock_rule_decider,
        rule_engine=mock_rule_engine,
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


class TestRuleDeciderContext:
    @pytest.mark.asyncio
    async def test_decide_receives_current_price(self, agent3, mock_rule_decider):
        """_make_decision 把事件中的当前价格传给 RuleDecider"""
        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "BOLL上轨突破", "timeframe": "15m", "price": 3510},
                       timestamp=datetime.now(timezone.utc)),
        ]

        await agent3._make_decision()

        mock_rule_decider.decide.assert_called_once()
        _, kwargs = mock_rule_decider.decide.call_args
        assert kwargs["current_price"] == 3510.0


class TestRuleEngineChecks:
    """风控检查统一走 RuleEngine（pre-trade / execution 两阶段）"""

    @staticmethod
    def _buy_decision():
        return {
            "action": "buy", "confidence": 80,
            "entry_price_min": "3490", "entry_price_max": "3510",
            "position_size_pct": "10", "stop_loss": "3450", "take_profit": "3600",
            "reason": "测试买入",
        }

    @staticmethod
    def _fill_buffer(agent3):
        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

    @pytest.mark.asyncio
    async def test_pre_trade_check_called(self, agent3, mock_rule_engine):
        """决策前先过 RuleEngine pre-trade 检查"""
        agent3.okx_client = MagicMock()
        self._fill_buffer(agent3)
        agent3.rule_decider.decide.return_value = self._buy_decision()
        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        await agent3._make_decision()
        mock_rule_engine.check_pre_trade.assert_called_once()

    @pytest.mark.asyncio
    async def test_execution_check_called(self, agent3, mock_rule_engine):
        """决策买入后过 RuleEngine execution 检查（上下文含方向/数量）"""
        agent3.okx_client = MagicMock()
        self._fill_buffer(agent3)
        agent3.rule_decider.decide.return_value = self._buy_decision()
        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        await agent3._make_decision()
        mock_rule_engine.check_execution.assert_called_once()
        ctx = mock_rule_engine.check_execution.call_args[0][0]
        assert ctx["side"] == "buy"
        assert ctx["size"] > 0

    @pytest.mark.asyncio
    async def test_pre_trade_rule_blocks_trade(self, agent3, mock_rule_engine):
        """pre-trade 规则拒绝（如波动过大）→ 不交易、不进入决策"""
        mock_rule_engine.check_pre_trade.return_value = [
            RuleResult(rule_name="volatility_check", passed=False,
                       reason="波动过大", data={"delay_seconds": 300}),
        ]
        agent3.okx_client = MagicMock()
        self._fill_buffer(agent3)
        agent3.rule_decider.decide.return_value = self._buy_decision()

        await agent3._make_decision()
        assert agent3._stats["trades_skipped"] == 1
        assert agent3._stats["trades_executed"] == 0
        agent3.rule_decider.decide.assert_not_called()

    @pytest.mark.asyncio
    async def test_execution_rule_blocks_trade(self, agent3, mock_rule_engine):
        """execution 规则拒绝（如深度不足）→ 不交易"""
        mock_rule_engine.check_execution.return_value = [
            RuleResult(rule_name="market_depth", passed=False,
                       reason="深度不足", data={"prefer_limit": True}),
        ]
        agent3.okx_client = MagicMock()
        self._fill_buffer(agent3)
        agent3.rule_decider.decide.return_value = self._buy_decision()

        await agent3._make_decision()
        assert agent3._stats["trades_skipped"] == 1
        assert agent3._stats["trades_executed"] == 0

    @pytest.mark.asyncio
    async def test_prefer_limit_passed_to_executor(self, agent3, mock_rule_engine):
        """market_depth 结果的 prefer_limit 被传递给执行器"""
        mock_rule_engine.check_execution.return_value = [
            RuleResult(rule_name="market_depth", passed=True, reason="价差过大",
                       severity="info", data={"prefer_limit": True}),
        ]
        agent3.okx_client = MagicMock()
        self._fill_buffer(agent3)
        agent3.rule_decider.decide.return_value = self._buy_decision()
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
        # PositionMonitor 是仓位事实源：mock 需返回真实的空持仓状态
        mock_monitor.get_status.return_value = {
            "position_side": "none", "position_size": 0.0, "entry_price": 0.0,
        }
        agent3.position_monitor = mock_monitor

        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.rule_decider.decide.return_value = {
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
        assert kwargs.get("side") == "long"  # "buy" → "long" (Fix 3: 方向字段一致性)
        assert kwargs.get("size") == 0.05  # max 0.5 × 10% (position_size_pct)
        assert kwargs.get("entry_price") == 3500.0
        assert kwargs.get("stop_loss") == 3450.0
        assert kwargs.get("take_profit") == 3600.0

    @pytest.mark.asyncio
    async def test_position_monitor_not_called_on_failure(self, agent3, mock_risk_manager):
        """交易失败时不通知 PositionMonitor"""
        mock_client = MagicMock()
        agent3.okx_client = mock_client

        mock_monitor = MagicMock()
        # PositionMonitor 是仓位事实源：mock 需返回真实的空持仓状态
        mock_monitor.get_status.return_value = {
            "position_side": "none", "position_size": 0.0, "entry_price": 0.0,
        }
        agent3.position_monitor = mock_monitor

        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.rule_decider.decide.return_value = {
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

        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]

        agent3.rule_decider.decide.return_value = {
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


class TestSlTpDirectionValidation:
    """决策给出方向错误的 SL/TP 时回退默认值（防开仓即触发止损）"""

    @pytest.mark.asyncio
    async def test_wrong_direction_sltp_falls_back_to_defaults(self, agent3, mock_risk_manager):
        """多头 SL>入场 / TP<入场 → 回退配置默认百分比"""
        agent3.okx_client = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_status.return_value = {
            "position_side": "none", "position_size": 0.0, "entry_price": 0.0,
        }
        agent3.position_monitor = mock_monitor

        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]
        # buy（多头）却给了 SL=3600 > 入场、TP=3400 < 入场 — 方向全错
        agent3.rule_decider.decide.return_value = {
            "action": "buy", "confidence": 80,
            "entry_price_min": "", "entry_price_max": "",
            "position_size_pct": "10", "stop_loss": "3600", "take_profit": "3400",
            "reason": "方向错误的止损止盈",
        }
        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        await agent3._make_decision()

        mock_monitor.update_position.assert_called_once()
        _, kwargs = mock_monitor.update_position.call_args
        # 回退到配置默认值：SL 5% → 3500*0.95, TP 10% → 3500*1.10
        assert kwargs["stop_loss"] == pytest.approx(3325.0)
        assert kwargs["take_profit"] == pytest.approx(3850.0)

    @pytest.mark.asyncio
    async def test_correct_direction_sltp_kept(self, agent3, mock_risk_manager):
        """方向正确的 SL/TP 原样保留"""
        agent3.okx_client = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.get_status.return_value = {
            "position_side": "none", "position_size": 0.0, "entry_price": 0.0,
        }
        agent3.position_monitor = mock_monitor

        agent3._event_buffer = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "测试", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
        ]
        agent3.rule_decider.decide.return_value = {
            "action": "buy", "confidence": 80,
            "entry_price_min": "", "entry_price_max": "",
            "position_size_pct": "10", "stop_loss": "3450", "take_profit": "3600",
            "reason": "正常的止损止盈",
        }
        agent3.executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "order123", "fill_price": 3500.0,
        })

        await agent3._make_decision()

        _, kwargs = mock_monitor.update_position.call_args
        assert kwargs["stop_loss"] == pytest.approx(3450.0)
        assert kwargs["take_profit"] == pytest.approx(3600.0)
