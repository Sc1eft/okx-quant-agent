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
        scores: list[dict] = []
        tf_scores: dict[str, list[float]] = {}

        for ev in events:
            if ev.source != "agent1" or not isinstance(ev.data, dict):
                continue

            sig = ev.data.get("signal", "")
            tf = ev.data.get("timeframe", "")
            sig_conf = ev.confidence  # 0~1, 来自 ChangeDetector

            direction = self._signal_directions.get(sig, 0.0)
            tf_weight = self._tf_weights.get(tf, 0.3)

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
            "individual_scores": scores,
            "timeframe_breakdown": tf_breakdown,
            "signal_count": len(scores),
        }
