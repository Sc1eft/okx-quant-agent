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

    def __init__(self, name: str, params: dict):
        super().__init__(name, params)
        period = params.get("period", 20)
        self._min_bars = max(period, 14, period // 2) + 2

    @property
    def description(self) -> str:
        period = self.params["period"]
        atr_mul = self.params.get("atr_multiplier", 2.0)
        return f"突破策略 (N{period}, ATR×{atr_mul})"

    # ── 私有参数读取 ──

    def _get_params(self) -> tuple:
        return (
            self.params["period"],
            self.params.get("atr_multiplier", 2.0),
            self.params.get("stop_loss_pct", 5.0) / 100,
            self.params.get("take_profit_pct", 10.0) / 100,
            self.params.get("trailing_stop_activation", 6.0) / 100,
            self.params.get("trailing_stop_distance", 3.0) / 100,
            self.params.get("position_timeout_bars", 72),
        )

    # ── 指标计算 ──

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period, min_periods=period).mean()

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        period, atr_mult, *_ = self._get_params()
        atr_period = max(14, period // 2)
        df = df.copy()
        df["high_n"] = df["high"].rolling(window=period, min_periods=period).max()
        df["low_n"] = df["low"].rolling(window=period, min_periods=period).min()
        df["prev_high_n"] = df["high_n"].shift(1)
        df["prev_low_n"] = df["low_n"].shift(1)
        df["prev_close"] = df["close"].shift(1)
        df["atr"] = self._compute_atr(df, atr_period)
        return df

    # ── 单步退出检查 ──

    def _check_exit(self, close_price: float, atr_value: float) -> tuple[Signal, str]:
        period, atr_mult, sl_pct, tp_pct, ts_activation, ts_distance, timeout_bars = self._get_params()
        self.position.bars_held += 1
        self.position.highest_price = max(self.position.highest_price, close_price)

        # ATR 自适应止损
        if not np.isnan(atr_value) and atr_value > 0:
            atr_stop_price = self.position.entry_price - atr_value * atr_mult
            if close_price <= atr_stop_price:
                return Signal.EXIT, f"ATR 止损 ({atr_value*atr_mult:.1f})"

        # 固定止损
        if sl_pct > 0 and close_price <= self.position.entry_price * (1 - sl_pct):
            return Signal.EXIT, f"止损 {sl_pct*100:.1f}%"
        # 止盈
        if tp_pct > 0 and close_price >= self.position.entry_price * (1 + tp_pct):
            return Signal.EXIT, f"止盈 {tp_pct*100:.1f}%"
        # 移动止损
        if ts_activation > 0 and ts_distance > 0:
            profit_pct = (self.position.highest_price - self.position.entry_price) / self.position.entry_price
            if profit_pct >= ts_activation and close_price <= self.position.highest_price * (1 - ts_distance):
                return Signal.EXIT, "移动止损"
        # 超时
        if self.position.bars_held >= timeout_bars:
            return Signal.EXIT, f"持仓超时 ({timeout_bars})"
        return Signal.HOLD, ""

    # ── 批处理模式（回测用） ──

    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        period, atr_mult, sl_pct, tp_pct, ts_activation, ts_distance, timeout_bars = self._get_params()
        df = self._compute_indicators(df)

        buy_condition = (df["close"] > df["prev_high_n"]) & (df["prev_close"] <= df["prev_high_n"])
        sell_condition = (df["close"] < df["prev_low_n"]) & (df["prev_close"] >= df["prev_low_n"])

        self.position = None
        signals = []
        reasons = []

        for idx in df.index:
            row = df.loc[idx]
            sig = Signal.HOLD
            reason = ""
            close_price = row["close"]
            current_atr = row.get("atr", np.nan)

            if self.position is not None:
                sig, reason = self._check_exit(close_price, current_atr)

            if self.position is None:
                if buy_condition.loc[idx]:
                    sig = Signal.BUY
                    reason = f"突破 {period}周期高点"
                    self.position = PositionInfo(
                        entry_price=close_price, entry_time=idx,
                        size=1.0, highest_price=close_price,
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
            metadata={"strategy": self.name, "period": period, "atr_multiplier": atr_mult},
        )

    # ── 增量模式（模拟盘用） ──

    def on_bar(self, bar: pd.Series) -> Signal:
        period, *_ = self._get_params()

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
        current_atr = float(current["atr"]) if not pd.isna(current.get("atr", np.nan)) else np.nan

        sig = Signal.HOLD
        if self.position is not None:
            sig, _ = self._check_exit(close_price, current_atr)

        if self.position is None:
            if float(current["prev_high_n"]) > 0 and close_price > current["prev_high_n"] and current["prev_close"] <= current["prev_high_n"]:
                sig = Signal.BUY
                self.position = PositionInfo(
                    entry_price=close_price, entry_time=self._bar_buffer.index[-1],
                    size=1.0, highest_price=close_price,
                )
        elif sig == Signal.HOLD:
            if float(current["prev_low_n"]) > 0 and close_price < current["prev_low_n"] and current["prev_close"] >= current["prev_low_n"]:
                sig = Signal.SELL

        if sig in (Signal.SELL, Signal.EXIT):
            self.position = None

        return sig
