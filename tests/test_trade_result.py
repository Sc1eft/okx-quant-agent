# tests/test_trade_result.py
"""统一 trade dict（execution/trade_result.py）schema 测试"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.trade_result import make_trade, reject_trade, is_rejected
from execution.paper import PaperAccount
from execution.futures_paper import FuturesAccount


class TestMakeTrade:
    def test_core_fields_present(self):
        t = make_trade("buy", 3450.0, 0.5, fee=1.725)
        assert t["side"] == "buy"
        assert t["price"] == 3450.0
        assert t["size"] == 0.5
        assert t["fee"] == 1.725
        assert "time" in t
        # 开仓不带 pnl 键（消费方以 "pnl" in trade 判定平仓）
        assert "pnl" not in t

    def test_pnl_only_when_given(self):
        t = make_trade("sell", 3450.0, 0.5, fee=1.725, pnl=25.5)
        assert t["pnl"] == 25.5

    def test_extra_fields_passthrough(self):
        t = make_trade("open_long", 3000.0, 1.0, fee=0.6,
                       margin=300.0, leverage=10, wallet_after=9699.4)
        assert t["margin"] == 300.0
        assert t["leverage"] == 10
        assert t["wallet_after"] == 9699.4


class TestRejectTrade:
    def test_shape(self):
        t = reject_trade("open_long", "exist_opposite")
        assert t["side"] == "open_long"
        assert t["note"] == "exist_opposite"
        assert t["size"] == 0.0
        assert t["price"] == 0.0
        assert "time" in t

    def test_is_rejected(self):
        assert is_rejected(reject_trade("sell", "no position")) is True
        assert is_rejected(make_trade("buy", 1.0, 1.0)) is False
        assert is_rejected(None) is True
        assert is_rejected({}) is True


class TestAccountAlignment:
    """两个账户层的返回统一走 trade_result schema"""

    def test_spot_buy_schema(self):
        acct = PaperAccount(initial_balance=10000.0)
        t = acct.execute_buy(price=3000.0, size=1.0)
        assert {"time", "side", "price", "size", "fee"} <= set(t)
        assert "pnl" not in t  # 开仓无 pnl
        assert not is_rejected(t)

    def test_spot_sell_reject_schema(self):
        acct = PaperAccount(initial_balance=10000.0)
        t = acct.execute_sell(price=3000.0)  # 无持仓
        assert is_rejected(t)
        assert {"time", "side", "price", "size", "fee", "note"} <= set(t)

    def test_futures_open_reject_schema(self):
        acct = FuturesAccount(wallet_balance=100.0)
        t = acct.open_long(price=3000.0, size=50.0, leverage=1)  # 手续费即超余额
        assert is_rejected(t)
        assert t["note"] == "insufficient_balance"
        assert {"time", "side", "price", "size", "fee", "note"} <= set(t)

    def test_futures_close_has_pnl(self):
        acct = FuturesAccount(wallet_balance=10000.0)
        acct.open_long(price=3000.0, size=1.0, leverage=10)
        t = acct.close_position(price=3100.0)
        assert not is_rejected(t)
        assert "pnl" in t
        assert {"time", "side", "price", "size", "fee"} <= set(t)

    def test_futures_close_reject_no_position(self):
        acct = FuturesAccount(wallet_balance=10000.0)
        t = acct.close_position(price=3000.0)
        assert is_rejected(t)
        assert t["note"] == "no_position"


class TestBarTimestamp:
    """回放场景：trade/equity_history 必须用 bar 时间而非墙钟时间（否则图表 x 轴塌缩）"""

    BAR_TS = "2026-01-01T00:00:00+08:00"

    def test_make_trade_with_time(self):
        t = make_trade("buy", 3000.0, 1.0, time=self.BAR_TS)
        assert t["time"] == self.BAR_TS

    def test_reject_trade_with_time(self):
        t = reject_trade("sell", "no position", time=self.BAR_TS)
        assert t["time"] == self.BAR_TS

    def test_spot_buy_uses_bar_ts(self):
        acct = PaperAccount(initial_balance=10000.0)
        t = acct.execute_buy(price=3000.0, size=1.0, ts=self.BAR_TS)
        assert t["time"] == self.BAR_TS

    def test_spot_update_price_uses_bar_ts(self):
        acct = PaperAccount(initial_balance=10000.0)
        acct.update_price(3000.0, ts=self.BAR_TS)
        assert acct.equity_history[-1]["time"] == self.BAR_TS

    def test_futures_open_uses_bar_ts(self):
        acct = FuturesAccount(wallet_balance=10000.0)
        t = acct.open_long(price=3000.0, size=1.0, leverage=10, ts=self.BAR_TS)
        assert t["time"] == self.BAR_TS

    def test_futures_update_price_uses_bar_ts(self):
        acct = FuturesAccount(wallet_balance=10000.0)
        acct.update_price(3000.0, ts=self.BAR_TS)
        assert acct.equity_history[-1]["time"] == self.BAR_TS

    def test_default_time_still_wallclock(self):
        """不传 ts 时保持原行为（实盘逐根 bar 调用场景）"""
        t = make_trade("buy", 3000.0, 1.0)
        assert t["time"] != self.BAR_TS
        assert "T" in t["time"]  # isoformat
