"""
回测指标计算
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def compute_metrics(
    equity_curve: pd.Series,
    trades: list,
    initial_capital: float,
    price_df: pd.DataFrame,
) -> dict:
    """计算回测绩效指标"""
    if equity_curve.empty:
        return {"total_trades": 0}

    final_equity = float(equity_curve.iloc[-1])
    total_return = (final_equity - initial_capital) / initial_capital * 100

    # 年化收益率
    days = (equity_curve.index[-1] - equity_curve.index[0]).total_seconds() / 86400
    years = max(days / 365.25, 1 / 365.25)
    annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100

    # 最大回撤
    peak = equity_curve.expanding().max()
    drawdown = (equity_curve - peak) / peak * 100
    max_drawdown = float(abs(drawdown.min()))

    # Sharpe 比率（假设年化无风险利率 2%）
    period_returns = equity_curve.pct_change().dropna()
    if len(period_returns) > 1:
        spacing_seconds = equity_curve.index.to_series().diff().dropna().dt.total_seconds().median()
        periods_per_year = 365.25 * 86400 / spacing_seconds if spacing_seconds and spacing_seconds > 0 else 365.25
        risk_free_per_period = (1.02 ** (1 / periods_per_year)) - 1
        excess_returns = period_returns - risk_free_per_period
        sharpe = float(np.sqrt(periods_per_year) * excess_returns.mean() / excess_returns.std()) if excess_returns.std() > 0 else 0
    else:
        sharpe = 0.0

    # 交易统计
    total_trades = len(trades)
    if total_trades > 0:
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl <= 0]
        win_rate = len(winning_trades) / total_trades * 100
        avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = abs(np.mean([t.pnl for t in losing_trades])) if losing_trades else 1
        profit_factor = (
            sum(t.pnl for t in winning_trades) / abs(sum(t.pnl for t in losing_trades))
            if losing_trades and sum(t.pnl for t in losing_trades) != 0
            else float("inf")
        )
        avg_hold_bars = np.mean([
            (t.exit_time - t.entry_time).total_seconds() / 3600
            for t in trades
        ])
    else:
        win_rate = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        profit_factor = 0.0
        avg_hold_bars = 0.0

    # Benchmark: 买入持有
    if len(price_df) > 1:
        first_close = float(price_df["close"].iloc[0])
        last_close = float(price_df["close"].iloc[-1])
        benchmark_return = (last_close - first_close) / first_close * 100
        outperform = total_return > benchmark_return
    else:
        benchmark_return = 0.0
        outperform = False

    # Calmar 比率
    calmar = annual_return / max_drawdown if max_drawdown > 0 else 0

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "sharpe": round(sharpe, 2),
        "calmar": round(calmar, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "total_trades": total_trades,
        "avg_hold_hours": round(avg_hold_bars, 1),
        "benchmark_return_pct": round(benchmark_return, 2),
        "outperform_benchmark": outperform,
        "benchmark_vs_strategy": f"策略 {total_return:+.2f}% vs 基准 {benchmark_return:+.2f}%",
    }
