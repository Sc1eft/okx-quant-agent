"""
策略基类
所有策略继承 BaseStrategy，实现 generate_signals()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd


class Signal(Enum):
    """交易信号"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    EXIT = "exit"  # 强制退出（止盈/止损/超时）


@dataclass
class PositionInfo:
    """当前持仓信息（策略内部状态用）"""
    entry_price: float = 0.0
    entry_time: Optional[pd.Timestamp] = None
    size: float = 0.0
    bars_held: int = 0
    highest_price: float = 0.0  # 持仓期间最高价（移动止损用）
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None


@dataclass
class StrategyResult:
    """策略输出结果"""
    signals: pd.DataFrame  # index=time, columns=['signal', 'price', 'reason']
    metadata: dict = field(default_factory=dict)

    @property
    def trade_count(self) -> int:
        if "signal" not in self.signals.columns:
            return 0
        return (self.signals["signal"] != Signal.HOLD).sum()


class BaseStrategy(ABC):
    """策略基类"""

    def __init__(self, name: str, params: dict):
        self.name = name
        self.params = params
        self.position: Optional[PositionInfo] = None

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        """
        生成交易信号
        df: K 线 DataFrame (index=datetime, columns=[open, high, low, close, volume])
        返回: StrategyResult
        """
        ...

    @property
    def description(self) -> str:
        return ""

    def reset_position(self):
        """重置持仓状态"""
        self.position = None


def get_available_strategies() -> dict:
    """获取所有可用策略"""
    from strategies.ma_cross import MACrossStrategy
    from strategies.rsi_mean_reversion import RSIMeanReversionStrategy
    from strategies.breakout import BreakoutStrategy

    return {
        "ma_cross": {
            "class": MACrossStrategy,
            "description": "MA 均线交叉 — 短线上穿长线买入，下穿卖出",
            "params": {"short_window": 7, "long_window": 25},
        },
        "rsi_mean_reversion": {
            "class": RSIMeanReversionStrategy,
            "description": "RSI 均值回归 — 超卖买入，超买卖出",
            "params": {"rsi_period": 14, "oversold": 30, "overbought": 70},
        },
        "breakout": {
            "class": BreakoutStrategy,
            "description": "突破策略 — 突破 N 周期高点买入，跌破低点卖出",
            "params": {"period": 20, "atr_multiplier": 2.0},
        },
    }


def create_strategy(name: str, params: Optional[dict] = None) -> BaseStrategy:
    """工厂方法：创建策略实例"""
    registry = get_available_strategies()
    if name not in registry:
        raise ValueError(f"未知策略: {name}，可用: {list(registry.keys())}")

    info = registry[name]
    merged = {**info["params"], **(params or {})}
    return info["class"](name, merged)
