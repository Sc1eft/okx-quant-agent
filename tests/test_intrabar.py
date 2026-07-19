"""tick 级 intrabar 退出测试 — 纯函数 + 现货/合约模拟盘引擎集成"""
from __future__ import annotations

import pytest

from config import Config
from execution.intrabar import check_tick_exit
from execution.paper import PaperEngine
from execution.futures_paper import FuturesPaperEngine
from risk.rules import RiskEngine


# ═══════════════════════════════════════════
# 纯函数 check_tick_exit（默认参数: 止损5 止盈10 移动6/3）
# ═══════════════════════════════════════════

_EXIT_KW = dict(
    stop_loss_pct=5.0, take_profit_pct=10.0,
    trailing_activation_pct=6.0, trailing_distance_pct=3.0,
)


class TestCheckTickExitLong:
    def test_no_trigger(self):
        assert check_tick_exit(
            3100.0, direction="long", entry_price=3000.0,
            highest_price=3100.0, lowest_price=2950.0, **_EXIT_KW,
        ) is None

    def test_stop_loss(self):
        # 3000 × 0.95 = 2850
        assert check_tick_exit(
            2849.0, direction="long", entry_price=3000.0,
            highest_price=3100.0, lowest_price=2849.0, **_EXIT_KW,
        ) == "stop_loss"

    def test_stop_loss_boundary_not_triggered(self):
        assert check_tick_exit(
            2850.01, direction="long", entry_price=3000.0,
            highest_price=3100.0, lowest_price=2850.0, **_EXIT_KW,
        ) is None

    def test_take_profit(self):
        # 3000 × 1.10 = 3300
        assert check_tick_exit(
            3301.0, direction="long", entry_price=3000.0,
            highest_price=3301.0, lowest_price=2950.0, **_EXIT_KW,
        ) == "take_profit"

    def test_trailing_stop_not_activated(self):
        # 最高价 3150 < 激活线 3180（3000×1.06），回落不触发移动止损
        assert check_tick_exit(
            3060.0, direction="long", entry_price=3000.0,
            highest_price=3150.0, lowest_price=2950.0, **_EXIT_KW,
        ) is None

    def test_trailing_stop_triggered(self):
        # 激活线 3180，highest=3200 后回落到 3200×0.97=3104 以下
        assert check_tick_exit(
            3103.0, direction="long", entry_price=3000.0,
            highest_price=3200.0, lowest_price=2950.0, **_EXIT_KW,
        ) == "trailing_stop"

    def test_trailing_stop_above_line_holds(self):
        # 回落但未跌破 3200×0.97=3104
        assert check_tick_exit(
            3105.0, direction="long", entry_price=3000.0,
            highest_price=3200.0, lowest_price=2950.0, **_EXIT_KW,
        ) is None

    def test_stop_loss_priority_over_trailing(self):
        # 同时满足止损（<2850）与移动止损条件 → 止损优先
        assert check_tick_exit(
            2800.0, direction="long", entry_price=3000.0,
            highest_price=3200.0, lowest_price=2800.0, **_EXIT_KW,
        ) == "stop_loss"

    def test_zero_entry_returns_none(self):
        assert check_tick_exit(
            3000.0, direction="long", entry_price=0.0,
            highest_price=0.0, lowest_price=0.0, **_EXIT_KW,
        ) is None

    def test_trailing_disabled_when_zero(self):
        # activation=0 时移动止损不生效，回落再多也只有固定止损/止盈
        kw = {**_EXIT_KW, "trailing_activation_pct": 0.0}
        assert check_tick_exit(
            3100.0, direction="long", entry_price=3000.0,
            highest_price=3300.0, lowest_price=2950.0, **kw,
        ) is None


class TestCheckTickExitShort:
    def test_stop_loss(self):
        # 空仓止损在上方：3000 × 1.05 = 3150
        assert check_tick_exit(
            3151.0, direction="short", entry_price=3000.0,
            highest_price=3151.0, lowest_price=2900.0, **_EXIT_KW,
        ) == "stop_loss"

    def test_take_profit(self):
        # 空仓止盈在下方：3000 × 0.90 = 2700
        assert check_tick_exit(
            2699.0, direction="short", entry_price=3000.0,
            highest_price=3050.0, lowest_price=2699.0, **_EXIT_KW,
        ) == "take_profit"

    def test_trailing_stop_triggered(self):
        # 激活：lowest ≤ 3000×0.94=2820；回升 ≥ 2820×1.03=2904.6 触发
        assert check_tick_exit(
            2905.0, direction="short", entry_price=3000.0,
            highest_price=3050.0, lowest_price=2810.0, **_EXIT_KW,
        ) == "trailing_stop"

    def test_no_trigger(self):
        assert check_tick_exit(
            2950.0, direction="short", entry_price=3000.0,
            highest_price=3050.0, lowest_price=2900.0, **_EXIT_KW,
        ) is None


# ═══════════════════════════════════════════
# 现货模拟盘 PaperEngine.check_tick_exit
# ═══════════════════════════════════════════

class TestPaperEngineTickExit:
    def _engine_with_long(self):
        cfg = Config()
        engine = PaperEngine(cfg, initial_balance=10000.0, position_size_pct=0.1)
        engine.account.execute_buy(3000.0, 0.1, fee_rate=0.001, ts="t0")  # $300 多仓
        return engine

    def test_no_position_returns_none(self):
        engine = PaperEngine(Config(), initial_balance=10000.0)
        assert engine.check_tick_exit(2800.0, ts="t1") is None

    def test_no_trigger_returns_none(self):
        engine = self._engine_with_long()
        assert engine.check_tick_exit(3000.0, ts="t1") is None
        assert engine.account.position > 0  # 持仓仍在

    def test_stop_loss_closes_long(self):
        engine = self._engine_with_long()
        risk = RiskEngine(Config().risk)
        trade = engine.check_tick_exit(2849.0, ts="t1", risk_engine=risk)
        assert trade is not None
        assert trade["reason"] == "stop_loss"
        assert trade["side"] == "sell"
        assert engine.account.position == 0.0
        assert risk.state.daily_trades == 1  # 风控已记录

    def test_take_profit_closes_long(self):
        engine = self._engine_with_long()
        trade = engine.check_tick_exit(3301.0, ts="t1")
        assert trade is not None
        assert trade["reason"] == "take_profit"
        assert trade["pnl"] > 0

    def test_trailing_stop_uses_tracked_highest(self):
        engine = self._engine_with_long()
        engine.account.update_price(3200.0, ts="t1")  # 推进最高价
        trade = engine.check_tick_exit(3103.0, ts="t2")
        assert trade is not None
        assert trade["reason"] == "trailing_stop"

    def test_exit_params_override(self):
        """页面 exit_params 覆盖 cfg.strategy 默认值"""
        cfg = Config()  # stop_loss 5%
        engine = PaperEngine(cfg, initial_balance=10000.0, position_size_pct=0.1,
                             exit_params={"stop_loss_pct": 1.0})
        engine.account.execute_buy(3000.0, 0.1, fee_rate=0.001, ts="t0")
        # 覆盖为 1% → 2970 触发；默认 5% 的 2850 不会提前触发
        assert engine.check_tick_exit(2971.0, ts="t1") is None
        trade = engine.check_tick_exit(2969.0, ts="t2")
        assert trade is not None and trade["reason"] == "stop_loss"

    def test_short_stop_loss(self):
        cfg = Config()
        engine = PaperEngine(cfg, initial_balance=10000.0, position_size_pct=0.1)
        engine.account.execute_short(3000.0, 0.1, fee_rate=0.001, ts="t0")
        trade = engine.check_tick_exit(3151.0, ts="t1")
        assert trade is not None
        assert trade["reason"] == "stop_loss"
        assert trade["side"] == "cover"
        assert engine.account.short_position == 0.0

    def test_equity_recorded_on_exit(self):
        engine = self._engine_with_long()
        n_before = len(engine.account.equity_history)
        engine.check_tick_exit(2849.0, ts="t1")
        assert len(engine.account.equity_history) == n_before + 1
        assert engine.account.equity_history[-1]["time"] == "t1"


# ═══════════════════════════════════════════
# 合约模拟盘 FuturesPaperEngine.check_tick_exit
# ═══════════════════════════════════════════

class TestFuturesTickExit:
    def _engine_with_long(self):
        cfg = Config()
        engine = FuturesPaperEngine(cfg, wallet_balance=10000.0, leverage=10,
                                    position_size_pct=0.1)
        engine.account.open_long(3000.0, 0.3, leverage=10, ts="t0")  # $900 仓位, margin $90
        return engine

    def test_no_position_returns_none(self):
        engine = FuturesPaperEngine(Config(), wallet_balance=10000.0)
        assert engine.check_tick_exit(2800.0, ts="t1") is None

    def test_stop_loss_closes_position(self):
        engine = self._engine_with_long()
        trade = engine.check_tick_exit(2849.0, ts="t1")
        assert trade is not None
        assert trade["reason"] == "stop_loss"
        assert not engine.account.position.is_active

    def test_short_trailing_uses_lowest(self):
        cfg = Config()
        engine = FuturesPaperEngine(cfg, wallet_balance=10000.0, leverage=10,
                                    position_size_pct=0.1)
        engine.account.open_short(3000.0, 0.3, leverage=10, ts="t0")
        engine.check_tick_exit(2810.0, ts="t1")  # 推进最低价 2810（激活移动止损）
        trade = engine.check_tick_exit(2905.0, ts="t2")  # 回升 >2810×1.03 触发
        assert trade is not None
        assert trade["reason"] == "trailing_stop"

    def test_liquidation_beats_stop_loss(self):
        """价格直接打到强平价以下 → 强平优先于止损"""
        engine = self._engine_with_long()
        # 10x 多仓强平价 = 3000×0.905 = 2715；2714 同时低于止损价 2850
        trade = engine.check_tick_exit(2714.0, ts="t1")
        assert trade is not None
        assert trade["side"] == "liquidation"
        assert trade["reason"] == "liquidation"

    def test_tick_updates_price_tracking(self):
        engine = self._engine_with_long()
        engine.check_tick_exit(3100.0, ts="t1")
        assert engine.account.position.highest_price == 3100.0
        engine.check_tick_exit(3050.0, ts="t2")
        assert engine.account.position.highest_price == 3100.0  # 不回落
        assert engine.account.position.lowest_price == 3050.0
