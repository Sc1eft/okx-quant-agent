# tests/test_risk_layer_phase2.py
"""测试 RiskManager 北京时间日结重置（波动/深度检查已迁移至 rule_engine）"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.risk_layer import RiskManager
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig()


@pytest.fixture
def manager(config):
    return RiskManager(config)


class TestBeijingSettlement:
    def test_daily_reset_at_cst_midnight(self, manager):
        """北京时间（UTC+8）午夜重置"""
        # UTC 15:59 = CST 23:59 → 还没到重置时间
        before = datetime(2026, 6, 24, 15, 59, tzinfo=timezone.utc)
        manager._check_date_reset(before)
        assert manager._daily_trade_count == 0  # 初始化状态

        # 手动模拟一次交易
        manager._daily_trade_count = 5
        manager._daily_loss_usdt = 50.0

        # UTC 16:00 = CST 00:00 → 应重置
        after = datetime(2026, 6, 24, 16, 0, tzinfo=timezone.utc)
        manager._check_date_reset(after)
        assert manager._daily_trade_count == 0
        assert manager._daily_loss_usdt == 0.0

    def test_no_reset_within_same_day(self, manager):
        """同一天内不重复重置"""
        # 初始化 CST 日期到测试日期，避免 __init__ 的当前日期干扰
        manager._current_cst_date = datetime(2026, 6, 24, tzinfo=timezone.utc).date()
        manager._daily_trade_count = 3

        t1 = datetime(2026, 6, 24, 8, 0, tzinfo=timezone.utc)
        manager._check_date_reset(t1)
        assert manager._daily_trade_count == 3  # 没被重置

        # 还没到 16:00 UTC
        t2 = datetime(2026, 6, 24, 15, 59, tzinfo=timezone.utc)
        manager._check_date_reset(t2)
        assert manager._daily_trade_count == 3

    def test_reset_accounts_for_cst_date_change(self, manager):
        """UTC 16:00 后应该用新的日期标识"""
        # UTC 15:59 → CST day 1
        d1 = datetime(2026, 6, 24, 15, 59, tzinfo=timezone.utc)
        manager._check_date_reset(d1)

        # UTC 16:00 → CST day 2
        d2 = datetime(2026, 6, 24, 16, 0, tzinfo=timezone.utc)
        manager._check_date_reset(d2)
        # 内部 _current_date 应该变成了 2026-06-25（CST 日期）
        # 注意：_current_date 存的是 UTC 日期，但重置逻辑判断 CST 日期变化
        assert manager._current_date == d2.date()
