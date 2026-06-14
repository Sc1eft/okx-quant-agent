"""
🔧 P2: 订单类型分析模块

对比 Market vs Limit 订单的成交质量和成本差异
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import numpy as np

from strategies.base import Signal

logger = logging.getLogger("execution.order")


@dataclass
class OrderSimulation:
    """订单模拟结果"""
    order_type: str  # "market" / "limit"
    executed: bool
    exec_price: float
    slippage_bps: float  # 滑点 bps
    fee_pct: float
    reason: str = ""


@dataclass
class OrderTypeComparison:
    """两种订单类型的完整对比"""
    strategy_name: str
    total_trades_market: int
    total_trades_limit: int
    market_return_pct: float
    limit_return_pct: float
    market_sharpe: float
    limit_sharpe: float
    avg_slippage_market_bps: float
    avg_slippage_limit_bps: float
    market_fee_total: float
    limit_fee_total: float
    recommendation: str


def simulate_market_order(
    price: float,
    side: str,
    slippage_pct: float = 0.05,
) -> OrderSimulation:
    """
    模拟市价单
    市价单保证成交，但有滑点
    """
    slippage_bps = slippage_pct * 100  # 转 bps
    if side == "buy":
        exec_price = price * (1 + slippage_pct / 100)
    else:
        exec_price = price * (1 - slippage_pct / 100)

    return OrderSimulation(
        order_type="market",
        executed=True,
        exec_price=round(exec_price, 2),
        slippage_bps=round(slippage_bps, 1),
        fee_pct=0.10,  # taker 0.10%
        reason="市价单成交",
    )


def simulate_limit_order(
    price: float,
    side: str,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    price_improvement_bps: float = 5.0,
    fill_probability: float = 0.7,
) -> OrderSimulation:
    """
    模拟限价单
    限价单有概率不成交，但滑点更优、费率更低
    """
    if side == "buy" and ask:
        # 买单挂到买一价或更低
        limit_price = ask * (1 - price_improvement_bps / 10000)
    elif side == "sell" and bid:
        limit_price = bid * (1 + price_improvement_bps / 10000)
    else:
        limit_price = price * (1 - price_improvement_bps / 10000) if side == "buy" else \
                      price * (1 + price_improvement_bps / 10000)

    # 随机成交
    if np.random.random() < fill_probability:
        # 吃单 vs 挂单（限价单可能变成市价单）
        if np.random.random() < 0.3:  # 30% 变成吃单
            return OrderSimulation(
                order_type="limit_filled_taker",
                executed=True,
                exec_price=round(limit_price, 2),
                slippage_bps=round(-price_improvement_bps, 1),  # 负滑点=改善了价格
                fee_pct=0.10,  # taker fee
                reason="限价单成交（吃单）",
            )
        else:
            return OrderSimulation(
                order_type="limit_filled_maker",
                executed=True,
                exec_price=round(limit_price, 2),
                slippage_bps=round(-price_improvement_bps, 1),
                fee_pct=0.08,  # maker fee
                reason="限价单成交（挂单）",
            )
    else:
        return OrderSimulation(
            order_type="limit_unfilled",
            executed=False,
            exec_price=round(limit_price, 2),
            slippage_bps=0,
            fee_pct=0,
            reason="限价单未成交",
        )


def simulate_limit_orders(
    df: pd.DataFrame,
    signals_df: pd.DataFrame,
    slippage_bps: float = 5.0,
    fill_probability: float = 0.7,
) -> pd.DataFrame:
    """
    用限价单模拟替代市价单
    返回带限价单成交信号的 DataFrame
    """
    result = signals_df.copy()
    result["limit_filled"] = True
    result["limit_price"] = result["close"]

    for idx in result.index:
        if result.loc[idx, "signal"] in (Signal.BUY, Signal.SELL):
            price = result.loc[idx, "close"]
            side = "buy" if result.loc[idx, "signal"] == Signal.BUY else "sell"
            sim = simulate_limit_order(
                price, side,
                price_improvement_bps=slippage_bps,
                fill_probability=fill_probability,
            )
            result.loc[idx, "limit_filled"] = sim.executed
            result.loc[idx, "limit_price"] = sim.exec_price

            if not sim.executed:
                # 未成交：尝试下一个 K 线以市价补
                result.loc[idx, "limit_filled"] = True
                result.loc[idx, "limit_price"] = price * 1.001

    return result


def compare_order_types(
    strategy_name: str,
    market_result_return: float,
    market_result_sharpe: float,
    market_trades: int,
    limit_result_return: float,
    limit_result_sharpe: float,
    limit_trades: int,
) -> OrderTypeComparison:
    """对比两种订单类型"""

    diff_return = limit_result_return - market_result_return
    diff_sharpe = limit_result_sharpe - market_result_sharpe
    trade_diff = limit_trades - market_trades

    # 推荐逻辑
    if limit_result_return > market_result_return and limit_result_sharpe > market_result_sharpe:
        recommendation = "推荐 Limit 订单（收益更高、Sharpe 更好）"
    elif limit_result_return > market_result_return:
        recommendation = "Limit 收益更高但 Sharpe 稍低，可接受"
    elif diff_return > -5:  # 差异不大
        recommendation = "两种订单差异不大，建议市价单（简单可靠）"
    else:
        recommendation = "推荐 Market 订单（限价单错失行情风险较高）"

    logger.info(f"📊 订单类型对比 ({strategy_name}):")
    logger.info(f"  Market: {market_result_return:+.2f}% | {market_trades} 笔")
    logger.info(f"  Limit:  {limit_result_return:+.2f}% | {limit_trades} 笔")
    logger.info(f"  差异:    {diff_return:+.2f}% | {trade_diff:+d} 笔")
    logger.info(f"  推荐:    {recommendation}")

    return OrderTypeComparison(
        strategy_name=strategy_name,
        total_trades_market=market_trades,
        total_trades_limit=limit_trades,
        market_return_pct=round(market_result_return, 2),
        limit_return_pct=round(limit_result_return, 2),
        market_sharpe=round(market_result_sharpe, 2),
        limit_sharpe=round(limit_result_sharpe, 2),
        avg_slippage_market_bps=5.0,
        avg_slippage_limit_bps=-5.0,
        market_fee_total=market_trades * 0.002,  # 0.1% * 2
        limit_fee_total=limit_trades * 0.0016,   # 0.08% * 2
        recommendation=recommendation,
    )
