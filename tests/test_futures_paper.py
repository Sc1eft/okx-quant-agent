"""
合约模拟盘引擎测试
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest
import pandas as pd

from config import Config, FuturesConfig
from strategies.base import Signal, PositionInfo
from execution.futures_paper import (
    calc_liquidation_price,
    FuturesPosition,
    FuturesAccount,
    FuturesPaperEngine,
    _mmr_for_leverage,
)


# ═══════════════════════════════════════════
# 强平价计算
# ═══════════════════════════════════════════


class TestLiquidationPrice:
    def test_long_10x(self):
        """10x 多仓强平价"""
        liq = calc_liquidation_price(entry_price=3000, direction="long", leverage=10)
        # 3000 × (1 - 0.1 + 0.005) = 3000 × 0.905 = 2715
        assert liq == pytest.approx(2715.0)

    def test_short_10x(self):
        """10x 空仓强平价"""
        liq = calc_liquidation_price(entry_price=3000, direction="short", leverage=10)
        # 3000 × (1 + 0.1 - 0.005) = 3000 × 1.095 = 3285
        assert liq == pytest.approx(3285.0)

    def test_long_125x(self):
        """125x 多仓强平价 (mmr = 5%)"""
        liq = calc_liquidation_price(entry_price=3000, direction="long", leverage=125)
        # 3000 × (1 - 0.008 + 0.05) = 3000 × 1.042 = 3126
        expected = 3000 * (1 - 1/125 + 0.05)
        assert liq == pytest.approx(expected)

    def test_short_custom_mmr(self):
        """自定义维持保证金率"""
        liq = calc_liquidation_price(entry_price=2000, direction="short", leverage=20, maintenance_margin_rate=0.01)
        expected = 2000 * (1 + 1/20 - 0.01)
        assert liq == pytest.approx(expected)


# ═══════════════════════════════════════════
# 维持保证金率
# ═══════════════════════════════════════════


class TestMMR:
    def test_low_leverage(self):
        assert _mmr_for_leverage(1) == 0.004
        assert _mmr_for_leverage(5) == 0.004

    def test_medium_leverage(self):
        assert _mmr_for_leverage(10) == 0.005

    def test_high_leverage(self):
        assert _mmr_for_leverage(20) == 0.010
        assert _mmr_for_leverage(50) == 0.025

    def test_max_leverage(self):
        assert _mmr_for_leverage(125) == 0.050


# ═══════════════════════════════════════════
# 持仓
# ═══════════════════════════════════════════


class TestFuturesPosition:
    @pytest.fixture
    def long_pos(self) -> FuturesPosition:
        return FuturesPosition(
            direction="long",
            size=1.0,
            entry_price=3000.0,
            leverage=10,
            position_value=3000.0,
            margin=300.0,
            highest_price=3000.0,
        )

    @pytest.fixture
    def short_pos(self) -> FuturesPosition:
        return FuturesPosition(
            direction="short",
            size=2.0,
            entry_price=3000.0,
            leverage=5,
            position_value=6000.0,
            margin=1200.0,
            lowest_price=3000.0,
        )

    def test_long_unrealized_pnl(self, long_pos):
        """多仓未实现盈亏"""
        assert long_pos.unrealized_pnl(3100) == 100.0   # 1 × (3100 - 3000)
        assert long_pos.unrealized_pnl(2900) == -100.0  # 1 × (2900 - 3000)

    def test_short_unrealized_pnl(self, short_pos):
        """空仓未实现盈亏"""
        assert short_pos.unrealized_pnl(2900) == 200.0   # 2 × (3000 - 2900)
        assert short_pos.unrealized_pnl(3100) == -200.0  # 2 × (3000 - 3100)

    def test_long_pnl_pct(self, long_pos):
        """多仓盈亏百分比 (杠杆放大)"""
        # +100 / 300 = +33.33%
        assert long_pos.unrealized_pnl_pct(3100) == pytest.approx(33.33, rel=0.01)

    def test_short_pnl_pct(self, short_pos):
        """空仓盈亏百分比"""
        # +200 / 1200 = +16.67% (5x)
        assert short_pos.unrealized_pnl_pct(2900) == pytest.approx(16.67, rel=0.01)

    def test_margin_rate_normal(self, long_pos):
        """正常保证金率"""
        # (300 + 0) / 3000 = 10%
        rate = long_pos.margin_rate(3000)
        assert rate == pytest.approx(10.0)

    def test_margin_rate_loss(self, long_pos):
        """亏损时保证金率下降"""
        rate = long_pos.margin_rate(2900)
        # (300 - 100) / 3000 = 6.67%
        assert rate == pytest.approx(6.67, rel=0.01)

    def test_is_liquidated_long(self, long_pos):
        """多仓强平检查"""
        assert not long_pos.is_liquidated(2720)   # 未触发
        assert long_pos.is_liquidated(2714)       # 低于强平价 2715
        assert long_pos.is_liquidated(2700)       # 已触发

    def test_is_liquidated_short(self, short_pos):
        """空仓强平检查"""
        # short_pos 5x, entry 3000 → liq = 3000 × (1 + 0.2 - 0.004) = 3588
        assert not short_pos.is_liquidated(3580)
        assert short_pos.is_liquidated(3590)
        assert short_pos.is_liquidated(3600)

    def test_is_active(self):
        """持仓是否有效"""
        pos = FuturesPosition(direction="long", size=1.0, entry_price=100, leverage=1, position_value=100, margin=100)
        assert pos.is_active
        pos.size = 0.0
        assert not pos.is_active


# ═══════════════════════════════════════════
# 合约账户
# ═══════════════════════════════════════════


class TestFuturesAccount:
    @pytest.fixture
    def account(self) -> FuturesAccount:
        return FuturesAccount(wallet_balance=10000.0)

    def test_init(self, account):
        assert account.wallet_balance == 10000.0
        assert account.available_balance == 10000.0
        assert account.used_margin == 0.0
        assert account.total_equity == 10000.0
        assert account.is_flat
        assert account.position_side == "flat"

    def test_open_long(self, account):
        """开多 — 占用保证金，钱包扣除手续费"""
        trade = account.open_long(price=3000, size=1.0, leverage=10)
        assert trade["side"] == "open_long"
        assert trade["size"] == 1.0
        assert trade["margin"] == pytest.approx(300.0)  # 3000 / 10

        assert account.position is not None
        assert account.position.direction == "long"
        assert account.position.size == 1.0
        assert account.position.entry_price == 3000.0
        assert account.position.leverage == 10
        assert account.position.position_value == 3000.0
        assert account.position.margin == pytest.approx(300.0)

        # 钱包 = 10000 - 手续费 (3000 * 0.001 = 3)
        assert account.wallet_balance == pytest.approx(9997.0)
        assert account.used_margin == pytest.approx(300.0)
        assert account.available_balance == pytest.approx(9697.0)

        # 总权益 = 9997 + 0 (未实现盈亏) = 9997
        account.update_price(3000)
        assert account.total_equity == pytest.approx(9997.0)

    def test_open_short(self, account):
        """开空"""
        trade = account.open_short(price=3000, size=2.0, leverage=5)
        assert trade["side"] == "open_short"

        assert account.position is not None
        assert account.position.direction == "short"
        assert account.position.size == 2.0
        assert account.position.position_value == 6000.0
        assert account.position.margin == pytest.approx(1200.0)

        # 手续费 = 6000 * 0.001 = 6
        assert account.wallet_balance == pytest.approx(9994.0)

    def test_open_long_then_close(self, account):
        """开多 → 平多 → 计算盈亏"""
        account.open_long(price=3000, size=1.0, leverage=10)
        account.update_price(3100)

        trade = account.close_position(price=3100)
        assert trade["side"] == "close_long"
        assert trade["pnl"] == pytest.approx(96.9, rel=0.01)  # (3100-3000)×1 - fee_fraction

        assert account.is_flat
        assert account.position.size == 0.0

    def test_open_short_then_close(self, account):
        """开空 → 平空 → 计算盈亏"""
        account.open_short(price=3000, size=2.0, leverage=5)
        account.update_price(2900)

        trade = account.close_position(price=2900)
        assert trade["side"] == "close_short"
        # PnL = 2 × (3000 - 2900) - fees = 200 - fees
        assert trade["pnl"] > 0

    def test_liquidation_long(self, account):
        """多仓强平 — 损失全部保证金"""
        account.open_long(price=3000, size=1.0, leverage=10)
        # liq ≈ 2715, 触发
        liq = account.liquidate(price=2700)
        assert liq is not None
        assert liq["side"] == "liquidation"
        assert liq["margin_lost"] > 0
        assert account.is_flat

    def test_liquidation_short(self, account):
        """空仓强平"""
        account.open_short(price=3000, size=1.0, leverage=10)
        # liq = 3000 * (1 + 0.1 - 0.005) = 3285
        liq = account.liquidate(price=3300)
        assert liq is not None
        assert liq["side"] == "liquidation"
        assert account.is_flat

    def test_close_all_no_position(self, account):
        """无仓位时平仓返回 None"""
        assert account.close_all(price=3000) is None

    def test_multiple_trades(self, account):
        """多次交易记录"""
        account.open_long(price=3000, size=1.0, leverage=10)
        account.close_position(price=3100)
        account.open_short(price=3100, size=0.5, leverage=20)
        account.close_position(price=3000)

        assert len(account.trades) == 4
        assert account.total_realized_pnl != 0

    def test_to_dict(self, account):
        """序列化 — 含所有关键字段"""
        account.open_long(price=3000, size=1.0, leverage=10)
        account.update_price(3100)

        d = account.to_dict()
        assert d["direction"] == "long"
        assert d["position"] == pytest.approx(1.0)
        assert d["leverage"] == 10
        assert d["liquidation_price"] > 0
        assert d["unrealized_pnl"] > 0
        assert d["margin_rate"] > 0

    def test_repr(self, account):
        """字符串表示"""
        account.open_long(price=3000, size=1.0, leverage=10)
        r = repr(account)
        assert "long" in r
        assert "10" in r  # leverage


# ═══════════════════════════════════════════
# 合约模拟引擎
# ═══════════════════════════════════════════


class MockStrategy:
    """模拟策略 — 返回预设信号"""

    def __init__(self, signals: list[Signal] = None):
        self.signals = signals or [Signal.HOLD]
        self._idx = 0
        self.position = None
        self._bar_buffer = None
        self._min_bars = 1
        self.name = "mock"

    def on_bar(self, bar: pd.Series) -> Signal:
        sig = self.signals[self._idx % len(self.signals)]
        self._idx += 1
        return sig

    def reset_buffer(self):
        self._bar_buffer = None

    def get_bar_buffer(self):
        return pd.DataFrame()


class TestFuturesPaperEngine:
    @pytest.fixture
    def cfg(self) -> Config:
        cfg = Config()
        cfg.futures = FuturesConfig(leverage=10, margin_mode="isolated")
        cfg.trading.symbol = "ETH-USDT"
        cfg.trading.taker_fee = 0.001
        cfg.risk.max_single_order_pct = 0.1
        return cfg

    @pytest.fixture
    def bar(self) -> pd.Series:
        return pd.Series(
            {"open": 3000, "high": 3050, "low": 2980, "close": 3020, "volume": 1000},
            name=pd.Timestamp("2025-01-01 00:00:00"),
        )

    def test_engine_init(self, cfg):
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        assert engine.account.wallet_balance == 10000.0
        assert engine.leverage == 10
        assert engine.position_size_pct == 0.1

    def test_run_bar_buy_signal(self, cfg, bar):
        """BUY 信号 → 开多"""
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        strategy = MockStrategy(signals=[Signal.BUY])

        state = engine.run_bar(bar, strategy)
        assert state["signal"] == "buy"
        assert state["trade"]["side"] == "open_long"
        assert state["account"]["direction"] == "long"
        assert state["account"]["position"] > 0

    def test_run_bar_sell_signal(self, cfg, bar):
        """SELL 信号 → 开空"""
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        strategy = MockStrategy(signals=[Signal.SELL])

        state = engine.run_bar(bar, strategy)
        assert state["signal"] == "sell"
        assert state["trade"]["side"] == "open_short"

    def test_run_bar_hold_signal(self, cfg, bar):
        """HOLD 信号 → 无交易"""
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        strategy = MockStrategy(signals=[Signal.HOLD])

        state = engine.run_bar(bar, strategy)
        assert state["signal"] == "hold"
        assert state["trade"] is None

    def test_run_bar_exit_signal(self, cfg, bar):
        """开多后 EXIT 信号 → 平多"""
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        strategy = MockStrategy(signals=[Signal.BUY, Signal.EXIT])

        # K 线 1: BUY → 开多
        state1 = engine.run_bar(bar, strategy)
        assert state1["account"]["direction"] == "long"

        # K 线 2: EXIT → 平多
        bar2 = bar.copy()
        bar2.name = pd.Timestamp("2025-01-01 01:00:00")
        bar2["close"] = 3050
        state2 = engine.run_bar(bar2, strategy)
        assert state2["signal"] == "exit"
        assert engine.account.is_flat

    def test_buy_when_short_flips(self, cfg, bar):
        """BUY 信号 + 空仓 → 先平空再开多"""
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        strategy = MockStrategy(signals=[Signal.SELL, Signal.BUY])

        # K 线 1: SELL → 开空
        engine.run_bar(bar, strategy)
        assert engine.account.position_side == "short"

        # K 线 2: BUY → 平空+开多
        bar2 = bar.copy()
        bar2.name = pd.Timestamp("2025-01-01 01:00:00")
        bar2["close"] = 2980
        state2 = engine.run_bar(bar2, strategy)
        assert state2["signal"] == "buy"
        assert engine.account.position_side == "long"

    def test_sell_when_long_flips(self, cfg, bar):
        """SELL 信号 + 多仓 → 先平多再开空"""
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        strategy = MockStrategy(signals=[Signal.BUY, Signal.SELL])

        # K 线 1: BUY → 开多
        engine.run_bar(bar, strategy)
        assert engine.account.position_side == "long"

        # K 线 2: SELL → 平多+开空
        bar2 = bar.copy()
        bar2.name = pd.Timestamp("2025-01-01 01:00:00")
        bar2["close"] = 3050
        state2 = engine.run_bar(bar2, strategy)
        assert state2["signal"] == "sell"
        assert engine.account.position_side == "short"

    def test_liquidation_in_run_bar(self, cfg, bar):
        """run_bar 中触发强平"""
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        strategy = MockStrategy(signals=[Signal.BUY])

        # 开多 @ 3000
        state = engine.run_bar(bar, strategy)
        assert state["account"]["direction"] == "long"

        # K 线 2: 价格暴跌至强平价以下 → 爆仓
        bar2 = bar.copy()
        bar2.name = pd.Timestamp("2025-01-01 01:00:00")
        bar2["close"] = 2600  # 低于 liq ≈ 2715
        state2 = engine.run_bar(bar2, strategy)
        assert state2.get("liquidation") is not None
        assert state2["liquidation"]["side"] == "liquidation"
        assert engine.account.is_flat

    def test_state_dict_keys(self, cfg, bar):
        """run_bar 返回的状态 dict 包含所有必要字段"""
        engine = FuturesPaperEngine(cfg, wallet_balance=10000, leverage=10, position_size_pct=0.1)
        strategy = MockStrategy(signals=[Signal.BUY])

        state = engine.run_bar(bar, strategy)
        assert "timestamp" in state
        assert "price" in state
        assert "signal" in state
        assert "risk_ok" in state
        assert "trade" in state
        assert "account" in state

        account = state["account"]
        assert "wallet_balance" in account
        assert "available_balance" in account
        assert "used_margin" in account
        assert "equity" in account
        assert "direction" in account
        assert "position" in account
        assert "leverage" in account
        assert "liquidation_price" in account
        assert "unrealized_pnl" in account
        assert "margin_rate" in account
        assert "total_realized_pnl" in account


# ═══════════════════════════════════════════
# Config 校验
# ═══════════════════════════════════════════


class TestFuturesConfig:
    def test_default_config(self):
        fc = FuturesConfig()
        assert fc.leverage == 10
        assert fc.margin_mode == "isolated"
        assert fc.maintenance_margin_ratio == 0.005

    def test_invalid_leverage(self):
        with pytest.raises(ValueError, match="杠杆"):
            FuturesConfig(leverage=-1)

    def test_invalid_margin_mode(self):
        with pytest.raises(ValueError):
            FuturesConfig(margin_mode="unknown")

    def test_integrated_in_main_config(self):
        cfg = Config()
        assert cfg.futures.leverage == 10
        assert cfg.futures.margin_mode == "isolated"
