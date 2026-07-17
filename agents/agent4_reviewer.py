"""
Agent 4 — 复盘改进 Agent

每 N 笔交易自动触发复盘：采集交易数据、行情、信号、新闻、链上数据，
调用 DeepSeek 做 AI 分析，输出参数调整建议，自动应用到共享 config。

替代 Phase 4 的规则式 param_adapter.py。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from data.db_manager import DatabaseManager

if TYPE_CHECKING:
    from agents.agent1_technical import Agent1
    from agents.agent2_news import Agent2
    from agents.config import AgentSystemConfig
    from agents.deepseek_caller import DeepSeekTrader
    from agents.kline_builder import KlineBuilder

logger = logging.getLogger("agent4_reviewer")

# 参数安全边界
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "agent3_max_daily_trades": (1, 30),
    "agent3_debounce_seconds": (5, 300),
    "agent3_min_interval_between_trades": (30, 3600),
    "agent3_max_daily_loss_usdt": (10, 500),
    "agent3_max_consecutive_losses": (1, 10),
    "agent3_max_position_eth": (0.01, 2.0),
    "agent3_position_size_multiplier": (0.1, 3.0),
    "agent3_default_stop_loss_pct": (0.5, 10.0),
    "agent3_default_take_profit_pct": (1.0, 20.0),
    "agent1_change_cooldown": (10, 600),
}

# 风险参数（AI 只能收窄不能放宽）
_RISK_PARAMS = {"agent3_max_daily_loss_usdt", "agent3_max_consecutive_losses"}

_REVIEW_PROMPT_TEMPLATE = """你是一个量化交易复盘分析AI。分析最近{count}笔交易，找出模式，输出参数调整建议。

【最近{count}笔交易】
{recent_trades}

【大盘行情背景】（交易时段内）
{market_context}

【Agent 1 信号统计】（交易时段内）
{signal_stats}

【同期新闻摘要】
{news_summary}

【链上数据快照】（均值）
{onchain_snapshot}

【当前运行参数】
{current_params}

【历史复盘记录】
{prev_reviews}

请输出JSON格式的分析结果（不要markdown围栏）：
{{
    "summary": "一句话总览（中文，50字内）",
    "market_regime": "当前市场形态判断",
    "strategy_insights": "策略洞察和发现",
    "trading_advisory": "具体的交易建议，如'趋势较强建议只做多不做空'或'震荡行情中谨慎入场、缩小止盈'——此建议会注入到后续 Agent 3 的 DeepSeek 决策上下文中",
    "param_adjustments": [
        {{
            "param": "参数名（必须使用【当前运行参数】的精确名称，如 agent3_min_interval_between_trades）",
            "from": 当前值,
            "to": 建议值,
            "reason": "调整原因"
        }}
    ]
}}"""

# 注意：param 字段必须使用【当前运行参数】列出的精确参数名，不能简写。




class Agent4Reviewer:
    """复盘改进 Agent

    每 N 笔交易自动触发一次完整复盘流程。
    """

    def __init__(
        self,
        config: AgentSystemConfig,
        deepseek: DeepSeekTrader,
        db_path: str,
        kline_builder: KlineBuilder,
        agent1: Agent1,
        agent2: Agent2,
    ):
        self._config = config
        self._deepseek = deepseek
        self._db_path = db_path
        self._db = DatabaseManager(db_path)  # 共享连接
        self._kline_builder = kline_builder
        self._agent1 = agent1
        self._agent2 = agent2

        self._trade_count = 0
        self._last_review_count = 0
        self._last_adjust_time: dict[str, float] = {}
        self._review_history: list[dict] = []
        self._trading_advisory: str = ""  # Step 3: 最新交易建议（供 Agent 3 注入 DeepSeek）
        self._lock = asyncio.Lock()
        self._running = False
        self._current_activity = ""
        self._last_activity_time = 0.0

        # 统计
        self._stats = {
            "total_reviews": 0,
            "total_adjustments": 0,
            "total_adjustment_errors": 0,
            "start_time": "",
            "last_review_time": "",
            "last_review_summary": "",
            "last_review_market_regime": "",
            "review_history": [],
        }

        # Step 4: 从 SQLite 恢复持久化交易计数
        self._init_persistent_state()

    # ── 主入口 ──

    async def notify_trade(self, trade_record: dict) -> None:
        """Agent 3 完成一笔交易后调用，触发计数检查

        Args:
            trade_record: SQLite trades 表的行字典
        """
        self._trade_count += 1

        # Step 4: 每次交易后持久化计数（防止重启后丢失）
        self._save_persistent_state()

        interval = self._config.agent4_review_interval_trades
        remaining = interval - (self._trade_count - self._last_review_count)
        self._current_activity = f"📊 交易 #{self._trade_count}，还需 {remaining} 笔触发复盘"
        self._last_activity_time = time.time()
        if self._trade_count - self._last_review_count >= interval:
            logger.info(
                f"Agent 4: {self._trade_count} 笔交易已达复盘阈值 "
                f"({interval})，开始复盘..."
            )
            await self._run_review()

    async def run(self) -> None:
        """主循环（空循环，保持与 asyncio 任务体系兼容）"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        self._current_activity = "🟢 待机中（等待交易触发复盘）"
        self._last_activity_time = time.time()
        logger.info("Agent 4 已启动（由 notify_trade 驱动，无独立循环）")
        try:
            while self._running:
                await asyncio.sleep(10)  # 只做心跳，不轮询
        except asyncio.CancelledError:
            logger.info("Agent 4 已停止")

    async def stop(self):
        """停止 Agent 4"""
        self._running = False

    # ── 复盘流程 ──

    async def _run_review(self) -> None:
        """执行一次完整复盘"""
        async with self._lock:
            try:
                # 1. 采集数据
                self._current_activity = "📡 采集复盘数据…"
                self._last_activity_time = time.time()
                interval = self._config.agent4_review_interval_trades
                trades = self._load_recent_trades(interval)
                market = self._collect_market_context()
                signals = self._collect_signal_stats()
                news = self._collect_recent_news()
                onchain = self._collect_onchain_snapshot()
                prev_reviews = self._review_history[-3:]

                # 2. 构建 Prompt
                self._current_activity = "📝 构建复盘分析 Prompt"
                self._last_activity_time = time.time()
                prompt = self._build_review_prompt(
                    trades=trades,
                    market=market,
                    signals=signals,
                    news=news,
                    onchain=onchain,
                    prev_reviews=prev_reviews,
                )

                # 3. 调 DeepSeek（同步 API，包到线程池）
                self._current_activity = "🤔 DeepSeek 复盘分析中…"
                self._last_activity_time = time.time()
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, self._deepseek.analyze_review, prompt
                )

                # Step 3: 提取交易建议（供后续 Agent 3 注入 DeepSeek）
                self._trading_advisory = result.get("trading_advisory", "") or ""
                self._current_activity = (
                    f"💡 交易建议: {self._trading_advisory[:50]}"
                    if self._trading_advisory
                    else "💡 本次无新交易建议"
                )
                self._last_activity_time = time.time()

                # 4. 校验并应用参数调整
                self._current_activity = "🔍 校验并应用参数调整"
                self._last_activity_time = time.time()
                applied = []
                adjustments = result.get("param_adjustments", [])
                for adj in adjustments[: self._config.agent4_max_param_adjustments]:
                    if self._validate_adjustment(adj):
                        self._apply_adjustment(adj)
                        applied.append(adj)
                    else:
                        logger.warning(
                            f"Agent 4: 调整被拒绝 {adj.get('param')} → {adj.get('to')}: "
                            f"{adj.get('reason', '未知原因')}"
                        )

                # 5. 记录复盘
                review_record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trade_count": self._trade_count,
                    "summary": result.get("summary", ""),
                    "market_regime": result.get("market_regime", ""),
                    "strategy_insights": result.get("strategy_insights", ""),
                    "trading_advisory": self._trading_advisory,
                    "adjustments_proposed": len(adjustments),
                    "adjustments_applied": len(applied),
                    "adjustments": applied,
                }
                self._review_history.append(review_record)
                self._last_review_count = self._trade_count

                # Step 4: 持久化交易计数
                self._save_persistent_state()

                # 更新统计
                self._stats["total_reviews"] += 1
                self._stats["total_adjustments"] += len(applied)
                self._stats["last_review_time"] = review_record["timestamp"]
                self._stats["last_review_summary"] = result.get("summary", "")
                self._stats["last_review_market_regime"] = result.get(
                    "market_regime", ""
                )
                # 保留最近 20 条历史
                self._stats["review_history"] = (
                    self._review_history[-20:]
                )

                self._current_activity = f"✅ 复盘完成: {result.get('summary', '')[:60]}"
                self._last_activity_time = time.time()
                logger.info(
                    f"Agent 4: 复盘完成 — "
                    f"建议 {len(adjustments)} 条，应用 {len(applied)} 条 | "
                    f"{result.get('summary', '')[:80]}"
                )

            except Exception as e:
                self._stats["total_adjustment_errors"] += 1
                self._current_activity = f"⚠️ 复盘失败: {str(e)[:50]}"
                self._last_activity_time = time.time()
                logger.error(f"Agent 4 复盘失败: {e}", exc_info=True)

    # ── Step 3: 交易建议（供 Agent 3 注入 DeepSeek 上下文） ──

    def get_advisory(self) -> str:
        """返回最新交易建议字符串"""
        return self._trading_advisory or ""

    # ── Step 4: 持久化交易计数 ──

    def _init_persistent_state(self) -> None:
        """从 SQLite 恢复 trade_count / last_review_count（使用共享连接）"""
        try:
            conn = self._db.conn
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent4_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            cursor = conn.execute("SELECT key, value FROM agent4_state")
            for row in cursor.fetchall():
                if row["key"] == "trade_count":
                    self._trade_count = int(row["value"])
                elif row["key"] == "last_review_count":
                    self._last_review_count = int(row["value"])
            logger.info(
                f"Agent 4: 恢复持久化状态 → "
                f"trade_count={self._trade_count}, "
                f"last_review_count={self._last_review_count}"
            )
        except Exception as e:
            logger.debug(f"Agent 4: 持久化状态初始化失败（首次运行）: {e}")

    def _save_persistent_state(self) -> None:
        """保存 trade_count / last_review_count 到 SQLite（使用共享连接）"""
        try:
            with self._db.write_lock:
                conn = self._db.conn
                conn.execute(
                    "INSERT OR REPLACE INTO agent4_state (key, value) VALUES (?, ?)",
                    ("trade_count", str(self._trade_count)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO agent4_state (key, value) VALUES (?, ?)",
                    ("last_review_count", str(self._last_review_count)),
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"Agent 4: 持久化状态保存失败: {e}")

    # ── 数据采集 ──

    def _load_recent_trades(self, n: int = 5) -> list[dict]:
        """从 SQLite 加载最近 N 笔已完成交易（使用共享连接）"""
        try:
            conn = self._db.conn
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM trades WHERE trade_type='close' ORDER BY id DESC LIMIT ?", (n,)
            )
            rows = [dict(row) for row in cursor.fetchall()]
            return rows
        except Exception as e:
            logger.debug(f"加载交易记录失败: {e}")
            return []

    def _collect_market_context(self) -> dict:
        """采集 KlineBuilder 行情数据（3m/5m/15m/1h/1d 最新 K 线）"""
        context: dict[str, Any] = {}
        for tf in ["3m", "5m", "15m", "1h"]:
            history = self._kline_builder.get_history(tf, 5)
            if history:
                prices = [b["close"] for b in history if "close" in b]
                context[tf] = {
                    "high": max(prices) if prices else 0,
                    "low": min(prices) if prices else 0,
                    "last_close": prices[-1] if prices else 0,
                    "count": len(history),
                }
        return context

    def _collect_signal_stats(self) -> dict:
        """采集 Agent 1 信号统计"""
        if self._agent1 and hasattr(self._agent1, "get_recent_signal_stats"):
            try:
                return self._agent1.get_recent_signal_stats()
            except Exception as e:
                logger.debug(f"采集 Agent 1 信号统计失败: {e}")
        return {"total_signals": 0}

    def _collect_recent_news(self) -> list[dict]:
        """采集最近新闻"""
        if self._agent2 and hasattr(self._agent2, "get_recent_news"):
            try:
                return self._agent2.get_recent_news(10)  # type: ignore[return-value]
            except Exception as e:
                logger.debug(f"采集 Agent 2 新闻失败: {e}")
        return []

    def _collect_onchain_snapshot(self) -> dict:
        """采集链上数据快照"""
        if self._agent2 and hasattr(self._agent2, "get_status"):
            try:
                s = self._agent2.get_status()
                onchain = s.get("onchain", {}) if isinstance(s, dict) else {}
                return {
                    "last_gas_gwei": onchain.get("last_gas_gwei", 0),
                    "last_taker_buy_ratio": onchain.get("last_taker_buy_ratio", 0),
                    "last_funding_rate": onchain.get("last_funding_rate", 0),
                    "last_whale_count": onchain.get("last_whale_count", 0),
                }
            except Exception as e:
                logger.debug(f"采集链上数据快照失败: {e}")
        return {}

    # ── Prompt 构建 ──

    def _build_review_prompt(
        self,
        trades: list[dict],
        market: dict,
        signals: dict,
        news: list,
        onchain: dict,
        prev_reviews: list[dict],
    ) -> str:
        """构建完整的 DeepSeek 复盘 Prompt"""
        # 交易记录
        trade_lines = []
        for i, t in enumerate(trades, 1):
            side = t.get("side", "?")
            pnl = t.get("pnl_close", t.get("pnl", 0))
            price = t.get("price", 0)
            confidence = t.get("confidence", 0)
            size_pct = t.get("position_size_pct", 0)
            decision_raw = t.get("decision", "{}")
            if isinstance(decision_raw, str):
                try:
                    decision_raw = json.loads(decision_raw)
                except (json.JSONDecodeError, TypeError):
                    decision_raw = {}
            reason = decision_raw.get("reason", "") if isinstance(decision_raw, dict) else ""
            ts = t.get("timestamp", "")[:19] if t.get("timestamp") else ""
            trade_lines.append(
                f"{i} | {ts} | {side} | ${price} | {pnl:+.2f} USDT"
                f" | 信心={confidence}% 仓位={size_pct}% | {reason}"
            )

        # 行情摘要
        market_lines = []
        for tf, data in market.items():
            market_lines.append(
                f"  {tf}: 区间 ${data['low']}-${data['high']}, "
                f"最新 ${data['last_close']}, {data['count']} 根 K 线"
            )

        # 信号统计
        signal_lines = []
        if signals.get("total_signals", 0) > 0:
            signal_lines.append(f"  总信号: {signals['total_signals']}")
            for tf, cnt in signals.get("by_timeframe", {}).items():
                signal_lines.append(f"  {tf}: {cnt} 个")
            for direction, cnt in signals.get("by_direction", {}).items():
                signal_lines.append(f"  {direction}: {cnt} 个")

        # 新闻摘要
        news_lines = [str(n)[:100] for n in news[-5:]]

        # 参数快照
        param_lines = [
            f"  agent3_max_daily_trades={self._config.agent3_max_daily_trades}",
            f"  agent3_debounce_seconds={self._config.agent3_debounce_seconds}",
            f"  agent3_min_interval_between_trades={self._config.agent3_min_interval_between_trades}s",
            f"  agent3_max_daily_loss_usdt={self._config.agent3_max_daily_loss_usdt}",
            f"  agent3_max_consecutive_losses={self._config.agent3_max_consecutive_losses}",
            f"  agent3_max_position_eth={self._config.agent3_max_position_eth}",
            f"  agent3_position_size_multiplier={self._config.agent3_position_size_multiplier}",
            f"  agent3_default_stop_loss_pct={self._config.agent3_default_stop_loss_pct}%",
            f"  agent3_default_take_profit_pct={self._config.agent3_default_take_profit_pct}%",
            f"  agent1_change_cooldown={self._config.agent1_change_cooldown}s",
        ]

        # 历史复盘
        prev_lines = []
        for r in prev_reviews[-3:]:
            ts = r.get("timestamp", "")[:19]
            summary = r.get("summary", "")[:80]
            applied = r.get("adjustments", [])
            params_changed = ", ".join(
                a.get("param", "?") for a in applied
            )
            prev_lines.append(
                f"  {ts}: {summary} — 调整了: {params_changed or '无'}"
            )

        return _REVIEW_PROMPT_TEMPLATE.format(
            count=len(trades),
            recent_trades="\n".join(trade_lines) or "  无交易记录",
            market_context="\n".join(market_lines) or "  无行情数据",
            signal_stats="\n".join(signal_lines) or "  无信号数据",
            news_summary="\n".join(news_lines) or "  无新闻",
            onchain_snapshot=(
                f"  Gas: {onchain.get('last_gas_gwei', '—')} Gwei\n"
                f"  吃单比(买): {onchain.get('last_taker_buy_ratio', '—')}\n"
                f"  资金费率: {onchain.get('last_funding_rate', '—')}%\n"
                f"  巨鲸转账: {onchain.get('last_whale_count', 0)} 笔"
            ),
            current_params="\n".join(param_lines),
            prev_reviews="\n".join(prev_lines) or "  无历史复盘",
        )

    # ── 校验与应用 ──

    def _validate_adjustment(self, adj: dict) -> bool:
        """边界校验 + 防抖

        Args:
            adj: {"target": str, "param": str, "from": float, "to": float, "reason": str}

        Returns:
            True 如果校验通过可以应用
        """
        param = adj.get("param", "")
        value = adj.get("to")
        reason = adj.get("reason", "")

        # 1. 参数名是否在安全边界表中
        bounds = _PARAM_BOUNDS.get(param)
        if bounds is None:
            logger.warning(f"Agent 4: 未知参数 '{param}'，拒绝")
            return False

        # 2. 值必须是数字
        if not isinstance(value, (int, float)):
            logger.warning(f"Agent 4: 参数 '{param}' 值类型错误: {type(value).__name__}")
            return False

        # 3. 值在安全范围内
        low, high = bounds
        if value < low or value > high:
            logger.warning(
                f"Agent 4: 参数 '{param}' 值 {value} 超出安全范围 [{low}, {high}]"
            )
            return False

        # 4. 风险参数只收窄不放宽
        if param in _RISK_PARAMS:
            current = getattr(self._config, param, None)
            if current is not None and value > current:
                logger.warning(
                    f"Agent 4: 风险参数 '{param}' 只能降低 ({current}→{value})，拒绝"
                )
                return False

        # 5. 防抖：同一参数不能频繁修改
        now = datetime.now(timezone.utc).timestamp()
        last = self._last_adjust_time.get(param, 0.0)
        min_interval = self._config.agent4_min_adjust_interval_seconds
        if now - last < min_interval:
            logger.debug(
                f"Agent 4: 参数 '{param}' 上次修改在 {now - last:.0f}s 前，"
                f"低于最小间隔 {min_interval}s，跳过"
            )
            return False

        # 6. 检查是否真的有变化
        current = getattr(self._config, param, None)
        if current is not None and abs(float(value) - float(current)) < 0.001:
            logger.debug(f"Agent 4: 参数 '{param}' 未变化 ({current})，跳过")
            return False

        self._last_adjust_time[param] = now
        return True

    def _apply_adjustment(self, adj: dict) -> None:
        """写入共享 config（锁保护）"""
        param = adj["param"]
        value = adj["to"]
        reason = adj.get("reason", "")
        old = getattr(self._config, param, None)

        setattr(self._config, param, value)

        logger.info(
            f"⚙ Agent 4 调整: {param}: {old} → {value} ({reason})"
        )

    # ── 状态 ──

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "current_activity": self._current_activity,
            "last_activity_time": self._last_activity_time,
            "trade_count": self._trade_count,
            "last_review_count": self._last_review_count,
            "next_review_in": max(
                0,
                self._config.agent4_review_interval_trades
                - (self._trade_count - self._last_review_count),
            ),
            **self._stats,
        }
