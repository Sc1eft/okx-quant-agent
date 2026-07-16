# RuleEngine — 可插拔规则引擎
#
# 将 RiskManager 中的硬编码检查提取为独立 Rule，实现:
#   - 规则可独立启用/禁用
#   - 规则按优先级排序执行
#   - 规则可分类（交易前/执行中/交易后）
#   - 新增规则无需修改引擎
#
# 使用方式:
#   engine = RuleEngine(config)
#   engine.register(DailyTradeLimitRule())
#   results = engine.check_pre_trade(context)
#   all_ok = all(r.passed for r in results)

from agents.rule_engine.engine import RuleEngine
from agents.rule_engine.base import Rule, RuleResult, RuleCategory

from agents.rule_engine.rules.pre_trade import (
    APIBreakerRule,
    DailyTradeLimitRule,
    DailyLossLimitRule,
    ConsecutiveLossRule,
    TradeIntervalRule,
    HFTProtectionRule,
    PositionSizeRule,
    DirectionConflictRule,
    VolatilityCheckRule,
)
from agents.rule_engine.rules.execution import (
    MarketDepthRule,
    SlippageRule,
)

__all__ = [
    "RuleEngine",
    "Rule",
    "RuleResult",
    "RuleCategory",
    # Pre-Trade Rules
    "APIBreakerRule",
    "DailyTradeLimitRule",
    "DailyLossLimitRule",
    "ConsecutiveLossRule",
    "TradeIntervalRule",
    "HFTProtectionRule",
    "PositionSizeRule",
    "DirectionConflictRule",
    "VolatilityCheckRule",
    # Execution Rules
    "MarketDepthRule",
    "SlippageRule",
]
