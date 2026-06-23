# tests/test_risk_layer_phase2.py
"""测试 RiskManager 阶段二功能"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.risk_layer import RiskManager
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig(
        btc_volatility_threshold_pct=3.0,
        btc_volatility_delay_seconds=300,
        market_depth_spread_bps=10.0,
        market_depth_min_liquidity_eth=1.0,
    )


@pytest.fixture
def manager(config):
    return RiskManager(config)


def make_mock_client(btc_klines=None, order_book=None):
    """构造模拟 OKXClient"""
    client = MagicMock()

    if btc_klines is None:
        btc_klines = [
            {"timestamp": 1000, "open": 60000, "high": 61000, "low": 59000, "close": 60500},
            {"timestamp": 2000, "open": 60500, "high": 62000, "low": 60000, "close": 61500},
        ]
    if order_book is None:
        order_book = {
            "asks": [["3451.0", "12.5"], ["3452.0", "8.3"]],
            "bids": [["3449.5", "15.2"], ["3448.0", "10.1"]],
        }

    client.get_klines.return_value = btc_klines
    client.get_order_book.return_value = order_book
    return client


class TestBtcVolatility:
    @pytest.mark.asyncio
    async def test_btc_normal_volatility(self, manager):
        """BTC 正常波动 → 通过"""
        client = make_mock_client()  # ~1.65% change
        ok, reason = await manager.check_btc_volatility_async(client)
        assert ok is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_btc_high_volatility(self, manager):
        """BTC 高波动 → 拒绝"""
        klines = [
            {"timestamp": 1000, "open": 60000, "high": 61000, "low": 59000, "close": 60000},
            {"timestamp": 2000, "open": 60000, "high": 64000, "low": 59500, "close": 63000},
        ]
        client = make_mock_client(btc_klines=klines)
        ok, reason = await manager.check_btc_volatility_async(client)
        assert ok is False
        assert "BTC" in reason
        assert "波动" in reason

    @pytest.mark.asyncio
    async def test_btc_insufficient_data(self, manager):
        """BTC 数据不足 → 通过（不阻塞交易）"""
        client = make_mock_client(btc_klines=[{"timestamp": 1000, "close": 60000}])
        ok, reason = await manager.check_btc_volatility_async(client)
        assert ok is True

    @pytest.mark.asyncio
    async def test_btc_delay_cooldown(self, manager):
        """BTC 波动延迟期内再次检查 → 仍拒绝"""
        client = make_mock_client(btc_klines=[
            {"timestamp": 1000, "close": 60000},
            {"timestamp": 2000, "close": 63000},
        ])
        # 第一次检查 → 拒绝，设置延迟
        ok, _ = await manager.check_btc_volatility_async(client)
        assert ok is False

        # 第二次检查（还在延迟期）→ 拒绝，但不重复查询
        ok, reason = await manager.check_btc_volatility_async(client)
        assert ok is False
        assert "延迟" in reason


class TestMarketDepth:
    @pytest.mark.asyncio
    async def test_depth_sufficient(self, manager):
        """市场深度充足 → 通过"""
        order_book = {
            "asks": [["3450.0", "5.0"], ["3451.0", "10.0"]],
            "bids": [["3449.5", "5.0"], ["3448.0", "8.0"]],
        }
        client = make_mock_client(order_book=order_book)
        ok, reason, prefer_limit = await manager.check_market_depth_async(
            client, "buy", 0.5
        )
        assert ok is True
        # 买卖价差 = (3450-3449.5)/3449.75*10000 ≈ 1.45bps < 10bps → 允许市价单
        assert prefer_limit is False

    @pytest.mark.asyncio
    async def test_depth_wide_spread(self, manager):
        """买卖价差过大 → 强制限价单"""
        order_book = {
            "asks": [["3500.0", "5.0"]],
            "bids": [["3400.0", "5.0"]],
        }
        client = make_mock_client(order_book=order_book)
        # 价差 = (3500-3400)/3450*10000 ≈ 290bps > 10bps
        ok, reason, prefer_limit = await manager.check_market_depth_async(
            client, "sell", 0.5
        )
        assert ok is True
        assert prefer_limit is True
        assert "价差" in reason

    @pytest.mark.asyncio
    async def test_depth_insufficient_liquidity(self, manager):
        """深度不足以完成交易 → 拒绝"""
        order_book = {
            "asks": [["3450.0", "0.3"]],  # 只有 0.3 ETH 深度
            "bids": [["3449.0", "0.3"]],
        }
        client = make_mock_client(order_book=order_book)
        ok, reason, prefer_limit = await manager.check_market_depth_async(
            client, "buy", 0.5
        )
        assert ok is False
        assert "深度" in reason


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
