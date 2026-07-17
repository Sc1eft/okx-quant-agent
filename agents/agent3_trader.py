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
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, TypedDict

from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.deepseek_caller import DeepSeekTrader
from agents.risk_layer import RiskManager
from agents.trade_executor import TradeExecutor
from agents.config import AgentSystemConfig
from agents.rule_engine.base import Ctx
# Phase 4
from agents.confidence_scorer import ConfidenceScorer
from agents.signal_aligner import SignalAligner

logger = logging.getLogger("agent3")


def _safe_float(value, default: float = 0.0) -> float:
    """安全地将值转换为 float，不可转换时返回 default"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ── DeepSeek 上下文 TypedDict（_build_context 返回结构） ──

class TradingContext(TypedDict):
    """Agent 3 构建的 DeepSeek 交易决策上下文

    包含持仓信息、技术面摘要、新闻/链上数据、风控状态、
    多周期指标表格、历史交易记录等字段。注入 DeepSeek prompt。
    """
    symbol: str
    position_direction: str
    position_size: float
    entry_price: float
    pnl_pct: str
    market_mode: str
    leverage: int
    liquidation_price: float
    margin_rate: float
    agent1_summary: str
    agent2_summary: str
    gas_gwei: float
    taker_buy_ratio: str
    funding_rate_pct: float
    whale_alert: str
    monthly_trades: int
    win_rate: float
    monthly_pnl: float
    max_drawdown: float
    current_price: float
    risk_status: dict
    composite_score: str | float
    composite_confidence: str | float
    signal_alignment: str
    agent1_indicators_table: str
    market_state_summary: str
    agent4_advisory: str
    recent_trades_summary: str
    adjusted_max_trades: str
    adjusted_debounce: str
    adjusted_trade_interval: str
    max_position_eth: float


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
        rule_engine=None,      # Phase 2: 可插拔规则引擎（替代 RiskManager 硬编码检查）
        agent1=None,           # Agent 1 引用，用于读取多周期指标 + 市场状态
        review_generator=None,  # Phase 4: 复盘报告生成器
        agent4_reviewer=None,  # Agent 4 复盘改进（替代 param_adapter）
        notifier=None,        # 交易报告推送器（ServerChan）
    ):
        self.config = config
        self.bus = event_bus
        self.deepseek = deepseek
        self.risk = risk_manager
        self.executor = trade_executor
        self.root_config = root_config
        self.position_monitor = position_monitor
        self.okx_client = okx_client
        self.rule_engine = rule_engine
        self._btc_checked = False

        # Phase 4
        self.confidence_scorer = ConfidenceScorer(config) if config.confidence_scorer_enabled else None
        self.signal_aligner = SignalAligner(config) if config.signal_aligner_enabled else None
        self.review_gen = review_generator
        self.agent4_reviewer = agent4_reviewer  # Agent 4（替代 param_adapter）
        self.notifier = notifier  # 交易报告推送器

        # Agent 1 引用（用于读取多周期指标 + 市场状态，注入 DeepSeek）
        self.agent1 = agent1

        # Phase 2: 注册平仓回调，使 Agent 3 的 _current_position 随平仓同步更新
        if self.position_monitor:
            self.position_monitor.close_callback = self._on_position_closed

        # 事件缓冲区
        self._event_buffer: list[AgentEvent] = []
        self._last_decision_time: Optional[datetime] = None
        self._last_event_time: float = time.time()  # 上次收到事件的时间
        self._last_idle_decision_time: float = 0.0  # 上次强制空闲决策时间
        self._last_idle_decision_price: float = 0.0  # 上次空闲决策时的价格（Step 7 价格门控）
        self._decision_lock = asyncio.Lock()  # re-entrancy guard

        # 每日交易上限暂停标记
        self._paused_for_daily_limit: bool = False

        # 最新价格缓存（跨 _build_context 调用持久化，防止事件缓冲区无价格时显示 $0）
        self._current_price: float = 0.0
        self._price_refresh_task: Optional[asyncio.Task] = None

        # 运行状态
        self._running = False
        self._current_activity = ""
        self._last_activity_time = 0.0
        self._current_position = {
            "side": "none",
            "size": 0.0,
            "entry_price": 0.0,
            "current_price": 0.0,
            # ── 浮动盈亏 ──
            "pnl": 0.0,       # 未实现盈亏 (USDT)
            "pnl_pct": 0.0,   # 未实现盈亏 (%)
            # ── 合约模式字段 ──
            "market_mode": self.config.market_mode,
            "leverage": self.config.futures_leverage,
            "margin": 0.0,
            "liquidation_price": 0.0,
            "position_value": 0.0,
            "margin_rate": 0.0,
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
        # 价格定时刷新（WebSocket 被 Agent1 处理，Agent3 单独用 REST API 兜底价格缓存）
        consumers.append(self._refresh_current_price())
        # 空闲定时决策（长时间无事件时强制触发评估）
        consumers.append(self._idle_decision_loop())

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
            try:
                await self._on_event(event)
            except Exception:
                logger.exception("_consume_a: _on_event 异常，跳过该事件")
                continue

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
            try:
                await self._on_event(event)
            except Exception:
                logger.exception("_consume_b: _on_event 异常，跳过该事件")
                continue

    async def _on_event(self, event: AgentEvent):
        """收到新事件后的处理"""
        self._event_buffer.append(event)
        self._last_event_time = time.time()
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

    async def _idle_decision_loop(self):
        """空闲定时决策循环 — 长时间无事件时强制触发 DeepSeek 评估"""
        interval = self.config.agent3_idle_decision_interval_seconds
        while self._running:
            await asyncio.sleep(30)  # 每 30s 检查一次
            now = time.time()
            # 没有持仓时才做空闲评估（有持仓时 position_monitor 在管）
            if self._pos_state()[0] != "none":
                continue
            idle_since = now - self._last_event_time
            if idle_since < interval:
                continue
            # 距离上次空闲决策不要太近
            if now - self._last_idle_decision_time < interval * 0.5:
                continue
            # 如果缓冲区已有事件或有锁，让事件驱动流程处理
            if self._event_buffer or self._decision_lock.locked():
                continue
            # Step 7: 价格变化门控 — 价格变动不足 threshold % 时不触发
            if self._last_idle_decision_price > 0 and self._current_price > 0:
                pct_change = abs(self._current_price - self._last_idle_decision_price) / self._last_idle_decision_price * 100
                if pct_change < self.config.agent3_idle_decision_price_change_pct:
                    continue
            # 构造合成事件触发一次评估
            self._last_idle_decision_time = now
            self._last_idle_decision_price = self._current_price
            logger.info(f"⏰ 空闲触发定期评估 ({idle_since:.0f}s 无事件)")
            self._current_activity = f"⏰ 空闲 {idle_since:.0f}s 触发定期评估"
            self._last_activity_time = now
            event = AgentEvent(
                type=AgentEventType.TECHNICAL_SIGNAL,
                source="agent3",
                data={
                    "signal": "idle_evaluation",
                    "timeframe": "5m",
                    "description": f"定时评估（{idle_since:.0f}s 无事件）",
                    "price": self._current_price,
                },
                confidence=0.3,
                urgency="low",
            )
            self._event_buffer.append(event)
            self._last_event_time = now  # 抑制重复触发
            await self._make_decision()

    async def _make_decision(self):
        """执行一次完整的交易决策周期"""
        # Step 7: 记录当前价格（供空闲决策价格门控使用）
        if self._current_price > 0:
            self._last_idle_decision_price = self._current_price

        # ── 每日交易上限暂停（非阻塞：置标记即返回，风控跨日自动重置） ──
        # 旧实现 asyncio.sleep 数小时会卡死事件消费协程，导致缓冲区堆积。
        if self.risk.is_daily_limit_reached():
            if not self._paused_for_daily_limit:
                self._paused_for_daily_limit = True
                now_utc = datetime.now(timezone.utc)
                cst_now = now_utc + timedelta(hours=8)
                next_midnight = cst_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                sleep_sec = (next_midnight - cst_now).total_seconds()
                logger.warning(
                    f"🌙 今日交易已达上限 ({self.risk._daily_trade_count}/{self.config.agent3_max_daily_trades})，"
                    f"暂停至 CST 午夜（约 {sleep_sec / 3600:.1f}h）"
                )
                self._current_activity = f"🌙 交易达上限，暂停至 CST 午夜（约 {sleep_sec / 3600:.1f}h）"
                self._last_activity_time = time.time()
            # 暂停期间丢弃缓冲事件，防止无限堆积（反正也不会交易）
            self._event_buffer.clear()
            return
        if self._paused_for_daily_limit:
            # is_daily_limit_reached() 内部已跨日重置 → 恢复交易
            self._paused_for_daily_limit = False
            self._current_activity = "🌅 新日重置，恢复交易"
            self._last_activity_time = time.time()
            logger.info("🌅 每日上限暂停结束，恢复交易")

        async with self._decision_lock:
            if not self._event_buffer:
                return

            self._last_decision_time = datetime.now(timezone.utc)
            events = list(self._event_buffer)
            self._event_buffer.clear()

            # ── 0. Pre-trade 风控检查（RuleEngine 统一调度） ──
            if self.rule_engine:
                rule_context = self._build_rule_engine_context()
                pre_results = await self.rule_engine.check_pre_trade(rule_context)

                # 波动检查状态同步回 RiskManager（无论规则是否阻断都需更新）
                vol_result = next(
                    (r for r in pre_results if r.rule_name == "volatility_check"), None
                )
                if vol_result and not vol_result.passed:
                    delay = vol_result.data.get("delay_seconds", 300)
                    self.risk._volatility_delay_until = datetime.now(timezone.utc) + timedelta(seconds=delay)
                elif vol_result and vol_result.passed:
                    self.risk._volatility_delay_until = None

                if not self.rule_engine.all_pass(pre_results):
                    blocker = self.rule_engine.blocked_by(pre_results) or "unknown"
                    reason = next((r.reason for r in pre_results if not r.passed), "风控拒绝")
                    self._current_activity = f"⏭ 风控拒绝 ({blocker}): {reason[:40]}"
                    self._last_activity_time = time.time()
                    logger.info(f"Pre-trade 规则拒绝: [{blocker}] {reason}")
                    self._stats["trades_skipped"] += 1
                    return
            else:
                # ── 旧版回退（RuleEngine 未配置时） ──
                if self.okx_client and hasattr(self.risk, 'check_volatility_async'):
                    self._current_activity = "🔍 检查波动…"
                    self._last_activity_time = time.time()
                    ok, reason = await self.risk.check_volatility_async(self.okx_client, symbol=self.executor.symbol)
                    if not ok:
                        self._current_activity = f"⏭ 波动检查跳过: {reason[:40]}"
                        self._last_activity_time = time.time()
                        logger.info(f"波动检查拒绝: {reason}")
                        self._stats["trades_skipped"] += 1
                        return

                ok, reason = self.risk.check_layer1_pre()
                if not ok:
                    self._current_activity = f"⏭ 风控预检跳过: {reason[:40]}"
                    self._last_activity_time = time.time()
                    logger.info(f"风控预检拒绝: {reason}")
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
            size_eth = self._suggested_size(context, decision)
            self._current_activity = f"📐 决策: {decision['action']} {size_eth:.4f} ETH (信心 {decision['confidence']}%)"
            self._last_activity_time = time.time()

            # ── 3a. 方向持久性检查：反转/同方向加仓 ──
            is_add = False
            if self.position_monitor:
                pm_status = self.position_monitor.get_status()
                current_side = pm_status.get("position_side", "none")
                current_size = pm_status.get("position_size", 0.0)
                if current_side != "none":
                    pos_is_long = current_side == "long"
                    decision_is_long = trade_side == "buy"
                    is_reversal = pos_is_long != decision_is_long

                    if is_reversal:
                        # ── 反转：需要更高信心 ──
                        reversal_confidence = decision.get("confidence", 0)
                        min_reversal_conf = 70
                        if reversal_confidence < min_reversal_conf:
                            logger.info(
                                f"方向持久性: 反转 {current_side}→{trade_side} 信心 {reversal_confidence}% "
                                f"< {min_reversal_conf}%，跳过"
                            )
                            self._current_activity = (
                                f"⏭ 反转信心不足: {current_side}→{trade_side} "
                                f"({reversal_confidence}% < {min_reversal_conf}%)"
                            )
                            self._last_activity_time = time.time()
                            self._stats["trades_skipped"] += 1
                            return
                    else:
                        # ── 同方向：主动补仓 ──
                        if not self._should_add_to_position(decision, current_size):
                            logger.info(
                                f"补仓被跳过: {current_side} + {trade_side} "
                                f"(confidence={decision.get('confidence', 0)}%, "
                                f"当前持仓={current_size:.4f} ETH)"
                            )
                            self._current_activity = (
                                f"⏭ 补仓跳过: 信心/仓位条件不满足"
                            )
                            self._last_activity_time = time.time()
                            self._stats["trades_skipped"] += 1
                            return

                        add_size = self._suggested_add_size(current_size, context, decision)
                        if add_size < 0.01:
                            logger.info(
                                f"补仓量 {add_size:.4f} < 0.01，跳过"
                            )
                            self._current_activity = f"⏭ 补仓量过小 ({add_size:.4f})"
                            self._last_activity_time = time.time()
                            self._stats["trades_skipped"] += 1
                            return

                        size_eth = add_size  # 使用追加量，不是全量
                        is_add = True
                        self._current_activity = (
                            f"📐 补仓: {decision['action']} +{add_size:.4f} ETH "
                            f"(信心 {decision['confidence']}%)"
                        )
                        self._last_activity_time = time.time()

            # ── 3b. Execution 风控检查（RuleEngine 统一调度，含深度/滑点/方向冲突） ──
            if self.rule_engine:
                exec_context = self._build_rule_engine_context({
                    Ctx.SIDE: trade_side,
                    Ctx.SIZE: size_eth,
                    Ctx.PRICE: context.get("current_price", 0),
                    "signal_price": context.get("current_price", 0),
                    "entry_price_min": 0,
                    "entry_price_max": 0,
                })
                self._current_activity = "🛡️ 执行风控检查…"
                self._last_activity_time = time.time()
                exec_results = await self.rule_engine.check_execution(exec_context)

                # 提取 market_depth 结果的 prefer_limit 设置
                depth_result = next(
                    (r for r in exec_results if r.rule_name == "market_depth"), None
                )
                prefer_limit = depth_result.data.get("prefer_limit", True) if depth_result else True

                if not self.rule_engine.all_pass(exec_results):
                    blocker = self.rule_engine.blocked_by(exec_results) or "unknown"
                    reason = next((r.reason for r in exec_results if not r.passed), "执行检查拒绝")
                    self._current_activity = f"⏭ 执行拒绝 ({blocker}): {reason[:40]}"
                    self._last_activity_time = time.time()
                    logger.info(f"Execution 规则拒绝: [{blocker}] {reason}")
                    self._stats["trades_skipped"] += 1
                    return
            else:
                # ── 旧版回退（RuleEngine 未配置时） ──
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

            trade_result = await self.executor.execute_safe(
                side=trade_side,
                size_eth=size_eth,
                signal_price=context.get("current_price", 0),
                prefer_limit=prefer_limit,
            )

            # ── 6. Layer 3 记录（Phase 4: P&L 跟踪） ──
            if trade_result["success"]:
                self._stats["trades_executed"] += 1
                # 成交价兜底：真实模式下市价单可能暂时查不到 fillPx，
                # 绝不用 0 入账（会污染 PnL/止损计算）
                fill_price = trade_result["fill_price"]
                if not fill_price or fill_price <= 0:
                    fill_price = context.get("current_price", 0)
                    logger.warning(f"成交价缺失，用当前价 ${fill_price:.2f} 兜底入账")
                trade_group_id = str(uuid.uuid4())[:8]
                # 止损止盈：取 DeepSeek 值并做方向校验（多头 SL<入场<TP，空头相反），
                # 方向错误回退配置默认百分比——错误值会让 PositionMonitor 立即触发。
                # 校验后的最终值随开仓记录入库，重启恢复时原样还原。
                stop_loss = _safe_float(decision.get("stop_loss"), 0)
                take_profit = _safe_float(decision.get("take_profit"), 0)
                current_px = context.get("current_price") or fill_price
                sl_pct = self.config.agent3_default_stop_loss_pct / 100
                tp_pct = self.config.agent3_default_take_profit_pct / 100
                if trade_side == "buy":
                    default_sl, default_tp = current_px * (1 - sl_pct), current_px * (1 + tp_pct)
                    sl_ok = 0 < stop_loss < fill_price
                    tp_ok = take_profit > fill_price
                else:
                    default_sl, default_tp = current_px * (1 + sl_pct), current_px * (1 - tp_pct)
                    sl_ok = stop_loss > fill_price
                    tp_ok = 0 < take_profit < fill_price
                if not sl_ok:
                    if stop_loss != 0:
                        logger.warning(
                            f"DeepSeek 止损方向错误: {trade_side} SL=${stop_loss:.2f} "
                            f"入场=${fill_price:.2f}，回退默认 {self.config.agent3_default_stop_loss_pct}%"
                        )
                    stop_loss = default_sl
                if not tp_ok:
                    if take_profit != 0:
                        logger.warning(
                            f"DeepSeek 止盈方向错误: {trade_side} TP=${take_profit:.2f} "
                            f"入场=${fill_price:.2f}，回退默认 {self.config.agent3_default_take_profit_pct}%"
                        )
                    take_profit = default_tp
                trade_record = {
                    "side": trade_side,
                    "size": size_eth,
                    "price": fill_price,
                    "pnl": 0,
                    "pnl_close": 0,
                    "fee": round(size_eth * fill_price * (
                        self.config.maker_fee_rate if prefer_limit else self.config.taker_fee_rate
                    ), 2),
                    "trade_group_id": trade_group_id,
                    "trade_type": "open",
                    "order_id": trade_result["order_id"],
                    "symbol": self.executor.symbol,
                    "decision": decision,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "confidence": decision.get("confidence", 0),
                    "position_size_pct": decision.get("position_size_pct", 0),
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                }
                if trade_side == "sell":
                    trade_record["short"] = True
                self.risk.record_trade(trade_record)
                logger.info(f"交易成功: {trade_side} {size_eth:.4f} ETH @ ${fill_price:.2f}")

                # Phase 2: 通知持仓监控器 (Phase 4: 传入 trade_group_id)
                if self.position_monitor:
                    pos_side = "long" if trade_side == "buy" else "short"
                    self.position_monitor.update_position(
                        side=pos_side,
                        size=size_eth,
                        entry_price=fill_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        trade_group_id=trade_group_id,
                        opened_with_limit=prefer_limit,
                        accumulate=is_add,
                        confidence=decision.get("confidence", 0),
                        position_size_pct=decision.get("position_size_pct", 0),
                    )
                # 通知 Agent 4 复盘（如果配置了）
                if self.agent4_reviewer:
                    trade_record = {
                        "id": trade_result["order_id"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "side": trade_side,
                        "size": size_eth,
                        "price": fill_price,
                        "pnl": 0.0,
                        "order_id": trade_result["order_id"],
                        "symbol": self.executor.symbol,
                        "decision": json.dumps(decision),
                        "pnl_close": 0.0,
                        "trade_group_id": trade_group_id,
                        "trade_type": "open",
                        "confidence": decision.get("confidence", 0),
                        "position_size_pct": _safe_float(decision.get("position_size_pct"), 0),
                    }
                    asyncio.create_task(self.agent4_reviewer.notify_trade(trade_record))
                # 更新当前持仓（供前端和上下文使用）
                if is_add:
                    old_size = self._current_position["size"]
                    old_entry = self._current_position["entry_price"]
                    new_size = old_size + size_eth
                    new_entry = (old_size * old_entry + size_eth * fill_price) / new_size if new_size > 0 else fill_price
                    self._current_position["size"] = new_size
                    self._current_position["entry_price"] = new_entry
                    self._current_position["current_price"] = context.get("current_price") or fill_price
                    # 合约字段累加
                    if trade_result.get("market_mode") == "futures":
                        self._current_position["market_mode"] = "futures"
                        self._current_position["leverage"] = trade_result.get("leverage", self._current_position.get("leverage", 0))
                        self._current_position["margin"] = self._current_position.get("margin", 0) + trade_result.get("margin", 0)
                        self._current_position["position_value"] = self._current_position.get("position_value", 0) + trade_result.get("position_value", 0)
                        if trade_result.get("liquidation_price"):
                            self._current_position["liquidation_price"] = trade_result["liquidation_price"]
                        self._current_position["margin_rate"] = trade_result.get("margin_rate", self._current_position.get("margin_rate", 0))
                else:
                    self._current_position["side"] = "long" if trade_side == "buy" else "short"
                    self._current_position["size"] = size_eth
                    self._current_position["entry_price"] = fill_price
                    self._current_position["current_price"] = context.get("current_price") or fill_price
                    # 合约模式：额外记录保证金/强平价等
                    if trade_result.get("market_mode") == "futures":
                        self._current_position["market_mode"] = "futures"
                        self._current_position["leverage"] = trade_result.get("leverage", 0)
                        self._current_position["margin"] = trade_result.get("margin", 0)
                        self._current_position["liquidation_price"] = trade_result.get("liquidation_price", 0)
                        self._current_position["position_value"] = trade_result.get("position_value", 0)
                        self._current_position["margin_rate"] = trade_result.get("margin_rate", 0)
                self._current_activity = f"✅ {trade_side} {size_eth:.4f} ETH @ ${fill_price:.2f}"
                self._last_activity_time = time.time()
            else:
                self.risk.report_api_error()
                self._current_activity = f"❌ 交易失败: {trade_result.get('error', '未知')[:40]}"
                self._last_activity_time = time.time()
                logger.error(f"交易失败: {trade_result['error']}")

    def _build_context(self, events: list[AgentEvent]) -> TradingContext:
        """从事件列表构建 DeepSeek 上下文（返回类型为 TradingContext TypedDict）"""
        agent1_lines = []
        agent2_lines = []
        # 从缓存开始（如果没有事件提供价格，沿用上次已知价格）
        current_price = self._current_price

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
                    self._current_price = price  # 持久化缓存
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

        pos_side, pos_size, pos_entry = self._pos_state()
        return {
            "symbol": self.root_config.trading.symbol,
            "position_direction": pos_side,
            "position_size": pos_size,
            "entry_price": pos_entry,
            "pnl_pct": self._calc_pnl_pct(current_price),
            # ── 合约模式字段 ──
            "market_mode": self._current_position.get("market_mode", "spot"),
            "leverage": self._current_position.get("leverage", 0),
            "liquidation_price": self._current_position.get("liquidation_price", 0),
            "margin_rate": self._current_position.get("margin_rate", 0),
            "agent1_summary": self._summarize_agent1(agent1_lines, agent2_lines, events),
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
            # ── 多周期指标表格（直接引用 Agent 1） ──
            "agent1_indicators_table": self.agent1.get_indicators_table() if self.agent1 else "Agent 1 未启用",
            # ── 市场状态分类 ──
            "market_state_summary": self.agent1.get_market_state().get("summary_line", "数据未就绪") if self.agent1 else "市场状态数据未就绪",
            # ── Step 3: Agent 4 交易建议 ──
            "agent4_advisory": (
                self.agent4_reviewer.get_advisory()
                if self.agent4_reviewer and hasattr(self.agent4_reviewer, 'get_advisory')
                else ""
            ),
            # ── Step 5: 近期交易历史（最近 5 笔已平仓） ──
            "recent_trades_summary": self._load_recent_trades_summary(),
            "adjusted_max_trades": str(self.config.agent3_max_daily_trades),
            "adjusted_debounce": str(self.config.agent3_debounce_seconds),
            "adjusted_trade_interval": str(self.config.agent3_min_interval_between_trades),
            "max_position_eth": self.config.agent3_max_position_eth,
        }

    def _build_rule_engine_context(self, extra: dict = None) -> dict:
        """从 RiskManager 状态构建 RuleEngine 上下文 dict

        将 RiskManager 的内部状态映射到 Ctx 常量键名，
        使 RuleEngine 的规则可以独立访问所有风控数据。

        Args:
            extra: 附加键值对（如 SIDE, SIZE, PRICE），在执行阶段传入

        Returns:
            RuleEngine context dict
        """
        now = datetime.now(timezone.utc)
        pos_side, pos_size = self.risk.get_position()
        ctx = {
            Ctx.TIME: now,
            Ctx.SYMBOL: self.executor.symbol,
            Ctx.DAILY_TRADE_COUNT: self.risk._daily_trade_count,
            Ctx.DAILY_LOSS_USDT: self.risk._daily_loss_usdt,
            Ctx.MAX_DAILY_LOSS_USDT: self.config.agent3_max_daily_loss_usdt,
            Ctx.CONSECUTIVE_LOSSES: self.risk._consecutive_losses,
            Ctx.LAST_TRADE_TIME: self.risk._last_trade_time,
            Ctx.API_BREAKER_UNTIL: self.risk._api_breaker_until,
            Ctx.POSITION_SIDE: pos_side,
            Ctx.POSITION_SIZE: pos_size,
            Ctx.MAX_POSITION_ETH: self.config.agent3_max_position_eth,
            Ctx.MAX_DAILY_TRADES: self.config.agent3_max_daily_trades,
            Ctx.MAX_CONSECUTIVE_LOSSES: self.config.agent3_max_consecutive_losses,
            Ctx.MIN_TRADE_INTERVAL: self.config.agent3_min_interval_between_trades,
            Ctx.MAX_TRADES_PER_HOUR: self.config.max_trades_per_hour,
            # OKX 客户端（波动检查、市场深度规则需要）
            "okx_client": self.okx_client,
            # SQLite 连接（HFT 防护规则需要）
            "db_conn": getattr(self.risk, '_db_conn', None),
            # 波动延迟状态
            "volatility_delay_until": getattr(self.risk, '_volatility_delay_until', None),
            "volatility_threshold_pct": self.config.volatility_threshold_pct,
            "volatility_delay_seconds": self.config.volatility_delay_seconds,
            # 市场深度配置
            "market_depth_spread_bps": self.config.market_depth_spread_bps,
            # 滑点保护配置
            "max_slippage_pct": self.config.max_slippage_pct,
        }
        if extra:
            ctx.update(extra)
        return ctx

    def _summarize_agent1(self, agent1_lines: list, agent2_lines: list, events: list) -> str:
        """智能构建技术面摘要 — 区分空闲触发、新闻驱动等场景"""
        if agent1_lines:
            return "\n".join(agent1_lines)
        # 检查是否有定时评估合成事件
        has_idle = any(
            isinstance(e, AgentEvent) and e.source == "agent3"
            and isinstance(e.data, dict) and e.data.get("signal") == "idle_evaluation"
            for e in events
        )
        if has_idle:
            return "【定时评估】当前无技术面事件触发，基于已有数据做例行检查"
        # 有新闻/链上事件但无技术面事件
        if agent2_lines:
            return "（非技术触发）当前无技术面信号，以下为新闻/链上数据驱动的评估"
        return "暂无技术面信号"

    def _load_recent_trades_summary(self, n: int = 5) -> str:
        """Step 5: 加载最近 N 笔已平仓交易摘要（供 DeepSeek 上下文注入）"""
        if self.review_gen and hasattr(self.review_gen, 'get_recent_trades_summary'):
            try:
                return self.review_gen.get_recent_trades_summary(n)
            except Exception as e:
                logger.debug(f"通过 review_gen 加载近期交易失败: {e}")
        try:
            conn = self.risk._db.conn
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE trade_type='close' ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
            if not rows:
                return "暂无近期交易"
            lines = []
            for r in reversed(rows):
                pnl = r["pnl_close"] or r["pnl"] or 0
                side = r["side"]
                price = r["price"] or 0
                ts = r["timestamp"][:16] if r["timestamp"] else ""
                emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                lines.append(f"  {emoji} {ts} | {side} @ ${price:.2f} | PnL {pnl:+.2f} USDT")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"直接查 DB 加载近期交易失败: {e}")
            return "近期交易数据不可用"

    def _pos_state(self) -> tuple[str, float, float]:
        """持仓权威状态: (side, size, entry_price)

        PositionMonitor 是唯一仓位事实源；未接入时（如单元测试）
        回退到本地 _current_position 展示缓存。
        """
        if self.position_monitor:
            st = self.position_monitor.get_status()
            return (
                st.get("position_side", "none"),
                st.get("position_size", 0.0),
                st.get("entry_price", 0.0),
            )
        pos = self._current_position
        return pos.get("side", "none"), pos.get("size", 0.0), pos.get("entry_price", 0.0)

    def _calc_pnl_pct(self, current_price: float) -> str:
        """根据当前仓位计算浮盈/浮亏百分比（合约模式含杠杆放大）"""
        direction, _, entry = self._pos_state()
        if direction != "none" and entry > 0 and current_price > 0:
            if direction == "long":
                pnl_pct = (current_price - entry) / entry * 100
            else:
                pnl_pct = (entry - current_price) / entry * 100
            # 合约模式：PnL 百分比含杠杆乘数
            if self._current_position.get("market_mode") == "futures":
                leverage = self._current_position.get("leverage", 1)
                if leverage > 0:
                    pnl_pct *= leverage
            return f"{pnl_pct:+.2f}"
        return ""

    def _suggested_size(self, context: dict, decision: dict = None) -> float:
        """根据 DeepSeek 把握程度 + 风控建议仓位大小

        DeepSeek 返回 position_size_pct (0-100)：
          - 高把握 (70-100) → 接近打满 max_position
          - 中等 (30-70)  → 正常仓位
          - 低把握 (5-30)  → 小仓试水

        最终经风控连亏递减 × Agent 4 乘数 × Step 6 胜率乘数调整。
        """
        max_pos = self.config.agent3_max_position_eth  # 默认 0.5 ETH

        # DeepSeek 把握程度 → 仓位比例
        deepseek_pct_str = "50"
        if decision and decision.get("position_size_pct"):
            deepseek_pct_str = str(decision["position_size_pct"])
        deepseek_pct = _safe_float(deepseek_pct_str, 50)
        deepseek_pct = max(5, min(100, deepseek_pct))  # 钳位 5%~100%

        base = max_pos * (deepseek_pct / 100)  # DeepSeek 决定基础比例

        # Agent 4 长期乘数 / 风控连亏递减
        agent4_mult = self.config.agent3_position_size_multiplier
        risk_mult = self.risk.get_position_size_multiplier()

        # Step 6: 胜率驱动乘数（仅当交易量 ≥ 5 笔时生效）
        win_rate = _safe_float(context.get("win_rate", 0))
        total_trades = int(context.get("monthly_trades", 0))
        if total_trades >= 5:
            # 胜率 / 50 = 中性值，上限 1.5x，下限 0.3x
            # 50% 胜率 → 1.0x, 75% → 1.5x, 30% → 0.6x
            win_rate_mult = min(1.5, max(0.3, win_rate / 50))
        else:
            win_rate_mult = 1.0  # 数据不足，不加乘数
            logger.debug(f"胜率乘数跳过: 交易量 {total_trades} < 5")

        size = base * agent4_mult * risk_mult * win_rate_mult
        size = min(size, max_pos)
        size = max(size, 0.01)

        logger.debug(
            f"仓位计算: max={max_pos} base={base:.4f} "
            f"agent4=×{agent4_mult:.2f} risk=×{risk_mult:.2f} "
            f"win_rate=×{win_rate_mult:.2f} ({win_rate:.1f}%) "
            f"→ {size:.4f} ETH"
        )
        return size

    def _suggested_add_size(self, current_size: float, context: dict, decision: dict = None) -> float:
        """计算补仓追加量（基于剩余仓位空间）

        与 _suggested_size 不同：追加量只用到 max_pos 的剩余空间，
        而不是从 0 开始算全量。
        """
        max_pos = self.config.agent3_max_position_eth
        remaining = max_pos - current_size
        if remaining <= 0.01:
            return 0.0  # 仓位已满，不加

        deepseek_pct = _safe_float(decision.get("position_size_pct", 50), 50) if decision else 50
        deepseek_pct = max(5, min(100, deepseek_pct))

        base = remaining * (deepseek_pct / 100)
        agent4_mult = self.config.agent3_position_size_multiplier
        risk_mult = self.risk.get_position_size_multiplier()

        win_rate = _safe_float(context.get("win_rate", 0))
        total_trades = int(context.get("monthly_trades", 0))
        win_rate_mult = min(1.5, max(0.3, win_rate / 50)) if total_trades >= 5 else 1.0

        add_size = base * agent4_mult * risk_mult * win_rate_mult
        add_size = min(add_size, remaining)  # 不超剩余空间
        add_size = max(add_size, 0.01)       # 最小单位

        logger.debug(
            f"补仓计算: max={max_pos}当前={current_size:.4f}剩余={remaining:.4f} "
            f"deepseek_pct={deepseek_pct} base={base:.4f} "
            f"agent4=×{agent4_mult:.2f} risk=×{risk_mult:.2f} "
            f"win_rate=×{win_rate_mult:.2f} → +{add_size:.4f} ETH"
        )
        return add_size

    def _should_add_to_position(self, decision: dict, current_size: float) -> bool:
        """判断是否应该补仓

        优先级：
        1. DeepSeek 显式指定 add_to_position
        2. 信心足够高（≥65）且仓位未满
        3. 仓位很小（<30% max）且信心 ≥50
        """
        # 1. DeepSeek 显式指定
        add_flag = decision.get("add_to_position")
        if add_flag is True:
            return True
        if add_flag is False:
            return False

        # 2. 默认 heuristic
        confidence = decision.get("confidence", 0)
        max_pos = self.config.agent3_max_position_eth

        if confidence >= 65:
            return True

        if current_size < max_pos * 0.3 and confidence >= 50:
            return True

        return False

    # ── 价格刷新（后台协程，确保 _current_price 始终有值） ──

    async def _refresh_current_price(self):
        """后台循环：定期从 OKX API 获取最新价格并缓存"""
        while self._running:
            try:
                if self.okx_client:
                    ticker = await asyncio.to_thread(
                        self.okx_client.get_ticker, self.root_config.trading.symbol
                    )
                    price = float(ticker.get("last", 0))
                    if price > 0:
                        self._current_price = price
            except Exception:
                pass  # 静默失败，下次重试
            await asyncio.sleep(30)  # 每 30s 刷新一次

    # ── Phase 4: 复盘报告调度 ──

    async def _review_scheduler(self):
        """定时检查并生成复盘报告 + 推送微信"""
        last_daily_date = ""
        last_weekly_week = ""
        last_monthly_month = ""
        while self._running:
            now_utc = datetime.now(timezone.utc)
            today_str = now_utc.strftime("%Y-%m-%d")
            week_str = now_utc.strftime("%Y-W%W")
            month_str = now_utc.strftime("%Y-%m")

            if self.config.review_generator_enabled and self.review_gen:
                # ── 每日报告 (UTC 16:00) ──
                if now_utc.hour >= self.config.review_daily_hour_utc and today_str != last_daily_date:
                    self._current_activity = "📊 生成每日复盘报告…"
                    self._last_activity_time = time.time()
                    report = self.review_gen.generate_daily_report()
                    last_daily_date = today_str
                    self._push_report_if_needed(report, "daily", today_str)
                    self._current_activity = f"📊 每日复盘完成: 胜率 {report['stats']['win_rate']:.1f}%"
                    self._last_activity_time = time.time()
                    logger.info(f"📊 每日复盘: 胜率 {report['stats']['win_rate']:.1f}%, "
                                f"盈亏 {report['stats']['total_pnl']:+.2f} USDT")

                # ── 每周报告 (周日 + UTC 16:00) ──
                if now_utc.weekday() == 6 and now_utc.hour >= self.config.review_daily_hour_utc and week_str != last_weekly_week:
                    self._current_activity = "📊 生成每周复盘报告…"
                    self._last_activity_time = time.time()
                    report = self.review_gen.generate_weekly_report()
                    last_weekly_week = week_str
                    self._push_report_if_needed(report, "weekly", week_str)
                    logger.info("📊 每周复盘已生成")

                # ── 每月报告 (1日 + UTC 16:00) ──
                if now_utc.day == 1 and now_utc.hour >= self.config.review_daily_hour_utc and month_str != last_monthly_month:
                    self._current_activity = "📊 生成月度复盘报告…"
                    self._last_activity_time = time.time()
                    report = self.review_gen.generate_monthly_report()
                    last_monthly_month = month_str
                    self._push_report_if_needed(report, "monthly", month_str)
                    logger.info("📊 月度复盘已生成")

            await asyncio.sleep(3600)  # 每小时检查一次

    def _push_report_if_needed(self, report: dict, report_type: str, date_str: str):
        """如果配置了推送且未推送，推送报告到微信"""
        if not self.notifier or not self.config.serverchan_enabled:
            return
        if report.get("pushed"):
            return

        try:
            ok = self.notifier.push_report(report_type, date_str, report)
            if ok:
                report["pushed"] = True
                report["push_time"] = datetime.now(timezone.utc).isoformat()
                self._rewrite_report_file(report, report_type, date_str)
        except Exception as e:
            logger.warning(f"推送报告失败: {e}")

    def _rewrite_report_file(self, report: dict, report_type: str, date_str: str):
        """更新报告文件的 pushed 标记"""
        base_dir = Path(self.config.report_dir) / report_type
        filename = f"{report_type}_{date_str}.json"
        path = base_dir / filename
        try:
            with open(str(path), "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.warning(f"更新报告文件推送状态失败: {e}")

    # Agent 4 替代了 param_adapter 的调参职责

    def update_position(self, side: str, size: float, entry_price: float,
                        market_mode: str = "spot", leverage: int = 0,
                        margin: float = 0, liquidation_price: float = 0,
                        position_value: float = 0, margin_rate: float = 0):
        """更新当前持仓（供外部或 main.py 调用）"""
        self._current_position = {
            "side": side,
            "size": size,
            "entry_price": entry_price,
            "market_mode": market_mode,
            "leverage": leverage,
            "margin": margin,
            "liquidation_price": liquidation_price,
            "position_value": position_value,
            "margin_rate": margin_rate,
        }

    def _on_position_closed(self, side: str, size: float, fill_price: float, pnl: float):
        """仓位监控器平仓后的回调 — 重置 Agent 3 的仓位状态"""
        self._current_position = {
            "side": "none", "size": 0.0, "entry_price": 0.0,
            "current_price": 0.0, "pnl": 0.0, "pnl_pct": 0.0,
        }
        logger.info(
            f"仓位已平仓: {side} {size:.4f} ETH @ ${fill_price:.2f} "
            f"PnL={pnl:+.2f} USDT — Agent 3 仓位状态已重置"
        )

    def _update_position_pnl(self):
        """用当前价格实时计算浮动盈亏，写入 _current_position

        合约模式：pnl_pct 按保证金比计算（含杠杆放大）。
        """
        pos = self._current_position
        pos["current_price"] = self._current_price
        # side/size/entry 以 PositionMonitor 为权威（未接入时回退本地缓存）
        direction, size, entry = self._pos_state()
        pos["side"] = direction
        pos["size"] = size
        pos["entry_price"] = entry
        price = self._current_price

        if direction != "none" and entry > 0 and price > 0 and size > 0:
            if direction in ("buy", "long"):
                diff = price - entry
            else:
                diff = entry - price
            raw_pnl = diff * size
            pos["pnl"] = round(raw_pnl, 2)
            # 原始百分比（按总仓位价值算）
            raw_pct = diff / entry * 100
            # 合约模式：百分比按保证金比算（含杠杆）
            if pos.get("market_mode") == "futures":
                lev = pos.get("leverage", 1)
                if lev > 0:
                    raw_pct *= lev
            pos["pnl_pct"] = round(raw_pct, 2)
        else:
            pos["pnl"] = 0.0
            pos["pnl_pct"] = 0.0

    def get_status(self) -> dict:
        # ── 实时计算浮动盈亏写入 _current_position ──
        self._update_position_pnl()

        # ── 今日数据：从 _daily_trades 计算 ──
        daily_trades = getattr(self.risk, '_daily_trades', [])
        daily_wins = sum(1 for t in daily_trades if t.get("pnl", 0) > 0)
        daily_losses = sum(1 for t in daily_trades if t.get("pnl", 0) < 0)
        daily_win_rate = round(daily_wins / max(daily_wins + daily_losses, 1) * 100, 1) if (daily_wins + daily_losses) > 0 else 0.0

        # ── 月度数据（review_gen 缓存，避免每 5s 查 DB）──
        monthly_stats = getattr(self, '_cached_monthly_stats', None)
        if not monthly_stats or not hasattr(self, '_monthly_cache_time'):
            # 首次获取，用 review_gen 或 stats 缓存
            if self.review_gen and hasattr(self.review_gen, 'compute_monthly_stats'):
                try:
                    monthly_stats = self.review_gen.compute_monthly_stats()
                except Exception:
                    monthly_stats = None
            self._cached_monthly_stats = monthly_stats
            self._monthly_cache_time = time.time()
        else:
            # 每分钟刷新一次
            now = time.time()
            if now - getattr(self, '_monthly_cache_time', 0) > 60 and self.review_gen:
                try:
                    monthly_stats = self.review_gen.compute_monthly_stats()
                    self._cached_monthly_stats = monthly_stats
                except Exception:
                    monthly_stats = self._cached_monthly_stats

        return {
            "running": self._running,
            "current_activity": self._current_activity,
            "last_activity_time": self._last_activity_time,
            "position": self._current_position,
            "event_buffer_size": len(self._event_buffer),
            "paused_for_daily_limit": self._paused_for_daily_limit,
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
            # ── 新增汇总板块 ──
            "daily_stats": {
                "trades": self.risk._daily_trade_count,
                "max_trades": self.config.agent3_max_daily_trades,
                "wins": daily_wins,
                "losses": daily_losses,
                "win_rate": daily_win_rate,
                "realized_pnl": round(self.risk._daily_realized_pnl, 2),
                "loss_usdt": round(self.risk._daily_loss_usdt, 2),
            },
            "monthly_stats": monthly_stats or {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "max_drawdown_pct": 0.0,
            },
        }
