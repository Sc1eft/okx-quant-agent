"""
Self-contained strategy backtesting engine for Streamlit frontend.

Replaces the old AIStrategyExecutor from execution/ai_executor.py.
No dependencies on agent/ or execution/ modules.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger("backtest_engine")

# ──────────────────────────────────────────────
# Indicator calculations (pure pandas)
# ──────────────────────────────────────────────


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=1).mean()


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, pd.Series]:
    ema_fast = _calc_ema(series, fast)
    ema_slow = _calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _calc_ema(macd_line, signal)
    return {"macd": macd_line, "signal": signal_line, "histogram": macd_line - signal_line}


def _calc_bollinger(series: pd.Series, period: int = 20, std: float = 2.0) -> dict[str, pd.Series]:
    sma = _calc_sma(series, period)
    std_dev = series.rolling(window=period, min_periods=1).std()
    return {"middle": sma, "upper": sma + std * std_dev, "lower": sma - std * std_dev}


def _calc_price_change(series: pd.Series, period: int = 1) -> pd.Series:
    return series.pct_change(period) * 100


# ──────────────────────────────────────────────
# Condition evaluation
# ──────────────────────────────────────────────


def _evaluate_condition(cond: dict, indicators: dict) -> bool:
    indicator = cond.get("indicator", "")
    comparison = cond.get("comparison", "greater_than")
    value = cond.get("value")
    cross_with = cond.get("cross_with", "")

    series = indicators.get(indicator)
    if series is None or series.empty:
        return False
    current = series.iloc[-1]
    if pd.isna(current):
        return False

    if comparison in ("greater_than", ">"):
        if value is not None:
            return current > value
        elif cross_with:
            other = indicators.get(cross_with)
            if other is None or len(other) < 2:
                return False
            return current > other.iloc[-1]
        return False

    if comparison in ("greater_or_equal", ">=", "≥"):
        if value is not None:
            return current >= value
        elif cross_with:
            other = indicators.get(cross_with)
            if other is None or len(other) < 2:
                return False
            return current >= other.iloc[-1]
        return False

    if comparison in ("less_than", "<"):
        if value is not None:
            return current < value
        elif cross_with:
            other = indicators.get(cross_with)
            if other is None or len(other) < 2:
                return False
            return current < other.iloc[-1]
        return False

    if comparison in ("less_or_equal", "<=", "≤"):
        if value is not None:
            return current <= value
        elif cross_with:
            other = indicators.get(cross_with)
            if other is None or len(other) < 2:
                return False
            return current <= other.iloc[-1]
        return False

    if comparison == "crosses_above":
        if len(series) < 2:
            return False
        if value is not None:
            return series.iloc[-2] <= value and current > value
        else:
            other = indicators.get(cross_with)
            if other is None or len(other) < 2:
                return False
            return series.iloc[-2] <= other.iloc[-2] and current > other.iloc[-1]

    if comparison == "crosses_below":
        if len(series) < 2:
            return False
        if value is not None:
            return series.iloc[-2] >= value and current < value
        else:
            other = indicators.get(cross_with)
            if other is None or len(other) < 2:
                return False
            return series.iloc[-2] >= other.iloc[-2] and current < other.iloc[-1]

    if comparison == "consecutive_gain":
        n = int(value) if value else 3
        if len(series) < n + 1:
            return False
        for i in range(-n, 0):
            if series.iloc[i - 1] >= series.iloc[i]:
                return False
        return True

    if comparison == "consecutive_loss":
        n = int(value) if value else 3
        if len(series) < n + 1:
            return False
        for i in range(-n, 0):
            if series.iloc[i - 1] <= series.iloc[i]:
                return False
        return True

    return False


# ──────────────────────────────────────────────
# Aggregate indicator builder
# ──────────────────────────────────────────────


def _calc_indicators(df: pd.DataFrame) -> dict[str, pd.Series]:
    close = df["close"]
    volume = df["volume"]

    ind: dict[str, pd.Series] = {
        "close": close,
        "high": df["high"],
        "low": df["low"],
        "volume": volume,
    }

    for p in [6, 14, 20]:
        if len(close) >= p:
            ind[f"rsi_{p}"] = _calc_rsi(close, p)

    for p in [5, 10, 20, 50, 200]:
        if len(close) >= p:
            ind[f"sma_{p}"] = _calc_sma(close, p)
            ind[f"ema_{p}"] = _calc_ema(close, p)

    if len(close) >= 26:
        macd = _calc_macd(close)
        ind["macd"] = macd["macd"]
        ind["macd_signal"] = macd["signal"]
        ind["macd_histogram"] = macd["histogram"]

    if len(close) >= 20:
        bb = _calc_bollinger(close)
        ind["bb_middle"] = bb["middle"]
        ind["bb_upper"] = bb["upper"]
        ind["bb_lower"] = bb["lower"]

    ind["price_change_pct"] = _calc_price_change(close, 1)

    body_size = (close - df["open"]).abs()
    ind["body_size"] = body_size
    ind["body_sum_2"] = body_size.rolling(window=2, min_periods=1).sum()
    direction_s = pd.Series(0, index=close.index)
    direction_s[close < df["open"]] = 1
    direction_s[close > df["open"]] = -1
    ind["body_direction"] = direction_s

    return ind


def _check_conditions(conditions: list[dict], indicators: dict) -> list[dict]:
    """Return list of triggered conditions"""
    triggered = []
    for cond in conditions:
        ind_name = cond.get("indicator", "")
        series = indicators.get(ind_name)
        if series is None:
            continue
        ind_map = dict(indicators)
        cross_with = cond.get("cross_with", "")
        if cross_with and cross_with in indicators:
            ind_map[cross_with] = indicators[cross_with]
        ind_map[ind_name] = series
        if _evaluate_condition(cond, ind_map):
            triggered.append(cond)
    return triggered


# ──────────────────────────────────────────────
# Backtest engine — replaces AIStrategyExecutor
# ──────────────────────────────────────────────


class BacktestEngine:
    """Lightweight backtesting engine for strategy rules on K-line data.

    Replaces the old AIStrategyExecutor. Self-contained.
    """

    def __init__(
        self,
        rules: dict,
        initial_balance: float = 10000.0,
        mode: str = "paper",
    ):
        self.rules = rules
        self.initial_balance = initial_balance
        self.mode = mode
        self._reset_state()

    def _reset_state(self):
        self.bar_buffer = pd.DataFrame()
        self.indicators: dict[str, pd.Series] = {}

        # Account
        self.balance = self.initial_balance
        self.position = 0.0
        self.position_cost = 0.0
        self.short_position = 0.0
        self.short_position_cost = 0.0
        self.trades: list[dict] = []
        self.equity_history: list[dict] = []
        self.last_price = 0.0

        # Position state
        self.in_position = False
        self.position_side = ""
        self.entry_price = 0.0
        self.entry_time: Optional[str] = None
        self.highest_since_entry = 0.0
        self.lowest_since_entry = 0.0
        self.bars_since_entry = 0

        # Multi-level trailing stop
        self.multi_tp_level = 0
        self.dynamic_stop_price = 0.0
        self.partial_close_done = False

        # Cooling
        self._bar_idx = 0
        self._last_long_bar = -999
        self._last_short_bar = -999
        self._prev_trade_loss = False

        # Stats
        self.total_signals = 0
        self.last_signal = "hold"
        self.last_signal_reason = ""

        # AI signal flag
        self.ai_signal_consumed = False
        self.ai_signal_skip_entry = False

    @property
    def equity(self) -> float:
        if self.in_position and self.position_side == "long":
            return self.balance + self.position * self.last_price - self.position_cost
        elif self.in_position and self.position_side == "short":
            unrealized = self.short_position_cost - self.short_position * self.last_price
            return self.balance + unrealized
        return self.balance

    def on_bar(self, bar: pd.Series) -> dict:
        """Process one K-line bar, return state."""
        self._append_bar(bar)
        ind = self._calc()
        self.last_price = float(bar["close"])
        self._bar_idx += 1
        if self.in_position:
            self.bars_since_entry += 1

        signal = "hold"
        reason = ""
        stype = self.rules.get("_strategy_type", "")
        price = float(bar["close"])

        # ── Exit check (in position) ──
        if self.in_position:
            exit_reason = self._check_hard_stops(price)
            if not exit_reason and stype == "volatility_contrarian":
                exit_reason = self._check_multi_trailing_stop(price)
            if exit_reason:
                self._execute_exit(price, reason=exit_reason)
                signal = "sell"
                reason = exit_reason
            elif stype != "volatility_contrarian":
                exit_conditions = self.rules.get("exit_conditions", [])
                if exit_conditions:
                    triggered = _check_conditions(exit_conditions, ind)
                    if triggered:
                        logic = self.rules.get("_condition_logic", "any")
                        met = len(triggered) == len(exit_conditions) if logic == "all" else len(triggered) > 0
                        if met:
                            r = " + ".join(t.get("indicator", "") for t in triggered) + " 条件触发"
                            self._execute_exit(price, reason=r)
                            signal = "sell"
                            reason = r

        # ── Entry check (no position) ──
        if not self.in_position:
            if stype == "volatility_contrarian":
                direction = self._check_volatility_triggers(bar)
                if direction:
                    cd_ok, cd_r = self._check_cd(direction)
                    if cd_ok:
                        dir_label = "多头" if direction == "long" else "空头"
                        self._execute_entry(price, direction=direction, reason=f"波动率反向开{dir_label}")
                        signal = "buy" if direction == "long" else "sell"
                        reason = f"波动率触发反向开{dir_label}"
                    else:
                        signal = "blocked"
                        reason = cd_r
            elif stype == "ai_signal" and not self.ai_signal_consumed and not self.ai_signal_skip_entry:
                ai_sig = self.rules.get("ai_signal", {})
                direction = ai_sig.get("original_direction", "")
                if direction in ("long", "short"):
                    dir_label = "多头" if direction == "long" else "空头"
                    self._execute_entry(price, direction=direction, reason=f"AI信号开{dir_label}")
                    self.ai_signal_consumed = True
                    signal = "buy" if direction == "long" else "sell"
                    reason = f"AI信号开{dir_label}"
            else:
                entry_conditions = self.rules.get("entry_conditions", [])
                if entry_conditions:
                    triggered = _check_conditions(entry_conditions, ind)
                    if triggered:
                        logic = self.rules.get("_condition_logic", "any")
                        met = len(triggered) == len(entry_conditions) if logic == "all" else len(triggered) > 0
                        if met:
                            r = " + ".join(t.get("indicator", "") for t in triggered) + " 条件触发"
                            self._execute_entry(price, direction="long", reason=r)
                            signal = "buy"
                            reason = r

        self.last_signal = signal
        self.last_signal_reason = reason
        return self.get_state()

    def get_state(self) -> dict:
        """Return full state dict matching AIStrategyExecutor format."""
        remaining = 0
        if self.position_side == "long" and self._last_long_bar > 0:
            remaining = max(0, 8 - (self._bar_idx - self._last_long_bar))
        elif self.position_side == "short" and self._last_short_bar > 0:
            remaining = max(0, 8 - (self._bar_idx - self._last_short_bar))

        return {
            "running": True,
            "mode": self.mode,
            "signal": self.last_signal,
            "signal_reason": self.last_signal_reason,
            "in_position": self.in_position,
            "position_side": self.position_side,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time or "",
            "bars_since_entry": self.bars_since_entry,
            "multi_tp_level": self.multi_tp_level,
            "dynamic_stop_price": self.dynamic_stop_price,
            "partial_close_done": self.partial_close_done,
            "cooldown_remaining": remaining,
            "prev_trade_loss": self._prev_trade_loss,
            "account": {
                "initial_balance": self.initial_balance,
                "balance": self.balance,
                "equity": self.equity,
                "in_position": self.in_position,
                "position_side": self.position_side,
                "entry_price": self.entry_price,
                "entry_time": self.entry_time or "",
                "bars_since_entry": self.bars_since_entry,
                "multi_tp_level": self.multi_tp_level,
                "dynamic_stop_price": self.dynamic_stop_price,
                "partial_close_done": self.partial_close_done,
                "cooldown_remaining": remaining,
                "prev_trade_loss": self._prev_trade_loss,
                "trades": self.trades,
            },
            "total_signals": self.total_signals,
            "total_trades": len(self.trades),
            "rules": self.rules,
            "buffer_size": len(self.bar_buffer),
            "strategy_name": self.rules.get("strategy_name", "AI策略"),
            "ai_signal_consumed": self.ai_signal_consumed,
        }

    # ── Internal methods ──

    def _append_bar(self, bar):
        if self.bar_buffer.empty:
            self.bar_buffer = bar.to_frame().T
        else:
            self.bar_buffer = pd.concat([self.bar_buffer, bar.to_frame().T]).tail(300)

    def _calc(self) -> dict:
        if self.bar_buffer.empty:
            return {}
        self.indicators = _calc_indicators(self.bar_buffer)
        return self.indicators

    def _position_pnl_pct(self, price: float) -> float:
        if self.position_side == "long" and self.entry_price > 0:
            return (price - self.entry_price) / self.entry_price * 100
        elif self.position_side == "short" and self.entry_price > 0:
            return (self.entry_price - price) / self.entry_price * 100
        return 0.0

    def _check_hard_stops(self, price: float) -> Optional[str]:
        if not self.in_position or self.entry_price <= 0:
            return None
        risk = self.rules.get("risk_params", {})
        self.highest_since_entry = max(self.highest_since_entry, price)
        if self.position_side == "short":
            self.lowest_since_entry = min(self.lowest_since_entry, price)
        pnl = self._position_pnl_pct(price)
        sl = risk.get("stop_loss_pct")
        if sl and sl > 0 and pnl <= -sl:
            return f"止损触发 (-{sl}%)"
        tp = risk.get("take_profit_pct")
        if tp and tp > 0 and pnl >= tp:
            return f"止盈触发 (+{tp}%)"
        timeout = risk.get("position_timeout_bars", 0)
        if timeout and timeout > 0 and self.bars_since_entry >= timeout:
            return f"持仓超时 ({timeout}根K线)"
        return None

    def _check_volatility_triggers(self, bar: pd.Series) -> Optional[str]:
        risk = self.rules.get("risk_params", {})
        body_threshold = risk.get("volatility_body_threshold", 15.0)
        sum_threshold = risk.get("volatility_sum_threshold", 20.0)
        close = float(bar["close"])
        open_ = float(bar["open"])
        body = abs(close - open_)
        two_bar_sum = body
        if len(self.bar_buffer) >= 2:
            prev = self.bar_buffer.iloc[-2]
            two_bar_sum += abs(float(prev["close"]) - float(prev["open"]))
        if not (body > body_threshold or two_bar_sum > sum_threshold):
            return None
        if close < open_:
            return "long"
        elif close > open_:
            return "short"
        return None

    def _check_cd(self, direction: str):
        risk = self.rules.get("risk_params", {})
        cd = risk.get("cooldown_bars", 8)
        if direction == "long":
            since = self._bar_idx - self._last_long_bar
            if since < cd:
                return False, f"多头冷却中 ({since}/{cd}根)"
        elif direction == "short":
            since = self._bar_idx - self._last_short_bar
            if since < cd:
                return False, f"空头冷却中 ({since}/{cd}根)"
        return True, ""

    def _get_position_size(self, price: float) -> float:
        risk = self.rules.get("risk_params", {})
        cap = self.equity
        ml = risk.get("max_loss_pct", 3.0) / 100.0
        sl = risk.get("stop_loss_pct", 1.25) / 100.0
        max_loss = cap * ml
        if sl <= 0:
            pv = max_loss * 10
        else:
            pv = max_loss / sl
        pv = min(pv, self.balance * 10)
        if pv <= 0:
            return 0.0
        size = pv / price
        if self._prev_trade_loss:
            size *= 0.5
        return size if size >= 0.001 else 0.0

    def _check_multi_trailing_stop(self, price: float) -> Optional[str]:
        if not self.in_position or self.entry_price <= 0:
            return None
        pnl = self._position_pnl_pct(price)
        if self.dynamic_stop_price > 0:
            if self.position_side == "long" and price <= self.dynamic_stop_price:
                return f"多级移动止盈触发 (级别{self.multi_tp_level})"
            elif self.position_side == "short" and price >= self.dynamic_stop_price:
                return f"多级移动止盈触发 (级别{self.multi_tp_level})"
        if pnl >= 1.25 and self.multi_tp_level < 1:
            self.dynamic_stop_price = self.entry_price
            self.multi_tp_level = 1
        if pnl >= 2.5 and self.multi_tp_level < 2:
            self.dynamic_stop_price = self.entry_price * (1.0125 if self.position_side == "long" else 0.9875)
            self.multi_tp_level = 2
        if pnl >= 5.0 and self.multi_tp_level < 3 and not self.partial_close_done:
            self._partial_close(price)
            self.partial_close_done = True
            self.dynamic_stop_price = self.entry_price * (1.025 if self.position_side == "long" else 0.975)
            self.multi_tp_level = 3
        return None

    def _partial_close(self, price: float):
        fee = 0.001
        if self.position_side == "long" and self.position > 0.001:
            half = self.position * 0.5
            proceed = half * price * (1 - fee)
            cost = half * (self.position_cost / self.position) if self.position > 0 else 0
            pnl = proceed - cost
            self.balance += proceed
            self.position -= half
            self.position_cost *= (1 - 0.5)
            self.trades.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "side": "partial_close_long",
                "price": price,
                "size": half,
                "pnl": round(pnl, 2),
                "fee": round(half * price * fee, 2),
            })
        elif self.position_side == "short" and self.short_position > 0.001:
            half = self.short_position * 0.5
            cost = half * price * (1 + fee)
            revenue = half * (self.short_position_cost / self.short_position) if self.short_position > 0 else 0
            pnl = revenue - cost
            self.balance -= cost
            self.short_position -= half
            self.short_position_cost *= (1 - 0.5)
            self.trades.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "side": "partial_close_short",
                "price": price,
                "size": half,
                "pnl": round(pnl, 2),
                "fee": round(half * price * fee, 2),
            })

    def _execute_entry(self, price: float, direction: str = "long", reason: str = ""):
        size = self._get_position_size(price)
        if size <= 0:
            return
        fee = 0.001
        if direction == "long":
            cost = size * price * (1 + fee)
            self.balance -= cost
            self.position += size
            self.position_cost += cost
            self.trades.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "side": "buy",
                "price": price,
                "size": size,
                "pnl": 0,
                "fee": round(size * price * fee, 2),
            })
        elif direction == "short":
            revenue = size * price * (1 - fee)
            self.balance += revenue
            self.short_position += size
            self.short_position_cost += revenue
            self.trades.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "side": "sell",
                "price": price,
                "size": size,
                "pnl": 0,
                "fee": round(size * price * fee, 2),
            })
        else:
            return
        self.in_position = True
        self.position_side = direction
        self.entry_price = price
        self.entry_time = datetime.now(timezone.utc).isoformat()
        self.highest_since_entry = price
        self.lowest_since_entry = price
        self.bars_since_entry = 0
        self.multi_tp_level = 0
        self.dynamic_stop_price = 0.0
        self.partial_close_done = False
        if direction == "long":
            self._last_long_bar = self._bar_idx
        elif direction == "short":
            self._last_short_bar = self._bar_idx
        self.total_signals += 1

    def _execute_exit(self, price: float, reason: str = ""):
        fee = 0.001
        if self.position_side == "long" and self.position > 0.001:
            proceed = self.position * price * (1 - fee)
            pnl = proceed - self.position_cost
            self.trades.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "side": "sell",
                "price": price,
                "size": self.position,
                "pnl": round(pnl, 2),
                "fee": round(self.position * price * fee, 2),
            })
            self.balance += proceed
            self._prev_trade_loss = pnl < 0
            self.position = 0.0
            self.position_cost = 0.0
        elif self.position_side == "short" and self.short_position > 0.001:
            cost = self.short_position * price * (1 + fee)
            pnl = self.short_position_cost - cost
            self.trades.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "side": "buy",
                "price": price,
                "size": self.short_position,
                "pnl": round(pnl, 2),
                "fee": round(self.short_position * price * fee, 2),
            })
            self.balance -= cost
            self._prev_trade_loss = pnl < 0
            self.short_position = 0.0
            self.short_position_cost = 0.0
        self.in_position = False
        self.position_side = ""
        self.entry_price = 0.0
        self.total_signals += 1
