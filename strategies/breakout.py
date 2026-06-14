"""
策略 3: 突破策略
- 价格突破 N 周期高点 → BUY
- 价格跌破 N 周期低点 → SELL
- ATR 自适应止损
🔧 P0 优化：ATR 动态止盈止损 + 移动止损
"""

from __future__ import annotations

import logging

import pandas as pd
import numpy as np

from strategies.base import BaseStrategy, StrategyResult, Signal, PositionInfo

logger = logging.getLogger("strategy.breakout")


class BreakoutStrategy(BaseStrategy):
    """
    突破策略
    用 ATR（平均真实波幅）动态设置止损距离
    突破力度越强（ATR 越大），止损越宽
    """

    @property
    def description(self) -> str:
        period = self.params["period"]
        atr_mul = self.params.get("atr_multiplier", 2.0)
        return f"突破策略 (N{period}, ATR×{atr_mul})"

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算 ATR（平均真实波幅）"""
        high, low, close = df["high"], df["low"], df["close"]
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=period).mean()
        return atr

    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        period = self.params["period"]
        atr_mult = self.params.get("atr_multiplier", 2.0)
        sl_pct = self.params.get("stop_loss_pct", 2.0) / 100
        tp_pct = self.params.get("take_profit_pct", 6.0) / 100
        ts_activation = self.params.get("trailing_stop_activation", 3.0) / 100
        ts_distance = self.params.get("trailing_stop_distance", 1.5) / 100
        timeout_bars = self.params.get("position_timeout_bars", 48)

        df = df.copy()

        # N 周期高/低点
        df["high_n"] = df["high"].rolling(window=period).max()
        df["low_n"] = df["low"].rolling(window=period).min()
        df["prev_high_n"] = df["high_n"].shift(1)
        df["prev_low_n"] = df["low_n"].shift(1)

        # ATR
        atr_period = max(14, period // 2)
        df["atr"] = self._compute_atr(df, atr_period)

        # BUY: 收盘价突破 N 周期高点
        buy_condition = (
            (df["close"] > df["prev_high_n"]) &
            (df["close"].shift(1) <= df["prev_high_n"])
        )
        # SELL: 收盘价跌破 N 周期低点
        sell_condition = (
            (df["close"] < df["prev_low_n"]) &
            (df["close"].shift(1) >= df["prev_low_n"])
        )

        self.position = None
        signals = []
        reasons = []

        for idx in df.index:
            row = df.loc[idx]
            sig = Signal.HOLD
            reason = ""
            close_price = row["close"]
            current_atr = row.get("atr", np.nan)

            # 有持仓 → 检查退出
            if self.position is not None:
                self.position.bars_held += 1
                self.position.highest_price = max(self.position.highest_price, close_price)

                # ATR 自适应止损（比固定百分比更智能）
                if not np.isnan(current_atr) and current_atr > 0:
                    atr_stop_dist = current_atr * atr_mult
                    atr_stop_price = self.position.entry_price - atr_stop_dist
                    if close_price <= atr_stop_price:
                        sig = Signal.EXIT
                        reason = f"ATR 止损 ({atr_stop_dist:.1f})"

                # 固定止损（ATR 止损未触发时）
                if sig == Signal.HOLD and sl_pct > 0:
                    if close_price <= self.position.entry_price * (1 - sl_pct):
                        sig = Signal.EXIT
                        reason = f"止损 {sl_pct*100:.1f}%"

                # 止盈
                if sig == Signal.HOLD and tp_pct > 0:
                    if close_price >= self.position.entry_price * (1 + tp_pct):
                        sig = Signal.EXIT
                        reason = f"止盈 {tp_pct*100:.1f}%"

                # 移动止损
                if sig == Signal.HOLD and ts_activation > 0 and ts_distance > 0:
                    profit_pct = (self.position.highest_price - self.position.entry_price) / self.position.entry_price
                    if profit_pct >= ts_activation:
                        if close_price <= self.position.highest_price * (1 - ts_distance):
                            sig = Signal.EXIT
                            reason = "移动止损触发"

                # 超时
                if sig == Signal.HOLD and self.position.bars_held >= timeout_bars:
                    sig = Signal.EXIT
                    reason = f"持仓超时 ({timeout_bars})"

            # 无持仓 → 入场
            if self.position is None:
                if buy_condition.loc[idx]:
                    sig = Signal.BUY
                    reason = f"突破 {period}周期高点"
                    self.position = PositionInfo(
                        entry_price=close_price,
                        entry_time=idx,
                        size=1.0,
                        highest_price=close_price,
                    )
            else:
                if sell_condition.loc[idx] and sig == Signal.HOLD:
                    sig = Signal.SELL
                    reason = f"跌破 {period}周期低点"

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
                "period": period,
                "atr_multiplier": atr_mult,
            },
        )
