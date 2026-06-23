"""
Agent 3 — 资深交易员

职责:
  1. 同时监听 Queue A（技术面）和 Queue B（新闻/基本面）
  2. 在时间窗口内缓冲合并事件
  3. 高优先级事件立即处理，低优先级攒批
  4. Layer 1 风控检查
  5. 构建上下文 → 调用 DeepSeek 综合分析
  6. DeepSeek 返回交易决策
  7. Layer 2 执行保护（限价单/滑点保护）
  8. 执行交易（通过 TradeExecutor）
  9. Layer 3 记录交易到风控系统 + SQLite
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.deepseek_caller import DeepSeekTrader
from agents.risk_layer import RiskManager
from agents.trade_executor import TradeExecutor
from agents.config import AgentSystemConfig

logger = logging.getLogger("agent3")


class Agent3:
    """Agent 3 — 交易决策与执行"""

    def __init__(
        self,
        config: AgentSystemConfig,
        event_bus: EventBus,
        deepseek: DeepSeekTrader,
        risk_manager: RiskManager,
        trade_executor: TradeExecutor,
        root_config,
    ):
        self.config = config
        self.bus = event_bus
        self.deepseek = deepseek
        self.risk = risk_manager
        self.executor = trade_executor
        self.root_config = root_config  # 根 Config（含 trading symbol 等）

        # 事件缓冲区
        self._event_buffer: list[AgentEvent] = []
        self._last_decision_time: Optional[datetime] = None
        self._decision_lock = asyncio.Lock()  # re-entrancy guard

        # 运行状态
        self._running = False
        self._current_position = {
            "side": "none",
            "size": 0.0,
            "entry_price": 0.0,
        }
        self._stats = {
            "events_received_a": 0,
            "events_received_b": 0,
            "deepseek_calls": 0,
            "trades_executed": 0,
            "trades_skipped": 0,
            "start_time": "",
        }

    async def run(self):
        """启动 Agent 3 主循环"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Agent 3 (交易员) 启动")

        # 同时监听两个队列
        consumers = [
            self._consume_a(),
            self._consume_b(),
        ]
        await asyncio.gather(*consumers)

    async def stop(self):
        self._running = False
        logger.info("Agent 3 已停止")

    async def _consume_a(self):
        """消费 Queue A（技术面事件）"""
        while self._running:
            try:
                event = await asyncio.wait_for(self.bus.consume_a(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                logger.exception("_consume_a 异常，1s 后重试")
                await asyncio.sleep(1)
                continue
            self._stats["events_received_a"] += 1
            await self._on_event(event)

    async def _consume_b(self):
        """消费 Queue B（新闻/基本面事件）"""
        while self._running:
            try:
                event = await asyncio.wait_for(self.bus.consume_b(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                logger.exception("_consume_b 异常，1s 后重试")
                await asyncio.sleep(1)
                continue
            self._stats["events_received_b"] += 1
            await self._on_event(event)

    async def _on_event(self, event: AgentEvent):
        """收到新事件后的处理"""
        self._event_buffer.append(event)

        # 高优先级事件 → 立即决策
        if event.urgency == "high":
            logger.info(f"高优先级事件触发立即决策: {event.type}")
            await self._make_decision()
        else:
            # 低优先级 → 攒批或超时触发
            await self._maybe_debounce()

    async def _maybe_debounce(self):
        """检查是否需要触发决策（攒批/超时）"""
        now = datetime.now(timezone.utc)

        # 如果自上次决策已超过缓冲窗口
        if self._last_decision_time:
            elapsed = (now - self._last_decision_time).total_seconds()
            if elapsed >= self.config.agent3_debounce_seconds and self._event_buffer:
                logger.info(f"缓冲超时触发决策 ({elapsed:.0f}s)")
                await self._make_decision()
                return

        # 缓冲区内累积足够事件
        if len(self._event_buffer) >= 5:
            logger.info(f"缓冲区满 ({len(self._event_buffer)} 事件) 触发决策")
            await self._make_decision()

    async def _make_decision(self):
        """执行一次完整的交易决策周期"""
        if self._decision_lock.locked():
            return
        async with self._decision_lock:
            if not self._event_buffer:
                return

            self._last_decision_time = datetime.now(timezone.utc)
            events = list(self._event_buffer)
            self._event_buffer.clear()

            # ── 1. 构建上下文摘要 ──
            context = await self._build_context(events)

            # ── 2. Layer 1 风控检查 ──
            size_eth = self._suggested_size(context)
            side = "buy" if context.get("suggested_direction") == "long" else "sell"
            ok, reason = self.risk.check_layer1(side, size_eth, context.get("current_price", 0))
            if not ok:
                logger.info(f"Layer 1 拒绝: {reason}")
                self._stats["trades_skipped"] += 1
                return

            # ── 3. 调用 DeepSeek ──
            self._stats["deepseek_calls"] += 1
            decision = await asyncio.to_thread(self.deepseek.analyze, context)

            if decision["action"] == "hold":
                logger.info(f"DeepSeek 建议持有: {decision.get('reason', '')}")
                self._stats["trades_skipped"] += 1
                return

            # ── 4. 执行交易 ──
            logger.info(f"DeepSeek 决策: {decision['action']} (信心 {decision['confidence']}%)")
            self._stats["trades_executed"] += 1

            trade_side = "buy" if decision["action"] == "buy" else "sell"
            trade_result = await self.executor.execute_safe(
                side=trade_side,
                size_eth=size_eth,
                signal_price=context.get("current_price", 0),
                prefer_limit=True,
            )

            # ── 5. Layer 3 记录 ──
            if trade_result["success"]:
                self.risk.record_trade({
                    "side": trade_side,
                    "size": size_eth,
                    "price": trade_result["fill_price"],
                    "pnl": 0,
                    "order_id": trade_result["order_id"],
                    "symbol": self.executor.symbol,
                    "decision": decision,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                logger.info(f"交易成功: {trade_side} {size_eth:.4f} ETH @ ${trade_result['fill_price']:.2f}")
            else:
                self.risk.report_api_error()
                logger.error(f"交易失败: {trade_result['error']}")

    async def _build_context(self, events: list[AgentEvent]) -> dict:
        """从事件列表构建 DeepSeek 上下文"""
        agent1_lines = []
        agent2_lines = []
        current_price = 0.0

        for e in events:
            if not isinstance(e.data, dict):
                continue
            d = e.data
            if e.source == "agent1":
                desc = d.get("description", "")
                tf = d.get("timeframe", "")
                price = d.get("price", 0)
                if price:
                    current_price = price
                agent1_lines.append(f"[{tf}] {desc}")
            elif e.source == "agent2":
                title = d.get("title", "")
                source = d.get("source", "")
                weight = d.get("weight", 0)
                agent2_lines.append(f"[{source} w={weight:.2f}] {title}")

        return {
            "position_direction": self._current_position["side"],
            "position_size": self._current_position["size"],
            "entry_price": self._current_position["entry_price"],
            "pnl_pct": "",  # 阶段一简单处理，暂不计算
            "agent1_summary": "\n".join(agent1_lines) if agent1_lines else "暂无技术面信号",
            "agent2_summary": "\n".join(agent2_lines) if agent2_lines else "暂无新闻数据",
            "monthly_trades": 0,
            "win_rate": 0,
            "monthly_pnl": 0,
            "current_price": current_price,
        }

    def _suggested_size(self, context: dict) -> float:
        """根据上下文和风控建议仓位大小"""
        multiplier = self.risk.get_position_size_multiplier()
        base_size = 0.01  # 基础 0.01 ETH
        return base_size * multiplier

    def update_position(self, side: str, size: float, entry_price: float):
        """更新当前持仓（供外部或 main.py 调用）"""
        self._current_position = {
            "side": side,
            "size": size,
            "entry_price": entry_price,
        }

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "position": self._current_position,
            "event_buffer_size": len(self._event_buffer),
            "deepseek_stats": self.deepseek.get_stats(),
            "executor_stats": self.executor.get_stats(),
            "risk_status": self.risk.get_status(),
            **self._stats,
        }
