"""
合约模拟盘引擎
支持 USDT 本位永续合约模拟：杠杆、保证金、强平价计算、多空双向

数据流与现有 PaperEngine 一致：
  K 线 → 策略信号 → 风控 → 合约执行 → 状态 dict → 前端

OKX 标准:
  - 合约面值 = 1（1 张 = 1 单位标的币）
  - 仓位价值 = size × 入场价 (USDT)
  - 保证金 = 仓位价值 / 杠杆
  - 多仓 PnL = size × (当前价 − 入场价)
  - 空仓 PnL = size × (入场价 − 当前价)
  - 强平价 (逐仓): entry × (1 ∓ 1/杠杆 ± 维持保证金率)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import inf
from pathlib import Path
from typing import Literal, Optional

import pandas as pd

from config import Config
from strategies.base import BaseStrategy, Signal
from risk.rules import RiskEngine

logger = logging.getLogger("execution.futures_paper")

# ─────────────────────────────────────────────
# 帮助函数
# ─────────────────────────────────────────────


def _mmr_for_leverage(leverage: int) -> float:
    """根据杠杆倍数返回 OKX ETH-USDT 维持保证金率"""
    if leverage <= 5:
        return 0.004
    if leverage <= 10:
        return 0.005
    if leverage <= 20:
        return 0.010
    if leverage <= 50:
        return 0.025
    return 0.050


def calc_liquidation_price(
    entry_price: float,
    direction: Literal["long", "short"],
    leverage: int,
    maintenance_margin_rate: float | None = None,
) -> float:
    """计算逐仓强平价 (USDT 本位永续)

    公式 (OKX):
      多仓: liq = entry × (1 − 1/leverage + mmr)
      空仓: liq = entry × (1 + 1/leverage − mmr)
    """
    mmr = maintenance_margin_rate if maintenance_margin_rate is not None else _mmr_for_leverage(leverage)
    if direction == "long":
        return entry_price * (1 - 1.0 / leverage + mmr)
    else:
        return entry_price * (1 + 1.0 / leverage - mmr)


# ─────────────────────────────────────────────
# 持仓
# ─────────────────────────────────────────────


@dataclass
class FuturesPosition:
    """合约持仓"""

    direction: Literal["long", "short"]
    size: float = 0.0                   # 持仓数量 (标的币单位)
    entry_price: float = 0.0            # 平均入场价
    leverage: int = 10
    position_value: float = 0.0         # size × entry_price (USDT)
    margin: float = 0.0                 # position_value / leverage
    maintenance_margin_rate: float = 0.005

    # 风控跟踪
    highest_price: float = 0.0
    lowest_price: float = inf
    bars_held: int = 0

    def __post_init__(self):
        self.entry_price = float(self.entry_price)
        self.size = float(self.size)

    # ── 计算属性 ──

    @property
    def liquidation_price(self) -> float:
        return calc_liquidation_price(
            self.entry_price, self.direction, self.leverage, self.maintenance_margin_rate,
        )

    @property
    def is_active(self) -> bool:
        return self.size > 1e-8

    # ── PnL ──

    def unrealized_pnl(self, current_price: float) -> float:
        if not self.is_active:
            return 0.0
        if self.direction == "long":
            return self.size * (current_price - self.entry_price)
        return self.size * (self.entry_price - current_price)

    def unrealized_pnl_pct(self, current_price: float) -> float:
        if self.margin <= 0:
            return 0.0
        return self.unrealized_pnl(current_price) / self.margin * 100.0

    def roi_pct(self, current_price: float) -> float:
        """总投资回报率 (含杠杆效果)"""
        return self.unrealized_pnl_pct(current_price)

    # ── 保证金率与爆仓检查 ──

    def margin_rate(self, current_price: float) -> float:
        """当前保证金率 (%)，低于维持保证金率即触发强平"""
        upnl = self.unrealized_pnl(current_price)
        margin_balance = self.margin + upnl
        if margin_balance <= 0:
            return 0.0
        return margin_balance / self.position_value * 100.0

    def is_liquidated(self, current_price: float) -> bool:
        """是否触发强平"""
        if self.direction == "long":
            return current_price <= self.liquidation_price
        return current_price >= self.liquidation_price

    # ── 序列化 ──

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "size": round(self.size, 6),
            "entry_price": round(self.entry_price, 2),
            "leverage": self.leverage,
            "position_value": round(self.position_value, 2),
            "margin": round(self.margin, 2),
            "liquidation_price": round(self.liquidation_price, 2),
            "bars_held": self.bars_held,
            "highest_price": round(self.highest_price, 2),
            "lowest_price": round(self.lowest_price, 2) if self.lowest_price != inf else 0,
        }


# ─────────────────────────────────────────────
# 合约账户
# ─────────────────────────────────────────────


class FuturesAccount:
    """合约账户 — 管理钱包余额、持仓、保证金"""

    def __init__(self, wallet_balance: float = 10000.0, taker_fee_rate: float = 0.001,
                 maker_fee_rate: float = 0.0002):
        self.wallet_balance = wallet_balance
        self.initial_wallet = wallet_balance
        self.taker_fee_rate = taker_fee_rate
        self.maker_fee_rate = maker_fee_rate
        self.trades: list[dict] = []
        self.equity_history: list[dict] = []
        self.last_price: float = 0.0
        self.position: FuturesPosition | None = None

    def __repr__(self) -> str:
        return (
            f"FuturesAccount(wallet={self.wallet_balance:.2f}, "
            f"equity={self.total_equity:.2f}, "
            f"position={'none' if not self.position else f'{self.position.direction} {self.position.size:.4f}×{self.position.leverage}'})"
        )

    def _fee_rate(self, prefer_limit: bool = False) -> float:
        """限价单挂单成交走 maker 费率，否则走 taker 费率"""
        return self.maker_fee_rate if prefer_limit else self.taker_fee_rate

    # ── 计算属性 ──

    @property
    def used_margin(self) -> float:
        return self.position.margin if self.position and self.position.is_active else 0.0

    @property
    def available_balance(self) -> float:
        return self.wallet_balance - self.used_margin

    @property
    def total_equity(self) -> float:
        """总权益 = 钱包余额 + 未实现盈亏"""
        upnl = self.position.unrealized_pnl(self.last_price) if self.position and self.position.is_active else 0.0
        return self.wallet_balance + upnl

    @property
    def total_realized_pnl(self) -> float:
        total = sum(t.get("pnl", 0) for t in self.trades if t.get("pnl") is not None)
        return round(total, 2)

    @property
    def is_flat(self) -> bool:
        return not self.position or not self.position.is_active

    @property
    def position_side(self) -> str:
        if self.position and self.position.is_active:
            return self.position.direction
        return "flat"

    # ── 价格更新 ──

    def update_price(self, price: float):
        self.last_price = price
        self.equity_history.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "price": price,
            "equity": self.total_equity,
        })
        if len(self.equity_history) > 1000:
            self.equity_history = self.equity_history[-1000:]

    # ── 开仓 ──

    def open_long(self, price: float, size: float, leverage: int, prefer_limit: bool = False) -> dict:
        """开多 / 加多"""
        if self.position and self.position.direction == "short" and self.position.is_active:
            return {"note": "exist_opposite", "side": "open_long"}

        pos_value = price * size
        fee = pos_value * self._fee_rate(prefer_limit)
        margin = pos_value / leverage

        if self.wallet_balance < margin + fee:
            max_pos_value = (self.wallet_balance - fee) * leverage
            if max_pos_value <= 0:
                return {"note": "insufficient_balance", "side": "open_long"}
            size = max_pos_value / price
            pos_value = price * size
            fee = pos_value * self._fee_rate(prefer_limit)
            margin = pos_value / leverage

        self.wallet_balance -= fee

        if self.position and self.position.direction == "long" and self.position.is_active:
            # 加仓 — 加权平均入场价
            total_size = self.position.size + size
            total_value = self.position.position_value + pos_value
            self.position.entry_price = total_value / total_size
            self.position.size = total_size
            self.position.position_value = total_value
            self.position.margin = total_value / self.position.leverage
            self.position.highest_price = max(self.position.highest_price, price)
        else:
            self.position = FuturesPosition(
                direction="long",
                size=size,
                entry_price=price,
                leverage=leverage,
                position_value=pos_value,
                margin=margin,
                highest_price=price,
                maintenance_margin_rate=_mmr_for_leverage(leverage),
            )

        trade = {
            "time": datetime.now(timezone.utc).isoformat(),
            "side": "open_long",
            "price": round(price, 2),
            "size": round(size, 6),
            "margin": round(margin, 2),
            "fee": round(fee, 4),
            "leverage": leverage,
            "wallet_after": round(self.wallet_balance, 2),
        }
        self.trades.append(trade)
        return trade

    def open_short(self, price: float, size: float, leverage: int, prefer_limit: bool = False) -> dict:
        """开空 / 加空"""
        if self.position and self.position.direction == "long" and self.position.is_active:
            return {"note": "exist_opposite", "side": "open_short"}

        pos_value = price * size
        fee = pos_value * self._fee_rate(prefer_limit)
        margin = pos_value / leverage

        if self.wallet_balance < margin + fee:
            max_pos_value = (self.wallet_balance - fee) * leverage
            if max_pos_value <= 0:
                return {"note": "insufficient_balance", "side": "open_short"}
            size = max_pos_value / price
            pos_value = price * size
            fee = pos_value * self._fee_rate(prefer_limit)
            margin = pos_value / leverage

        self.wallet_balance -= fee

        if self.position and self.position.direction == "short" and self.position.is_active:
            total_size = self.position.size + size
            total_value = self.position.position_value + pos_value
            self.position.entry_price = total_value / total_size
            self.position.size = total_size
            self.position.position_value = total_value
            self.position.margin = total_value / self.position.leverage
            self.position.lowest_price = min(self.position.lowest_price, price)
        else:
            self.position = FuturesPosition(
                direction="short",
                size=size,
                entry_price=price,
                leverage=leverage,
                position_value=pos_value,
                margin=margin,
                lowest_price=price,
                maintenance_margin_rate=_mmr_for_leverage(leverage),
            )

        trade = {
            "time": datetime.now(timezone.utc).isoformat(),
            "side": "open_short",
            "price": round(price, 2),
            "size": round(size, 6),
            "margin": round(margin, 2),
            "fee": round(fee, 4),
            "leverage": leverage,
            "wallet_after": round(self.wallet_balance, 2),
        }
        self.trades.append(trade)
        return trade

    # ── 平仓 ──

    def close_position(self, price: float, size: float | None = None, prefer_limit: bool = False) -> dict:
        """平仓 (多仓卖出 / 空仓买入)"""
        if not self.position or not self.position.is_active:
            return {"note": "no_position", "side": "close"}

        close_size = size if size is not None else self.position.size
        if close_size > self.position.size:
            close_size = self.position.size
        if close_size <= 0:
            return {"note": "invalid_size", "side": "close"}

        pos = self.position
        fee_rate = self._fee_rate(prefer_limit)

        if pos.direction == "long":
            revenue = price * close_size
            fee = revenue * fee_rate
            cost_portion = pos.position_value * (close_size / pos.size)
            pnl = revenue - fee - cost_portion
            # 开仓时保证金未从钱包扣除（仅扣手续费），
            # 平仓只入账已实现盈亏，不能把名义价值计入钱包
            self.wallet_balance += pnl
        else:  # short
            cost = price * close_size
            fee = cost * fee_rate
            credit_portion = pos.position_value * (close_size / pos.size)
            pnl = credit_portion - cost - fee
            # 与多头一致：平仓只入账已实现盈亏
            self.wallet_balance += pnl

        # 更新持仓
        remaining_size = pos.size - close_size
        remaining_value = pos.position_value * (remaining_size / pos.size) if pos.size > 0 else 0

        pos.size = remaining_size
        pos.position_value = remaining_value
        if remaining_size > 1e-8:
            pos.margin = remaining_value / pos.leverage
        else:
            pos.size = 0.0
            pos.position_value = 0.0
            pos.margin = 0.0

        trade = {
            "time": datetime.now(timezone.utc).isoformat(),
            "side": "close_short" if pos.direction == "short" else "close_long",
            "price": round(price, 2),
            "size": round(close_size, 6),
            "pnl": round(pnl, 2),
            "fee": round(fee, 4),
            "wallet_after": round(self.wallet_balance, 2),
            "leverage": pos.leverage,
        }
        self.trades.append(trade)
        return trade

    def close_all(self, price: float, prefer_limit: bool = False) -> dict | None:
        """全额平仓"""
        if not self.position or not self.position.is_active:
            return None
        return self.close_position(price, prefer_limit=prefer_limit)

    # ── 爆仓 ──

    def liquidate(self, price: float) -> dict | None:
        """强平 — 剩余资产归零 / 按破产价处理"""
        if not self.position or not self.position.is_active:
            return None
        pos = self.position
        upnl = pos.unrealized_pnl(price)
        margin_remaining = max(pos.margin + upnl, 0.0)
        lost = pos.margin - margin_remaining

        # 破产后钱包扣除损失
        self.wallet_balance -= lost
        self.wallet_balance = max(self.wallet_balance, 0)

        trade = {
            "time": datetime.now(timezone.utc).isoformat(),
            "side": "liquidation",
            "direction": pos.direction,
            "price": round(price, 2),
            "size": round(pos.size, 6),
            "pnl": round(-pos.margin, 2),
            "margin_lost": round(lost, 2),
            "wallet_after": round(self.wallet_balance, 2),
            "leverage": pos.leverage,
        }

        pos.size = 0.0
        pos.position_value = 0.0
        pos.margin = 0.0

        self.trades.append(trade)
        return trade

    # ── 序列化 ──

    def to_dict(self) -> dict:
        pos = self.position
        return {
            "wallet_balance": round(self.wallet_balance, 2),
            "available_balance": round(self.available_balance, 2),
            "used_margin": round(self.used_margin, 2),
            "equity": round(self.total_equity, 2),
            "total_realized_pnl": round(self.total_realized_pnl, 2),
            "unrealized_pnl": round(pos.unrealized_pnl(self.last_price), 2) if pos and pos.is_active else 0.0,
            "unrealized_pnl_pct": round(pos.unrealized_pnl_pct(self.last_price), 2) if pos and pos.is_active else 0.0,
            "direction": pos.direction if pos and pos.is_active else "flat",
            "position": round(pos.size, 6) if pos and pos.is_active else 0.0,
            "entry_price": round(pos.entry_price, 2) if pos and pos.is_active else 0.0,
            "leverage": pos.leverage if pos and pos.is_active else 0,
            "liquidation_price": round(pos.liquidation_price, 2) if pos and pos.is_active else 0.0,
            "position_value": round(pos.position_value, 2) if pos and pos.is_active else 0.0,
            "margin_rate": round(pos.margin_rate(self.last_price), 4) if pos and pos.is_active else 0.0,
            "trades": self.trades[-50:],
            "equity_history": self.equity_history[-200:],
        }

    def report(self):
        """打印账户摘要 (CLI)"""
        print(f"\n📋 合约模拟盘状态")
        print(f"  钱包余额: ${self.wallet_balance:,.2f}")
        print(f"  可用余额: ${self.available_balance:,.2f}")
        print(f"  占用保证金: ${self.used_margin:,.2f}")
        if self.position and self.position.is_active:
            p = self.position
            print(f"  持仓: {p.direction.upper()} {p.size:.6f} × {p.leverage}x")
            print(f"  入场价: ${p.entry_price:,.2f}")
            print(f"  强平价: ${p.liquidation_price:,.2f}")
            print(f"  未实现盈亏: ${p.unrealized_pnl(self.last_price):,.2f}")
        print(f"  总权益: ${self.total_equity:,.2f}")
        print(f"  总交易: {len(self.trades)} 笔")


# ─────────────────────────────────────────────
# 合约模拟引擎
# ─────────────────────────────────────────────


class FuturesPaperEngine:
    """合约模拟盘引擎 — 逐根 K 线驱动"""

    def __init__(
        self,
        cfg: Config,
        wallet_balance: float | None = None,
        leverage: int | None = None,
        position_size_pct: float | None = None,
    ):
        self.cfg = cfg
        bal = wallet_balance if wallet_balance is not None else 10000.0
        self.account = FuturesAccount(wallet_balance=bal)
        self.leverage = leverage if leverage is not None else cfg.futures.leverage
        self.position_size_pct = position_size_pct if position_size_pct is not None else cfg.risk.max_single_order_pct

    def run_bar(
        self,
        bar: pd.Series,
        strategy: BaseStrategy,
        risk_engine: RiskEngine | None = None,
    ) -> dict:
        """
        处理一根新 K 线。

        流程:
          1. 更新价格 (含强平检查)
          2. 策略生成信号
          3. 风控审核
          4. 信号 → 合约执行 (开多/开空/平仓)
          5. 返回状态 dict

        信号映射:
          Signal.BUY  → 无仓→开多 | 空仓→平空
          Signal.SELL → 无仓→开空 | 多仓→平多
          Signal.EXIT → 平仓
        """
        close_price = float(bar["close"])

        # 1. 更新价格
        self.account.update_price(close_price)

        # 1b. 强平检查
        liq_event = None
        if self.account.position and self.account.position.is_active:
            if self.account.position.is_liquidated(close_price):
                liq_event = self.account.liquidate(close_price)
                logger.warning(
                    f"💥 强平触发! {self.account.position.direction.upper()} "
                    f"@ ${close_price:.2f} (强平价 ${self.account.position.liquidation_price:.2f})"
                )

        # 2. 策略信号
        signal = strategy.on_bar(bar)

        # 3. 风控审核
        risk_ok = True
        risk_reason = ""
        if risk_engine is not None and signal in (Signal.BUY, Signal.SELL):
            pos_val = self.account.position.position_value if self.account.position and self.account.position.is_active else 0
            position_pct = pos_val / max(self.account.total_equity, 1) * 100
            try:
                risk_ok, risk_reason = risk_engine.check_signal(
                    signal, current_equity=self.account.total_equity,
                    current_position_pct=position_pct,
                )
            except Exception as e:
                risk_ok = False
                risk_reason = str(e)

        # 4. 执行信号
        trade = None
        if risk_ok and not liq_event:
            if signal in (Signal.BUY, Signal.SELL, Signal.EXIT):
                trade = self._execute_signal(signal, close_price)

        # 5. 风控记录
        if trade and risk_engine:
            pnl = trade.get("pnl")
            if pnl is not None:
                pnl_pct = pnl / max(self.account.used_margin, 1) * 100
                risk_engine.record_trade_result(pnl_pct)

        return {
            "timestamp": bar.name.isoformat() if hasattr(bar.name, "isoformat") else str(bar.name),
            "price": close_price,
            "signal": signal.value,
            "risk_ok": risk_ok,
            "risk_reason": risk_reason,
            "trade": trade,
            "liquidation": liq_event,
            "account": self.account.to_dict(),
        }

    def _execute_signal(self, signal: Signal, price: float) -> dict | None:
        """将策略信号映射为合约操作"""
        account = self.account

        # ── EXIT: 平所有仓 ──
        if signal == Signal.EXIT:
            return account.close_all(price)

        # ── BUY ──
        if signal == Signal.BUY:
            if account.is_flat:
                # 开多
                pos_value = account.wallet_balance * self.position_size_pct * self.leverage
                size = pos_value / price
                return account.open_long(price, size, self.leverage)
            elif account.position_side == "short":
                # 平空 — 全平并开多 (翻转)
                close_trade = account.close_all(price)
                if close_trade and close_trade.get("note", "").startswith("no_"):
                    return close_trade
                pos_value = account.wallet_balance * self.position_size_pct * self.leverage
                size = pos_value / price
                open_trade = account.open_long(price, size, self.leverage)
                if close_trade and open_trade:
                    return {**close_trade, **{f"next_{k}": v for k, v in open_trade.items()}}
                return open_trade
            # 已有多仓 → 忽略 (不重复开)
            return None

        # ── SELL ──
        if signal == Signal.SELL:
            if account.is_flat:
                # 开空
                pos_value = account.wallet_balance * self.position_size_pct * self.leverage
                size = pos_value / price
                return account.open_short(price, size, self.leverage)
            elif account.position_side == "long":
                # 平多并开空
                close_trade = account.close_all(price)
                if close_trade and close_trade.get("note", "").startswith("no_"):
                    return close_trade
                pos_value = account.wallet_balance * self.position_size_pct * self.leverage
                size = pos_value / price
                open_trade = account.open_short(price, size, self.leverage)
                if close_trade and open_trade:
                    return {**close_trade, **{f"next_{k}": v for k, v in open_trade.items()}}
                return open_trade
            # 已有空仓 → 忽略
            return None

        return None

    def run(self):
        """CLI 占位"""
        logger.info("🚀 合约模拟盘 (通过 Streamlit 前端驱动)")
        print("\n⚠️  合约模拟盘需要从 Streamlit 前端驱动")
        print("   运行方式:")
        print("   cd frontend && streamlit run app.py")
        print("   然后打开 Paper Trading 页面\n")
        self.account.report()
