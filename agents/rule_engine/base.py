"""
RuleEngine 基础类型 — Rule, RuleResult, RuleCategory

设计原则:
  - Rule 是单次检查的原子单元，一个 Rule 只检查一件事
  - Rule 通过 context dict 获取外部状态，保持自身无状态（可复用）
  - RuleResult 携带通过/拒绝 + 原因，引擎层聚合决策
  - RuleCategory 决定规则在交易周期中的执行阶段
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("rule_engine")


class RuleCategory(str, Enum):
    """规则分类 — 决定规则在交易周期中的执行阶段

    PRE_TRADE:    交易前检查（DeepSeek 调用前），快速拦截不满足条件的交易
    EXECUTION:    执行中保护（DeepSeek 调用后），检查方向/数量/深度
    POST_TRADE:   交易后记录，记录结果到数据库/更新风控状态
    RISK_MONITOR: 持续风控监控，独立于交易周期运行
    """
    PRE_TRADE = "pre_trade"
    EXECUTION = "execution"
    POST_TRADE = "post_trade"
    RISK_MONITOR = "risk_monitor"


@dataclass
class RuleResult:
    """单条规则的执行结果

    Attributes:
        rule_name:  规则名称（与 Rule.name 一致）
        passed:     是否通过检查
        reason:     通过/拒绝的具体原因描述
        severity:   严重程度 ("info" / "warning" / "error")，error 会阻塞交易
        data:       规则可选附加数据（如建议的止损价、调整后的仓位等）
    """
    rule_name: str
    passed: bool
    reason: str = ""
    severity: str = "error"
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule_name,
            "passed": self.passed,
            "reason": self.reason,
            "severity": self.severity,
        }

    def __bool__(self) -> bool:
        """RuleResult 可直接用于布尔判断"""
        return self.passed


class Rule:
    """规则基类

    所有具体规则继承此类，通过重写 check() 实现检查逻辑。
    Rule 本身保持无状态——所有输入通过 context dict 传入。
    这样设计使得 Rule 可复用、可测试、可序列化。

    Class-level attributes（可在子类中覆盖）:
        name:             规则唯一标识
        description:      规则描述
        category:         规则分类（决定执行阶段）
        priority:         优先级（越小越先执行）
        default_enabled:  默认是否启用

    Instance attributes:
        config:  配置对象引用（通常是 AgentSystemConfig）
    """

    # ── 类级别元数据 ──
    name: str = ""
    description: str = ""
    category: RuleCategory = RuleCategory.PRE_TRADE
    priority: int = 100
    default_enabled: bool = True

    def __init__(self, config: Any = None):
        self.config = config
        self._enabled: bool = self.default_enabled
        self._stats: dict[str, Any] = {
            "checks": 0,
            "passes": 0,
            "blocks": 0,
            "last_result": None,
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    @property
    def is_async(self) -> bool:
        """是否需要异步执行（如调用外部 API 的规则）"""
        return False

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ── 子类需重写以下方法之一 ──

    def check(self, context: dict) -> RuleResult:
        """同步检查 — 子类重写此方法实现检查逻辑

        Args:
            context: 全局上下文 dict，包含：
                - "current_time": datetime (UTC)
                - "side": "buy" | "sell"
                - "size": float (ETH)
                - "price": float
                - "daily_trade_count": int
                - "daily_loss_usdt": float
                - "consecutive_losses": int
                - "last_trade_time": datetime | None
                - "current_position_side": "long" | "short" | None
                - "current_position_size": float
                - "api_breaker_until": datetime | None
                - "symbol": str
                - "mode": str
                - ... 规则可按需扩展

        Returns:
            RuleResult(passed=True, ...) 表示通过
            RuleResult(passed=False, reason="...") 表示被规则拦截
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement check() or check_async()"
        )

    async def check_async(self, context: dict) -> RuleResult:
        """异步检查 — 调用外部 API 的规则重写此方法

        默认委托给 sync check，纯 sync 规则无需重写。
        """
        return self.check(context)

    def _ok(self, reason: str = "", data: dict[str, Any] | None = None) -> RuleResult:
        """快捷方法：返回通过结果"""
        self._stats["checks"] += 1
        self._stats["passes"] += 1
        result = RuleResult(
            rule_name=self.name,
            passed=True,
            reason=reason,
            severity="info",
            data=data or {},
        )
        self._stats["last_result"] = result
        return result

    def _reject(self, reason: str, severity: str = "error",
                 data: dict[str, Any] | None = None) -> RuleResult:
        """快捷方法：返回拒绝结果"""
        self._stats["checks"] += 1
        self._stats["blocks"] += 1
        result = RuleResult(
            rule_name=self.name,
            passed=False,
            reason=reason,
            severity=severity,
            data=data or {},
        )
        self._stats["last_result"] = result
        return result

    def reset_stats(self):
        """重置统计计数"""
        self._stats = {"checks": 0, "passes": 0, "blocks": 0, "last_result": None}


# ── 上下文键名常量（便于规则间统一引用） ──

class Ctx:
    """context dict 键名常量"""
    TIME = "current_time"
    SIDE = "side"          # "buy" / "sell"
    SIZE = "size"          # float (ETH)
    PRICE = "price"        # float
    SYMBOL = "symbol"
    MODE = "mode"

    # 风控状态
    DAILY_TRADE_COUNT = "daily_trade_count"
    DAILY_LOSS_USDT = "daily_loss_usdt"
    MAX_DAILY_LOSS_USDT = "max_daily_loss_usdt"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    LAST_TRADE_TIME = "last_trade_time"
    API_BREAKER_UNTIL = "api_breaker_until"

    # 持仓
    POSITION_SIDE = "current_position_side"   # "long" / "short" / None
    POSITION_SIZE = "current_position_size"   # float (ETH)

    # 配置覆盖
    MAX_POSITION_ETH = "max_position_eth"
    MAX_DAILY_TRADES = "max_daily_trades"
    MAX_CONSECUTIVE_LOSSES = "max_consecutive_losses"
    MIN_TRADE_INTERVAL = "min_trade_interval"
    MAX_TRADES_PER_HOUR = "max_trades_per_hour"
