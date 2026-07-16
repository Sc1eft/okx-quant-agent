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

        # 24h 滚动成交量跟踪（从 ticker vol24h 字段差量推算实时成交量）
        self._last_vol24h: float = 0.0
        self._vol24h_initialized: bool = False

    def add_tick(self, price: float, timestamp_s: int, vol24h: Optional[float] = None):
        """添加一个 tick 数据（每秒最多一个）

        Args:
            price: 最新价格
            timestamp_s: Unix 秒级时间戳
            vol24h: OKX ticker 的 vol24h（24h 滚动成交量），用于推算实时成交量
        """
        # ── 从 vol24h 差量推算实时成交量 ──
        tick_volume = 0.0
        if vol24h is not None and vol24h >= 0:
            if self._vol24h_initialized:
                delta = vol24h - self._last_vol24h
                # 防回滚（新 K 线开盘时 vol24h 可能重置）
                tick_volume = max(0.0, delta)
            self._vol24h_initialized = True
            self._last_vol24h = vol24h

        # ── 1秒 K 线 ──
        if self._sec_candle is None:
            self._sec_candle = {
                "timestamp": timestamp_s,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": tick_volume,
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
                "volume": tick_volume,
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

    def add_history_batch(self, timeframe: str, bars: list[dict]) -> None:
        """批量注入已完成的历史 K 线（用于启动预热）

        Args:
            timeframe: 周期标识（"3m", "5m", "15m", "1h", "1d"）
            bars: K 线列表，每根需包含 timestamp, open, high, low, close,
                  volume（兼容 OKX API 的 vol 字段名）
        """
        if timeframe not in self.TIMEFRAMES:
            logger.warning(f"add_history_batch: 未知周期 {timeframe}，跳过")
            return

        # 标准化字段名（OKX 的 vol → volume；OKX 的 timestamp 是毫秒）
        normalized = []
        for b in bars:
            try:
                ts = int(b["timestamp"])
                if ts > 1_000_000_000_00:  # ms → s
                    ts //= 1000
                n = {
                    "timestamp": ts,
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "volume": float(b.get("volume", b.get("vol", 0))),
                }
                normalized.append(n)
            except (KeyError, ValueError, TypeError) as e:
                logger.debug(f"跳过异常 K 线: {e}")

        if not normalized:
            logger.warning(f"add_history_batch({timeframe}): 无有效 K 线")
            return

        # 按时间戳排序（API 返回正序，安全起见排序一次）
        normalized.sort(key=lambda x: x["timestamp"])

        # 注入历史（不超过上限）
        self._history[timeframe] = normalized[-self._max_history:]

        # 记录最后周期边界，让下一根真实 tick 的周期翻转自然触发 K线完成回调
        span = self.TIMEFRAMES[timeframe]
        last_ts = normalized[-1]["timestamp"]
        boundary = (last_ts // span) * span

        self._last_boundary[timeframe] = boundary
        # 不设 _candles——第一根真实 tick 会自然创建

        logger.info(
            f"预热 {timeframe}: 注入 {len(normalized)} 根 K 线 "
            f"(最后边界: {boundary})"
        )

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
