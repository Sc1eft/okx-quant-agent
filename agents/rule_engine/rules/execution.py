"""
执行中保护规则（Execution Rules）

在 DeepSeek 返回交易决策后执行，检查市场环境是否允许执行。
包含市场深度检查、滑点保护等。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agents.rule_engine.base import Rule, RuleResult, RuleCategory, Ctx

logger = logging.getLogger("rule_engine.execution")


class MarketDepthRule(Rule):
    """市场深度检查（Phase 2）

    检查买卖盘口深度是否足以完成交易，以及买卖价差是否过大。
    需要 context 提供 "okx_client" 实例。
    """

    name = "market_depth"
    description = "市场深度和买卖价差检查"
    category = RuleCategory.EXECUTION
    priority = 70

    @property
    def is_async(self) -> bool:
        return True

    async def check_async(self, context: dict) -> RuleResult:
        okx_client = context.get("okx_client")
        if not okx_client:
            return self._ok("无 OKX 客户端，跳过深度检查")

        side = context.get(Ctx.SIDE, "")
        size = context.get(Ctx.SIZE, 0.0)
        symbol = context.get(Ctx.SYMBOL, "ETH-USDT")
        spread_bps_limit = context.get("market_depth_spread_bps", 10.0)

        if size <= 0:
            return self._ok("仓位为零，无需深度检查")

        try:
            import asyncio
            order_book = await asyncio.to_thread(
                okx_client.get_order_book, symbol, depth=5
            )
        except Exception as e:
            logger.warning("市场深度检查失败: %s", e)
            # 失败时保守返回：检查不通过，但建议走限价单
            return RuleResult(
                rule_name=self.name,
                passed=True,
                reason="深度检查跳过（API 异常），强制限价单",
                severity="warning",
                data={"prefer_limit": True},
            )

        asks = order_book.get("asks", [])
        bids = order_book.get("bids", [])

        if not asks or not bids:
            return RuleResult(
                rule_name=self.name,
                passed=True,
                reason="深度数据为空，强制限价单",
                severity="warning",
                data={"prefer_limit": True},
            )

        best_ask = float(asks[0][0])
        best_bid = float(bids[0][0])
        mid_price = (best_ask + best_bid) / 2

        if mid_price <= 0:
            return RuleResult(
                rule_name=self.name,
                passed=True,
                reason="中间价异常，强制限价单",
                severity="warning",
                data={"prefer_limit": True},
            )

        # 计算价差（基点）
        spread_bps = (best_ask - best_bid) / mid_price * 10000

        # 计算可用深度
        if side == "buy":
            available_depth = sum(
                float(ask[1]) for ask in asks
                if float(ask[0]) <= best_ask * 1.005
            )
        else:
            available_depth = sum(
                float(bid[1]) for bid in bids
                if float(bid[0]) >= best_bid * 0.995
            )

        # 深度不足
        if available_depth < size:
            return self._reject(
                f"{'卖方' if side == 'buy' else '买方'}深度不足: "
                f"可用 {available_depth:.4f} < 需求 {size} ETH",
                data={"prefer_limit": True},
            )

        # 价差过大 → 强制限价单
        if spread_bps > spread_bps_limit:
            return RuleResult(
                rule_name=self.name,
                passed=True,
                reason=f"价差 {spread_bps:.1f}bps 超过 {spread_bps_limit}bps，走限价单",
                severity="warning",
                data={"prefer_limit": True},
            )

        return self._ok(
            f"深度充足: {available_depth:.4f} ETH, 价差 {spread_bps:.1f}bps",
            data={"prefer_limit": False},
        )


class SlippageRule(Rule):
    """滑点保护规则

    检查 DeepSeek 返回的 entry_price 与当前价格的偏差是否在可接受范围内。
    配置: max_slippage_pct (默认 0.3%)

    适用于执行前二次验证，防止信号生成到执行期间价格大幅变动。
    """

    name = "slippage"
    description = "滑点检查：信号价格与实际价格偏差不超过阈值"
    category = RuleCategory.EXECUTION
    priority = 71

    def check(self, context: dict) -> RuleResult:
        current_price = context.get(Ctx.PRICE, 0.0)
        signal_price = context.get("signal_price", 0.0)
        entry_min = context.get("entry_price_min", 0.0)
        entry_max = context.get("entry_price_max", 0.0)
        max_slippage = context.get("max_slippage_pct", 0.3)

        if current_price <= 0:
            return self._ok("当前价格未知，跳过滑点检查")

        # 优先使用 DeepSeek 建议的价格范围
        if entry_min and entry_max:
            if entry_min <= current_price <= entry_max:
                return self._ok(f"当前价格在入场区间内 (${entry_min}~${entry_max})")
            # 计算偏离幅度
            mid = (entry_min + entry_max) / 2
            deviation = abs(current_price - mid) / mid * 100
            if deviation > max_slippage:
                return self._reject(
                    f"滑点 {deviation:.2f}% > {max_slippage}%: "
                    f"入场区间 ${entry_min}~${entry_max}, 现价 ${current_price}"
                )
            return RuleResult(
                rule_name=self.name,
                passed=True,
                reason=f"价格偏离 {deviation:.2f}%，在滑点容忍范围内",
                severity="warning",
            )

        # 用 DeepSeek 的信号价格（无区间时）
        if signal_price > 0:
            deviation = abs(current_price - signal_price) / signal_price * 100
            if deviation > max_slippage:
                return self._reject(
                    f"滑点 {deviation:.2f}% > {max_slippage}%: "
                    f"信号价 ${signal_price:.2f}, 现价 ${current_price:.2f}"
                )
            return self._ok(f"滑点 {deviation:.2f}%，在容忍范围内")

        return self._ok("无参考价格，跳过滑点检查")


# ════════════════════════════════════════════════════════════════
# 规则工厂
# ════════════════════════════════════════════════════════════════

def create_execution_rules(config=None) -> list[Rule]:
    """创建所有执行中保护规则实例"""
    rules: list[Rule] = [
        MarketDepthRule(config),
        SlippageRule(config),
    ]
    rules.sort(key=lambda r: r.priority)
    return rules
