"""
RuleEngine 单元测试

测试覆盖:
  1. 核心类型 — Rule, RuleResult, RuleCategory
  2. 所有 Pre-Trade 规则
  3. 所有 Execution 规则
  4. RuleEngine 调度器（sync / async 混合执行）
  5. 规则启用 / 禁用
  6. 阻断行为
  7. 上下文构建 + 集成场景（模拟 RiskManager 输入）

运行:
    cd okx-quant-agent && python -m pytest tests/test_rule_engine.py -v
"""
from __future__ import annotations

import sys; sys.path.insert(0, ".")
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from agents.rule_engine.base import Rule, RuleResult, RuleCategory, Ctx
from agents.rule_engine.engine import RuleEngine
from agents.rule_engine.rules.pre_trade import (
    APIBreakerRule,
    TradeIntervalRule,
    DailyTradeLimitRule,
    DailyLossLimitRule,
    ConsecutiveLossRule,
    HFTProtectionRule,
    PositionSizeRule,
    DirectionConflictRule,
    VolatilityCheckRule,
    create_pre_trade_rules,
)
from agents.rule_engine.rules.execution import (
    MarketDepthRule,
    SlippageRule,
    create_execution_rules,
)


# ════════════════════════════════════════════════════════════════
# 1. 核心类型测试
# ════════════════════════════════════════════════════════════════

def test_rule_category_values():
    """RuleCategory 应包含所有预期分类"""
    assert RuleCategory.PRE_TRADE.value == "pre_trade"
    assert RuleCategory.EXECUTION.value == "execution"
    assert RuleCategory.POST_TRADE.value == "post_trade"
    assert RuleCategory.RISK_MONITOR.value == "risk_monitor"


def test_rule_result_bool():
    """RuleResult 应支持布尔判断"""
    assert RuleResult("test", passed=True)
    assert not RuleResult("test", passed=False)


def test_rule_result_to_dict():
    """RuleResult.to_dict 应返回标准格式"""
    r = RuleResult("test_rule", passed=False, reason="something wrong", severity="error")
    d = r.to_dict()
    assert d["rule"] == "test_rule"
    assert d["passed"] is False
    assert d["reason"] == "something wrong"
    assert d["severity"] == "error"


def test_rule_base_class():
    """Rule 基类应提供 _ok / _reject 快捷方法"""
    class SampleRule(Rule):
        name = "sample"
        def check(self, ctx):
            v = ctx.get("value", 0)
            return self._ok("all good") if v > 0 else self._reject("bad value")

    rule = SampleRule()
    assert rule.name == "sample"
    assert rule.enabled is True
    assert rule.category == RuleCategory.PRE_TRADE

    # 通过
    r1 = rule.check({"value": 1})
    assert r1.passed is True
    assert r1.reason == "all good"

    # 拒绝
    r2 = rule.check({"value": 0})
    assert r2.passed is False
    assert r2.reason == "bad value"

    # 统计
    stats = rule.stats
    assert stats["checks"] == 2
    assert stats["passes"] == 1
    assert stats["blocks"] == 1


def test_rule_enabled_property():
    """Rule 应支持启用/禁用"""
    rule = APIBreakerRule()
    assert rule.enabled is True
    rule.enabled = False
    assert rule.enabled is False
    rule.enabled = True
    assert rule.enabled is True


# ════════════════════════════════════════════════════════════════
# 2. Pre-Trade 规则测试
# ════════════════════════════════════════════════════════════════

def test_api_breaker_pass():
    rule = APIBreakerRule()
    # 无熔断
    ctx = {}
    assert rule.check(ctx).passed is True

    # 熔断已过期
    ctx = {
        Ctx.API_BREAKER_UNTIL: datetime.now(timezone.utc) - timedelta(seconds=10),
    }
    assert rule.check(ctx).passed is True


def test_api_breaker_block():
    rule = APIBreakerRule()
    ctx = {
        Ctx.API_BREAKER_UNTIL: datetime.now(timezone.utc) + timedelta(seconds=120),
    }
    result = rule.check(ctx)
    assert result.passed is False
    assert "熔断" in result.reason


def test_trade_interval_pass():
    rule = TradeIntervalRule()
    # 无上次交易
    assert rule.check({}).passed is True

    # 间隔充足
    ctx = {
        Ctx.LAST_TRADE_TIME: datetime.now(timezone.utc) - timedelta(seconds=900),
        Ctx.MIN_TRADE_INTERVAL: 600,
    }
    assert rule.check(ctx).passed is True


def test_trade_interval_block():
    rule = TradeIntervalRule()
    ctx = {
        Ctx.LAST_TRADE_TIME: datetime.now(timezone.utc) - timedelta(seconds=60),
        Ctx.MIN_TRADE_INTERVAL: 600,
    }
    result = rule.check(ctx)
    assert result.passed is False
    assert "还需" in result.reason


def test_daily_trade_limit_pass():
    rule = DailyTradeLimitRule()
    ctx = {Ctx.DAILY_TRADE_COUNT: 5, Ctx.MAX_DAILY_TRADES: 20}
    assert rule.check(ctx).passed is True


def test_daily_trade_limit_block():
    rule = DailyTradeLimitRule()
    ctx = {Ctx.DAILY_TRADE_COUNT: 20, Ctx.MAX_DAILY_TRADES: 20}
    result = rule.check(ctx)
    assert result.passed is False
    assert "已达上限" in result.reason


def test_daily_loss_limit_pass():
    rule = DailyLossLimitRule()
    ctx = {Ctx.DAILY_LOSS_USDT: 30.0, Ctx.MAX_DAILY_LOSS_USDT: 100.0}
    assert rule.check(ctx).passed is True


def test_daily_loss_limit_block():
    rule = DailyLossLimitRule()
    ctx = {Ctx.DAILY_LOSS_USDT: 100.0, Ctx.MAX_DAILY_LOSS_USDT: 100.0}
    result = rule.check(ctx)
    assert result.passed is False
    assert "亏损" in result.reason


def test_consecutive_loss_pass():
    rule = ConsecutiveLossRule()
    ctx = {Ctx.CONSECUTIVE_LOSSES: 2, Ctx.MAX_CONSECUTIVE_LOSSES: 3}
    assert rule.check(ctx).passed is True


def test_consecutive_loss_block():
    rule = ConsecutiveLossRule()
    ctx = {Ctx.CONSECUTIVE_LOSSES: 3, Ctx.MAX_CONSECUTIVE_LOSSES: 3}
    result = rule.check(ctx)
    assert result.passed is False
    assert "连续亏损" in result.reason


def test_position_size_pass():
    rule = PositionSizeRule()
    ctx = {Ctx.SIZE: 0.3, Ctx.MAX_POSITION_ETH: 0.5}
    assert rule.check(ctx).passed is True


def test_position_size_block():
    rule = PositionSizeRule()
    ctx = {Ctx.SIZE: 0.6, Ctx.MAX_POSITION_ETH: 0.5}
    result = rule.check(ctx)
    assert result.passed is False
    assert "超过上限" in result.reason


def test_direction_conflict_pass():
    """同方向和反方向都通过"""
    rule = DirectionConflictRule()
    # 不同方向
    ctx = {
        Ctx.SIDE: "buy", Ctx.SIZE: 0.3,
        Ctx.POSITION_SIDE: "short", Ctx.POSITION_SIZE: 0.2,
        Ctx.MAX_POSITION_ETH: 0.5,
    }
    assert rule.check(ctx).passed is True

    # 同方向但累加不超限
    ctx = {
        Ctx.SIDE: "buy", Ctx.SIZE: 0.2,
        Ctx.POSITION_SIDE: "long", Ctx.POSITION_SIZE: 0.2,
        Ctx.MAX_POSITION_ETH: 0.5,
    }
    assert rule.check(ctx).passed is True


def test_direction_conflict_block():
    """同方向累加超限"""
    rule = DirectionConflictRule()
    ctx = {
        Ctx.SIDE: "buy", Ctx.SIZE: 0.4,
        Ctx.POSITION_SIDE: "long", Ctx.POSITION_SIZE: 0.3,
        Ctx.MAX_POSITION_ETH: 0.5,
    }
    result = rule.check(ctx)
    assert result.passed is False
    assert "方向冲突" in result.reason


# ════════════════════════════════════════════════════════════════
# 3. Execution 规则测试
# ════════════════════════════════════════════════════════════════

def test_slippage_no_price():
    rule = SlippageRule()
    # 无参考价格
    assert rule.check({}).passed is True


def test_slippage_within_range():
    rule = SlippageRule()
    ctx = {
        Ctx.PRICE: 3050,
        "entry_price_min": 3000,
        "entry_price_max": 3100,
        "max_slippage_pct": 0.5,
    }
    assert rule.check(ctx).passed is True


def test_slippage_exceeded():
    rule = SlippageRule()
    ctx = {
        Ctx.PRICE: 3200,
        "entry_price_min": 3000,
        "entry_price_max": 3050,
        "max_slippage_pct": 0.3,
    }
    result = rule.check(ctx)
    assert result.passed is False
    assert "滑点" in result.reason


# ════════════════════════════════════════════════════════════════
# 4. RuleEngine 调度器测试
# ════════════════════════════════════════════════════════════════

def test_engine_register():
    engine = RuleEngine()
    assert engine.rule_count == 0

    engine.register(DailyTradeLimitRule())
    assert engine.rule_count == 1

    engine.register(TradeIntervalRule())
    assert engine.rule_count == 2


def test_engine_unregister():
    engine = RuleEngine()
    engine.register(DailyTradeLimitRule())
    engine.register(TradeIntervalRule())
    assert engine.rule_count == 2

    assert engine.unregister("daily_trade_limit") is True
    assert engine.rule_count == 1

    assert engine.unregister("nonexistent") is False
    assert engine.rule_count == 1


def test_engine_register_many():
    engine = RuleEngine()
    engine.register_many(create_pre_trade_rules())
    assert engine.rule_count == len(create_pre_trade_rules())


def test_engine_load_defaults():
    engine = RuleEngine()
    engine.load_defaults()
    assert engine.rule_count > 0
    # 验证同时有 pre-trade 和 execution 规则
    pre = engine.get_rules(RuleCategory.PRE_TRADE)
    exec_ = engine.get_rules(RuleCategory.EXECUTION)
    assert len(pre) > 0
    assert len(exec_) > 0


def test_engine_enable_disable():
    engine = RuleEngine()
    engine.register(DailyTradeLimitRule())

    assert engine.disable_rule("daily_trade_limit") is True
    assert engine.get_rule("daily_trade_limit").enabled is False

    assert engine.enable_rule("daily_trade_limit") is True
    assert engine.get_rule("daily_trade_limit").enabled is True

    assert engine.enable_rule("nonexistent") is False


def test_engine_all_pass():
    engine = RuleEngine()
    results = [
        RuleResult("r1", passed=True),
        RuleResult("r2", passed=True),
    ]
    assert engine.all_pass(results) is True

    results.append(RuleResult("r3", passed=False))
    assert engine.all_pass(results) is False


def test_engine_blocked_by():
    engine = RuleEngine()
    results = [
        RuleResult("r1", passed=True),
        RuleResult("r2", passed=False, reason="limit reached"),
    ]
    assert engine.blocked_by(results) == "r2"

    results_all_pass = [RuleResult("r1", passed=True)]
    assert engine.blocked_by(results_all_pass) is None


def test_engine_get_warnings():
    results = [
        RuleResult("r1", passed=True),
        RuleResult("r2", passed=True, severity="warning", reason="use limit order"),
        RuleResult("r3", passed=False, reason="blocked"),
    ]
    warnings = RuleEngine.get_warnings(results)
    assert len(warnings) == 1
    assert warnings[0].rule_name == "r2"


def test_engine_get_errors():
    results = [
        RuleResult("r1", passed=True),
        RuleResult("r2", passed=False, reason="blocked"),
        RuleResult("r3", passed=True),
    ]
    errors = RuleEngine.get_errors(results)
    assert len(errors) == 1
    assert errors[0].rule_name == "r2"


# ════════════════════════════════════════════════════════════════
# 5. 集成场景测试
# ════════════════════════════════════════════════════════════════

def test_scenario_normal_trade():
    """正常交易应通过所有规则"""
    engine = RuleEngine()
    engine.register(DailyTradeLimitRule())
    engine.register(DailyLossLimitRule())
    engine.register(PositionSizeRule())
    engine.register(DirectionConflictRule())

    import asyncio
    context = {
        Ctx.DAILY_TRADE_COUNT: 5,
        Ctx.DAILY_LOSS_USDT: 20.0,
        Ctx.MAX_DAILY_TRADES: 20,
        Ctx.MAX_DAILY_LOSS_USDT: 100.0,
        Ctx.CONSECUTIVE_LOSSES: 0,
        Ctx.SIDE: "buy",
        Ctx.SIZE: 0.3,
        Ctx.MAX_POSITION_ETH: 0.5,
        Ctx.POSITION_SIDE: None,
        Ctx.POSITION_SIZE: 0.0,
        Ctx.PRICE: 3000,
        Ctx.LAST_TRADE_TIME: datetime.now(timezone.utc) - timedelta(seconds=900),
        Ctx.MIN_TRADE_INTERVAL: 600,
    }

    # 用 asyncio.run 运行 async check
    results = asyncio.run(engine.check_all(context))
    all_results = results["pre_trade"] + results["execution"]

    assert engine.all_pass(all_results), (
        f"Expected all pass, got errors: "
        f"{[r.reason for r in RuleEngine.get_errors(all_results)]}"
    )


def test_scenario_daily_limit_reached():
    """每日交易达上限应阻断"""
    engine = RuleEngine()
    engine.register(DailyTradeLimitRule())

    import asyncio
    context = {
        Ctx.DAILY_TRADE_COUNT: 20,
        Ctx.MAX_DAILY_TRADES: 20,
    }
    results = asyncio.run(engine.check_pre_trade(context))

    assert not engine.all_pass(results)
    assert engine.blocked_by(results) == "daily_trade_limit"


def test_scenario_consecutive_losses_then_profit():
    """连亏后应阻断，盈利后应恢复"""
    engine = RuleEngine()
    engine.register(ConsecutiveLossRule())
    engine.register(DailyLossLimitRule())

    ctx = {
        Ctx.CONSECUTIVE_LOSSES: 3,
        Ctx.MAX_CONSECUTIVE_LOSSES: 3,
        Ctx.DAILY_LOSS_USDT: 80.0,
        Ctx.MAX_DAILY_LOSS_USDT: 100.0,
    }

    # 应被连亏阻断
    r1 = engine.get_rule("consecutive_loss").check(ctx)
    assert not r1.passed

    # 盈利后重置连亏（模拟风险管理器行为）
    ctx[Ctx.CONSECUTIVE_LOSSES] = 0
    r2 = engine.get_rule("consecutive_loss").check(ctx)
    assert r2.passed


def test_scenario_partial_block():
    """单个规则阻断后后续规则不应继续执行"""
    import asyncio

    class AlwaysBlock(Rule):
        name = "always_block"
        priority = 50
        def check(self, ctx):
            return self._reject("always blocks")

    class NeverCheck(Rule):
        name = "never_check"
        priority = 60
        def check(self, ctx):
            raise AssertionError("This rule should not be called")

    engine = RuleEngine()
    engine.register(NeverCheck())
    engine.register(AlwaysBlock())  # 更高优先级，先执行

    results = asyncio.run(engine.check_pre_trade({}))
    assert not engine.all_pass(results)
    assert engine.blocked_by(results) == "always_block"
    # NeverCheck 不应被执行 — 但无法直接验证没执行，只是确保结果不包含它
    # （因为 AlwaysBlock 阻断后 _run_category 就 break 了）


def test_engine_get_status():
    engine = RuleEngine()
    engine.load_defaults()
    status = engine.get_status()

    assert status["total_rules"] > 0
    assert status["enabled_rules"] > 0
    assert len(status["rules"]) > 0
    assert "name" in status["rules"][0]
    assert "category" in status["rules"][0]
    assert "enabled" in status["rules"][0]


def test_reset_all_stats():
    """reset_all_stats 应将所有规则统计归零"""
    rule = DailyTradeLimitRule()
    rule.check({Ctx.DAILY_TRADE_COUNT: 5, Ctx.MAX_DAILY_TRADES: 20})
    assert rule.stats["checks"] == 1

    engine = RuleEngine()
    engine.register(rule)
    engine.reset_all_stats()

    assert engine.get_rule("daily_trade_limit").stats["checks"] == 0


# ════════════════════════════════════════════════════════════════
# 6. 规则优先级顺序测试
# ════════════════════════════════════════════════════════════════

def test_rule_priority_order():
    """规则应按 priority 升序执行"""
    engine = RuleEngine()

    class LowPri(Rule):
        name = "low_pri"
        priority = 100
        def check(self, ctx): return self._ok()

    class HighPri(Rule):
        name = "high_pri"
        priority = 10
        def check(self, ctx): return self._ok()

    engine.register(LowPri())
    engine.register(HighPri())

    rules = engine.get_rules()
    assert rules[0].name == "high_pri"
    assert rules[1].name == "low_pri"


# ════════════════════════════════════════════════════════════════
# 7. 异步规则测试（Mock OKXClient）
# ════════════════════════════════════════════════════════════════

def test_volatility_skips_without_client():
    """无 OKX 客户端时 VolatilityCheckRule 应放行"""
    import asyncio
    rule = VolatilityCheckRule()
    result = asyncio.run(rule.check_async({}))
    assert result.passed is True
    assert "跳过" in result.reason


def test_volatility_symbol_default():
    """VolatilityCheckRule 默认使用 ETH-USDT"""
    import asyncio
    rule = VolatilityCheckRule()
    result = asyncio.run(rule.check_async({}))
    # 没有 okx_client 但检查消息中无 BTC 字样
    assert "BTC" not in result.reason


def test_slippage_with_signal_price():
    rule = SlippageRule()
    # 信号价格 vs 当前价格，偏差小
    ctx = {
        Ctx.PRICE: 3010,
        "signal_price": 3000,
        "max_slippage_pct": 0.5,
    }
    result = rule.check(ctx)
    # 3010 vs 3000 = 0.33% < 0.5%
    assert result.passed is True

    # 偏差大
    ctx = {
        Ctx.PRICE: 3050,
        "signal_price": 3000,
        "max_slippage_pct": 0.3,
    }
    result = rule.check(ctx)
    # 3050 vs 3000 = 1.67% > 0.3%
    assert result.passed is False


# ════════════════════════════════════════════════════════════════
# 8. HFT 规则测试（带 mock DB）
# ════════════════════════════════════════════════════════════════

def test_hft_rule_no_db():
    """无 DB 连接时 HFT 规则应放行"""
    rule = HFTProtectionRule()
    result = rule.check({})
    assert result.passed is True
    assert "跳过" in result.reason


# ════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_rule_category_values()
    print("✓ test_rule_category_values")

    test_rule_result_bool()
    print("✓ test_rule_result_bool")

    test_rule_result_to_dict()
    print("✓ test_rule_result_to_dict")

    test_rule_base_class()
    print("✓ test_rule_base_class")

    test_rule_enabled_property()
    print("✓ test_rule_enabled_property")

    # Pre-Trade
    test_api_breaker_pass()
    print("✓ test_api_breaker_pass")
    test_api_breaker_block()
    print("✓ test_api_breaker_block")
    test_trade_interval_pass()
    print("✓ test_trade_interval_pass")
    test_trade_interval_block()
    print("✓ test_trade_interval_block")
    test_daily_trade_limit_pass()
    print("✓ test_daily_trade_limit_pass")
    test_daily_trade_limit_block()
    print("✓ test_daily_trade_limit_block")
    test_daily_loss_limit_pass()
    print("✓ test_daily_loss_limit_pass")
    test_daily_loss_limit_block()
    print("✓ test_daily_loss_limit_block")
    test_consecutive_loss_pass()
    print("✓ test_consecutive_loss_pass")
    test_consecutive_loss_block()
    print("✓ test_consecutive_loss_block")
    test_position_size_pass()
    print("✓ test_position_size_pass")
    test_position_size_block()
    print("✓ test_position_size_block")
    test_direction_conflict_pass()
    print("✓ test_direction_conflict_pass")
    test_direction_conflict_block()
    print("✓ test_direction_conflict_block")

    # Execution
    test_slippage_no_price()
    print("✓ test_slippage_no_price")
    test_slippage_within_range()
    print("✓ test_slippage_within_range")
    test_slippage_exceeded()
    print("✓ test_slippage_exceeded")
    test_slippage_with_signal_price()
    print("✓ test_slippage_with_signal_price")

    # Engine
    test_engine_register()
    print("✓ test_engine_register")
    test_engine_unregister()
    print("✓ test_engine_unregister")
    test_engine_register_many()
    print("✓ test_engine_register_many")
    test_engine_load_defaults()
    print("✓ test_engine_load_defaults")
    test_engine_enable_disable()
    print("✓ test_engine_enable_disable")
    test_engine_all_pass()
    print("✓ test_engine_all_pass")
    test_engine_blocked_by()
    print("✓ test_engine_blocked_by")
    test_engine_get_warnings()
    print("✓ test_engine_get_warnings")
    test_engine_get_errors()
    print("✓ test_engine_get_errors")

    # Scenarios
    test_scenario_normal_trade()
    print("✓ test_scenario_normal_trade")
    test_scenario_daily_limit_reached()
    print("✓ test_scenario_daily_limit_reached")
    test_scenario_consecutive_losses_then_profit()
    print("✓ test_scenario_consecutive_losses_then_profit")
    test_scenario_partial_block()
    print("✓ test_scenario_partial_block")
    test_engine_get_status()
    print("✓ test_engine_get_status")
    test_reset_all_stats()
    print("✓ test_reset_all_stats")
    test_rule_priority_order()
    print("✓ test_rule_priority_order")

    # Async
    test_volatility_skips_without_client()
    print("✓ test_volatility_skips_without_client")
    test_hft_rule_no_db()
    print("✓ test_hft_rule_no_db")

    print("\n✅ All RuleEngine tests PASSED")
