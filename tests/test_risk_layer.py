# tests/test_risk_layer.py
import sys; sys.path.insert(0, ".")
from datetime import datetime, timezone, timedelta
from agents.config import AgentSystemConfig
from agents.risk_layer import RiskManager


def test_layer1_min_interval():
    cfg = AgentSystemConfig(agent3_min_interval_between_trades=300)
    rm = RiskManager(cfg)
    now = datetime.now(timezone.utc)

    # First trade should pass
    ok, reason = rm.check_layer1("buy", 0.1, 3000, now)
    assert ok, f"Expected pass, got: {reason}"
    rm.record_trade({"side": "buy", "size": 0.1, "pnl": 0})

    # Immediate second trade should fail
    ok, reason = rm.check_layer1("buy", 0.1, 3000, now + timedelta(seconds=10))
    assert not ok, "Should fail: min interval"
    assert "交易间隔" in reason

    # After 5 minutes should pass
    ok, reason = rm.check_layer1("buy", 0.1, 3000, now + timedelta(seconds=301))
    assert ok, f"Expected pass after cooldown, got: {reason}"
    print("test_layer1_min_interval PASSED")


def test_layer1_daily_loss():
    cfg = AgentSystemConfig(agent3_max_daily_loss_usdt=100.0)
    rm = RiskManager(cfg)
    now = datetime.now(timezone.utc)

    ok, _ = rm.check_layer1("buy", 0.01, 3000, now)
    assert ok
    rm.record_trade({"side": "sell", "size": 0.01, "pnl": -60})

    ok, _ = rm.check_layer1("buy", 0.01, 3000, now + timedelta(seconds=301))
    assert ok
    rm.record_trade({"side": "sell", "size": 0.01, "pnl": -50})

    # Now daily loss exceeds limit
    ok, reason = rm.check_layer1("buy", 0.01, 3000, now + timedelta(seconds=602))
    assert not ok, "Should fail: daily loss exceeded"
    assert "亏损" in reason
    print("test_layer1_daily_loss PASSED")


def test_consecutive_losses():
    cfg = AgentSystemConfig(agent3_max_consecutive_losses=3)
    rm = RiskManager(cfg)
    now = datetime.now(timezone.utc)

    # 3 consecutive losses
    for i in range(3):
        rm.record_trade({"side": "sell", "size": 0.01, "pnl": -10})
        # 重置间隔以便继续检查
        rm._last_trade_time = None

    ok, reason = rm.check_layer1("buy", 0.01, 3000, now + timedelta(seconds=999))
    assert not ok, "Should fail: consecutive losses"
    assert "连续亏损" in reason
    print("test_consecutive_losses PASSED")


if __name__ == "__main__":
    test_layer1_min_interval()
    test_layer1_daily_loss()
    test_consecutive_losses()
    print("ALL PASSED")
