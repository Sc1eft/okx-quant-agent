"""Backtest engine with next-bar execution and conservative OHLC exits."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from config import Config
from strategies.base import Signal, create_strategy, get_available_strategies
from backtest.metrics import compute_metrics

logger = logging.getLogger("backtest.engine")


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    side: str
    size: float
    pnl: float
    pnl_pct: float
    fee: float
    reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    strategy_name: str
    trades: list[Trade]
    equity_curve: pd.Series
    metrics: dict
    signals_df: pd.DataFrame
    fee_model: str
    slippage_pct: float


class BacktestEngine:
    """Long-only spot backtester using no-lookahead signal execution."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.initial_capital = 10000.0

    def run(self, df: pd.DataFrame, strategy_name: Optional[str] = None,
            params: Optional[dict] = None, order_type: str = "market") -> BacktestResult:
        if df.empty:
            raise ValueError("Cannot backtest empty data")
        if order_type not in {"market", "limit"}:
            raise ValueError("order_type must be 'market' or 'limit'")

        if strategy_name:
            strategies = [create_strategy(strategy_name, params)]
        else:
            strategies = [create_strategy(name, self._strategy_params()) for name in self.cfg.strategy.enabled_strategies]
        if not strategies:
            raise ValueError("No enabled strategies")

        signal_frames = [strategy.generate_signals(df).signals for strategy in strategies]
        signals_df = self._combine_signals(signal_frames, strategies)
        trades: list[Trade] = []
        equity = self.initial_capital
        equity_curve = []
        position = entry_price = entry_fee = highest_price = 0.0
        entry_time: Optional[pd.Timestamp] = None
        fee_rate = self.cfg.trading.taker_fee if order_type == "market" else self.cfg.trading.maker_fee
        slippage = self.cfg.trading.slippage_pct / 100 if order_type == "market" else 0.0
        pending_signal, pending_reason, pending_reference_price = Signal.HOLD, "", 0.0

        for idx, row in signals_df.iterrows():
            open_price, high_price = float(row["open"]), float(row["high"])
            low_price, close_price = float(row["low"]), float(row["close"])

            if pending_signal == Signal.BUY and position == 0:
                fill_price = self._entry_fill_price(order_type, pending_reference_price, open_price, low_price, slippage)
                if fill_price is not None:
                    position = equity * self.cfg.risk.max_single_order_pct / fill_price
                    entry_fee = fill_price * position * fee_rate
                    equity -= fill_price * position + entry_fee
                    entry_price, entry_time, highest_price = fill_price, idx, open_price
            elif pending_signal in (Signal.SELL, Signal.EXIT) and position > 0:
                fill_price = self._exit_fill_price(order_type, pending_reference_price, open_price, high_price, slippage)
                if fill_price is not None:
                    equity, position, entry_price, entry_time, entry_fee, highest_price = self._close_position(
                        trades, equity, position, entry_price, entry_time, entry_fee, idx, fill_price, fee_rate, pending_reason)
            pending_signal = Signal.HOLD

            if position > 0:
                intrabar_exit = self._intrabar_exit_price(open_price, high_price, low_price, entry_price, highest_price)
                if intrabar_exit is not None:
                    fill_price, reason = intrabar_exit
                    equity, position, entry_price, entry_time, entry_fee, highest_price = self._close_position(
                        trades, equity, position, entry_price, entry_time, entry_fee, idx,
                        fill_price * (1 - slippage), fee_rate, reason)
                else:
                    highest_price = max(highest_price, high_price)

            signal = row["signal"]
            if position == 0 or signal in (Signal.SELL, Signal.EXIT):
                pending_signal, pending_reason, pending_reference_price = signal, row.get("reason", ""), close_price
            equity_curve.append({"time": idx, "equity": equity + position * close_price if position > 0 else equity})

        if position > 0 and entry_time is not None:
            last_idx, last_close = signals_df.index[-1], float(signals_df.iloc[-1]["close"])
            equity, position, entry_price, entry_time, entry_fee, highest_price = self._close_position(
                trades, equity, position, entry_price, entry_time, entry_fee, last_idx,
                last_close * (1 - slippage), fee_rate, "end_of_data")
            equity_curve[-1]["equity"] = equity

        equity_series = pd.DataFrame(equity_curve).set_index("time")["equity"]
        metrics = compute_metrics(equity_series, trades, self.initial_capital, signals_df)
        return BacktestResult(
            symbol=self.cfg.trading.symbol,
            strategy_name=strategy_name or "+".join(strategy.name for strategy in strategies),
            trades=trades, equity_curve=equity_series, metrics=metrics, signals_df=signals_df,
            fee_model=order_type, slippage_pct=self.cfg.trading.slippage_pct if order_type == "market" else 0.0)

    def _strategy_params(self) -> dict:
        return {
            "stop_loss_pct": self.cfg.strategy.stop_loss_pct,
            "take_profit_pct": self.cfg.strategy.take_profit_pct,
            "trailing_stop_activation": self.cfg.strategy.trailing_stop_activation,
            "trailing_stop_distance": self.cfg.strategy.trailing_stop_distance,
            "position_timeout_bars": self.cfg.strategy.position_timeout_bars,
        }

    def _combine_signals(self, signal_frames: list[pd.DataFrame], strategies: list) -> pd.DataFrame:
        combined = signal_frames[0].copy()
        combined["signal"], combined["reason"] = Signal.HOLD, ""
        for idx in combined.index:
            buy_weight = sell_weight = 0.0
            reasons = []
            for frame, strategy in zip(signal_frames, strategies):
                signal = frame.at[idx, "signal"]
                weight = self.cfg.strategy.strategy_weights.get(strategy.name, 1.0)
                if signal == Signal.BUY:
                    buy_weight += weight
                    reasons.append(f"{strategy.name}:buy")
                elif signal in (Signal.SELL, Signal.EXIT):
                    sell_weight += weight
                    reasons.append(f"{strategy.name}:{signal.value}")
            if buy_weight > sell_weight and buy_weight > 0:
                combined.at[idx, "signal"] = Signal.BUY
            elif sell_weight > buy_weight and sell_weight > 0:
                combined.at[idx, "signal"] = Signal.SELL
            combined.at[idx, "reason"] = "; ".join(reasons)
        return combined

    @staticmethod
    def _entry_fill_price(order_type: str, reference: float, open_price: float,
                          low_price: float, slippage: float) -> Optional[float]:
        if order_type == "market":
            return open_price * (1 + slippage)
        limit_price = reference * (1 - 0.0005)
        return limit_price if low_price <= limit_price else None

    @staticmethod
    def _exit_fill_price(order_type: str, reference: float, open_price: float,
                         high_price: float, slippage: float) -> Optional[float]:
        if order_type == "market":
            return open_price * (1 - slippage)
        limit_price = reference * (1 + 0.0005)
        return limit_price if high_price >= limit_price else None

    def _intrabar_exit_price(self, open_price: float, high_price: float, low_price: float,
                             entry_price: float, highest_price: float) -> Optional[tuple[float, str]]:
        """Conservative OHLC exit model: stop loss wins if stop and target share a bar."""
        stop_price = entry_price * (1 - self.cfg.strategy.stop_loss_pct / 100)
        target_price = entry_price * (1 + self.cfg.strategy.take_profit_pct / 100)
        if open_price <= stop_price:
            return open_price, "stop_loss_gap"
        if low_price <= stop_price:
            return stop_price, "stop_loss"
        activation = self.cfg.strategy.trailing_stop_activation / 100
        distance = self.cfg.strategy.trailing_stop_distance / 100
        if activation > 0 and distance > 0 and highest_price >= entry_price * (1 + activation):
            trail_price = highest_price * (1 - distance)
            if open_price <= trail_price:
                return open_price, "trailing_stop_gap"
            if low_price <= trail_price:
                return trail_price, "trailing_stop"
        if open_price >= target_price:
            return open_price, "take_profit_gap"
        if high_price >= target_price:
            return target_price, "take_profit"
        return None

    @staticmethod
    def _close_position(trades: list[Trade], equity: float, position: float, entry_price: float,
                        entry_time: pd.Timestamp, entry_fee: float, exit_time: pd.Timestamp,
                        exit_price: float, fee_rate: float, reason: str) -> tuple[float, float, float, Optional[pd.Timestamp], float, float]:
        exit_fee = exit_price * position * fee_rate
        pnl = (exit_price - entry_price) * position - entry_fee - exit_fee
        trades.append(Trade(
            entry_time=entry_time, exit_time=exit_time, entry_price=round(entry_price, 2),
            exit_price=round(exit_price, 2), side="long", size=round(position, 6), pnl=round(pnl, 2),
            pnl_pct=round(pnl / (entry_price * position) * 100, 2), fee=round(entry_fee + exit_fee, 4), reason=reason))
        return equity + exit_price * position - exit_fee, 0.0, 0.0, None, 0.0, 0.0

    def run_all_strategies(self, df: pd.DataFrame, order_type: str = "market") -> dict[str, BacktestResult]:
        results = {}
        for name in get_available_strategies():
            try:
                results[name] = self.run(df, strategy_name=name, order_type=order_type)
            except Exception as exc:
                logger.error("strategy %s failed: %s", name, exc)
        return results

    def run_order_type_comparison(self, df: pd.DataFrame, strategy_name: str) -> dict:
        market_result = self.run(df, strategy_name=strategy_name, order_type="market")
        limit_result = self.run(df, strategy_name=strategy_name, order_type="limit")
        return {
            "market": {"total_return": market_result.metrics["total_return_pct"], "sharpe": market_result.metrics["sharpe"], "trade_count": len(market_result.trades), "fee_model": "taker"},
            "limit": {"total_return": limit_result.metrics["total_return_pct"], "sharpe": limit_result.metrics["sharpe"], "trade_count": len(limit_result.trades), "fee_model": "maker, next-bar OHLC fill"},
        }

    def report(self, result: BacktestResult):
        metrics = result.metrics
        print("\n" + "=" * 60)
        print(f"Backtest: {result.strategy_name} @ {result.symbol}")
        print(f"Order type: {result.fee_model}")
        print(f"Initial capital: ${self.initial_capital:,.2f}")
        print(f"Final equity: ${metrics.get('final_equity', 0):,.2f}")
        print(f"Total return: {metrics.get('total_return_pct', 0):+.2f}%")
        print(f"Max drawdown: {metrics.get('max_drawdown_pct', 0):.2f}%")
        print(f"Sharpe ratio: {metrics.get('sharpe', 0):.2f}")
        print(f"Trades: {metrics.get('total_trades', 0)}")
        print("=" * 60 + "\n")
