"""
参数自适应 — Phase 4

根据近期交易表现自动调整交易参数:
  - 胜率高 → 增加日交易次数上限, 缩短信号采集间隔
  - 胜率低 → 减少日交易次数上限, 延长采集间隔
  - 连续亏损 → 延长最小交易间隔

所有调整在安全边界内进行, 不会极端调参。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from agents.config import AgentSystemConfig

logger = logging.getLogger("param_adapter")


class ParamAdapter:
    """参数自适应调整器"""

    def __init__(self, config: AgentSystemConfig, db_path: str):
        self.config = config
        self.db_path = db_path
        self._last_adjust_time: datetime | None = None
        self._adjustment_log: list[dict] = []

    def should_adjust(self, now: datetime | None = None) -> bool:
        """检查是否到达调整间隔"""
        if self._last_adjust_time is None:
            return True
        elapsed = (now or datetime.now(timezone.utc)) - self._last_adjust_time
        return elapsed.total_seconds() >= self.config.param_adapter_adjust_interval_hours * 3600

    def adjust(self, now: datetime | None = None) -> dict[str, Any]:
        """评估近期表现并调整参数

        Returns:
            dict: {"adjusted": bool, "changes": list[str], "reason": str}
        """
        now = now or datetime.now(timezone.utc)

        if not self.should_adjust(now):
            remaining = self.config.param_adapter_adjust_interval_hours * 3600
            if self._last_adjust_time:
                remaining -= (now - self._last_adjust_time).total_seconds()
            return {"adjusted": False, "changes": [], "reason": f"调整间隔未到 (剩余 {remaining:.0f}s)"}

        conn = sqlite3.connect(self.db_path)
        try:
            win_rate = self._get_recent_win_rate(conn)
            recent_pnl = self._get_recent_pnl(conn)
            changes: list[str] = []

            if win_rate is None:
                return {"adjusted": False, "changes": [], "reason": "数据不足"}

            if win_rate > 0.60:
                changes += self._adjust_on_high_win_rate(win_rate)
            elif win_rate < 0.40:
                changes += self._adjust_on_low_win_rate(win_rate)

            if self.config.agent3_max_consecutive_losses >= 2:
                # 使用 RiskManager 的状态; 这里从数据库推断连续亏损
                consec_losses = self._get_consecutive_losses(conn)
                if consec_losses >= 2:
                    changes += self._adjust_on_consecutive_losses(consec_losses)

            self._last_adjust_time = now
            if changes:
                result = {"adjusted": True, "changes": changes, "reason": "正常调整"}
                logger.info(f"参数自适应: {' | '.join(changes)}")
            else:
                result = {"adjusted": False, "changes": [], "reason": "无需调整"}
            self._adjustment_log.append(result)
            return result
        finally:
            conn.close()

    def _get_recent_win_rate(self, conn: sqlite3.Connection, num_trades: int = 20) -> float | None:
        """计算最近 N 笔已平仓交易的胜率"""
        min_trades = self.config.param_adapter_min_trades_for_adjust
        row = conn.execute(
            """
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl_close > 0 THEN 1 ELSE 0 END) as wins
            FROM (
                SELECT pnl_close FROM trades
                WHERE pnl_close != 0
                ORDER BY id DESC LIMIT ?
            )
            """,
            (num_trades,),
        ).fetchone()

        total = row[0] if row else 0
        wins = row[1] if row else 0

        if total < min_trades:
            return None
        return wins / total

    def _get_recent_pnl(self, conn: sqlite3.Connection, num_trades: int = 20) -> float:
        """计算最近 N 笔已平仓交易的总盈亏"""
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_close), 0) as total "
            "FROM (SELECT pnl_close FROM trades WHERE pnl_close != 0 ORDER BY id DESC LIMIT ?)",
            (num_trades,),
        ).fetchone()
        return row[0] if row else 0.0

    def _get_consecutive_losses(self, conn: sqlite3.Connection) -> int:
        """从数据库推断连续亏损次数"""
        rows = conn.execute(
            "SELECT pnl_close FROM trades WHERE pnl_close != 0 ORDER BY id DESC LIMIT 10"
        ).fetchall()
        count = 0
        for r in rows:
            if r[0] < 0:
                count += 1
            else:
                break
        return count

    def _adjust_on_high_win_rate(self, win_rate: float) -> list[str]:
        """胜率偏高 → 适当激進"""
        changes = []
        min_val, max_val = self.config.param_adapter_max_trades_range

        new_max = self.config.agent3_max_daily_trades + 2
        if new_max <= max_val:
            self.config.agent3_max_daily_trades = new_max
            changes.append(f"max_daily_trades → {new_max} (胜率 {win_rate:.0%})")

        old_debounce = self.config.agent3_debounce_seconds
        new_debounce = max(10.0, old_debounce - 5.0)
        if new_debounce != old_debounce:
            self.config.agent3_debounce_seconds = new_debounce
            changes.append(f"debounce → {new_debounce:.0f}s")

        return changes

    def _adjust_on_low_win_rate(self, win_rate: float) -> list[str]:
        """胜率偏低 → 保守"""
        changes = []
        min_val, max_val = self.config.param_adapter_max_trades_range

        new_max = self.config.agent3_max_daily_trades - 2
        if new_max >= min_val:
            self.config.agent3_max_daily_trades = new_max
            changes.append(f"max_daily_trades → {new_max} (胜率 {win_rate:.0%})")

        old_debounce = self.config.agent3_debounce_seconds
        new_debounce = min(120.0, old_debounce + 10.0)
        if new_debounce != old_debounce:
            self.config.agent3_debounce_seconds = new_debounce
            changes.append(f"debounce → {new_debounce:.0f}s")

        return changes

    def _adjust_on_consecutive_losses(self, losses: int) -> list[str]:
        """连续亏损 → 延长交易间隔"""
        changes = []
        old_interval = self.config.agent3_min_interval_between_trades
        new_interval = min(600, old_interval + 60)
        if new_interval != old_interval:
            self.config.agent3_min_interval_between_trades = new_interval
            changes.append(f"min_interval → {new_interval}s (连续亏损 {losses} 次)")
        return changes

    def get_adjustment_log(self) -> list[dict]:
        """返回最近的调整记录"""
        return self._adjustment_log[-10:]
