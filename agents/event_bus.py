"""
事件总线 — asyncio.Queue 封装 + 标准化事件格式

队列：
  Queue A (技术面): Agent1 → Agent3，携带指标变化事件
  Queue B (基本面): Agent2 → Agent3，携带新闻/链上事件
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class AgentEventType(Enum):
    """Agent 事件类型"""
    TECHNICAL_SIGNAL = "technical_signal"   # Agent1 → Agent3: 技术指标变化
    NEWS_EVENT = "news_event"               # Agent2 → Agent3: 新闻/基本面事件
    TRADE_DECISION = "trade_decision"       # Agent3 → 日志: 交易决策记录
    SYSTEM_STATUS = "system_status"         # 任意 Agent → 监控: 心跳/状态
    ERROR = "error"                         # 任意 Agent → 监控: 错误报告


@dataclass
class AgentEvent:
    """标准化 Agent 事件

    type:      事件类型
    source:    来源 ("agent1" / "agent2" / "agent3")
    timestamp: ISO 格式时间戳
    data:      具体负载 (dict)
    confidence: 置信度 0~1
    urgency:   优先级
    """
    type: AgentEventType
    source: str
    data: dict[str, Any]
    confidence: float = 0.5
    urgency: str = "medium"  # "high" / "medium" / "low"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "source": self.source,
            "timestamp": self.timestamp,
            "data": self.data,
            "confidence": self.confidence,
            "urgency": self.urgency,
        }


class EventBus:
    """事件总线 — 管理两条 asyncio.Queue"""

    def __init__(self, maxsize: int = 100):
        self.queue_a: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=maxsize)
        self.queue_b: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=maxsize)

    async def publish_a(self, event: AgentEvent):
        """向 Queue A (技术面) 发布事件"""
        try:
            self.queue_a.put_nowait(event)
        except asyncio.QueueFull:
            # 队列满时丢弃最旧事件
            await self.queue_a.get()
            self.queue_a.put_nowait(event)

    async def publish_b(self, event: AgentEvent):
        """向 Queue B (基本面) 发布事件"""
        try:
            self.queue_b.put_nowait(event)
        except asyncio.QueueFull:
            await self.queue_b.get()
            self.queue_b.put_nowait(event)

    async def consume_a(self) -> AgentEvent:
        """消费 Queue A (阻塞)"""
        return await self.queue_a.get()

    async def consume_b(self) -> AgentEvent:
        """消费 Queue B (阻塞)"""
        return await self.queue_b.get()

    def qsize_a(self) -> int:
        return self.queue_a.qsize()

    def qsize_b(self) -> int:
        return self.queue_b.qsize()
