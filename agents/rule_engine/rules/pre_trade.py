"""
交易前检查规则（Pre-Trade Rules）

在 DeepSeek API 调用前执行，快速拦截明显不合规的交易请求。
所有规则继承 Rule 基类，保持无状态设计。

对照现有 RiskManager._check_common_pre() + check_layer1() 拆分。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from agents.rule_engine.base import Rule, RuleResult, RuleCategory, Ctx

logger = logging.getLogger("rule_engine.pre_trade")


# ════════════════════════════════════════════════════════════════
# 通用检查规则（方向无关，DeepSeek 前拦截）
# ════════════════════════════════════════════════════════════════


class APIBreakerRule(Rule):
    """API 熔断检查

    连续 API 错误次数超限后，熔断一段时间，避免对不稳定交易所重复下单。
    配置: api_breaker_until 时间戳
    """

    name = "api_breaker"
    description = "API 熔断：连续错误后暂停交易"
    category = RuleCategory.PRE_TRADE
    priority = 10  # 最高优先级

    def check(self, context: dict) -> RuleResult:
        breaker_until = context.get(Ctx.API_BREAKER_UNTIL)
        if not breaker_until:
            return self._ok("API 熔断未激活")

        now = context.get(Ctx.TIME, datetime.now(timezone.utc))
        if now < breaker_until:
            remaining = (breaker_until - now).total_seconds()
            return self._reject(
                f"API 熔断中，剩余 {remaining:.0f}s"
            )

        return self._ok("API 熔断已解除")


class TradeIntervalRule(Rule):
    """最小交易间隔检查

    防止短时间内频繁交易（信号翻转保护）。
    配置: agent3_min_interval_between_trades (默认 600s = 10min)
    """

    name = "trade_interval"
    description = "最小交易间隔：距上次交易 N 秒内不可重复交易"
    category = RuleCategory.PRE_TRADE
    priority = 20

    def check(self, context: dict) -> RuleResult:
        last_time = context.get(Ctx.LAST_TRADE_TIME)
        if not last_time:
            return self._ok("无上次交易记录")

        min_interval = context.get(Ctx.MIN_TRADE_INTERVAL, 600)
        now = context.get(Ctx.TIME, datetime.now(timezone.utc))
        elapsed = (now - last_time).total_seconds()

        if elapsed < min_interval:
            remaining = int(min_interval - elapsed)
            return self._reject(
                f"交易间隔未到，还需 {remaining}s（最小间隔 {min_interval}s）"
            )

        return self._ok(f"距上次交易 {elapsed:.0f}s，满足间隔要求")


class DailyTradeLimitRule(Rule):
    """每日交易次数上限检查

    按北京时间午夜重置。
    配置: agent3_max_daily_trades (默认 20)
    """

    name = "daily_trade_limit"
    description = "每日交易次数上限"
    category = RuleCategory.PRE_TRADE
    priority = 30

    def check(self, context: dict) -> RuleResult:
        current = context.get(Ctx.DAILY_TRADE_COUNT, 0)
        maximum = context.get(Ctx.MAX_DAILY_TRADES, 20)

        if current >= maximum:
            return self._reject(
                f"今日交易已达上限 ({current}/{maximum} 次)"
            )

        return self._ok(f"今日交易 {current}/{maximum} 次")


class DailyLossLimitRule(Rule):
    """每日亏损上限检查

    超过该上限后当天不再交易，风控优先。
    配置: agent3_max_daily_loss_usdt (默认 100 USDT)
    """

    name = "daily_loss_limit"
    description = "每日亏损上限保护"
    category = RuleCategory.PRE_TRADE
    priority = 30

    def check(self, context: dict) -> RuleResult:
        current_loss = context.get(Ctx.DAILY_LOSS_USDT, 0.0)
        max_loss = context.get(Ctx.MAX_DAILY_LOSS_USDT, 100.0)

        if current_loss >= max_loss:
            return self._reject(
                f"今日亏损已达上限 ({current_loss:.2f}/{max_loss} USDT)"
            )

        return self._ok(f"今日亏损 {current_loss:.2f}/{max_loss} USDT")


class ConsecutiveLossRule(Rule):
    """连续亏损检查

    连续亏损 N 笔后暂停交易，防止情绪化交易。
    配置: agent3_max_consecutive_losses (默认 3)
    """

    name = "consecutive_loss"
    description = "连续亏损超过阈值后暂停交易"
    category = RuleCategory.PRE_TRADE
    priority = 40

    def check(self, context: dict) -> RuleResult:
        consec = context.get(Ctx.CONSECUTIVE_LOSSES, 0)
        maximum = context.get(Ctx.MAX_CONSECUTIVE_LOSSES, 3)

        if consec >= maximum:
            return self._reject(
                f"连续亏损 {consec} 次（上限 {maximum}），交易暂停"
            )

        return self._ok(
            f"连续亏损 {consec}/{maximum} 次"
        ) if consec > 0 else self._ok("无连续亏损")


class HFTProtectionRule(Rule):
    """HFT 防护 — 每小时交易频率上限

    通过查询 SQLite 检查过去一小时的交易笔数。
    需要 context 提供 "db_conn"（sqlite3 Connection）或由子类注入。
    无 DB 连接时不拦截（保守放行）。
    """

    name = "hft_protection"
    description = "每小时交易频率上限检查"
    category = RuleCategory.PRE_TRADE
    priority = 50

    def check(self, context: dict) -> RuleResult:
        db_conn = context.get("db_conn")
        max_per_hour = context.get(Ctx.MAX_TRADES_PER_HOUR, 4)

        if not db_conn:
            logger.warning("HFT 防护：无 DB 连接，保守放行")
            return self._ok("DB 不可用，跳过频率检查")

        try:
            now = context.get(Ctx.TIME, datetime.now(timezone.utc))
            hour_ago = (now - timedelta(hours=1)).isoformat()
            cur = db_conn.execute(
                "SELECT COUNT(*) FROM trades WHERE timestamp >= ? AND trade_type = 'open'",
                (hour_ago,)
            )
            count = cur.fetchone()[0] or 0

            if count >= max_per_hour:
                return self._reject(
                    f"过去 1 小时交易 {count} 笔，超过上限 {max_per_hour}"
                )

            return self._ok(f"过去 1 小时交易 {count}/{max_per_hour} 笔")

        except Exception as e:
            logger.warning("HFT 检查异常，保守拦截: %s", e)
            return self._reject(f"HFT 检查异常: {e}")


# ════════════════════════════════════════════════════════════════
# 方向相关检查规则（DeepSeek 决策后，执行前）
# ════════════════════════════════════════════════════════════════


class PositionSizeRule(Rule):
    """单笔上限检查

    DeepSeek 给出的建议仓位不得超过配置的单笔最大 ETH。
    配置: agent3_max_position_eth (默认 0.5 ETH)
    """

    name = "position_size"
    description = "单笔仓位大小不超过配置上限"
    category = RuleCategory.EXECUTION
    priority = 60

    def check(self, context: dict) -> RuleResult:
        size = context.get(Ctx.SIZE, 0.0)
        max_pos = context.get(Ctx.MAX_POSITION_ETH, 0.5)

        if size > max_pos:
            return self._reject(
                f"单笔 {size:.4f} ETH 超过上限 {max_pos} ETH"
            )

        return self._ok(f"仓位 {size:.4f} ETH 未超限 (上限 {max_pos} ETH)")


class DirectionConflictRule(Rule):
    """方向冲突检查

    已有同方向持仓时，累加后不得超过上限。
    防止在同一个方向过度集中。
    """

    name = "direction_conflict"
    description = "同方向累加仓位不超过上限"
    category = RuleCategory.EXECUTION
    priority = 61

    def check(self, context: dict) -> RuleResult:
        side = context.get(Ctx.SIDE, "")
        size = context.get(Ctx.SIZE, 0.0)
        current_side = context.get(Ctx.POSITION_SIDE)
        current_size = context.get(Ctx.POSITION_SIZE, 0.0)
        max_pos = context.get(Ctx.MAX_POSITION_ETH, 0.5)

        direction = "long" if side == "buy" else "short"

        if current_side == direction:
            new_total = current_size + size
            if new_total > max_pos:
                return self._reject(
                    f"方向冲突：同方向累加 {new_total:.4f} ETH 超过上限 {max_pos} ETH"
                )

        return self._ok("方向冲突检查通过")


# ════════════════════════════════════════════════════════════════
# 异步检查规则（需调用外部 API）
# ════════════════════════════════════════════════════════════════


class VolatilityCheckRule(Rule):
    """价格波动检查（Phase 2）

    检查交易品种 15m K 线波动率，超过阈值则延迟交易 N 秒。
    需要 context 提供 "okx_client" 实例用于获取 K 线。
    交易品种通过 Ctx.SYMBOL 指定（默认 ETH-USDT）。
    """

    name = "volatility_check"
    description = "15m 波动率检查，超阈值延迟交易"
    category = RuleCategory.PRE_TRADE
    priority = 15
    is_async_rule = True  # 标记为异步规则

    @property
    def is_async(self) -> bool:
        return True

    async def check_async(self, context: dict) -> RuleResult:
        okx_client = context.get("okx_client")
        if not okx_client:
            return self._ok("无 OKX 客户端，跳过波动检查")

        symbol = context.get(Ctx.SYMBOL, "ETH-USDT")

        # 先查延迟状态
        delay_until = context.get("volatility_delay_until")
        now = context.get(Ctx.TIME, datetime.now(timezone.utc))
        if delay_until and now < delay_until:
            remaining = (delay_until - now).total_seconds()
            return self._reject(
                f"波动延迟中，剩余 {remaining:.0f}s"
            )

        threshold = context.get("volatility_threshold_pct", 3.0)

        # 异步获取 K 线
        try:
            import asyncio
            klines = await asyncio.to_thread(
                okx_client.get_klines, symbol, "15m", 2
            )
        except Exception as e:
            logger.warning("波动检查失败（API 异常）: %s", e)
            return self._ok("波动检查跳过（API 异常）")

        if len(klines) < 2:
            return self._ok("K 线数据不足，跳过波动检查")

        prev_close = klines[0]["close"] if isinstance(klines[0], dict) else float(klines[0][4])
        curr_close = klines[1]["close"] if isinstance(klines[1], dict) else float(klines[1][4])

        if prev_close <= 0:
            return self._ok("价格数据无效")

        change_pct = abs(curr_close - prev_close) / prev_close * 100
        if change_pct > threshold:
            delay_seconds = context.get("volatility_delay_seconds", 300)
            logger.warning(
                "%s 15m 波动 %.1f%% > %.1f%%，延迟 %ds",
                symbol, change_pct, threshold, delay_seconds,
            )
            return self._reject(
                f"{symbol} 15m 波动 {change_pct:.1f}% > {threshold}%，"
                f"延迟 {delay_seconds}s",
                data={"delay_seconds": delay_seconds},
            )

        return self._ok(f"{symbol} 波动 {change_pct:.1f}%，在阈值 {threshold}% 内")


# ════════════════════════════════════════════════════════════════
# 规则工厂：从配置批量创建规则实例
# ════════════════════════════════════════════════════════════════

def create_pre_trade_rules(config=None) -> list[Rule]:
    """创建所有交易前检查规则实例

    Args:
        config: AgentSystemConfig 实例（用于读取默认值）

    Returns:
        按 priority 排序的规则列表
    """
    rules: list[Rule] = [
        APIBreakerRule(config),
        TradeIntervalRule(config),
        DailyTradeLimitRule(config),
        DailyLossLimitRule(config),
        ConsecutiveLossRule(config),
        HFTProtectionRule(config),
        VolatilityCheckRule(config),
    ]

    # 按优先级排序
    rules.sort(key=lambda r: r.priority)
    return rules
