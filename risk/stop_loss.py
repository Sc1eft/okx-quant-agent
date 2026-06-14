"""
🔧 P0: 止盈 / 止损 / 移动止损 计算模块
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StopLossLevels:
    """当前持仓的止损水平"""
    fixed_stop: Optional[float] = None      # 固定止损价
    fixed_take_profit: Optional[float] = None  # 固定止盈价
    trailing_stop: Optional[float] = None   # 当前移动止损价
    trailing_activated: bool = False        # 移动止损是否已激活
    atr_stop: Optional[float] = None        # ATR 动态止损


def compute_stop_levels(
    entry_price: float,
    current_price: float,
    highest_price: float,
    # 参数
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.06,
    trailing_activation_pct: float = 0.03,
    trailing_distance_pct: float = 0.015,
    atr_value: Optional[float] = None,
    atr_multiplier: float = 2.0,
) -> StopLossLevels:
    """
    计算所有止损水平

    Args:
        entry_price: 入场价
        current_price: 当前价
        highest_price: 持仓期间最高价
        stop_loss_pct: 止损百分比 (如 0.02 = 2%)
        take_profit_pct: 止盈百分比 (如 0.06 = 6%)
        trailing_activation_pct: 移动止损激活阈值 (如 0.03 = 浮盈 3%)
        trailing_distance_pct: 移动止损距离 (如 0.015 = 从最高点回落 1.5%)
        atr_value: ATR 值（用于动态止损）
        atr_multiplier: ATR 倍数

    Returns:
        StopLossLevels 对象
    """
    levels = StopLossLevels()

    # 固定止损
    if stop_loss_pct > 0:
        levels.fixed_stop = round(entry_price * (1 - stop_loss_pct), 2)

    # 固定止盈
    if take_profit_pct > 0:
        levels.fixed_take_profit = round(entry_price * (1 + take_profit_pct), 2)

    # ATR 动态止损
    if atr_value and atr_multiplier > 0:
        levels.atr_stop = round(entry_price - atr_value * atr_multiplier, 2)

    # 移动止损
    if trailing_activation_pct > 0 and trailing_distance_pct > 0:
        profit_pct = (current_price - entry_price) / entry_price
        if profit_pct >= trailing_activation_pct:
            levels.trailing_activated = True
            levels.trailing_stop = round(highest_price * (1 - trailing_distance_pct), 2)
        else:
            # 未激活时，把移动止损设在入场价下方（防止亏损）
            levels.trailing_stop = round(entry_price * (1 - trailing_distance_pct * 0.5), 2)

    return levels


def should_exit(levels: StopLossLevels, current_price: float) -> tuple[bool, str]:
    """
    检查是否应该退出
    返回: (should_exit, reason)
    """
    # 1. 固定止损
    if levels.fixed_stop and current_price <= levels.fixed_stop:
        return True, f"固定止损触发 ({levels.fixed_stop})"

    # 2. 固定止盈
    if levels.fixed_take_profit and current_price >= levels.fixed_take_profit:
        return True, f"固定止盈触发 ({levels.fixed_take_profit})"

    # 3. 移动止损（已激活）
    if levels.trailing_activated and levels.trailing_stop:
        if current_price <= levels.trailing_stop:
            return True, f"移动止损触发 ({levels.trailing_stop})"

    # 4. ATR 止损
    if levels.atr_stop and current_price <= levels.atr_stop:
        return True, f"ATR 动态止损触发 ({levels.atr_stop})"

    return False, ""
