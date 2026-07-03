"""测试多周期信心分 — Phase 4"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.confidence_scorer import ConfidenceScorer
from agents.event_bus import AgentEvent, AgentEventType
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig(
        confidence_scorer_enabled=True,
        confidence_timeframe_weights={"15m": 0.3, "1h": 0.5, "1d": 0.7},
        confidence_signal_directions={
            "macd_bullish_cross": 0.8,
            "macd_bearish_cross": -0.8,
            "macd_hist_positive": 0.5,
            "macd_hist_negative": -0.5,
            "kdj_bullish_cross": 0.6,
            "kdj_bearish_cross": -0.6,
            "kdj_overbought": -0.4,
            "kdj_oversold": 0.4,
            "boll_break_upper": 0.3,
            "boll_break_lower": -0.3,
            "boll_squeeze": 0.0,
        },
    )


def _make_agent1_event(signal: str, timeframe: str, confidence: float = 0.8) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.TECHNICAL_SIGNAL,
        source="agent1",
        data={"signal": signal, "timeframe": timeframe, "price": 3500, "description": f"{signal} on {timeframe}"},
        confidence=confidence,
        urgency="high",
        timestamp=datetime.now(timezone.utc),
    )


class TestConfidenceScorer:

    def test_empty_events_returns_neutral(self, config):
        """无信号时返回中性"""
        scorer = ConfidenceScorer(config)
        result = scorer.compute([])
        assert result["composite_score"] == 0.0
        assert result["composite_confidence"] == 0.0
        assert result["signal_count"] == 0

    def test_non_agent1_events_ignored(self, config):
        """非 agent1 事件被忽略"""
        scorer = ConfidenceScorer(config)
        events = [
            AgentEvent(type=AgentEventType.NEWS_EVENT, source="agent2",
                       data={"title": "test"}, confidence=0.8, urgency="low"),
        ]
        result = scorer.compute(events)
        assert result["signal_count"] == 0
        assert result["composite_score"] == 0.0

    def test_single_bullish_signal(self, config):
        """单一偏多信号"""
        scorer = ConfidenceScorer(config)
        events = [_make_agent1_event("macd_bullish_cross", "1h", 0.85)]
        result = scorer.compute(events)
        assert result["signal_count"] == 1
        assert result["composite_score"] > 0
        # macd_bullish_cross = 0.8 * 0.85 * 0.5 = 0.34 / 0.34 = 1.0
        assert result["composite_score"] == pytest.approx(1.0, abs=0.01)

    def test_single_bearish_signal(self, config):
        """单一偏空信号"""
        scorer = ConfidenceScorer(config)
        events = [_make_agent1_event("macd_bearish_cross", "1h", 0.85)]
        result = scorer.compute(events)
        assert result["signal_count"] == 1
        assert result["composite_score"] < 0

    def test_timeframe_weighting(self, config):
        """相同信号在不同时间帧上有不同权重"""
        scorer = ConfidenceScorer(config)
        events_15m = [_make_agent1_event("macd_bullish_cross", "15m")]
        events_1d = [_make_agent1_event("macd_bullish_cross", "1d")]
        # 15m 的权重 0.3 < 1d 的权重 0.7, 所以 1d 的得分起点更高
        # 但归一化后都是 1.0 (单一信号), 所以需验证 individual_scores
        result_15m = scorer.compute(events_15m)
        result_1d = scorer.compute(events_1d)
        s_15m = result_15m["individual_scores"][0]
        s_1d = result_1d["individual_scores"][0]
        assert s_15m["tf_weight"] == 0.3
        assert s_1d["tf_weight"] == 0.7
        assert s_1d["weighted_score"] > s_15m["weighted_score"]

    def test_opposing_signals_cancel(self, config):
        """相反信号互相抵消"""
        scorer = ConfidenceScorer(config)
        events = [
            _make_agent1_event("macd_bullish_cross", "1h", 0.8),
            _make_agent1_event("macd_bearish_cross", "15m", 0.8),
        ]
        result = scorer.compute(events)
        # 两个方向冲突, composite_score 应在 0 附近
        assert abs(result["composite_score"]) < 0.3
        # 一致性信心较低
        assert result["composite_confidence"] < 0.6

    def test_composite_confidence_with_agreement(self, config):
        """同方向信号越多, 一致性信心越高"""
        scorer = ConfidenceScorer(config)
        events = [
            _make_agent1_event("macd_bullish_cross", "1h", 0.85),
            _make_agent1_event("kdj_bullish_cross", "15m", 0.7),
        ]
        result = scorer.compute(events)
        # 两个偏多信号 → score > 0
        assert result["composite_score"] > 0
        # 一致性信心应高于 0.5
        assert result["composite_confidence"] >= 0.5

    def test_unknown_signal_type_ignored(self, config):
        """未知信号类型被跳过"""
        scorer = ConfidenceScorer(config)
        events = [_make_agent1_event("unknown_signal", "1h")]
        result = scorer.compute(events)
        assert result["signal_count"] == 0

    def test_timeframe_breakdown(self, config):
        """时间帧分解包含所有输入的时间帧"""
        scorer = ConfidenceScorer(config)
        events = [
            _make_agent1_event("macd_bullish_cross", "1h"),
            _make_agent1_event("kdj_oversold", "15m"),
        ]
        result = scorer.compute(events)
        assert "1h" in result["timeframe_breakdown"]
        assert "15m" in result["timeframe_breakdown"]
