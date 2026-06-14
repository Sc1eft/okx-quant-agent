"""
🔧 P3: 风控暂停后的恢复策略

三种恢复模式：
  1. manual — 手动恢复（默认，最安全）
  2. auto_cool — 冷却 N 根 K 线后自动恢复
  3. switch_strategy — 冷却后自动切换到其他策略
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from config import RiskConfig, StrategyConfig

logger = logging.getLogger("risk.recovery")


class RecoveryManager:
    """
    暂停恢复管理器
    决定"暂停之后怎么办"
    """

    def __init__(self, risk_config: RiskConfig, strategy_config: StrategyConfig):
        self.config = risk_config
        self.strategy_config = strategy_config
        self.switch_count = 0

    def evaluate_recovery(
        self,
        state: "RiskState",  # noqa: F821
        current_strategy: str,
        available_strategies: list[str],
    ) -> dict:
        """
        评估恢复方案
        返回恢复决策
        """
        if not state.is_paused:
            return {"should_recover": False, "action": "none"}

        elapsed_bars = self._elapsed_bars(state.pause_start_time)
        cooldown_ok = elapsed_bars >= self.config.recovery_cooldown_bars

        logger.info(
            f"恢复评估: 已冷却 {elapsed_bars}/{self.config.recovery_cooldown_bars} 根 K 线, "
            f"当日暂停 {state.total_pauses_today} 次"
        )

        # 1. 已达每日最大重启次数
        if state.total_pauses_today >= self.config.max_daily_starts:
            return {
                "should_recover": False,
                "action": "daily_limit_reached",
                "message": "今日已达最大重启次数，明天自动恢复",
                "next_available": "next_day",
            }

        # 2. 冷却中
        if not cooldown_ok:
            remaining = self.config.recovery_cooldown_bars - elapsed_bars
            return {
                "should_recover": False,
                "action": "cooling",
                "message": f"冷却中，还需 {remaining} 根 K 线",
                "remaining_bars": remaining,
            }

        # 3. 冷却完成，可以恢复
        if self.config.recovery_mode == "manual":
            return {
                "should_recover": True,
                "action": "wait_manual",
                "message": "冷却完成，等待手动确认恢复",
                "confirm_required": True,
            }

        elif self.config.recovery_mode == "auto_cool":
            return {
                "should_recover": True,
                "action": "auto_recover",
                "message": "冷却完成，自动恢复交易",
                "confirm_required": False,
            }

        elif self.config.recovery_mode == "switch_strategy":
            self.switch_count += 1
            new_strategy = self._select_alternative_strategy(
                current_strategy, available_strategies
            )
            if new_strategy and self.switch_count < 3:
                return {
                    "should_recover": True,
                    "action": "switch_strategy",
                    "message": f"冷却完成，切换到策略: {new_strategy}",
                    "new_strategy": new_strategy,
                    "confirm_required": False,
                }
            else:
                # 切换太多次了，转手动
                return {
                    "should_recover": True,
                    "action": "auto_recover",
                    "message": f"已切换 {self.switch_count} 次，改为自动恢复",
                    "confirm_required": False,
                }

        return {
            "should_recover": False,
            "action": "unknown",
            "message": "未知恢复模式",
        }

    def _select_alternative_strategy(
        self,
        current: str,
        available: list[str],
    ) -> Optional[str]:
        """选择备用策略（不用当前失败的）"""
        others = [s for s in available if s != current]
        if not others:
            return None
        # 简单的轮询策略
        idx = self.switch_count % len(others)
        return others[idx]

    @staticmethod
    def _elapsed_bars(since: Optional[datetime]) -> int:
        if since is None:
            return 999
        elapsed_hours = (datetime.now(timezone.utc) - since).total_seconds() / 3600
        return int(elapsed_hours)

    @staticmethod
    def get_recovery_guide() -> str:
        """恢复策略配置指南"""
        return """
        🔧 恢复策略配置建议:

        [新手 / 第一版]
          recovery_mode = "manual"
          → 暂停后必须手动确认，不出意外

        [有经验 / 半自动]
          recovery_mode = "auto_cool"
          recovery_cooldown_bars = 24
          max_daily_starts = 2
          → 冷却 24 根 K 线后自动恢复，每天最多 2 次

        [多策略 / 高级]
          recovery_mode = "switch_strategy"
          recovery_switch_threshold = 2
          → 连续 2 次暂停自动切到其他策略
        """
