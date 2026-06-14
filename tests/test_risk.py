"""
风控模块测试
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from risk.rules import RiskEngine, RiskConfig
from risk.stop_loss import compute_stop_levels, should_exit
from risk.recovery import RecoveryManager


@pytest.fixture
def risk_config():
    return RiskConfig(
        max_position_pct=0.5,
        max_single_order_pct=0.1,
        max_daily_loss_pct=2.0,
        max_consecutive_losses=3,
        cooldown_bars=4,
        signal_expiry_bars=1,
    )


def test_risk_engine_init(risk_config):
    engine = RiskEngine(risk_config)
    assert engine.state.is_paused == False
    assert engine.state.daily_loss_pct == 0.0
    assert engine.state.consecutive_losses == 0


def test_signal_allowed_within_limits(risk_config):
    engine = RiskEngine(risk_config)
    allowed, reason = engine.check_signal("buy", current_equity=10000, current_position_pct=0.2)
    assert allowed == True
    assert reason == ""


def test_signal_rejected_when_max_position(risk_config):
    engine = RiskEngine(risk_config)
    allowed, reason = engine.check_signal("buy", current_equity=10000, current_position_pct=0.6)
    assert allowed == False
    assert "仓位" in reason


def test_consecutive_losses_triggers_pause(risk_config):
    engine = RiskEngine(risk_config)
    for i in range(3):
        engine.record_trade_result(-1.0)  # 连续亏 3 次
    assert engine.state.is_paused == True
    assert engine.state.consecutive_losses >= 3


def test_daily_loss_limit_triggers_pause(risk_config):
    engine = RiskEngine(risk_config)
    engine.record_trade_result(-2.5)  # 单次亏损超过 2%
    # 注意：这里 single trade loss 不等于 daily loss
    # 实际上 daily_loss_pct 会在 record_trade_result 里累加
    assert engine.state.daily_loss_pct >= 2.0
    assert engine.state.is_paused == True


def test_win_resets_consecutive_losses(risk_config):
    engine = RiskEngine(risk_config)
    engine.record_trade_result(-1.0)
    engine.record_trade_result(-1.0)
    engine.record_trade_result(2.0)  # 盈利
    assert engine.state.consecutive_losses == 0


def test_signal_expiry(risk_config):
    engine = RiskEngine(risk_config)
    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
    expired = engine.check_signal_expiry(old_time, datetime.now(timezone.utc))
    assert expired == True


# ─── Stop Loss Tests ───

def test_fixed_stop_loss():
    levels = compute_stop_levels(
        entry_price=100.0,
        current_price=95.0,
        highest_price=105.0,
        stop_loss_pct=0.02,
    )
    assert levels.fixed_stop == 98.0  # 100 * 0.98
    exit_signal, reason = should_exit(levels, 97.0)
    assert exit_signal == True
    assert "止损" in reason


def test_take_profit():
    levels = compute_stop_levels(
        entry_price=100.0,
        current_price=110.0,
        highest_price=115.0,
        take_profit_pct=0.06,
    )
    assert levels.fixed_take_profit == 106.0
    exit_signal, reason = should_exit(levels, 107.0)
    assert exit_signal == True
    assert "止盈" in reason


def test_trailing_stop_activates():
    """浮盈达到阈值后，移动止损生效"""
    levels = compute_stop_levels(
        entry_price=100.0,
        current_price=105.0,    # 浮盈 5%
        highest_price=108.0,    # 最高到 108
        trailing_activation_pct=0.03,  # 3% 激活
        trailing_distance_pct=0.015,   # 从最高回落 1.5%
    )
    assert levels.trailing_activated == True
    assert levels.trailing_stop is not None
    # 从最高 108 回落 1.5% = 106.38
    assert levels.trailing_stop < 108.0


def test_no_exit_when_price_normal():
    levels = compute_stop_levels(
        entry_price=100.0,
        current_price=101.0,
        highest_price=102.0,
        stop_loss_pct=2.0,
        take_profit_pct=6.0,
    )
    exit_signal, reason = should_exit(levels, 101.0)
    assert exit_signal == False


# ─── Recovery Tests ───

def test_recovery_starts_without_pause():
    mgr = RecoveryManager(RiskConfig(), None)  # type: ignore
    # Need to create a mock state
    from risk.rules import RiskState
    state = RiskState()
    result = mgr.evaluate_recovery(state, "ma_cross", ["ma_cross", "rsi_mean_reversion"])
    assert result["should_recover"] == False
