"""测试三方信号对齐 — Phase 4"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.signal_aligner import SignalAligner
from agents.event_bus import AgentEvent, AgentEventType
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig(signal_aligner_enabled=True, signal_aligner_conflict_threshold=0.5)


def _agent1(signal: str, tf: str = "1h", conf: float = 0.8) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
        data={"signal": signal, "timeframe": tf, "description": f"{signal} on {tf}"},
        confidence=conf, urgency="high", timestamp=datetime.now(timezone.utc),
    )


def _agent2(title: str, weight: float = 0.8, source: str = "CoinDesk") -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.NEWS_EVENT, source="agent2",
        data={"title": title, "weight": weight, "source": source},
        confidence=weight, urgency="high", timestamp=datetime.now(timezone.utc),
    )


def _onchain(source: str, **data) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.NEWS_EVENT, source=source,
        data=data, confidence=0.7, urgency="medium",
        timestamp=datetime.now(timezone.utc),
    )


class TestSignalAligner:

    def test_empty_events_returns_neutral(self, config):
        """无事件时全部中性"""
        aligner = SignalAligner(config)
        result = aligner.align([])
        assert result["technical_score"] == 0.0
        assert result["news_score"] == 0.0
        assert result["onchain_score"] == 0.0
        assert result["alignment_score"] == 0.5  # 中性
        assert not result["is_conflict"]

    def test_all_sources_agree_consensus(self, config):
        """三方看多 → 共识"""
        aligner = SignalAligner(config)
        events = [
            _agent1("macd_bullish_cross"),
            _agent2("ETH ETF approved by SEC", 0.9),
            _onchain("agent2_taker", sentiment="bullish", buy_ratio=0.65, description=""),
        ]
        result = aligner.align(events)
        assert result["technical_score"] > 0
        assert result["news_score"] > 0
        assert result["onchain_score"] > 0
        assert result["is_consensus"]
        assert not result["is_conflict"]

    def test_technical_news_conflict(self, config):
        """技术看多, 新闻看空 → 冲突"""
        aligner = SignalAligner(config)
        events = [
            _agent1("macd_bullish_cross"),
            _agent2("SEC hack leads to crypto ban", 0.9),
        ]
        result = aligner.align(events)
        assert result["technical_score"] > 0
        assert result["news_score"] < 0
        assert result["is_conflict"]

    def test_whale_to_exchange_is_bearish(self, config):
        """巨鲸转交易所 → 偏空"""
        aligner = SignalAligner(config)
        events = [
            _onchain("agent2_whale", direction="→ 交易所", amount_usdt=10_000_000,
                     description="🐋 5000 ETH → 交易所"),
        ]
        # 只有链上事件, 没有技术和新闻
        result = aligner.align(events)
        assert result["onchain_score"] < -0.3
        assert result["technical_score"] == 0.0
        assert result["news_score"] == 0.0

    def test_whale_out_of_exchange_is_bullish(self, config):
        """巨鲸出交易所 → 偏多"""
        aligner = SignalAligner(config)
        events = [
            _onchain("agent2_whale", direction="← 出交易所", amount_usdt=10_000_000,
                     description=""),
        ]
        result = aligner.align(events)
        assert result["onchain_score"] > 0.3

    def test_funding_rate_high_positive_is_bearish(self, config):
        """资金费率高正 → 偏空"""
        aligner = SignalAligner(config)
        events = [
            _onchain("agent2_funding", funding_rate_pct=0.015, is_high=True,
                     description=""),
        ]
        result = aligner.align(events)
        assert result["onchain_score"] < -0.3

    def test_taker_bullish_is_bullish(self, config):
        """吃单比偏多 → 偏多"""
        aligner = SignalAligner(config)
        events = [
            _onchain("agent2_taker", sentiment="bullish", buy_ratio=0.65, description=""),
        ]
        result = aligner.align(events)
        assert result["onchain_score"] >= 0.4

    def test_gas_extreme_is_bearish(self, config):
        """Gas 极高 → 轻微偏空"""
        aligner = SignalAligner(config)
        events = [
            _onchain("agent2_gas", level="extreme", gas_gwei=250, description=""),
        ]
        result = aligner.align(events)
        assert result["onchain_score"] < 0

    def test_weak_signals_no_alignment(self, config):
        """弱信号 → 中性对齐"""
        aligner = SignalAligner(config)
        events = [
            _agent1("boll_squeeze"),  # 中性信号
            _agent2("Minor update to protocol", 0.4),  # 低权重无关键词
        ]
        result = aligner.align(events)
        # 技术和新闻都不够强, 数据不足 → alignment=0.5
        assert result["alignment_score"] == 0.5

    def test_summary_line_format(self, config):
        """摘要格式包含三类信号方向"""
        aligner = SignalAligner(config)
        events = [
            _agent1("macd_bullish_cross"),
            _agent2("ETH ETF approved", 0.8),
        ]
        result = aligner.align(events)
        summary = result["summary_line"]
        assert "技术面" in summary
        assert "新闻" in summary
        assert "链上" in summary

    def test_confidence_scores_override_technical(self, config):
        """传入 confidence_scores 时优先使用其技术面分数"""
        aligner = SignalAligner(config)
        events = [_agent1("macd_bullish_cross")]
        cs = {"composite_score": -0.8, "signal_count": 1}  # 故意给反方向
        result = aligner.align(events, confidence_scores=cs)
        assert result["technical_score"] == -0.8  # 使用了 cs 的值而非从事件推断
