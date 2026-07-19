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
from execution import intrabar
from execution.trade_result import make_trade, reject_trade

logger = logging.getLogger("execution.futures_paper")

# ─────────────────────────────────────────────
# 帮助函数
# ─────────────────────────────────────────────


def _to_bar_tz(ts: str | None) -> str | None:
    """tick 路径时间戳（UTC ISO，来自心跳）→ bar 路径时区（Asia/Shanghai）。

    同一本账（account.trades / equity_history）里 bar 成交记 +08:00、
    tick 成交记 +00:00 会导致前端按时间排序/展示错乱，入账前统一。
    """
    if not ts:
        return ts
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        return t.tz_convert("Asia/Shanghai").isoformat()
    except (ValueError, TypeError):
        return ts


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
        self.funding_fee_total: float = 0.0  # 累计资金费（负=净支出，正=净收入）
        self.funding_events: list[dict] = []

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

    def update_price(self, price: float, ts: str | None = None):
        self.last_price = price
        self.equity_history.append({
            "time": ts or datetime.now(timezone.utc).isoformat(),
            "price": price,
            "equity": self.total_equity,
        })
        if len(self.equity_history) > 1000:
            self.equity_history = self.equity_history[-1000:]

    # ── 资金费结算（永续合约每 8h 一次） ──

    def settle_funding(self, rate: float, mark_price: float, ts: str | None = None) -> dict | None:
        """按 OKX 永续规则结算一次资金费。

        fee = -方向符号 × 持仓名义价值 × rate：
          rate > 0 → 多方支付给空方；rate < 0 → 空方支付给多方。
        空仓时无结算，返回 None。
        """
        if not self.position or not self.position.is_active:
            return None
        notional = self.position.size * mark_price
        sign = 1.0 if self.position.direction == "long" else -1.0
        fee = -sign * notional * rate
        self.wallet_balance += fee
        self.funding_fee_total += fee
        event = {
            "time": ts or datetime.now(timezone.utc).isoformat(),
            "rate": rate,
            "direction": self.position.direction,
            "notional": round(notional, 2),
            "fee": round(fee, 4),
            "wallet_after": round(self.wallet_balance, 2),
        }
        self.funding_events.append(event)
        return event

    # ── 开仓 ──

    def open_long(self, price: float, size: float, leverage: int, prefer_limit: bool = False, ts: str | None = None) -> dict:
        """开多 / 加多"""
        if self.position and self.position.direction == "short" and self.position.is_active:
            return reject_trade("open_long", "exist_opposite", time=ts)

        pos_value = price * size
        fee = pos_value * self._fee_rate(prefer_limit)
        margin = pos_value / leverage

        if self.wallet_balance < margin + fee:
            max_pos_value = (self.wallet_balance - fee) * leverage
            if max_pos_value <= 0:
                return reject_trade("open_long", "insufficient_balance", time=ts)
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

        trade = make_trade(
            "open_long", price, size, fee=fee, time=ts,
            margin=round(margin, 2),
            leverage=leverage,
            wallet_after=round(self.wallet_balance, 2),
        )
        self.trades.append(trade)
        return trade

    def open_short(self, price: float, size: float, leverage: int, prefer_limit: bool = False, ts: str | None = None) -> dict:
        """开空 / 加空"""
        if self.position and self.position.direction == "long" and self.position.is_active:
            return reject_trade("open_short", "exist_opposite", time=ts)

        pos_value = price * size
        fee = pos_value * self._fee_rate(prefer_limit)
        margin = pos_value / leverage

        if self.wallet_balance < margin + fee:
            max_pos_value = (self.wallet_balance - fee) * leverage
            if max_pos_value <= 0:
                return reject_trade("open_short", "insufficient_balance", time=ts)
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

        trade = make_trade(
            "open_short", price, size, fee=fee, time=ts,
            margin=round(margin, 2),
            leverage=leverage,
            wallet_after=round(self.wallet_balance, 2),
        )
        self.trades.append(trade)
        return trade

    # ── 平仓 ──

    def close_position(self, price: float, size: float | None = None, prefer_limit: bool = False, ts: str | None = None) -> dict:
        """平仓 (多仓卖出 / 空仓买入)"""
        if not self.position or not self.position.is_active:
            return reject_trade("close", "no_position", time=ts)

        close_size = size if size is not None else self.position.size
        if close_size > self.position.size:
            close_size = self.position.size
        if close_size <= 0:
            return reject_trade("close", "invalid_size", time=ts)

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

        trade = make_trade(
            "close_short" if pos.direction == "short" else "close_long",
            price, close_size, fee=fee, pnl=pnl, time=ts,
            wallet_after=round(self.wallet_balance, 2),
            leverage=pos.leverage,
        )
        self.trades.append(trade)
        return trade

    def close_all(self, price: float, prefer_limit: bool = False, ts: str | None = None) -> dict | None:
        """全额平仓"""
        if not self.position or not self.position.is_active:
            return None
        return self.close_position(price, prefer_limit=prefer_limit, ts=ts)

    # ── 爆仓 ──

    def liquidate(self, price: float, ts: str | None = None) -> dict | None:
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

        trade = make_trade(
            "liquidation", price, pos.size, pnl=-pos.margin, time=ts,
            direction=pos.direction,
            margin_lost=round(lost, 2),
            wallet_after=round(self.wallet_balance, 2),
            leverage=pos.leverage,
        )

        pos.size = 0.0
        pos.position_value = 0.0
        pos.margin = 0.0

        self.trades.append(trade)
        return trade

    # ── 序列化 ──

    def to_dict(self) -> dict:
        pos = self.position
        return {
            "initial_balance": round(self.initial_wallet, 2),
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
            "funding_fee_total": round(self.funding_fee_total, 2),
            "funding_events": self.funding_events[-20:],
            "total_trades": len(self.trades),
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
        exit_params: dict | None = None,
    ):
        self.cfg = cfg
        bal = wallet_balance if wallet_balance is not None else 10000.0
        self.account = FuturesAccount(wallet_balance=bal)
        self.leverage = leverage if leverage is not None else cfg.futures.leverage
        self.position_size_pct = position_size_pct if position_size_pct is not None else cfg.risk.max_single_order_pct
        # tick 级退出参数覆盖（页面策略参数里的止损/止盈/移动止损），缺省回退 cfg.strategy.*
        self._exit_params = exit_params or {}
        # tick 级止损总开关：run_bar 按策略 use_engine_stops 属性逐根更新
        # （日线趋势等策略声明 False 时，退出由 bar 级信号负责，tick 级不插手）
        self._stops_enabled = True
        # 资金费：None 表示不结算（保持纯回测/旧行为）；由驱动方通过 set_funding_rate 注入
        self._funding_rate: float | None = None
        self._funding_watermark: pd.Timestamp | None = None  # 已结算到的 bar 开盘时刻

    def set_funding_rate(self, rate: float | None):
        """设置当前资金费率（小数，0.0001 = 0.01%）。None 则禁用资金费结算。"""
        self._funding_rate = rate

    @staticmethod
    def _funding_boundaries(start: pd.Timestamp, end: pd.Timestamp):
        """生成 (start, end] 内的资金费结算时刻（OKX: UTC 00/08/16 点 = 北京 08/16/00 点）。

        调用方传入的 bar 时间戳为 Asia/Shanghai 时区，直接按本地 0/8/16 点判定即可
        （UTC+8 且 8 整除 24，两套时区的 8h 网格完全重合）。
        """
        day = start.normalize()
        while day <= end:
            for hour in (0, 8, 16):
                b = day + pd.Timedelta(hours=hour)
                if start < b <= end:
                    yield b
            day += pd.Timedelta(days=1)

    def _settle_funding_if_due(self, bar: pd.Series) -> list[dict]:
        """对本 bar 区间内跨过的每个资金费结算时刻逐次结算（以 bar 开盘价为标记价）。"""
        events: list[dict] = []
        if self._funding_rate is None:
            return events
        try:
            bar_open_ts = pd.Timestamp(bar.name)
        except Exception:
            return events
        if self._funding_watermark is None:
            self._funding_watermark = bar_open_ts  # 首根 bar 只建立水位线
            return events
        mark = float(bar["open"]) if "open" in bar.index else float(bar["close"])
        for boundary in self._funding_boundaries(self._funding_watermark, bar_open_ts):
            event = self.account.settle_funding(
                self._funding_rate, mark, ts=boundary.isoformat())
            if event:
                events.append(event)
                logger.info(
                    f"💱 资金费结算: {event['direction']} 名义 ${event['notional']:,.2f} "
                    f"× rate {self._funding_rate:+.4%} = ${event['fee']:+.4f}")
        self._funding_watermark = bar_open_ts
        return events

    def _exit_cfg(self, key: str) -> float:
        """退出参数：页面覆盖值优先，回退 cfg.strategy.*"""
        return self._exit_params.get(key, getattr(self.cfg.strategy, key))

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
        bar_ts = bar.name.isoformat() if hasattr(bar.name, "isoformat") else str(bar.name)

        # 1. 更新价格（权益历史按 bar 时间戳记录，回放不塌缩）
        self.account.update_price(close_price, ts=bar_ts)

        # 1b. 强平检查
        liq_event = None
        if self.account.position and self.account.position.is_active:
            if self.account.position.is_liquidated(close_price):
                liq_event = self.account.liquidate(close_price, ts=bar_ts)
                logger.warning(
                    f"💥 强平触发! {self.account.position.direction.upper()} "
                    f"@ ${close_price:.2f} (强平价 ${self.account.position.liquidation_price:.2f})"
                )

        # 1c. 资金费结算（结算时刻落在本 bar 区间内的逐次结算，用持仓中的仓位）
        funding_events = self._settle_funding_if_due(bar)

        # 2. 策略信号
        signal = strategy.on_bar(bar)
        # 策略可声明 use_engine_stops=False 关闭 tick 级止损（check_tick_exit）
        self._stops_enabled = getattr(strategy, "use_engine_stops", True)

        # 3. 风控审核
        risk_ok = True
        risk_reason = ""
        if risk_engine is not None and signal in (Signal.BUY, Signal.SELL):
            pos_val = self.account.position.position_value if self.account.position and self.account.position.is_active else 0
            # check_signal 的 max_position_pct 为小数比例（0.50 = 50%），这里保持一致
            position_pct = pos_val / max(self.account.total_equity, 1)
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
        _used_margin_before = self.account.used_margin  # 平仓前记录（平仓后归零）
        if risk_ok and not liq_event:
            if signal in (Signal.BUY, Signal.SELL, Signal.EXIT):
                trade = self._execute_signal(signal, close_price, ts=bar_ts)

        # 5. 风控记录
        if trade and risk_engine:
            pnl = trade.get("pnl")
            if pnl is not None:
                pnl_pct = pnl / max(_used_margin_before, 1) * 100
                risk_engine.record_trade_result(pnl_pct)

        return {
            "timestamp": bar.name.isoformat() if hasattr(bar.name, "isoformat") else str(bar.name),
            "price": close_price,
            "signal": signal.value,
            "risk_ok": risk_ok,
            "risk_reason": risk_reason,
            "trade": trade,
            "liquidation": liq_event,
            "funding_events": funding_events,
            "funding_rate": self._funding_rate,
            "account": self.account.to_dict(),
        }

    def check_tick_exit(self, price: float, ts: str | None = None,
                        risk_engine: RiskEngine | None = None) -> dict | None:
        """tick 级强平 + 止损/止盈/移动止损检查（秒级心跳价格驱动）。

        持仓期间每个 tick 调用一次；触及条件立即平仓/强平并返回 trade
        （trade["reason"] 标明触发原因），否则返回 None。多空均支持。
        退出参数与回测引擎共用 cfg.strategy.*（止损 > 移动止损 > 止盈）。
        """
        ts = _to_bar_tz(ts)  # 与 bar 路径时间串统一时区（Asia/Shanghai）
        pos = self.account.position
        if not pos or not pos.is_active:
            return None

        # tick 价格推进最高/最低跟踪（开仓时初始化，此处持续更新）
        pos.highest_price = max(pos.highest_price, price)
        pos.lowest_price = min(pos.lowest_price, price)

        # 1. 强平检查（最优先，同 run_bar 的 bar 级逻辑）
        if pos.is_liquidated(price):
            trade = self.account.liquidate(price, ts=ts)
            if trade:
                trade["reason"] = "liquidation"
                self.account.update_price(price, ts=ts)
                logger.warning(f"💥 tick 级强平触发: {pos.direction} @ ${price:.2f}")
            return trade

        # 2. 止损/止盈/移动止损（策略声明 use_engine_stops=False 时跳过：
        #    退出由 bar 级 regime 翻转负责，tick 级分钟噪声止损对周线级
        #    持仓是干扰；上方强平检查不受此开关影响）
        reason = (
            intrabar.check_tick_exit(
                price, direction=pos.direction, entry_price=pos.entry_price,
                highest_price=pos.highest_price, lowest_price=pos.lowest_price,
                stop_loss_pct=self._exit_cfg("stop_loss_pct"),
                take_profit_pct=self._exit_cfg("take_profit_pct"),
                trailing_activation_pct=self._exit_cfg("trailing_stop_activation"),
                trailing_distance_pct=self._exit_cfg("trailing_stop_distance"),
            )
            if self._stops_enabled else None
        )
        if reason is None:
            return None

        used_margin = self.account.used_margin  # 平仓前记录（平仓后归零）
        trade = self.account.close_all(price, ts=ts)
        if trade:
            trade["reason"] = reason
            self.account.update_price(price, ts=ts)  # 记录退出后的真实权益
            if risk_engine:
                pnl = trade.get("pnl")
                if pnl is not None:
                    risk_engine.record_trade_result(pnl / max(used_margin, 1) * 100)
            logger.info(f"⚡ tick 级 {reason} 触发: {pos.direction} @ ${price:.2f}")
        return trade

    def _execute_signal(self, signal: Signal, price: float, ts: str | None = None) -> dict | None:
        """将策略信号映射为合约操作"""
        account = self.account

        # ── EXIT: 平所有仓 ──
        if signal == Signal.EXIT:
            return account.close_all(price, ts=ts)

        # ── BUY ──
        if signal == Signal.BUY:
            if account.is_flat:
                # 开多
                pos_value = account.wallet_balance * self.position_size_pct * self.leverage
                size = pos_value / price
                return account.open_long(price, size, self.leverage, ts=ts)
            elif account.position_side == "short":
                # 平空 — 全平并开多 (翻转)
                close_trade = account.close_all(price, ts=ts)
                if close_trade and close_trade.get("note", "").startswith("no_"):
                    return close_trade
                pos_value = account.wallet_balance * self.position_size_pct * self.leverage
                size = pos_value / price
                open_trade = account.open_long(price, size, self.leverage, ts=ts)
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
                return account.open_short(price, size, self.leverage, ts=ts)
            elif account.position_side == "long":
                # 平多并开空
                close_trade = account.close_all(price, ts=ts)
                if close_trade and close_trade.get("note", "").startswith("no_"):
                    return close_trade
                pos_value = account.wallet_balance * self.position_size_pct * self.leverage
                size = pos_value / price
                open_trade = account.open_short(price, size, self.leverage, ts=ts)
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
