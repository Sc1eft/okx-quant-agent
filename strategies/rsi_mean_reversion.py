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

    def __init__(self, name: str, params: dict):
        super().__init__(name, params)
        self._min_bars = params.get("rsi_period", 14) + 1

    @property
    def description(self) -> str:
        period = self.params["rsi_period"]
        os = self.params["oversold"]
        ob = self.params["overbought"]
        return f"RSI 均值回归 (RSI{period}, 超卖{os}, 超买{ob})"

    # ── 私有参数读取 ──

    def _get_params(self) -> tuple:
        return (
            self.params["rsi_period"],
            self.params["oversold"],
            self.params["overbought"],
            self.params.get("stop_loss_pct", 2.0) / 100,
            self.params.get("take_profit_pct", 5.0) / 100,
            self.params.get("trailing_stop_activation", 2.5) / 100,
            self.params.get("trailing_stop_distance", 1.2) / 100,
            self.params.get("position_timeout_bars", 36),
        )

    @staticmethod
    def _compute_rsi(series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        period = self._get_params()[0]
        df = df.copy()
        df["rsi"] = self._compute_rsi(df["close"], period)
        df["rsi_prev"] = df["rsi"].shift(1)
        return df

    # ── 单步退出检查（用于 on_bar） ──

    def _check_exit(self, close_price: float) -> tuple[Signal, str]:
        sl_pct, tp_pct, ts_activation, ts_distance, timeout_bars = self._get_params()[3:]
        self.position.bars_held += 1
        self.position.highest_price = max(self.position.highest_price, close_price)

        if sl_pct > 0 and close_price <= self.position.entry_price * (1 - sl_pct):
            return Signal.EXIT, f"RSI 止损 {sl_pct*100:.1f}%"
        if tp_pct > 0 and close_price >= self.position.entry_price * (1 + tp_pct):
            return Signal.EXIT, f"RSI 止盈 {tp_pct*100:.1f}%"
        if ts_activation > 0 and ts_distance > 0:
            profit_pct = (self.position.highest_price - self.position.entry_price) / self.position.entry_price
            if profit_pct >= ts_activation:
                if close_price <= self.position.highest_price * (1 - ts_distance):
                    return Signal.EXIT, "RSI 移动止损"
        if self.position.bars_held >= timeout_bars:
            return Signal.EXIT, "RSI 持仓超时"
        return Signal.HOLD, ""

    # ── 批处理模式（回测用） ──

    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        period, oversold, overbought, sl_pct, tp_pct, ts_activation, ts_distance, timeout_bars = self._get_params()
        df = self._compute_indicators(df)

        buy_condition = (df["rsi_prev"] <= oversold) & (df["rsi"] > oversold)
        sell_condition = (df["rsi_prev"] >= overbought) & (df["rsi"] < overbought)

        self.position = None
        signals = []
        reasons = []

        for idx in df.index:
            row = df.loc[idx]
            sig = Signal.HOLD
            reason = ""
            close_price = row["close"]

            if self.position is not None:
                sig, reason = self._check_exit(close_price)

            if self.position is None:
                if buy_condition.loc[idx]:
                    sig = Signal.BUY
                    reason = f"RSI 超卖回升 ({row['rsi']:.1f})"
                    self.position = PositionInfo(
                        entry_price=close_price, entry_time=idx,
                        size=1.0, highest_price=close_price,
                    )
            else:
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
            metadata={"strategy": self.name, "rsi_period": period, "oversold": oversold, "overbought": overbought},
        )

    # ── 增量模式（模拟盘用） ──

    def on_bar(self, bar: pd.Series) -> Signal:
        _, oversold, overbought, *_ = self._get_params()

        new_df = bar.to_frame().T.infer_objects(copy=False)
        if self._bar_buffer is None:
            self._bar_buffer = new_df
        else:
            self._bar_buffer = pd.concat([self._bar_buffer, new_df])

        if len(self._bar_buffer) < self._min_bars:
            return Signal.HOLD

        df = self._compute_indicators(self._bar_buffer)
        current = df.iloc[-1]
        close_price = float(current["close"])

        sig = Signal.HOLD
        if self.position is not None:
            sig, _ = self._check_exit(close_price)

        if self.position is None:
            if current["rsi_prev"] <= oversold and current["rsi"] > oversold:
                sig = Signal.BUY
                self.position = PositionInfo(
                    entry_price=close_price, entry_time=self._bar_buffer.index[-1],
                    size=1.0, highest_price=close_price,
                )
        elif sig == Signal.HOLD:
            if current["rsi_prev"] >= overbought and current["rsi"] < overbought:
                sig = Signal.SELL

        if sig in (Signal.SELL, Signal.EXIT):
            self.position = None

        return sig
