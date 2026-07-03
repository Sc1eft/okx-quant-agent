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
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.deepseek_caller import DeepSeekTrader
from agents.risk_layer import RiskManager
from agents.trade_executor import TradeExecutor
from agents.config import AgentSystemConfig
# Phase 4
from agents.confidence_scorer import ConfidenceScorer
from agents.signal_aligner import SignalAligner

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
        position_monitor=None,  # Phase 2: 持仓监控器
        okx_client=None,       # Phase 2: OKX客户端（用于BTC/深度检查）
        review_generator=None,  # Phase 4: 复盘报告生成器
        agent4_reviewer=None,  # Agent 4 复盘改进（替代 param_adapter）
    ):
        self.config = config
        self.bus = event_bus
        self.deepseek = deepseek
        self.risk = risk_manager
        self.executor = trade_executor
        self.root_config = root_config
        self.position_monitor = position_monitor
        self.okx_client = okx_client
        self._btc_checked = False

        # Phase 4
        self.confidence_scorer = ConfidenceScorer(config) if config.confidence_scorer_enabled else None
        self.signal_aligner = SignalAligner(config) if config.signal_aligner_enabled else None
        self.review_gen = review_generator
        self.agent4_reviewer = agent4_reviewer  # Agent 4（替代 param_adapter）

        # 事件缓冲区
        self._event_buffer: list[AgentEvent] = []
        self._last_decision_time: Optional[datetime] = None
        self._decision_lock = asyncio.Lock()  # re-entrancy guard

        # 运行状态
        self._running = False
        self._current_activity = ""
        self._last_activity_time = 0.0
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
            # Phase 4
            "last_composite_score": 0.0,
            "last_composite_confidence": 0.0,
            "last_alignment_score": 0.0,
            "last_monthly_pnl": 0.0,
            "last_win_rate": 0.0,
        }

    async def run(self):
        """启动 Agent 3 主循环"""
        self._running = True
        self._last_decision_time = datetime.now(timezone.utc)  # 初始化 debounce 计时起点
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Agent 3 (交易员) 启动")

        # 同时监听两个队列
        consumers = [
            self._consume_a(),
            self._consume_b(),
        ]
        # Phase 4: 后台协程
        if self.review_gen:
            consumers.append(self._review_scheduler())
        # Agent 4 替代了 param_adapter 的调参职责

        await asyncio.gather(*consumers)

    async def stop(self):
        self._running = False
        logger.info("Agent 3 已停止")

    async def _consume_a(self):
        """消费 Queue A（技术面事件）"""
        idle_ticks = 0
        while self._running:
            try:
                event = await asyncio.wait_for(self.bus.consume_a(), timeout=1.0)
            except asyncio.TimeoutError:
                idle_ticks += 1
                if idle_ticks % 5 == 0:
                    buf = len(self._event_buffer)
                    self._current_activity = f"⏳ 等待信号 | 缓冲 {buf} 事件"
                    self._last_activity_time = time.time()
                continue
            except Exception:
                logger.exception("_consume_a 异常，1s 后重试")
                await asyncio.sleep(1)
                continue
            idle_ticks = 0
            self._stats["events_received_a"] += 1
            self._current_activity = f"📨 收到技术信号 (#{self._stats['events_received_a']})"
            self._last_activity_time = time.time()
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
            self._current_activity = f"📨 收到新闻/链上事件 (#{self._stats['events_received_b']})"
            self._last_activity_time = time.time()
            await self._on_event(event)

    async def _on_event(self, event: AgentEvent):
        """收到新事件后的处理"""
        self._event_buffer.append(event)
        buf = len(self._event_buffer)

        if event.urgency == "high":
            self._current_activity = f"⚡ 高优事件触发即时决策 ({buf} 缓冲)"
            self._last_activity_time = time.time()
            logger.info(f"高优先级事件触发立即决策: {event.type}")
            await self._make_decision()
        else:
            self._current_activity = f"📥 缓冲事件 ({buf}/5, debounce {self.config.agent3_debounce_seconds}s)"
            self._last_activity_time = time.time()
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
            self._current_activity = "⏳ 上次决策进行中，跳过"
            return
        async with self._decision_lock:
            if not self._event_buffer:
                return

            self._last_decision_time = datetime.now(timezone.utc)
            events = list(self._event_buffer)
            self._event_buffer.clear()

            # ── 0. BTC 波动检查（Phase 2） ──
            if self.okx_client and hasattr(self.risk, 'check_btc_volatility_async'):
                self._current_activity = "🔍 检查 BTC 波动…"
                self._last_activity_time = time.time()
                ok, reason = await self.risk.check_btc_volatility_async(self.okx_client)
                if not ok:
                    self._current_activity = f"⏭ BTC 波动检查跳过: {reason[:40]}"
                    self._last_activity_time = time.time()
                    logger.info(f"BTC 波动检查拒绝: {reason}")
                    self._stats["trades_skipped"] += 1
                    return

            # ── 1. 构建上下文摘要（不含方向） ──
            self._current_activity = "🧠 构建 DeepSeek 上下文 ({len(events)} 事件)"
            self._last_activity_time = time.time()
            context = self._build_context(events)

            # ── 2. 调用 DeepSeek ──
            self._current_activity = "🤔 等待 DeepSeek 决策…"
            self._last_activity_time = time.time()
            self._stats["deepseek_calls"] += 1
            decision = await asyncio.to_thread(self.deepseek.analyze, context)

            if decision["action"] == "hold":
                reason = decision.get('reason', '')
                self._current_activity = f"⏭ DeepSeek 建议持有: {reason[:40]}"
                self._last_activity_time = time.time()
                logger.info(f"DeepSeek 建议持有: {reason}")
                self._stats["trades_skipped"] += 1
                return

            # ── 3. 从 DeepSeek 输出获取交易方向 ──
            trade_side = "buy" if decision["action"] == "buy" else "sell"
            size_eth = self._suggested_size(context)
            self._current_activity = f"📐 决策: {decision['action']} {size_eth:.4f} ETH (信心 {decision['confidence']}%)"
            self._last_activity_time = time.time()

            # ── 3b. 市场深度检查（Phase 2） ──
            prefer_limit = True
            if self.okx_client and hasattr(self.risk, 'check_market_depth_async'):
                self._current_activity = "🔍 检查市场深度…"
                self._last_activity_time = time.time()
                ok, reason, prefer_limit = await self.risk.check_market_depth_async(
                    self.okx_client, trade_side, size_eth
                )
                if not ok:
                    self._current_activity = f"⏭ 深度检查跳过: {reason[:40]}"
                    self._last_activity_time = time.time()
                    logger.info(f"市场深度拒绝: {reason}")
                    self._stats["trades_skipped"] += 1
                    return
                if prefer_limit:
                    logger.info(f"市场深度检查: {reason}")

            # ── 4. Layer 1 风控检查（使用真实交易方向） ──
            self._current_activity = "🛡️ 风控检查中…"
            self._last_activity_time = time.time()
            ok, reason = self.risk.check_layer1(trade_side, size_eth, context.get("current_price", 0))
            if not ok:
                self._current_activity = f"⏭ 风控拒绝: {reason[:40]}"
                self._last_activity_time = time.time()
                logger.info(f"Layer 1 拒绝: {reason}")
                self._stats["trades_skipped"] += 1
                return

            # ── 5. 执行交易 ──
            self._current_activity = f"💱 执行 {trade_side} {size_eth:.4f} ETH…"
            self._last_activity_time = time.time()
            logger.info(f"DeepSeek 决策: {decision['action']} (信心 {decision['confidence']}%)")
            self._stats["trades_executed"] += 1

            trade_result = await self.executor.execute_safe(
                side=trade_side,
                size_eth=size_eth,
                signal_price=context.get("current_price", 0),
                prefer_limit=prefer_limit,
            )

            # ── 6. Layer 3 记录（Phase 4: P&L 跟踪） ──
            if trade_result["success"]:
                trade_group_id = str(uuid.uuid4())[:8]
                self.risk.record_trade({
                    "side": trade_side,
                    "size": size_eth,
                    "price": trade_result["fill_price"],
                    "pnl": 0,
                    "pnl_close": 0,
                    "trade_group_id": trade_group_id,
                    "trade_type": "open",
                    "order_id": trade_result["order_id"],
                    "symbol": self.executor.symbol,
                    "decision": decision,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                logger.info(f"交易成功: {trade_side} {size_eth:.4f} ETH @ ${trade_result['fill_price']:.2f}")

                # Phase 2: 通知持仓监控器 (Phase 4: 传入 trade_group_id)
                if self.position_monitor:
                    stop_loss = float(decision.get("stop_loss", 0))
                    take_profit = float(decision.get("take_profit", 0))
                    self.position_monitor.update_position(
                        side=trade_side,
                        size=size_eth,
                        entry_price=trade_result["fill_price"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        trade_group_id=trade_group_id,
                    )
                # 通知 Agent 4 复盘（如果配置了）
                if self.agent4_reviewer:
                    trade_record = {
                        "id": trade_result["order_id"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "side": trade_side,
                        "size": size_eth,
                        "price": trade_result["fill_price"],
                        "pnl": 0.0,
                        "order_id": trade_result["order_id"],
                        "symbol": self.executor.symbol,
                        "decision": json.dumps(decision),
                        "pnl_close": 0.0,
                        "trade_group_id": trade_group_id,
                        "trade_type": "open",
                    }
                    asyncio.create_task(self.agent4_reviewer.notify_trade(trade_record))
                self._current_activity = f"✅ {trade_side} {size_eth:.4f} ETH @ ${trade_result['fill_price']:.2f}"
                self._last_activity_time = time.time()
            else:
                self.risk.report_api_error()
                self._current_activity = f"❌ 交易失败: {trade_result.get('error', '未知')[:40]}"
                self._last_activity_time = time.time()
                logger.error(f"交易失败: {trade_result['error']}")

    def _build_context(self, events: list[AgentEvent]) -> dict:
        """从事件列表构建 DeepSeek 上下文"""
        agent1_lines = []
        agent2_lines = []
        current_price = 0.0

        # Phase 3: 链上数据汇总
        gas_gwei = 0.0
        taker_buy_ratio = 0.0
        funding_rate_pct = 0.0
        whale_alerts: list[str] = []

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
            # Phase 3: 链上事件处理
            elif e.source == "agent2_gas":
                agent2_lines.append(d.get("description", ""))
                gas_gwei = d.get("gas_gwei", gas_gwei)
            elif e.source == "agent2_whale":
                agent2_lines.append(d.get("description", ""))
                whale_alerts.append(d.get("description", ""))
            elif e.source == "agent2_taker":
                agent2_lines.append(d.get("description", ""))
                taker_buy_ratio = d.get("buy_ratio", taker_buy_ratio)
            elif e.source == "agent2_funding":
                agent2_lines.append(d.get("description", ""))
                funding_rate_pct = d.get("funding_rate_pct", funding_rate_pct)

        # Phase 2: 注入风控状态
        risk_status = self.risk.get_status()

        # Phase 4: 多周期信心分
        composite = {}
        if self.confidence_scorer:
            composite = self.confidence_scorer.compute(events)
            self._stats["last_composite_score"] = composite.get("composite_score", 0)
            self._stats["last_composite_confidence"] = composite.get("composite_confidence", 0)

        # Phase 4: 信号对齐
        alignment = {}
        if self.signal_aligner:
            alignment = self.signal_aligner.align(events, composite)
            self._stats["last_alignment_score"] = alignment.get("alignment_score", 0)

        # Phase 4: 月度统计
        monthly = {"trades": 0, "win_rate": 0, "total_pnl": 0, "max_drawdown": 0}
        if self.review_gen:
            monthly = self.review_gen.compute_monthly_stats()
            self._stats["last_monthly_pnl"] = monthly.get("total_pnl", 0)
            self._stats["last_win_rate"] = monthly.get("win_rate", 0)

        return {
            "symbol": self.root_config.trading.symbol,
            "position_direction": self._current_position["side"],
            "position_size": self._current_position["size"],
            "entry_price": self._current_position["entry_price"],
            "pnl_pct": "",
            "agent1_summary": "\n".join(agent1_lines) if agent1_lines else "暂无技术面信号",
            "agent2_summary": "\n".join(agent2_lines) if agent2_lines else "暂无新闻数据",
            # Phase 3: 链上指标
            "gas_gwei": round(gas_gwei, 1),
            "taker_buy_ratio": f"{taker_buy_ratio:.1%}" if taker_buy_ratio else "—",
            "funding_rate_pct": round(funding_rate_pct, 4),
            "whale_alert": whale_alerts[-1] if whale_alerts else "无",
            # Phase 4: 真实统计替代硬编码零值
            "monthly_trades": monthly["trades"],
            "win_rate": monthly["win_rate"],
            "monthly_pnl": monthly["total_pnl"],
            "max_drawdown": monthly.get("max_drawdown", 0),
            "current_price": current_price,
            "risk_status": risk_status,
            # Phase 4: 信心分 + 对齐 + 自适应参数
            "composite_score": round(composite.get("composite_score", 0), 2) if composite else "—",
            "composite_confidence": round(composite.get("composite_confidence", 0), 2) if composite else "—",
            "signal_alignment": alignment.get("summary_line", "暂无对齐数据"),
            "adjusted_max_trades": str(self.config.agent3_max_daily_trades),
            "adjusted_debounce": str(self.config.agent3_debounce_seconds),
            "adjusted_trade_interval": str(self.config.agent3_min_interval_between_trades),
        }

    def _suggested_size(self, context: dict) -> float:
        """根据上下文和风控建议仓位大小 (Phase 4: 信号对齐调节)"""
        multiplier = self.risk.get_position_size_multiplier()
        base_size = 0.01  # 基础 0.01 ETH

        # Phase 4: 信号对齐调节
        if self.signal_aligner:
            alignment = context.get("_alignment_cache", {})
            if not alignment:
                # 从上下文中的 alignment 字段推断
                score = context.get("composite_score", 0)
                if isinstance(score, (int, float)):
                    pass  # 后续按需扩展
            # 交易中会通过 _build_context 的 alignment 结果感知风险

        return base_size * multiplier

    # ── Phase 4: 复盘报告调度 ──

    async def _review_scheduler(self):
        """定时检查并生成复盘报告"""
        last_daily_date = ""
        last_weekly_week = ""
        while self._running:
            now_utc = datetime.now(timezone.utc)
            today_str = now_utc.strftime("%Y-%m-%d")
            week_str = now_utc.strftime("%Y-W%W")

            if self.config.review_generator_enabled and self.review_gen:
                if now_utc.hour >= self.config.review_daily_hour_utc and today_str != last_daily_date:
                    self._current_activity = "📊 生成每日复盘报告…"
                    self._last_activity_time = time.time()
                    report = self.review_gen.generate_daily_report()
                    last_daily_date = today_str
                    self._current_activity = f"📊 每日复盘完成: 胜率 {report['stats']['win_rate']:.1f}%"
                    self._last_activity_time = time.time()
                    logger.info(f"📊 每日复盘: 胜率 {report['stats']['win_rate']:.1f}%, "
                                f"盈亏 {report['stats']['total_pnl']:+.2f} USDT")

                if now_utc.weekday() == 6 and week_str != last_weekly_week:  # 周日
                    self._current_activity = "📊 生成每周复盘报告…"
                    self._last_activity_time = time.time()
                    report = self.review_gen.generate_weekly_report()
                    last_weekly_week = week_str
                    logger.info(f"📊 每周复盘已生成")

            await asyncio.sleep(3600)  # 每小时检查一次

    # Agent 4 替代了 param_adapter 的调参职责

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
            "current_activity": self._current_activity,
            "last_activity_time": self._last_activity_time,
            "position": self._current_position,
            "event_buffer_size": len(self._event_buffer),
            "adjusted_max_trades": self.config.agent3_max_daily_trades,
            "adjusted_debounce": self.config.agent3_debounce_seconds,
            "adjusted_trade_interval": self.config.agent3_min_interval_between_trades,
            "deepseek_stats": self.deepseek.get_stats(),
            "executor_stats": self.executor.get_stats(),
            "risk_status": self.risk.get_status(),
            "phase4": {
                "confidence_scorer": self.confidence_scorer is not None,
                "signal_aligner": self.signal_aligner is not None,
                "review_generator": self.review_gen is not None,
                "agent4_reviewer": self.agent4_reviewer is not None,
            },
            **self._stats,
        }
