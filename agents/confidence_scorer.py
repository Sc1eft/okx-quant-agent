"""
多周期信心分 — Phase 4

将 Agent 1 来自多个时间帧的技术信号聚合为综合方向性评分。

原理:
  1. 每个信号类型映射到方向值 [-1.0, +1.0]
  2. 按时间帧权重 (15m: 0.3, 1h: 0.5, 1d: 0.7) 和信号置信度加权
  3. 计算综合得分和一致性信心分
"""
from __future__ import annotations

import logging
from typing import Any

from agents.event_bus import AgentEvent
from agents.config import AgentSystemConfig

logger = logging.getLogger("confidence_scorer")


def score_signals(
    signals: list[dict],
    directions: dict[str, float],
    tf_weights: dict[str, float],
) -> dict[str, Any]:
    """纯函数：将一组信号字典聚合为综合方向性评分。

    供 ConfidenceScorer（实时事件）、RuleDecider（规则决策器）和
    strategies/macd_agent（回测）三处共用，保证实盘与回测评分一致。

    Args:
        signals: [{"signal": str, "timeframe": str, "confidence": float(0~1)}]
        directions: 信号类型 → 方向值 [-1.0, +1.0]
        tf_weights: 时间帧 → 权重

    Returns:
        与 ConfidenceScorer.compute 相同的结构：
        composite_score / composite_confidence / individual_scores /
        timeframe_breakdown / signal_count
    """
    scores: list[dict] = []
    tf_scores: dict[str, list[float]] = {}

    for s in signals:
        sig = s.get("signal", "")
        tf = s.get("timeframe", "")
        sig_conf = float(s.get("confidence", 0.5))

        direction = directions.get(sig, 0.0)
        tf_weight = tf_weights.get(tf, 0.3)

        if direction == 0.0:
            continue  # 中性信号或未知类型不参与计算

        weighted = direction * sig_conf * tf_weight
        scores.append({
            "signal": sig,
            "timeframe": tf,
            "direction": direction,
            "signal_confidence": sig_conf,
            "tf_weight": tf_weight,
            "weighted_score": round(weighted, 4),
        })

        if tf not in tf_scores:
            tf_scores[tf] = []
        tf_scores[tf].append(direction * sig_conf)

    if not scores:
        return {
            "composite_score": 0.0,
            "composite_confidence": 0.0,
            "raw_score": 0.0,
            "individual_scores": [],
            "timeframe_breakdown": {},
            "signal_count": 0,
        }

    total_weighted = sum(s["weighted_score"] for s in scores)
    # 分子是有符号的, 分母是无符号的归一化权重, 使得结果在 [-1, +1]
    abs_weights = sum(
        abs(s["direction"]) * s["signal_confidence"] * s["tf_weight"]
        for s in scores
    )
    composite_score = total_weighted / abs_weights if abs_weights > 0 else 0.0
    composite_score = max(-1.0, min(1.0, composite_score))

    # 一致性信心: 信号越一致→越高
    # 如果所有信号同方向, 接近 1; 互相抵消则接近 0
    max_possible = abs_weights
    agreement_ratio = abs(total_weighted) / max_possible if max_possible > 0 else 0.0
    composite_confidence = round(agreement_ratio * 0.9 + 0.1, 4)  # 保底 0.1
    composite_confidence = min(1.0, composite_confidence)

    # 各时间帧均分
    tf_breakdown = {}
    for tf, vals in tf_scores.items():
        tf_breakdown[tf] = round(sum(vals) / len(vals), 4)

    return {
        "composite_score": round(composite_score, 4),
        "composite_confidence": composite_confidence,
        # 未归一化的加权和：反映信号强度本身（单条弱信号≈0，多周期共振→绝对值大），
        # 供规则决策器做入场阈值判断；composite_score 只反映方向一致性
        "raw_score": round(total_weighted, 4),
        "individual_scores": scores,
        "timeframe_breakdown": tf_breakdown,
        "signal_count": len(scores),
    }


class ConfidenceScorer:
    """多周期信心分计算器

    用法:
        scorer = ConfidenceScorer(config)
        result = scorer.compute(agent1_events)
    """

    def __init__(self, config: AgentSystemConfig):
        self._signal_directions: dict[str, float] = dict(config.confidence_signal_directions)
        self._tf_weights: dict[str, float] = dict(config.confidence_timeframe_weights)

    def compute(self, events: list[AgentEvent]) -> dict[str, Any]:
        """计算综合信心分

        Args:
            events: Agent 1 技术事件列表 (source == "agent1")

        Returns:
            dict:
                composite_score:   float  -1.0 ~ +1.0  总体方向偏倚
                composite_confidence: float 0.0 ~ 1.0  加权一致性信心
                individual_scores:  list[dict]         每条信号的明细
                timeframe_breakdown: dict[str, float]  各时间帧平均分
                signal_count:       int                参与计算的信号数
        """
        signal_dicts = [
            {
                "signal": ev.data.get("signal", ""),
                "timeframe": ev.data.get("timeframe", ""),
                "confidence": ev.confidence,
            }
            for ev in events
            if ev.source == "agent1" and isinstance(ev.data, dict)
        ]
        return score_signals(signal_dicts, self._signal_directions, self._tf_weights)
