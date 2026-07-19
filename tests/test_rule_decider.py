"""测试 RuleDecider — 规则决策器（替代 DeepSeek 实时决策）"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.config import AgentSystemConfig
from agents.event_bus import AgentEvent, AgentEventType
from agents.rule_decider import RuleDecider


@pytest.fixture
def decider():
    return RuleDecider(AgentSystemConfig())


def _ev(signal: str, tf: str, confidence: float) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.TECHNICAL_SIGNAL,
        source="agent1",
        data={"signal": signal, "timeframe": tf, "price": 3500},
        timestamp=datetime.now(timezone.utc),
        confidence=confidence,
    )


class TestHoldCases:
    def test_no_events_holds(self, decider):
        d = decider.decide([], current_price=3500.0)
        assert d["action"] == "hold"
        assert d["confidence"] == 0

    def test_single_weak_signal_holds(self, decider):
        """单条短周期弱信号强度不足，不应交易"""
        d = decider.decide([_ev("kdj_overbought", "3m", 0.6)], current_price=3500.0)
        assert d["action"] == "hold"
        assert "评分不足" in d["reason"]

    def test_single_strong_signal_holds(self, decider):
        """单条 1h 金叉强度仍低于阈值（需要多周期共振）"""
        d = decider.decide([_ev("macd_bullish_cross", "1h", 0.85)], current_price=3500.0)
        assert d["action"] == "hold"

    def test_conflicting_signals_hold(self, decider):
        """多空矛盾 → 一致性不足 → hold"""
        d = decider.decide(
            [_ev("macd_bullish_cross", "1h", 0.85),
             _ev("macd_bearish_cross", "1d", 0.85)],
            current_price=3500.0,
        )
        assert d["action"] == "hold"
        assert "一致性不足" in d["reason"]

    def test_non_agent1_events_ignored(self, decider):
        """agent2 新闻事件不参与评分"""
        ev = AgentEvent(
            type=AgentEventType.NEWS_EVENT, source="agent2",
            data={"title": "利好", "weight": 0.9},
            timestamp=datetime.now(timezone.utc), confidence=0.9,
        )
        d = decider.decide([ev], current_price=3500.0)
        assert d["action"] == "hold"


class TestTradeCases:
    def test_multi_tf_bullish_alignment_buys(self, decider):
        d = decider.decide(
            [_ev("macd_bullish_cross", "15m", 0.85),
             _ev("macd_bullish_cross", "1h", 0.85)],
            current_price=3500.0,
        )
        assert d["action"] == "buy"
        assert d["confidence"] >= 60
        # 多头：SL < 入场 < TP
        assert 0 < d["stop_loss"] < 3500.0
        assert d["take_profit"] > 3500.0
        assert 5 <= d["position_size_pct"] <= 100

    def test_bearish_alignment_sells(self, decider):
        d = decider.decide(
            [_ev("macd_bearish_cross", "1h", 0.85),
             _ev("boll_break_lower", "15m", 0.75)],
            current_price=3500.0,
        )
        assert d["action"] == "sell"
        # 空头：TP < 入场 < SL
        assert 0 < d["take_profit"] < 3500.0
        assert d["stop_loss"] > 3500.0

    def test_output_shape_compatible_with_deepseek(self, decider):
        """输出 dict 必须与 deepseek.analyze 的下游消费字段兼容"""
        d = decider.decide(
            [_ev("macd_bullish_cross", "15m", 0.85),
             _ev("macd_bullish_cross", "1h", 0.85)],
            current_price=3500.0,
        )
        for key in ("action", "confidence", "entry_price_min", "entry_price_max",
                    "position_size_pct", "stop_loss", "take_profit",
                    "add_to_position", "reason"):
            assert key in d

    def test_zero_price_gives_empty_sltp(self, decider):
        d = decider.decide(
            [_ev("macd_bullish_cross", "15m", 0.85),
             _ev("macd_bullish_cross", "1h", 0.85)],
            current_price=0.0,
        )
        assert d["action"] == "buy"
        assert d["stop_loss"] == ""
        assert d["take_profit"] == ""

    def test_stats_count_calls(self, decider):
        decider.decide([], current_price=3500.0)
        decider.decide([], current_price=3500.0)
        assert decider.get_stats()["total_calls"] == 2
