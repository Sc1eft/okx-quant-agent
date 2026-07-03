"""
K 线构建器 — WebSocket tick → 1秒 K线 → 聚合到标准周期

从 OKX WebSocket ticker 消息中提取 last price，
按时间窗口构建 1s K线，再聚合到 15m / 1h / 1d 周期。

每个新完成的 K 线触发回调，供 Agent 1 计算指标。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger("kline_builder")


class KlineBuilder:
    """K 线构建器

    用法:
        builder = KlineBuilder()
        builder.on_completed_bar = my_callback

        # 每次收到 tick 时调用
        builder.add_tick(price, timestamp)

        # 或手动检查
        completed = builder.check_completed()
    """

    TIMEFRAMES = {
        "3m": 3 * 60,
        "5m": 5 * 60,
        "15m": 15 * 60,
        "1h": 60 * 60,
        "1d": 24 * 60 * 60,
    }

    def __init__(self):
        # 缓存: {timeframe: {"timestamp": int, "open": float, ...}}
        self._candles: dict[str, dict] = {}
        # 历史完整 K 线: {timeframe: [dict, ...]}
        self._history: dict[str, list[dict]] = defaultdict(
            lambda: list[dict]()
        )
        # 最大保留的历史 K 线数
        self._max_history = 500

        # 1s 精度 K 线（中间产物）
        self._sec_candle: Optional[dict] = None

        # 外部回调: on_completed_bar(timeframe, bar_dict) -> None
        self.on_completed_bar: Optional[Callable[[str, dict], None]] = None

        # 上一周期的时间戳边界（用于判断是否翻转）
        self._last_boundary: dict[str, int] = {}

    def add_tick(self, price: float, timestamp_s: int):
        """添加一个 tick 数据（每秒最多一个）

        Args:
            price: 最新价格
            timestamp_s: Unix 秒级时间戳
        """
        # ── 1秒 K 线 ──
        if self._sec_candle is None:
            self._sec_candle = {
                "timestamp": timestamp_s,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            }
        elif self._sec_candle["timestamp"] < timestamp_s:
            # 完成上一根 1s K 线 → 聚合到各周期
            self._aggregate_sec_candle()
            # 新建当前秒 K 线
            self._sec_candle = {
                "timestamp": timestamp_s,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            }
            # 检查新秒级 K 线是否跨越了标准周期边界
            # 使用 self._last_boundary 避免与 _aggregate_sec_candle 重复触发
            self._check_new_sec_boundary()
        else:
            # 同秒内更新
            self._sec_candle["high"] = max(self._sec_candle["high"], price)
            self._sec_candle["low"] = min(self._sec_candle["low"], price)
            self._sec_candle["close"] = price

    def _aggregate_sec_candle(self):
        """将刚完成的 1s K 线聚合到各标准周期"""
        sec = self._sec_candle
        if sec is None:
            return
        ts = sec["timestamp"]

        for tf, span in self.TIMEFRAMES.items():
            boundary = (ts // span) * span  # 周期起始时间

            if tf not in self._candles:
                # 新建周期 K 线
                self._candles[tf] = {
                    "timestamp": boundary,
                    "open": sec["open"],
                    "high": sec["high"],
                    "low": sec["low"],
                    "close": sec["close"],
                    "volume": sec.get("volume", 0),
                }
                self._last_boundary[tf] = boundary
            elif boundary != self._last_boundary.get(tf):
                # 周期翻转 — 完成旧 K 线，触发回调
                old = self._candles[tf]
                self._add_to_history(tf, old)
                if self.on_completed_bar:
                    self.on_completed_bar(tf, dict(old))

                # 新建周期 K 线
                self._candles[tf] = {
                    "timestamp": boundary,
                    "open": sec["open"],
                    "high": sec["high"],
                    "low": sec["low"],
                    "close": sec["close"],
                    "volume": sec.get("volume", 0),
                }
                self._last_boundary[tf] = boundary
            else:
                # 同周期内更新
                c = self._candles[tf]
                c["high"] = max(c["high"], sec["high"])
                c["low"] = min(c["low"], sec["low"])
                c["close"] = sec["close"]
                c["volume"] = c.get("volume", 0) + sec.get("volume", 0)

    def _check_new_sec_boundary(self):
        """检查新秒级 K 线是否跨过标准周期边界

        在 _aggregate_sec_candle 之后调用。_aggregate_sec_candle 已经将
        _last_boundary 更新为旧秒级 K 线的边界。如果新秒级 K 线的边界与
        之不同，说明跨越了边界，需要完成当前周期 K 线并开始新的周期 K 线。

        由于 _aggregate_sec_candle 先更新了 _last_boundary，此方法不会
        与其重复检测（double-trigger）。
        """
        sec = self._sec_candle
        if sec is None:
            return
        ts = sec["timestamp"]

        for tf, span in self.TIMEFRAMES.items():
            boundary = (ts // span) * span
            last = self._last_boundary.get(tf)

            if tf in self._candles and boundary != last:
                # 周期翻转 — 完成旧 K 线
                old = self._candles[tf]
                self._add_to_history(tf, old)
                if self.on_completed_bar:
                    self.on_completed_bar(tf, dict(old))

                # 新建周期 K 线（使用新秒级 K 线的数据）
                self._candles[tf] = {
                    "timestamp": boundary,
                    "open": sec["open"],
                    "high": sec["high"],
                    "low": sec["low"],
                    "close": sec["close"],
                    "volume": sec.get("volume", 0),
                }
                self._last_boundary[tf] = boundary

    def _add_to_history(self, timeframe: str, bar: dict):
        """将完成的 K 线加入历史"""
        self._history[timeframe].append(dict(bar))
        if len(self._history[timeframe]) > self._max_history:
            self._history[timeframe] = self._history[timeframe][-self._max_history:]

    def get_current_candle(self, timeframe: str) -> Optional[dict]:
        """获取当前进行中的 K 线"""
        return self._candles.get(timeframe)

    def get_history(self, timeframe: str, n: int = 100) -> list[dict]:
        """获取最近 N 根已完成 K 线"""
        history = self._history.get(timeframe, [])
        return history[-n:]

    def get_all_history(self) -> dict[str, list[dict]]:
        """获取所有周期的历史"""
        return dict(self._history)

    def has_history(self, timeframe: str, min_count: int = 1) -> bool:
        """是否有足够的历史数据"""
        return len(self._history.get(timeframe, [])) >= min_count
