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

    def __init__(self, name: str, params: dict):
        super().__init__(name, params)
        self._min_bars = max(params.get("short_window", 7), params.get("long_window", 25))

    @property
    def description(self) -> str:
        sw = self.params["short_window"]
        lw = self.params["long_window"]
        return f"MA 交叉 (MA{sw}/MA{lw}) + 止盈止损移动止损"

    # ── 私有参数读取 ──

    def _get_params(self) -> tuple:
        return (
            self.params["short_window"],
            self.params["long_window"],
            self.params.get("stop_loss_pct", 2.0) / 100,
            self.params.get("take_profit_pct", 6.0) / 100,
            self.params.get("trailing_stop_activation", 3.0) / 100,
            self.params.get("trailing_stop_distance", 1.5) / 100,
            self.params.get("position_timeout_bars", 48),
        )

    # ── 指标计算 ──

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        sw, lw, *_ = self._get_params()
        df = df.copy()
        df["ma_short"] = df["close"].rolling(window=sw, min_periods=sw).mean()
        df["ma_long"] = df["close"].rolling(window=lw, min_periods=lw).mean()
        df["prev_short"] = df["ma_short"].shift(1)
        df["prev_long"] = df["ma_long"].shift(1)
        return df

    # ── 单步退出检查（用于 on_bar） ──

    def _check_exit(self, close_price: float) -> tuple[Signal, str]:
        """检查退出条件，返回 (signal, reason)"""
        sl_pct, tp_pct, ts_activation, ts_distance, timeout_bars = self._get_params()[2:]
        sig = Signal.HOLD
        reason = ""

        self.position.bars_held += 1
        self.position.highest_price = max(self.position.highest_price, close_price)

        if sl_pct > 0 and close_price <= self.position.entry_price * (1 - sl_pct):
            return Signal.EXIT, f"止损 {sl_pct*100:.1f}% (入场 {self.position.entry_price:.1f}, 当前 {close_price:.1f})"

        if tp_pct > 0 and close_price >= self.position.entry_price * (1 + tp_pct):
            return Signal.EXIT, f"止盈 {tp_pct*100:.1f}% (入场 {self.position.entry_price:.1f}, 当前 {close_price:.1f})"

        if ts_activation > 0 and ts_distance > 0:
            profit_pct = (self.position.highest_price - self.position.entry_price) / self.position.entry_price
            if profit_pct >= ts_activation:
                trail_stop = self.position.highest_price * (1 - ts_distance)
                if close_price <= trail_stop:
                    return Signal.EXIT, f"移动止损 (最高 {self.position.highest_price:.1f}, 回落至 {close_price:.0f})"

        if self.position.bars_held >= timeout_bars:
            return Signal.EXIT, f"持仓超时 ({timeout_bars} 根)"

        return Signal.HOLD, ""

    # ── 批处理模式（回测用） ──

    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        sw, lw, sl_pct, tp_pct, ts_activation, ts_distance, timeout_bars = self._get_params()
        df = self._compute_indicators(df)

        buy_condition = (
            (df["prev_short"] < df["prev_long"]) &
            (df["ma_short"] > df["ma_long"])
        )
        sell_condition = (
            (df["prev_short"] > df["prev_long"]) &
            (df["ma_short"] < df["ma_long"])
        )

        self.position = None
        signals = []
        exit_reasons = []

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
                    reason = f"MA{sw} 上穿 MA{lw}"
                    self.position = PositionInfo(
                        entry_price=close_price, entry_time=idx,
                        size=1.0, highest_price=close_price,
                    )
            else:
                if sell_condition.loc[idx] and sig == Signal.HOLD:
                    sig = Signal.SELL
                    reason = f"MA{sw} 下穿 MA{lw}"

            if sig in (Signal.SELL, Signal.EXIT):
                self.position = None

            signals.append(sig)
            exit_reasons.append(reason)

        df["signal"] = signals
        df["reason"] = exit_reasons

        return StrategyResult(
            signals=df,
            metadata={"strategy": self.name, "short_window": sw, "long_window": lw, "params": dict(self.params)},
        )

    # ── 增量模式（模拟盘用） ──

    def on_bar(self, bar: pd.Series) -> Signal:
        sw, lw, *_ = self._get_params()

        # 追加到缓冲区
        new_df = bar.to_frame().T.infer_objects(copy=False)
        if self._bar_buffer is None:
            self._bar_buffer = new_df
        else:
            self._bar_buffer = pd.concat([self._bar_buffer, new_df])

        if len(self._bar_buffer) < self._min_bars:
            return Signal.HOLD

        # 计算指标
        df = self._compute_indicators(self._bar_buffer)
        current = df.iloc[-1]
        close_price = float(current["close"])

        # 有持仓 → 检查退出
        sig = Signal.HOLD
        if self.position is not None:
            sig, _ = self._check_exit(close_price)

        # 无持仓 → 检查入场
        if self.position is None:
            if current["prev_short"] < current["prev_long"] and current["ma_short"] > current["ma_long"]:
                sig = Signal.BUY
                self.position = PositionInfo(
                    entry_price=close_price, entry_time=self._bar_buffer.index[-1],
                    size=1.0, highest_price=close_price,
                )
        elif sig == Signal.HOLD:
            # 有持仓 → 检查趋势反转
            if current["prev_short"] > current["prev_long"] and current["ma_short"] < current["ma_long"]:
                sig = Signal.SELL

        if sig in (Signal.SELL, Signal.EXIT):
            self.position = None

        return sig
