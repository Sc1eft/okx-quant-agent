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
    SHORT = "short"  # 开空（做空入场）
    COVER = "cover"  # 平空（做空退出）


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
        # 增量模式（模拟盘/实盘）的 K 线缓冲区
        self._bar_buffer: Optional[pd.DataFrame] = None
        # 策略需要的最少 K 线数（用于指标预热）
        self._min_bars: int = 1

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        """
        生成交易信号（批处理模式 — 用于回测）
        df: K 线 DataFrame (index=datetime, columns=[open, high, low, close, volume])
        返回: StrategyResult
        """
        ...

    def on_bar(self, bar: pd.Series) -> Signal:
        """
        增量模式：逐根 K 线处理，返回单根 K 线的信号。
        策略内部维护 self.position 跨调用持久化。

        bar: 一根 K 线的 Series (index=[open, high, low, close, volume], name=timestamp)
        返回: Signal
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 不支持增量模式，请实现 on_bar()"
        )

    def get_bar_buffer(self) -> pd.DataFrame:
        """获取当前 K 线缓冲区（用于指标计算）"""
        if self._bar_buffer is None:
            return pd.DataFrame()
        return self._bar_buffer

    def reset_buffer(self):
        """重置 K 线缓冲区（切换策略或重新开始时调用）"""
        self._bar_buffer = None

    def reset_position(self):
        """重置持仓状态"""
        self.position = None

    @property
    def description(self) -> str:
        return ""


def get_available_strategies() -> dict:
    """获取所有可用策略"""
    from strategies.ma_cross import MACrossStrategy
    from strategies.rsi_mean_reversion import RSIMeanReversionStrategy
    from strategies.breakout import BreakoutStrategy
    from strategies.macd_agent import MACDAgentStrategy
    from strategies.daily_trend import DailyTrendStrategy

    return {
        "daily_trend": {
            "class": DailyTrendStrategy,
            "description": "日线趋势 — EMA50 闸门 + 日线突破/KDJ 金叉触发，跌破离场（IC 证据驱动，仅多头）",
            "params": {"trend_span": 50, "entry_mode": "trigger"},
        },
        "macd_agent": {
            "class": MACDAgentStrategy,
            "description": "MACD 多周期共振 — 与实盘 Agent 规则决策器同源",
            "params": {},
        },
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
