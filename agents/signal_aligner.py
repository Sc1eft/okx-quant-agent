"""
三方信号对齐 — Phase 4

评估技术面、新闻面、链上三方信号的方向一致性。

方向判断:
  - 技术面: 从 ConfidenceScorer 获取综合方向
  - 新闻面: 关键词启发式判断
  - 链上面: Gas / 巨鲸 / 吃单比 / 资金费率映射方向

对齐度: 0~1, 三方同向则高, 冲突则低
"""
from __future__ import annotations

import logging
import re
from typing import Any

from agents.event_bus import AgentEvent
from agents.config import AgentSystemConfig

logger = logging.getLogger("signal_aligner")

# 新闻关键词语种映射 (方向值 * 权重调节)
_BULLISH_KEYWORDS = re.compile(
    r"(ETF|approve|upgrade|bullish|突破|获批|升级|利好|看涨)", re.IGNORECASE
)
_BEARISH_KEYWORDS = re.compile(
    r"(hack|ban|crash|bearish|监管|禁止|黑客|暴跌|利空|看跌)", re.IGNORECASE
)


class SignalAligner:
    """三方信号对齐器"""

    def __init__(self, config: AgentSystemConfig):
        self._conflict_threshold = config.signal_aligner_conflict_threshold

    def align(
        self,
        events: list[AgentEvent],
        confidence_scores: dict | None = None,
    ) -> dict[str, Any]:
        """计算三方信号对齐度

        Args:
            events:    当前缓冲区的所有事件
            confidence_scores: 可选的 ConfidenceScorer 输出, 用于技术面方向

        Returns:
            dict:
                technical_score:  -1~+1  技术面方向
                news_score:       -1~+1  新闻面方向
                onchain_score:    -1~+1  链上方向
                alignment_score:  0~1    对齐度 (1=完全一致)
                is_consensus:     bool   是否达成共识
                is_conflict:      bool   是否冲突
                summary_line:     str    中文摘要
        """
        technical = self._score_technical(events, confidence_scores)
        news = self._score_news(events)
        onchain = self._score_onchain(events)

        non_zero = [s for s in [technical, news, onchain] if abs(s) > 0.05]

        if len(non_zero) < 2:
            alignment_score = 0.5  # 数据不足, 中性
        else:
            spread = max(non_zero) - min(non_zero)
            # spread 最大 2.0 (从 -1 到 +1)
            alignment_score = max(0.0, min(1.0, 1.0 - spread / 2.0))

        is_consensus = len(non_zero) >= 2 and (
            all(s > 0 for s in non_zero)
            or all(s < 0 for s in non_zero)
        )
        is_conflict = alignment_score < self._conflict_threshold

        summary_line = self._build_summary(technical, news, onchain, is_consensus, is_conflict)

        return {
            "technical_score": round(technical, 4),
            "news_score": round(news, 4),
            "onchain_score": round(onchain, 4),
            "alignment_score": round(alignment_score, 4),
            "is_consensus": is_consensus,
            "is_conflict": is_conflict,
            "summary_line": summary_line,
        }

    def _score_technical(self, events: list[AgentEvent], confidence_scores: dict | None) -> float:
        """从 ConfidenceScorer 输出或事件中提取技术面方向"""
        if confidence_scores and confidence_scores.get("signal_count", 0) > 0:
            return confidence_scores["composite_score"]
        # 没有预计算的结果, 从事件中快速估算
        bullish = 0
        bearish = 0
        for ev in events:
            if ev.source != "agent1" or not isinstance(ev.data, dict):
                continue
            sig = ev.data.get("signal", "")
            if any(k in sig for k in ("bullish", "positive", "oversold", "break_lower")):
                bullish += 1
            elif any(k in sig for k in ("bearish", "negative", "overbought", "break_upper")):
                bearish += 1
            # expansion 本身无方向，但价格破位向上＝偏多 破位向下＝偏空
            if "expansion" in sig:
                desc = (ev.data.get("description", "") or "").lower()
                if "向上" in desc:
                    bullish += 1
                elif "向下" in desc:
                    bearish += 1
        total = bullish + bearish
        if total == 0:
            return 0.0
        return (bullish - bearish) / total

    def _score_news(self, events: list[AgentEvent]) -> float:
        """从新闻事件中判断方向"""
        score = 0.0
        count = 0
        for ev in events:
            if ev.source != "agent2" or not isinstance(ev.data, dict):
                continue
            title = ev.data.get("title", "")
            desc = ev.data.get("description", "")
            weight = float(ev.data.get("weight", 0.5))
            text = f"{title} {desc}"

            if _BULLISH_KEYWORDS.search(text):
                direction = 0.5 + 0.2 * (weight - 0.5) / 0.5  # 0.5~0.7
            elif _BEARISH_KEYWORDS.search(text):
                direction = -0.5 - 0.2 * (weight - 0.5) / 0.5  # -0.5~-0.7
            else:
                if weight > 0.7:
                    direction = 0.3  # 高权重未知方向, 轻微偏多
                else:
                    continue  # 低权重无关键词, 跳过

            score += direction
            count += 1

        if count == 0:
            return 0.0
        return max(-1.0, min(1.0, score / count))

    def _score_onchain(self, events: list[AgentEvent]) -> float:
        """从链上事件中判断方向"""
        score = 0.0
        count = 0
        for ev in events:
            if ev.source not in ("agent2_gas", "agent2_whale", "agent2_taker", "agent2_funding", "agent2_oi"):
                continue
            d = ev.data
            if not isinstance(d, dict):
                continue

            if ev.source == "agent2_gas":
                level = d.get("level", "")
                if level == "extreme":
                    score += -0.3
                elif level == "high":
                    score += -0.1
                elif level == "low":
                    score += 0.1
                else:
                    continue
                count += 1

            elif ev.source == "agent2_whale":
                direction = d.get("direction", "")
                if "→ 交易所" in direction:
                    score += -0.5  # 入交易所 = 抛压
                elif "← 出交易所" in direction:
                    score += 0.4  # 出交易所 = 囤积
                else:
                    continue
                count += 1

            elif ev.source == "agent2_taker":
                sentiment = d.get("sentiment", "")
                s = 0.6 if d.get("extreme") else 0.5  # 分位极端加权
                if sentiment == "bullish":
                    score += s
                elif sentiment == "bearish":
                    score += -s
                else:
                    continue
                count += 1

            elif ev.source == "agent2_funding":
                is_high = d.get("is_high", False)
                rate = d.get("funding_rate_pct", 0)
                s = 0.5 if d.get("extreme") else 0.4  # 分位极端加权
                if is_high and rate > 0:
                    score += -s  # 正费率过高 = 多头过热
                elif is_high and rate < 0:
                    score += s  # 负费率过低 = 空头过热
                else:
                    continue
                count += 1

            elif ev.source == "agent2_oi":
                sentiment = d.get("sentiment", "")
                if sentiment == "bullish":
                    score += 0.4  # OI 激增 + 买方主导 = 新多进场
                elif sentiment == "bearish":
                    score += -0.4  # OI 激增 + 卖方主导 = 新空进场
                else:
                    continue  # 去杠杆（neutral）只做上下文，不定方向
                count += 1

        if count == 0:
            return 0.0
        return max(-1.0, min(1.0, score / count))

    @staticmethod
    def _build_summary(tech: float, news: float, onchain: float,
                       is_consensus: bool, is_conflict: bool) -> str:
        """生成中文摘要"""
        def label(v: float) -> str:
            if v > 0.3:
                return "看多"
            elif v < -0.3:
                return "看空"
            return "中性"

        parts = [
            f"技术面{label(tech)} ({tech:+.1f})",
            f"新闻{label(news)} ({news:+.1f})",
            f"链上{label(onchain)} ({onchain:+.1f})",
        ]
        summary = " / ".join(parts)

        if is_consensus:
            summary += " → 三方共识 ✅"
        elif is_conflict:
            summary += " → 信号冲突 ⚠️"
        else:
            summary += " → 部分一致"

        return summary
