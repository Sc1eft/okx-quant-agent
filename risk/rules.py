"""
风控规则引擎
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from config import RiskConfig

logger = logging.getLogger("risk.rules")


class RiskState:
    """风控状态"""

    def __init__(self):
        self.daily_loss_pct = 0.0
        self.consecutive_losses = 0
        self.daily_trades = 0
        self.last_trade_time: Optional[datetime] = None
        self.daily_start_equity: Optional[float] = None
        self.is_paused = False
        self.pause_reason = ""
        self.pause_start_time: Optional[datetime] = None
        self.total_pauses_today = 0
        self.current_strategy: Optional[str] = None
        self.switched_strategies: list[str] = []

    def reset_daily(self, current_equity: float):
        """每日重置"""
        self.daily_loss_pct = 0.0
        self.consecutive_losses = 0
        self.daily_trades = 0
        self.daily_start_equity = current_equity
        self.total_pauses_today = 0


class RiskEngine:
    """
    风控引擎
    在策略信号进入执行器之前过滤
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self.state = RiskState()

    def check_signal(self, signal: str, current_equity: float, current_position_pct: float) -> tuple[bool, str]:
        """
        检查信号是否允许执行
        返回: (allowed, reason)
        """
        if self.state.is_paused:
            # 检查是否过了冷却期
            if self.state.pause_start_time:
                elapsed_bars = self._estimate_elapsed_bars(self.state.pause_start_time)
                # 用恢复策略判断
                if self.config.recovery_mode == "auto_cool":
                    if elapsed_bars >= self.config.recovery_cooldown_bars:
                        # 冷却期结束，自动恢复
                        if self.state.total_pauses_today < self.config.max_daily_starts:
                            self.state.is_paused = False
                            self.state.pause_reason = ""
                            logger.info("🔄 冷却期结束，自动恢复交易")
                        else:
                            return False, f"当日已达最大重启次数 ({self.config.max_daily_starts})"
                    else:
                        return False, f"冷却中 (还需 {self.config.recovery_cooldown_bars - elapsed_bars} 根 K 线)"
                else:
                    return False, f"风控暂停中: {self.state.pause_reason}"

        # 仓位检查
        if current_position_pct >= self.config.max_position_pct:
            return False, f"已达最大仓位 ({self.config.max_position_pct:.0%})"

        # 日内交易次数限制
        if self.state.daily_trades >= 20:
            return False, "日内交易次数超限 (20)"

        return True, ""

    def record_trade_result(self, pnl_pct: float):
        """记录交易结果，更新风控状态"""
        self.state.daily_trades += 1

        if pnl_pct < 0:
            self.state.consecutive_losses += 1
            self.state.daily_loss_pct += abs(pnl_pct)

            # 检查是否需要暂停
            if self.state.consecutive_losses >= self.config.max_consecutive_losses:
                self._pause("连续亏损超过阈值")
                return

            if self.state.daily_loss_pct >= self.config.max_daily_loss_pct:
                self._pause("单日亏损超限")
                return
        else:
            self.state.consecutive_losses = 0

        self.state.last_trade_time = datetime.now(timezone.utc)

    def _pause(self, reason: str):
        """暂停交易"""
        self.state.is_paused = True
        self.state.pause_reason = reason
        self.state.pause_start_time = datetime.now(timezone.utc)
        self.state.total_pauses_today += 1
        logger.warning(f"⛔ 风控暂停: {reason}")

    def check_signal_expiry(self, signal_time: datetime, current_time: datetime) -> bool:
        """检查信号是否过期"""
        if self.config.signal_expiry_bars <= 0:
            return False
        # 估算经过的 K 线数（按 1h 估算）
        elapsed_hours = (current_time - signal_time).total_seconds() / 3600
        return elapsed_hours >= self.config.signal_expiry_bars

    @staticmethod
    def _estimate_elapsed_bars(since: datetime) -> int:
        """估算经过的 K 线数（按 1h）"""
        elapsed_hours = (datetime.now(timezone.utc) - since).total_seconds() / 3600
        return int(elapsed_hours)
