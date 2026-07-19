"""
本地模拟盘引擎
支持逐根 K 线驱动（用于 Streamlit 前端）
"""

from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from config import Config
from strategies.base import BaseStrategy, Signal
from risk.rules import RiskEngine
from execution import intrabar
from execution.trade_result import make_trade, reject_trade

logger = logging.getLogger("execution.paper")


class PaperAccount:
    """模拟账户 — 支持多空双向"""

    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        # 多头
        self.position = 0.0  # 多头持仓数量 (e.g. ETH)
        self.position_cost = 0.0  # 多头持仓总成本（用于计算盈亏）
        # 空头
        self.short_position = 0.0  # 空头持仓数量
        self.short_position_cost = 0.0  # 空头开仓总收入（平仓时计算 PnL）
        # 通用
        self.trades: list[dict] = []
        self.equity_history: list[dict] = []
        self.last_price: float = 0.0
        # 持仓期间最高/最低价跟踪（tick 级移动止损用，0.0 = 未跟踪）
        self.highest_price: float = 0.0
        self.lowest_price: float = 0.0

    @property
    def is_flat(self) -> bool:
        """是否空仓（多空均为空）"""
        return self.position < 0.001 and self.short_position < 0.001

    @property
    def equity(self) -> float:
        """当前总权益 = 现金 + 多头市值 + 空头未实现盈亏"""
        return self.balance + self.position * self.last_price + self._short_unrealized_pnl()

    def _short_unrealized_pnl(self) -> float:
        """空头未实现盈亏"""
        if self.short_position > 0 and self.short_position_cost > 0:
            avg_entry = self.short_position_cost / self.short_position
            return self.short_position * (avg_entry - self.last_price)
        return 0.0

    @property
    def unrealized_pnl(self) -> float:
        """总未实现盈亏 (USD)"""
        long_pnl = self.position * (self.last_price - (self.position_cost / self.position if self.position > 0 else 0))
        short_pnl = self._short_unrealized_pnl()
        return long_pnl + short_pnl

    @property
    def total_realized_pnl(self) -> float:
        """所有已平仓交易的总盈亏"""
        return round(sum(t.get("pnl", 0) for t in self.trades if t.get("pnl") is not None), 2)

    @property
    def unrealized_pnl_pct(self) -> float:
        """未实现盈亏 (%) — 根据当前持仓类型计算"""
        if self.position > 0.001 and self.position_cost > 0:
            avg_price = self.position_cost / self.position
            return (self.last_price - avg_price) / avg_price * 100
        if self.short_position > 0.001 and self.short_position_cost > 0:
            avg_entry = self.short_position_cost / self.short_position
            return (avg_entry - self.last_price) / avg_entry * 100
        return 0.0

    def update_price(self, price: float, ts: Optional[str] = None):
        """更新最新价格，记录权益历史（ts 为 bar 时间，回放时必传）"""
        self.last_price = price
        # 更新持仓期间最高/最低价跟踪（开仓时重置，此处持续推进）
        if self.highest_price <= 0:
            self.highest_price = price
        if self.lowest_price <= 0:
            self.lowest_price = price
        self.highest_price = max(self.highest_price, price)
        self.lowest_price = min(self.lowest_price, price)
        self.equity_history.append({
            "time": ts or datetime.now(timezone.utc).isoformat(),
            "price": price,
            "equity": self.equity,
        })
        # 只保留最近 1000 条
        if len(self.equity_history) > 1000:
            self.equity_history = self.equity_history[-1000:]

    def execute_buy(self, price: float, size: float, fee_rate: float = 0.001, ts: Optional[str] = None) -> dict:
        """执行买入，返回 trade dict"""
        cost = price * size
        fee = cost * fee_rate
        if cost + fee > self.balance:
            # 余量不足时按余额买入
            size = (self.balance - fee) / price
            cost = price * size
            fee = cost * fee_rate

        self.balance -= (cost + fee)
        self.position += size
        self.position_cost += cost
        # 开仓/加仓后从入场价重新跟踪最高/最低价（移动止损基准）
        self.highest_price = price
        self.lowest_price = price

        trade = make_trade(
            "buy", price, size, fee=fee, time=ts,
            cost=round(cost, 2),
            balance_after=round(self.balance, 2),
        )
        self.trades.append(trade)
        return trade

    def execute_sell(self, price: float, size: Optional[float] = None, fee_rate: float = 0.001, ts: Optional[str] = None) -> dict:
        """执行卖出，返回 trade dict"""
        size = size if size is not None else self.position
        if size > self.position:
            size = self.position
        if size <= 0:
            return reject_trade("sell", "no position", time=ts)

        revenue = price * size
        fee = revenue * fee_rate
        cost_portion = self.position_cost * (size / self.position) if self.position > 0 else 0
        pnl = revenue - fee - cost_portion

        self.balance += (revenue - fee)
        self.position -= size
        self.position_cost -= cost_portion

        trade = make_trade(
            "sell", price, size, fee=fee, pnl=pnl, time=ts,
            balance_after=round(self.balance, 2),
        )
        self.trades.append(trade)
        # 全仓卖出后重置成本与价格跟踪
        if self.position <= 0.001:
            self.position = 0.0
            self.position_cost = 0.0
            self.highest_price = 0.0
            self.lowest_price = 0.0
        return trade

    def execute_short(self, price: float, size: float, fee_rate: float = 0.001, ts: Optional[str] = None) -> dict:
        """执行开空（卖空），返回 trade dict"""
        revenue = price * size
        fee = revenue * fee_rate
        margin_required = revenue / 5.0  # 默认 5 倍杠杆保证金
        if self.balance < fee + margin_required:
            # 余额不足时缩小仓位
            max_revenue = (self.balance - fee) * 5.0
            if max_revenue <= 0:
                return reject_trade("short", "insufficient balance", time=ts)
            size = max_revenue / price
            revenue = price * size
            fee = revenue * fee_rate

        self.balance += (revenue - fee)  # 卖空获得资金入账
        self.short_position += size
        self.short_position_cost += revenue
        # 开空/加空后从入场价重新跟踪最高/最低价（移动止损基准）
        self.highest_price = price
        self.lowest_price = price

        trade = make_trade(
            "short", price, size, fee=fee, time=ts,
            revenue=round(revenue, 2),
            balance_after=round(self.balance, 2),
        )
        self.trades.append(trade)
        return trade

    def execute_cover(self, price: float, size: Optional[float] = None, fee_rate: float = 0.001, ts: Optional[str] = None) -> dict:
        """执行平空（买入平仓），支持部分平仓"""
        size = size if size is not None else self.short_position
        if size > self.short_position:
            size = self.short_position
        if size <= 0:
            return reject_trade("cover", "no short position", time=ts)

        cost = price * size
        fee = cost * fee_rate
        credit_portion = self.short_position_cost * (size / self.short_position)
        pnl = credit_portion - cost - fee

        self.balance -= (cost + fee)
        self.short_position -= size
        self.short_position_cost -= credit_portion

        trade = make_trade(
            "cover", price, size, fee=fee, pnl=pnl, time=ts,
            balance_after=round(self.balance, 2),
        )
        self.trades.append(trade)
        if self.short_position <= 0.001:
            self.short_position = 0.0
            self.short_position_cost = 0.0
            self.highest_price = 0.0
            self.lowest_price = 0.0
        return trade

    def to_dict(self) -> dict:
        """序列化为 JSON 友好 dict（给前端用）"""
        d = {
            "initial_balance": self.initial_balance,
            "balance": round(self.balance, 2),
            "position": round(self.position, 6),
            "position_cost": round(self.position_cost, 2),
            "short_position": round(self.short_position, 6),
            "short_position_cost": round(self.short_position_cost, 2),
            "last_price": round(self.last_price, 2),
            "equity": round(self.equity, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 2),
            "total_realized_pnl": round(self.total_realized_pnl, 2),
            "total_trades": len(self.trades),
            "trades": self.trades[-50:],
            "equity_history": self.equity_history[-200:],
        }
        return d

    def save_state(self, path: str = "data/paper_state.json"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        state = {
            "balance": self.balance,
            "position": self.position,
            "position_cost": self.position_cost,
            "short_position": self.short_position,
            "short_position_cost": self.short_position_cost,
            "last_price": self.last_price,
            "trades": self.trades[-100:],
        }
        with open(path, "w") as f:
            json.dump(state, f, indent=2)

    def load_state(self, path: str = "data/paper_state.json"):
        p = Path(path)
        if p.exists():
            with open(p) as f:
                state = json.load(f)
            self.balance = state.get("balance", self.initial_balance)
            self.position = state.get("position", 0)
            self.position_cost = state.get("position_cost", 0)
            self.short_position = state.get("short_position", 0)
            self.short_position_cost = state.get("short_position_cost", 0)
            self.last_price = state.get("last_price", 0)
            self.trades = state.get("trades", [])
            logger.info(f"已从 {path} 恢复模拟盘状态")

    def report(self):
        print(f"\n📋 模拟盘状态")
        print(f"  余额: ${self.balance:,.2f}")
        if self.position > 0.001:
            print(f"  多头持仓: {self.position:.6f}")
        if self.short_position > 0.001:
            print(f"  空头持仓: {self.short_position:.6f}")
        print(f"  最新价: ${self.last_price:,.2f}")
        print(f"  总权益: ${self.equity:,.2f}")
        print(f"  总交易: {len(self.trades)} 笔")
        if self.trades:
            winning = [t for t in self.trades if t.get("pnl", 0) > 0]
            print(f"  胜率: {len(winning)/len(self.trades)*100:.1f}%")


class PaperEngine:
    """模拟盘引擎 — 逐根 K 线驱动"""

    def __init__(self, cfg: Config, initial_balance: float = None, position_size_pct: float = None,
                 exit_params: dict = None):
        self.cfg = cfg
        init_bal = initial_balance if initial_balance is not None else 10000.0
        self.account = PaperAccount(initial_balance=init_bal)
        # max_single_order_pct 为小数比例（0.10 = 10%），与 config 语义一致
        self.position_size_pct = position_size_pct if position_size_pct is not None else cfg.risk.max_single_order_pct
        # tick 级退出参数覆盖（页面策略参数里的止损/止盈/移动止损），缺省回退 cfg.strategy.*
        self._exit_params = exit_params or {}

    def _exit_cfg(self, key: str) -> float:
        """退出参数：页面覆盖值优先，回退 cfg.strategy.*"""
        return self._exit_params.get(key, getattr(self.cfg.strategy, key))

    def run_bar(self, bar: pd.Series, strategy: BaseStrategy, risk_engine: Optional[RiskEngine] = None) -> dict:
        """
        处理一根新 K 线，执行完整模拟盘循环。

        流程:
        1. 更新价格
        2. 策略生成信号 (on_bar)
        3. 风控审核
        4. 执行信号
        5. 记录结果

        返回: 当前状态 dict (给前端用)
        """
        close_price = float(bar["close"])
        timestamp = bar.name if hasattr(bar, "name") else datetime.now(timezone.utc)
        bar_ts = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)

        # 1. 更新价格（权益历史按 bar 时间戳记录，回放不塌缩）
        self.account.update_price(close_price, ts=bar_ts)

        # 2. 策略信号
        signal = strategy.on_bar(bar)

        # 3. 风控审核
        risk_ok = True
        risk_reason = ""
        if risk_engine is not None:
            try:
                current_pos_pct = (self.account.position * close_price) / max(self.account.equity, 1)
                risk_ok, risk_reason = risk_engine.check_signal(
                    signal, current_equity=self.account.equity, current_position_pct=current_pos_pct
                )
            except Exception as e:
                risk_ok = False
                risk_reason = str(e)

        # 4. 执行信号
        trade = None
        if risk_ok and signal in (Signal.BUY, Signal.SELL, Signal.EXIT):
            fee_rate = self.cfg.trading.taker_fee / 100 if self.cfg.trading else 0.001

            if signal == Signal.BUY and self.account.position < 0.001:
                size = (self.account.balance * self.position_size_pct) / close_price
                trade = self.account.execute_buy(close_price, size, fee_rate, ts=bar_ts)
                if risk_engine:
                    risk_engine.record_trade_result(0)

            elif signal in (Signal.SELL, Signal.EXIT) and self.account.position > 0.001:
                _cost = self.account.position_cost  # 平仓前记录成本（平仓后会清零）
                trade = self.account.execute_sell(close_price, fee_rate=fee_rate, ts=bar_ts)
                if risk_engine and trade and "pnl" in trade:
                    pnl_pct = trade["pnl"] / max(_cost, 1) * 100
                    risk_engine.record_trade_result(pnl_pct)

        return {
            "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
            "price": close_price,
            "signal": signal.value,
            "risk_ok": risk_ok,
            "risk_reason": risk_reason,
            "trade": trade,
            "account": self.account.to_dict(),
        }

    def check_tick_exit(self, price: float, ts: Optional[str] = None,
                        risk_engine: Optional[RiskEngine] = None) -> Optional[dict]:
        """tick 级止损/止盈/移动止损检查（秒级心跳价格驱动）。

        持仓期间每个 tick 调用一次；触及退出条件立即平仓并返回 trade
        （trade["reason"] 标明触发原因），否则返回 None。多空均支持。
        退出参数与回测引擎共用 cfg.strategy.*（止损 > 移动止损 > 止盈）。
        """
        acct = self.account
        fee_rate = self.cfg.trading.taker_fee if self.cfg.trading else 0.001

        if acct.position > 0.001:
            entry = acct.position_cost / acct.position
            cost = acct.position_cost  # 平仓前记录（平仓后清零）
            reason = intrabar.check_tick_exit(
                price, direction="long", entry_price=entry,
                highest_price=acct.highest_price, lowest_price=acct.lowest_price,
                stop_loss_pct=self._exit_cfg("stop_loss_pct"),
                take_profit_pct=self._exit_cfg("take_profit_pct"),
                trailing_activation_pct=self._exit_cfg("trailing_stop_activation"),
                trailing_distance_pct=self._exit_cfg("trailing_stop_distance"),
            )
            if reason is None:
                return None
            trade = acct.execute_sell(price, fee_rate=fee_rate, ts=ts)
            trade["reason"] = reason
            acct.update_price(price, ts=ts)  # 记录退出后的真实权益
            if risk_engine and trade.get("pnl") is not None:
                risk_engine.record_trade_result(trade["pnl"] / max(cost, 1) * 100)
            logger.info(f"⚡ tick 级 {reason} 触发: 多仓 @ ${price:.2f}")
            return trade

        if acct.short_position > 0.001:
            entry = acct.short_position_cost / acct.short_position
            credit = acct.short_position_cost
            reason = intrabar.check_tick_exit(
                price, direction="short", entry_price=entry,
                highest_price=acct.highest_price, lowest_price=acct.lowest_price,
                stop_loss_pct=self._exit_cfg("stop_loss_pct"),
                take_profit_pct=self._exit_cfg("take_profit_pct"),
                trailing_activation_pct=self._exit_cfg("trailing_stop_activation"),
                trailing_distance_pct=self._exit_cfg("trailing_stop_distance"),
            )
            if reason is None:
                return None
            trade = acct.execute_cover(price, fee_rate=fee_rate, ts=ts)
            trade["reason"] = reason
            acct.update_price(price, ts=ts)
            if risk_engine and trade.get("pnl") is not None:
                risk_engine.record_trade_result(trade["pnl"] / max(credit, 1) * 100)
            logger.info(f"⚡ tick 级 {reason} 触发: 空仓 @ ${price:.2f}")
            return trade

        return None

    def run(self):
        """CLI 模式占位 — 前端驱动时用 run_bar"""
        logger.info("🚀 启动模拟盘模式")
        print("\n⚠️  模拟盘模式需要从 Streamlit 前端驱动")
        print("   运行方式:")
        print("   cd frontend && streamlit run app.py")
        print("   然后打开 Paper Trading 页面\n")
        self.account.report()
