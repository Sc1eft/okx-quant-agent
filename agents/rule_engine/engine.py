"""
RuleEngine — 可插拔规则引擎主模块

职责:
  1. 管理规则注册表（增删改查）
  2. 按阶段（PRE_TRADE → EXECUTION → POST_TRADE）调度规则
  3. 自动区分 sync / async 规则并正确执行
  4. 聚合结果，提供阻断决策
  5. 从 AgentSystemConfig 批量加载规则

与 RiskManager 的关系:
  RuleEngine 专注于「检查」逻辑，RiskManager 继续持有「状态」
  （daily_trade_count, consecutive_losses 等）。
  Agent 3 在决策周期中按顺序调用:
    1. rule_engine.check_pre_trade(context)    # DeepSeek 前
    2. deepseek.analyze(context)               # AI 决策
    3. rule_engine.check_execution(context)    # 执行前
    4. rule_engine.check_post_trade(context)   # 执行后

使用方式:
    engine = RuleEngine()
    engine.register(DailyTradeLimitRule(config))
    engine.register(VolatilityCheckRule(config))
    engine.load_defaults(config)  # 或批量注册

    context = {"daily_trade_count": 5, "max_daily_trades": 20}
    results = await engine.check_all(context)
    if engine.all_pass(results):
        execute_trade()
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from agents.rule_engine.base import Rule, RuleResult, RuleCategory
from agents.rule_engine.rules.pre_trade import create_pre_trade_rules
from agents.rule_engine.rules.execution import create_execution_rules

logger = logging.getLogger("rule_engine.engine")


class RuleEngine:
    """规则引擎 — 规则注册、调度、结果聚合

    Attributes:
        rules: list[Rule] — 所有已注册规则（按 priority 排序）
    """

    def __init__(self):
        self._rules: list[Rule] = []
        self._rule_map: dict[str, Rule] = {}  # name → Rule

    # ── 规则注册 ──

    def register(self, rule: Rule) -> None:
        """注册一条规则

        如果已存在同名规则，会被替换。
        注册后规则列表按 priority 重新排序。
        """
        self._rule_map[rule.name] = rule
        # 重建有序列表
        self._rules = sorted(self._rule_map.values(), key=lambda r: r.priority)
        logger.debug("Rule registered: %s (priority=%d)", rule.name, rule.priority)

    def register_many(self, rules: list[Rule]) -> None:
        """批量注册规则"""
        for rule in rules:
            self._rule_map[rule.name] = rule
        self._rules = sorted(self._rule_map.values(), key=lambda r: r.priority)
        logger.debug("Batch registered %d rules", len(rules))

    def unregister(self, name: str) -> bool:
        """注销一条规则

        Args:
            name: 规则名称

        Returns:
            True 如果找到并移除
        """
        if name in self._rule_map:
            del self._rule_map[name]
            self._rules = sorted(self._rule_map.values(), key=lambda r: r.priority)
            logger.debug("Rule unregistered: %s", name)
            return True
        return False

    # ── 规则查询 ──

    def get_rule(self, name: str) -> Optional[Rule]:
        """按名称查找规则"""
        return self._rule_map.get(name)

    def get_rules(self, category: Optional[RuleCategory] = None) -> list[Rule]:
        """获取规则列表，可按分类过滤"""
        if category is None:
            return list(self._rules)
        return [r for r in self._rules if r.category == category]

    def get_enabled_rules(self, category: Optional[RuleCategory] = None) -> list[Rule]:
        """获取已启用的规则"""
        rules = self.get_rules(category)
        return [r for r in rules if r.enabled]

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def load_defaults(self, config=None) -> None:
        """从工厂函数加载默认规则集

        Args:
            config: AgentSystemConfig 实例（可选）
        """
        rules: list[Rule] = []
        rules.extend(create_pre_trade_rules(config))
        rules.extend(create_execution_rules(config))
        self.register_many(rules)
        logger.info("Loaded %d default rules", len(rules))

    # ── 规则执行 ──

    async def check_pre_trade(self, context: dict) -> list[RuleResult]:
        """执行所有交易前检查规则

        PRE_TRADE 分类的规则在 DeepSeek 调用前执行。
        任一规则返回 severity="error" 的拒绝，交易应终止。

        Args:
            context: 全局上下文 dict

        Returns:
            执行结果列表（按 priority 排序）
        """
        return await self._run_category(RuleCategory.PRE_TRADE, context)

    async def check_execution(self, context: dict) -> list[RuleResult]:
        """执行所有执行中保护规则

        EXECUTION 分类的规则在 DeepSeek 决策后执行。
        """
        return await self._run_category(RuleCategory.EXECUTION, context)

    async def check_all(self, context: dict) -> dict[str, list[RuleResult]]:
        """按顺序执行所有阶段的规则

        Returns:
            {"pre_trade": [...], "execution": [...], "post_trade": [...]}
        """
        return {
            "pre_trade": await self.check_pre_trade(context),
            "execution": await self.check_execution(context),
        }

    async def _run_category(self, category: RuleCategory,
                            context: dict) -> list[RuleResult]:
        """运行一个分类下的所有规则

        Args:
            category: 规则分类
            context:  上下文

        Returns:
            执行结果列表
        """
        rules = self.get_enabled_rules(category)
        if not rules:
            return []

        # 确保 context 有时钟
        if Ctx.TIME not in context:
            context[Ctx.TIME] = datetime.now(timezone.utc)

        results: list[RuleResult] = []
        for rule in rules:
            try:
                if rule.is_async:
                    result = await rule.check_async(context)
                else:
                    result = rule.check(context)
            except Exception as e:
                logger.exception(
                    "Rule '%s' threw exception: %s", rule.name, e
                )
                result = RuleResult(
                    rule_name=rule.name,
                    passed=False,
                    reason=f"规则执行异常: {e}",
                    severity="error",
                )

            results.append(result)

            # 阻断逻辑：仅在 PRE_TRADE 和 EXECUTION 阶段阻断
            if not result.passed and result.severity == "error" \
                    and category in (RuleCategory.PRE_TRADE, RuleCategory.EXECUTION):
                logger.info(
                    "Rule '%s' blocked: %s", rule.name, result.reason
                )
                break  # 遇到第一个 error 级别拒绝就停止，节省后续规则调用

        return results

    # ── 结果判断 ──

    @staticmethod
    def all_pass(results: list[RuleResult]) -> bool:
        """所有规则都通过？"""
        return all(r.passed for r in results)

    @staticmethod
    def blocked_by(results: list[RuleResult]) -> Optional[str]:
        """如果被阻断，返回第一个阻断规则的名称"""
        for r in results:
            if not r.passed and r.severity == "error":
                return r.rule_name
        return None

    @staticmethod
    def get_warnings(results: list[RuleResult]) -> list[RuleResult]:
        """获取所有警告（passed=True 但 severity=warning）"""
        return [r for r in results if r.passed and r.severity == "warning"]

    @staticmethod
    def get_errors(results: list[RuleResult]) -> list[RuleResult]:
        """获取所有错误（passed=False）"""
        return [r for r in results if not r.passed]

    # ── 规则控制 ──

    def enable_rule(self, name: str) -> bool:
        """启用一条规则"""
        rule = self._rule_map.get(name)
        if rule:
            rule.enabled = True
            return True
        return False

    def disable_rule(self, name: str) -> bool:
        """禁用一条规则"""
        rule = self._rule_map.get(name)
        if rule:
            rule.enabled = False
            return True
        return False

    def set_rule_enabled(self, name: str, enabled: bool) -> bool:
        """设置规则启用状态"""
        if enabled:
            return self.enable_rule(name)
        return self.disable_rule(name)

    # ── 状态查询 ──

    def get_status(self) -> dict:
        """返回引擎状态摘要"""
        return {
            "total_rules": self.rule_count,
            "enabled_rules": sum(1 for r in self._rules if r.enabled),
            "rules": [
                {
                    "name": r.name,
                    "category": r.category.value,
                    "enabled": r.enabled,
                    "priority": r.priority,
                    "stats": r.stats,
                }
                for r in self._rules
            ],
        }

    def reset_all_stats(self):
        """重置所有规则的统计计数"""
        for rule in self._rules:
            rule.reset_stats()


# 引用 Ctx 以在 engine 中使用
from agents.rule_engine.base import Ctx
