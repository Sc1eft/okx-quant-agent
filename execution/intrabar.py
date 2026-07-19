"""tick 级 intrabar 退出判定（秒级止损/止盈/移动止损）。

与回测引擎 backtest/engine.py 的 _intrabar_exit_price 使用同一组
cfg.strategy.* 参数，但面向 tick 价格流：tick 价即成交价，
无需 OHLC 保守模型。多空对称（short 用 lowest_price 跟踪移动止损）。

判定优先级与回测一致（保守）：止损 > 移动止损 > 止盈。
"""

from __future__ import annotations

from typing import Literal, Optional


def check_tick_exit(
    price: float,
    *,
    direction: Literal["long", "short"],
    entry_price: float,
    highest_price: float,
    lowest_price: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trailing_activation_pct: float,
    trailing_distance_pct: float,
) -> Optional[str]:
    """判断当前 tick 价是否触及退出条件，返回原因或 None。

    price:          最新 tick 成交价
    direction:      "long" / "short"
    entry_price:    持仓均价
    highest_price:  持仓期间最高价（多头移动止损用）
    lowest_price:   持仓期间最低价（空头移动止损用）
    各 pct 参数:    百分比数值（5.0 = 5%），与 cfg.strategy.* 一致
    """
    if entry_price <= 0 or price <= 0:
        return None

    if direction == "long":
        if price <= entry_price * (1 - stop_loss_pct / 100):
            return "stop_loss"
        if (
            trailing_activation_pct > 0
            and trailing_distance_pct > 0
            and highest_price >= entry_price * (1 + trailing_activation_pct / 100)
            and price <= highest_price * (1 - trailing_distance_pct / 100)
        ):
            return "trailing_stop"
        if price >= entry_price * (1 + take_profit_pct / 100):
            return "take_profit"
    else:  # short — 对称：止损在上方，止盈在下方，移动止损跟最低价
        if price >= entry_price * (1 + stop_loss_pct / 100):
            return "stop_loss"
        if (
            trailing_activation_pct > 0
            and trailing_distance_pct > 0
            and lowest_price <= entry_price * (1 - trailing_activation_pct / 100)
            and price >= lowest_price * (1 + trailing_distance_pct / 100)
        ):
            return "trailing_stop"
        if price <= entry_price * (1 - take_profit_pct / 100):
            return "take_profit"
    return None
