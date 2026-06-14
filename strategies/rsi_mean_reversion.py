"""
策略 2: RSI 均值回归
- RSI < oversold → BUY（超卖买入）
- RSI > overbought → SELL（超买卖出）
🔧 P0 优化：止盈/止损/移动止损
"""

from __future__ import annotations

import logging

import pandas as pd
import numpy as np

from strategies.base import BaseStrategy, StrategyResult, Signal, PositionInfo

logger = logging.getLogger("strategy.rsi")


class RSIMeanReversionStrategy(BaseStrategy):
    """RSI 均值回归策略"""

    @property
    def description(self) -> str:
        period = self.params["rsi_period"]
        os = self.params["oversold"]
        ob = self.params["overbought"]
        return f"RSI 均值回归 (RSI{period}, 超卖{os}, 超买{ob})"

    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        period = self.params["rsi_period"]
        oversold = self.params["oversold"]
        overbought = self.params["overbought"]
        sl_pct = self.params.get("stop_loss_pct", 2.0) / 100
        tp_pct = self.params.get("take_profit_pct", 5.0) / 100
        ts_activation = self.params.get("trailing_stop_activation", 2.5) / 100
        ts_distance = self.params.get("trailing_stop_distance", 1.2) / 100
        timeout_bars = self.params.get("position_timeout_bars", 36)

        df = df.copy()

        # RSI 计算
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))

        # 信号条件
        df["rsi_prev"] = df["rsi"].shift(1)

        # BUY: RSI 从超卖区回升
        buy_condition = (
            (df["rsi_prev"] <= oversold) &
            (df["rsi"] > oversold)
        )
        # SELL: RSI 从超买区回落
        sell_condition = (
            (df["rsi_prev"] >= overbought) &
            (df["rsi"] < overbought)
        )

        # ── 状态机执行 ──
        self.position = None
        signals = []
        reasons = []

        for idx in df.index:
            row = df.loc[idx]
            sig = Signal.HOLD
            reason = ""
            close_price = row["close"]

            # 有持仓 → 检查退出
            if self.position is not None:
                self.position.bars_held += 1
                self.position.highest_price = max(self.position.highest_price, close_price)

                # 止损
                if sl_pct > 0 and close_price <= self.position.entry_price * (1 - sl_pct):
                    sig = Signal.EXIT
                    reason = f"RSI 止损 {sl_pct*100:.1f}%"
                # 止盈
                elif tp_pct > 0 and close_price >= self.position.entry_price * (1 + tp_pct):
                    sig = Signal.EXIT
                    reason = f"RSI 止盈 {tp_pct*100:.1f}%"
                # 移动止损
                elif ts_activation > 0 and ts_distance > 0:
                    profit_pct = (self.position.highest_price - self.position.entry_price) / self.position.entry_price
                    if profit_pct >= ts_activation:
                        if close_price <= self.position.highest_price * (1 - ts_distance):
                            sig = Signal.EXIT
                            reason = "RSI 移动止损触发"
                # 超时
                if sig == Signal.HOLD and self.position.bars_held >= timeout_bars:
                    sig = Signal.EXIT
                    reason = f"RSI 持仓超时"

            # 无持仓 → 入场
            if self.position is None:
                if buy_condition.loc[idx]:
                    sig = Signal.BUY
                    reason = f"RSI 超卖回升 ({row['rsi']:.1f})"
                    self.position = PositionInfo(
                        entry_price=close_price,
                        entry_time=idx,
                        size=1.0,
                        highest_price=close_price,
                    )
            else:
                # 有持仓 → 趋势反转退出
                if sell_condition.loc[idx] and sig == Signal.HOLD:
                    sig = Signal.SELL
                    reason = f"RSI 超买回落 ({row['rsi']:.1f})"

            if sig in (Signal.SELL, Signal.EXIT):
                self.position = None

            signals.append(sig)
            reasons.append(reason)

        df["signal"] = signals
        df["reason"] = reasons

        return StrategyResult(
            signals=df,
            metadata={
                "strategy": self.name,
                "rsi_period": period,
                "oversold": oversold,
                "overbought": overbought,
            },
        )
