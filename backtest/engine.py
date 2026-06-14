"""
回测引擎
支持多策略并行、手续费/滑点计算、止盈止损
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np

from config import Config
from strategies.base import Signal, create_strategy, get_available_strategies
from backtest.metrics import compute_metrics

logger = logging.getLogger("backtest.engine")


@dataclass
class Trade:
    """单笔交易记录"""
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    side: str  # "long"
    size: float
    pnl: float
    pnl_pct: float
    fee: float
    reason: str = ""


@dataclass
class BacktestResult:
    """回测结果"""
    symbol: str
    strategy_name: str
    trades: list[Trade]
    equity_curve: pd.Series  # index=time, values=equity
    metrics: dict
    signals_df: pd.DataFrame
    fee_model: str
    slippage_pct: float


class BacktestEngine:
    """
    回测引擎
    - 多策略独立回测
    - 可选 market / limit 订单类型
    - 手续费 + 滑点
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.initial_capital = 10000.0  # 初始资金 $10000

    def run(
        self,
        df: pd.DataFrame,
        strategy_name: Optional[str] = None,
        params: Optional[dict] = None,
        order_type: str = "market",
    ) -> BacktestResult:
        """运行回测"""
        strategies = []

        if strategy_name:
            # 单一策略
            strat = create_strategy(strategy_name, params)
            strategies = [strat]
        else:
            # 所有启用的策略
            for name in self.cfg.strategy.enabled_strategies:
                weight = self.cfg.strategy.strategy_weights.get(name, 1.0)
                strat = create_strategy(name, {
                    "stop_loss_pct": self.cfg.strategy.stop_loss_pct,
                    "take_profit_pct": self.cfg.strategy.take_profit_pct,
                    "trailing_stop_activation": self.cfg.strategy.trailing_stop_activation,
                    "trailing_stop_distance": self.cfg.strategy.trailing_stop_distance,
                    "position_timeout_bars": self.cfg.strategy.position_timeout_bars,
                })
                strategies.append(strat)

        if not strategies:
            raise ValueError("没有可运行的策略")

        # 用第一个（或唯一）策略
        strat = strategies[0]
        result = strat.generate_signals(df)

        trades = []
        equity = self.initial_capital
        equity_curve = []
        position = 0.0  # 持仓数量
        entry_price = 0.0
        entry_time = None
        fee_rate = self.cfg.trading.taker_fee if order_type == "market" else self.cfg.trading.maker_fee
        slippage = self.cfg.trading.slippage_pct / 100 if order_type == "market" else 0.0

        for idx, row in result.signals.iterrows():
            sig = row["signal"]
            price = float(row["close"])

            if sig == Signal.BUY and position == 0:
                # 买入
                exec_price = price * (1 + slippage)
                position = equity * self.cfg.risk.max_single_order_pct / exec_price
                fee = exec_price * position * fee_rate
                equity -= exec_price * position + fee
                entry_price = exec_price
                entry_time = idx

            elif sig in (Signal.SELL, Signal.EXIT) and position > 0:
                # 卖出/退出
                exec_price = price * (1 - slippage)
                fee = exec_price * position * fee_rate
                pnl = (exec_price - entry_price) * position - fee * 2  # 进出双倍费用

                if entry_time:
                    hold_time = (idx - entry_time).total_seconds() / 3600

                    trades.append(Trade(
                        entry_time=entry_time,
                        exit_time=idx,
                        entry_price=round(entry_price, 2),
                        exit_price=round(exec_price, 2),
                        side="long",
                        size=round(position, 6),
                        pnl=round(pnl, 2),
                        pnl_pct=round(pnl / (entry_price * position) * 100, 2),
                        fee=round(fee * 2, 4),
                        reason=row.get("reason", ""),
                    ))

                equity += exec_price * position - fee
                position = 0.0
                entry_price = 0.0
                entry_time = None

            # 每日估值（持仓按市价）
            current_value = equity + position * price if position > 0 else equity
            equity_curve.append({"time": idx, "equity": current_value})

        equity_series = pd.DataFrame(equity_curve).set_index("time")["equity"]
        metrics = compute_metrics(equity_series, trades, self.initial_capital, df)

        return BacktestResult(
            symbol=self.cfg.trading.symbol,
            strategy_name=strat.name,
            trades=trades,
            equity_curve=equity_series,
            metrics=metrics,
            signals_df=result.signals,
            fee_model=order_type,
            slippage_pct=self.cfg.trading.slippage_pct,
        )

    def run_all_strategies(
        self,
        df: pd.DataFrame,
        order_type: str = "market",
    ) -> dict[str, BacktestResult]:
        """运行所有策略"""
        results = {}
        for name in get_available_strategies():
            try:
                result = self.run(df, strategy_name=name, order_type=order_type)
                results[name] = result
                logger.info(f"策略 {name}: {result.metrics.get('total_return_pct', 0):.2f}%")
            except Exception as e:
                logger.error(f"策略 {name} 回测失败: {e}")
        return results

    def run_order_type_comparison(self, df: pd.DataFrame, strategy_name: str) -> dict:
        """🔧 P2: 比较 Market vs Limit 订单的差异"""
        from execution.order import simulate_limit_orders
        results = {}

        # Market
        market_result = self.run(df, strategy_name=strategy_name, order_type="market")
        results["market"] = {
            "total_return": market_result.metrics["total_return_pct"],
            "sharpe": market_result.metrics["sharpe"],
            "trade_count": len(market_result.trades),
            "fee_model": "taker (0.1%)",
        }

        # Limit（模拟挂单成交）
        limit_signals = simulate_limit_orders(
            df, market_result.signals_df,
            slippage_bps=5,  # 挂单比市价优 5bps
        )
        limit_result = self.run(df, strategy_name=strategy_name, order_type="limit")
        results["limit"] = {
            "total_return": limit_result.metrics["total_return_pct"],
            "sharpe": limit_result.metrics["sharpe"],
            "trade_count": len(limit_result.trades),
            "fee_model": "maker (0.08%)",
        }

        logger.info(f"📊 订单类型对比 ({strategy_name}):")
        logger.info(f"  Market: {results['market']['total_return']:.2f}% | Sharp {results['market']['sharpe']:.2f}")
        logger.info(f"  Limit:  {results['limit']['total_return']:.2f}% | Sharp {results['limit']['sharpe']:.2f}")

        return results

    def report(self, result: BacktestResult):
        """打印回测报告"""
        m = result.metrics
        print("\n" + "=" * 60)
        print(f"  回测报告: {result.strategy_name} @ {result.symbol}")
        print(f"  订单类型: {result.fee_model}")
        print("=" * 60)
        print(f"  初始资金:     ${self.initial_capital:,.2f}")
        print(f"  最终权益:     ${m.get('final_equity', 0):,.2f}")
        print(f"  总收益率:     {m.get('total_return_pct', 0):+.2f}%")
        print(f"  年化收益率:   {m.get('annual_return_pct', 0):+.2f}%")
        print(f"  最大回撤:     {m.get('max_drawdown_pct', 0):.2f}%")
        print(f"  Sharpe 比率:  {m.get('sharpe', 0):.2f}")
        print(f"  胜率:         {m.get('win_rate', 0):.1f}%")
        print(f"  盈亏比:       {m.get('profit_factor', 0):.2f}")
        print(f"  总交易次数:   {m.get('total_trades', 0)}")
        print(f"  Benchmark:    {m.get('benchmark_return_pct', 0):+.2f}% (买入持有)")
        print(f"  跑赢基准:     {m.get('outperform_benchmark', False)}")
        print("-" * 60)
        print(f"  Buy-and-Hold 对比: {m.get('benchmark_vs_strategy', '')}")
        print("=" * 60 + "\n")
