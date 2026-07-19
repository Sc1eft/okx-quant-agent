"""
规则决策器 — 替代 DeepSeek 的实时交易决策

设计动机:
  - LLM 的"信心%"是自报数字，无校准依据；30s 级 API 延迟对分钟级信号是致命的；
  - Agent 1 的信号本来就有类型、方向、周期权重和置信度（ChangeDetector），
    ConfidenceScorer 已能把它们聚合为方向评分——LLM 在这之上没有增量信息；
  - 规则决策可以原样搬进回测引擎（strategies/macd_agent），
    让"回测什么就跑什么"成立。

决策规则（全部参数见 agents/config.py）:
  1. 用 score_signals 聚合事件缓冲区的技术信号 → composite_score [-1,1] 和
     composite_confidence [0,1]（方向一致性）
  2. raw_score（未归一化加权和，反映信号强度）>= agent3_rule_score_threshold
     且 composite_confidence >= agent3_rule_min_confidence → buy；对称 → sell；
     否则 hold。归一化后的 composite_score 只反映方向一致性，不作入场依据
  3. 止损止盈直接取配置百分比（agent3_default_stop_loss_pct / take_profit_pct），
     不再使用 LLM 自报的绝对价（下游仍有方向校验兜底）
  4. 仓位百分比 = 基础值 × (0.5 + 0.5 × confidence)，钳位 5~100

输出 dict 与 DeepSeekTrader.analyze 的返回结构兼容，
下游（仓位计算 / 反转持久性 / 补仓判断 / 入库）无需改动。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agents.config import AgentSystemConfig
from agents.confidence_scorer import score_signals
from agents.event_bus import AgentEvent

logger = logging.getLogger("rule_decider")


class RuleDecider:
    """基于多周期信号综合评分的规则交易决策器"""

    def __init__(self, config: AgentSystemConfig):
        self._directions: dict[str, float] = dict(config.confidence_signal_directions)
        self._tf_weights: dict[str, float] = dict(config.confidence_timeframe_weights)
        self._score_threshold = config.agent3_rule_score_threshold
        self._min_confidence = config.agent3_rule_min_confidence
        self._base_position_pct = config.agent3_rule_base_position_pct
        self._sl_pct = config.agent3_default_stop_loss_pct / 100
        self._tp_pct = config.agent3_default_take_profit_pct / 100
        # 统计（对齐 DeepSeekTrader.get_stats 的展示位）
        self.total_calls = 0

    def decide(
        self,
        events: list[AgentEvent],
        current_price: float = 0.0,
    ) -> dict[str, Any]:
        """从事件缓冲区做出交易决策，返回与 deepseek.analyze 兼容的 dict"""
        self.total_calls += 1

        signal_dicts = [
            {
                "signal": ev.data.get("signal", ""),
                "timeframe": ev.data.get("timeframe", ""),
                "confidence": ev.confidence,
            }
            for ev in events
            if ev.source == "agent1" and isinstance(ev.data, dict)
        ]
        comp = score_signals(signal_dicts, self._directions, self._tf_weights)
        # raw_score（未归一化加权和）衡量信号强度；composite_confidence 衡量方向一致性
        score = comp["raw_score"]
        conf = comp["composite_confidence"]
        confidence_pct = round(conf * 100)

        # 贡献最大的前 3 条信号作为决策依据说明
        top = sorted(
            comp["individual_scores"], key=lambda s: -abs(s["weighted_score"])
        )[:3]
        evidence = "、".join(f"{s['signal']}@{s['timeframe']}" for s in top) or "无有效信号"

        hold = not signal_dicts
        if not hold and conf < self._min_confidence:
            hold = True
            evidence = f"方向一致性不足 ({conf:.2f} < {self._min_confidence})；{evidence}"
        if not hold and abs(score) < self._score_threshold:
            hold = True
            evidence = f"综合评分不足 ({score:+.2f}，阈值 ±{self._score_threshold})；{evidence}"

        if hold:
            logger.info(f"规则决策 hold: {evidence}")
            return {
                "action": "hold",
                "confidence": confidence_pct,
                "entry_price_min": "",
                "entry_price_max": "",
                "position_size_pct": "",
                "stop_loss": "",
                "take_profit": "",
                "add_to_position": None,
                "reason": evidence,
                "_composite": comp,
            }

        action = "buy" if score > 0 else "sell"
        # 仓位随一致性信心缩放：基础值 × (0.5 + 0.5×conf)，钳位 5~100
        position_pct = self._base_position_pct * (0.5 + 0.5 * conf)
        position_pct = max(5, min(100, round(position_pct)))

        # 止损止盈：规则化绝对价（下游仍有方向校验兜底）
        stop_loss: float | str = ""
        take_profit: float | str = ""
        if current_price > 0:
            if action == "buy":
                stop_loss = round(current_price * (1 - self._sl_pct), 2)
                take_profit = round(current_price * (1 + self._tp_pct), 2)
            else:
                stop_loss = round(current_price * (1 + self._sl_pct), 2)
                take_profit = round(current_price * (1 - self._tp_pct), 2)

        reason = (
            f"综合评分 {score:+.2f}（一致性 {conf:.2f}）；{evidence}"
        )
        logger.info(f"规则决策 {action}: {reason}")
        return {
            "action": action,
            "confidence": confidence_pct,
            "entry_price_min": "",
            "entry_price_max": "",
            "position_size_pct": position_pct,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "add_to_position": None,
            "reason": reason,
            "_composite": comp,
        }

    def get_stats(self) -> dict:
        return {"total_calls": self.total_calls, "model": "rule-based"}
