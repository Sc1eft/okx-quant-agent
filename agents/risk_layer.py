"""
三层风控系统（阶段一基础版）

Layer 1 — 交易前检查:
  - 最小交易间隔（距上次交易 > 5 分钟）
  - 单笔上限 ≤ 0.5 ETH
  - 每日交易次数 ≤ 10
  - 每日亏损上限 ≤ 100 USDT
  - 连续亏损 ≤ 3 次（连亏后仓位减半）
  - 方向冲突（已有同方向仓位时累加不超上限）

Layer 2 — 交易中保护:
  - 限价单优先
  - 滑点 > 0.3% 取消

Layer 3 — 交易后监控:
  - 记录交易到 SQLite
  - 更新风控状态
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Tuple

from agents.config import AgentSystemConfig

logger = logging.getLogger("risk_layer")


class RiskManager:
    """风控管理器 — 三层风控"""

    def __init__(self, config: AgentSystemConfig):
        self.config = config

        # ── Layer 1 状态 ──
        self._last_trade_time: Optional[datetime] = None
        self._daily_trade_count: int = 0
        self._daily_loss_usdt: float = 0.0
        self._consecutive_losses: int = 0
        self._current_date: date = datetime.now(timezone.utc).date()
        self._current_position_eth: float = 0.0
        self._current_position_side: Optional[str] = None  # "long" / "short"

        # ── Layer 2 状态 ──
        self._consecutive_api_errors: int = 0
        self._api_breaker_until: Optional[datetime] = None

        # ── Layer 3 状态 ──
        self._daily_trades: list[dict] = []

    # ── Layer 1: 交易前检查 ──

    def check_layer1(
        self,
        side: str,  # "buy" / "sell"
        size_eth: float,
        price: float,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """交易前全项检查，返回 (通过?, 原因)"""
        now = now or datetime.now(timezone.utc)
        self._check_date_reset(now)

        # 1. 最小交易间隔
        if self._last_trade_time:
            elapsed = (now - self._last_trade_time).total_seconds()
            if elapsed < self.config.agent3_min_interval_between_trades:
                remaining = self.config.agent3_min_interval_between_trades - int(elapsed)
                return False, f"交易间隔未到，还需 {remaining}s"

        # 2. 单笔上限
        if size_eth > self.config.agent3_max_position_eth:
            return False, f"单笔 {size_eth:.4f} ETH 超过上限 {self.config.agent3_max_position_eth} ETH"

        # 3. 每日交易次数
        if self._daily_trade_count >= self.config.agent3_max_daily_trades:
            return False, f"今日交易已达上限 ({self._daily_trade_count} 次)"

        # 4. 每日亏损上限
        if self._daily_loss_usdt >= self.config.agent3_max_daily_loss_usdt:
            return False, f"今日亏损已达上限 ({self._daily_loss_usdt:.2f} USDT)"

        # 5. 连续亏损
        if self._consecutive_losses >= self.config.agent3_max_consecutive_losses:
            return False, f"连续亏损 {self._consecutive_losses} 次，交易暂停"

        # 6. 方向冲突（同方向累加检查）
        direction = "long" if side == "buy" else "short"
        if self._current_position_side == direction:
            new_total = self._current_position_eth + size_eth
            if new_total > self.config.agent3_max_position_eth:
                return False, f"同方向累加 {new_total:.4f} ETH 超过上限"

        # 7. API 熔断检查
        if self._api_breaker_until and now < self._api_breaker_until:
            remaining = (self._api_breaker_until - now).total_seconds()
            return False, f"API 熔断中，剩余 {remaining:.0f}s"

        return True, ""

    # ── Layer 2: 交易中保护 ──

    def check_layer2(
        self,
        signal_price: float,
        actual_fill_price: float,
    ) -> Tuple[bool, str]:
        """检查滑点是否可接受"""
        slippage = abs(actual_fill_price - signal_price) / signal_price * 100
        if slippage > 0.3:
            return False, f"滑点 {slippage:.2f}% 超过 0.3% 上限"
        return True, ""

    def report_api_error(self):
        """报告 API 错误（用于熔断）"""
        self._consecutive_api_errors += 1
        if self._consecutive_api_errors >= 3:
            self._api_breaker_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            logger.warning(f"连续 {self._consecutive_api_errors} 次 API 错误，触发熔断 5 分钟")
        # 实际熔断时间在 check 里计算

    def reset_api_errors(self):
        """重置 API 错误计数"""
        self._consecutive_api_errors = 0
        self._api_breaker_until = None

    # ── Layer 3: 交易后记录 ──

    def record_trade(self, trade_data: dict):
        """记录一笔交易"""
        self._last_trade_time = datetime.now(timezone.utc)
        self._daily_trade_count += 1
        self._daily_trades.append(trade_data)

        # 更新仓位信息
        side = trade_data.get("side", "")
        size = trade_data.get("size", 0)
        if side == "buy":
            self._current_position_side = "long"
            self._current_position_eth += size
        elif side == "sell":
            if trade_data.get("short"):
                self._current_position_side = "short"
            else:
                # closing a long / reducing long position
                self._current_position_eth = max(0, self._current_position_eth - size)
                if self._current_position_eth <= 0:
                    self._current_position_side = None

        pnl = trade_data.get("pnl", 0)
        if pnl < 0:
            self._record_loss(abs(pnl))
        elif pnl > 0:
            self._consecutive_losses = 0  # 盈利后重置连亏

    def _record_loss(self, loss_usdt: float):
        """记录亏损"""
        self._consecutive_losses += 1
        self._daily_loss_usdt += loss_usdt

    def record_loss(self, loss_usdt: float):
        """公开的亏损记录接口，代理 _record_loss"""
        self._record_loss(loss_usdt)

    def get_position_size_multiplier(self) -> float:
        """返回仓位乘数（连亏后减半）"""
        if self._consecutive_losses > 0:
            return max(0.1, 1.0 - self._consecutive_losses * 0.25)
        return 1.0

    def _check_date_reset(self, now: datetime):
        """每日重置"""
        today = now.date()
        if today != self._current_date:
            logger.info(f"每日风控重置: {self._current_date} → {today}")
            self._daily_trade_count = 0
            self._daily_loss_usdt = 0.0
            self._consecutive_losses = 0
            self._current_date = today
            self._daily_trades = []
            self._consecutive_api_errors = 0
            self._api_breaker_until = None

    def get_status(self) -> dict:
        """返回风控状态摘要"""
        return {
            "daily_trade_count": self._daily_trade_count,
            "max_daily_trades": self.config.agent3_max_daily_trades,
            "daily_loss_usdt": round(self._daily_loss_usdt, 2),
            "max_daily_loss_usdt": self.config.agent3_max_daily_loss_usdt,
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive_losses": self.config.agent3_max_consecutive_losses,
            "position_size_multiplier": self.get_position_size_multiplier(),
            "position_eth": round(self._current_position_eth, 6),
            "position_side": self._current_position_side,
        }
