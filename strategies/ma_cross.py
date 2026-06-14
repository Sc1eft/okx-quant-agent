"""
策略 1: MA 均线交叉
🔧 P0 优化：增加止盈/止损/移动止损/持仓超时退出
"""

from __future__ import annotations

import logging

import pandas as pd
import numpy as np

from strategies.base import BaseStrategy, StrategyResult, Signal, PositionInfo

logger = logging.getLogger("strategy.ma_cross")


class MACrossStrategy(BaseStrategy):
    """
    MA 均线交叉策略
    信号规则：
      - 短均线上穿长均线 → BUY
      - 短均线下穿长均线 → SELL
      - BUY 后触发止盈/止损/移动止损 → EXIT
      - 持仓超过 N 根 K 线 → EXIT
    """

    @property
    def description(self) -> str:
        sw = self.params["short_window"]
        lw = self.params["long_window"]
        return f"MA 交叉 (MA{sw}/MA{lw}) + 止盈止损移动止损"

    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        sw = self.params["short_window"]
        lw = self.params["long_window"]
        sl_pct = self.params.get("stop_loss_pct", 2.0) / 100
        tp_pct = self.params.get("take_profit_pct", 6.0) / 100
        ts_activation = self.params.get("trailing_stop_activation", 3.0) / 100
        ts_distance = self.params.get("trailing_stop_distance", 1.5) / 100
        timeout_bars = self.params.get("position_timeout_bars", 48)

        df = df.copy()

        # 均线
        df["ma_short"] = df["close"].rolling(window=sw).mean()
        df["ma_long"] = df["close"].rolling(window=lw).mean()

        # 交叉信号
        df["prev_short"] = df["ma_short"].shift(1)
        df["prev_long"] = df["ma_long"].shift(1)

        # BUY: 短上穿长（prev 短 < prev 长，当前短 > 当前长）
        buy_condition = (
            (df["prev_short"] < df["prev_long"]) &
            (df["ma_short"] > df["ma_long"])
        )
        # SELL: 短下穿长
        sell_condition = (
            (df["prev_short"] > df["prev_long"]) &
            (df["ma_short"] < df["ma_long"])
        )

        # ── 状态机执行（含持仓管理和退出逻辑） ──
        self.position = None
        signals = []
        exit_reasons = []

        for idx in df.index:
            row = df.loc[idx]
            sig = Signal.HOLD
            reason = ""

            close_price = row["close"]

            # 检查已有持仓的退出条件
            if self.position is not None:
                self.position.bars_held += 1
                # 更新持仓期间最高价（移动止损用）
                self.position.highest_price = max(self.position.highest_price, close_price)

                # 1) 止损
                if sl_pct > 0 and close_price <= self.position.entry_price * (1 - sl_pct):
                    sig = Signal.EXIT
                    reason = f"止损 {sl_pct*100:.1f}% (入场 {self.position.entry_price:.1f}, 当前 {close_price:.1f})"
                # 2) 止盈
                elif tp_pct > 0 and close_price >= self.position.entry_price * (1 + tp_pct):
                    sig = Signal.EXIT
                    reason = f"止盈 {tp_pct*100:.1f}% (入场 {self.position.entry_price:.1f}, 当前 {close_price:.1f})"
                # 3) 移动止损
                elif ts_activation > 0 and ts_distance > 0:
                    # 浮盈达到激活比例
                    profit_pct = (self.position.highest_price - self.position.entry_price) / self.position.entry_price
                    if profit_pct >= ts_activation:
                        trail_stop = self.position.highest_price * (1 - ts_distance)
                        if close_price <= trail_stop:
                            sig = Signal.EXIT
                            reason = f"移动止损触发 (最高 {self.position.highest_price:.1f}, 回落至 {close_price:.1f})"
                # 4) 持仓超时
                if sig == Signal.HOLD and self.position.bars_held >= timeout_bars:
                    sig = Signal.EXIT
                    reason = f"持仓超时 ({timeout_bars} 根 K 线)"

            # 没有持仓 → 检查入场信号
            if self.position is None:
                if buy_condition.loc[idx]:
                    sig = Signal.BUY
                    reason = f"MA{sw} 上穿 MA{lw}"
                    # 开仓
                    self.position = PositionInfo(
                        entry_price=close_price,
                        entry_time=idx,
                        size=1.0,
                        highest_price=close_price,
                    )
            else:
                # 有持仓 → 检查趋势反转信号
                if sell_condition.loc[idx] and sig == Signal.HOLD:
                    sig = Signal.SELL
                    reason = f"MA{sw} 下穿 MA{lw}"

            # 卖出/退出 → 清仓
            if sig in (Signal.SELL, Signal.EXIT):
                self.position = None

            signals.append(sig)
            exit_reasons.append(reason)

        df["signal"] = signals
        df["reason"] = exit_reasons

        return StrategyResult(
            signals=df,
            metadata={
                "strategy": self.name,
                "short_window": sw,
                "long_window": lw,
                "params": dict(self.params),
            },
        )
