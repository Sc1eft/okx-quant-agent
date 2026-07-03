"""
Agent 1 — 实时技术分析师

职责:
  1. 通过 OKX WebSocket 获取 ETH-USDT 实时 ticks
  2. 构建 1s K 线并聚合到 15m / 1h / 1d
  3. 每根新完成的 K 线计算 MACD / KDJ / BOLL
  4. 检测与上次值相比的有意义变化
  5. 检测到变化时推送事件到 Queue A

启动方式: await Agent1(config, event_bus).run()
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import sys
# 已有项目使用 sys.path.insert 方式引用 frontend 模块
if "." not in sys.path and "" not in sys.path:
    sys.path.insert(0, "")

from agents.okx_ws import OKXWebSocketClient
from agents.kline_builder import KlineBuilder
from agents.change_detector import ChangeDetector
from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.config import AgentSystemConfig

# 复用前端已有的指标计算函数
from frontend.utils.eth_ai_analysis import _calc_macd, _calc_kdj, _calc_boll

logger = logging.getLogger("agent1")

MAX_SIGNAL_HISTORY = 20


class Agent1:
    """Agent 1 — 技术分析师"""

    def __init__(self, config: AgentSystemConfig, event_bus: EventBus):
        self.config = config
        self.bus = event_bus
        self.kline_builder = KlineBuilder()
        self.change_detector = ChangeDetector(default_cooldown=config.agent1_change_cooldown)
        self.ws_client = OKXWebSocketClient(
            symbols=[config.ws_symbol],
            reconnect_delay_base=config.ws_reconnect_delay_base,
            reconnect_delay_max=config.ws_reconnect_delay_max,
        )

        # 指标缓存（用于在 on_bar 中快速获取最新值）
        self._latest_indicators: dict[str, dict] = {}

        # Phase 4+: 逐周期 K 线计数 + 信号历史
        self._bar_counts: dict[str, int] = {}
        self._signal_history: deque[dict] = deque(maxlen=MAX_SIGNAL_HISTORY)

        # 回调绑定
        self.kline_builder.on_completed_bar = self._on_bar
        self.ws_client.set_callbacks(on_message=self._on_tick)

        # 运行状态
        self._running = False
        self._pending_tasks: set[asyncio.Task] = set()
        self._current_activity = ""
        self._last_activity_time = 0.0
        self._stats = {
            "ticks_received": 0,
            "bars_completed": 0,
            "signals_pushed": 0,
            "start_time": "",
        }

    async def run(self):
        """启动 Agent 1 主循环"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Agent 1 (技术分析师) 启动")

        # 启动 WebSocket 连接（阻塞直到断开）
        await self.ws_client.connect()

    async def stop(self):
        """停止 Agent 1"""
        self._running = False
        await self.ws_client.disconnect()
        # 等待所有待处理的发布任务完成
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        logger.info("Agent 1 已停止")

    def _on_tick(self, msg: dict):
        """处理 WebSocket ticker 消息"""
        try:
            data_list = msg.get("data", [])
            for data in data_list:
                ts_str = data.get("ts", "0")
                ts_s = int(ts_str) // 1000  # ms → s
                price = float(data.get("last", "0"))
                self.kline_builder.add_tick(price, ts_s)
                self._stats["ticks_received"] += 1
                self._current_activity = f"📡 接收 Tick #{self._stats['ticks_received']} @ ${price:,.2f}"
                self._last_activity_time = time.time()
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"tick 解析失败: {e} | msg={msg}")

    def _on_bar(self, timeframe: str, bar: dict):
        """处理新完成的 K 线"""
        self._stats["bars_completed"] += 1
        self._bar_counts[timeframe] = self._bar_counts.get(timeframe, 0) + 1
        self._current_activity = f"📐 构建 {timeframe} K线 @ ${bar['close']:.2f}"
        self._last_activity_time = time.time()
        logger.debug(f"新K线完成: {timeframe} @ {bar['close']:.2f}")

        # 收集该周期所有历史 K 线
        history = self.kline_builder.get_history(timeframe)
        history.append(bar)  # 把刚完成的这根也算进去

        # 各周期所需最小 K 线数（自适应：短周期快出信号，长周期多积累）
        _MIN_BARS = {"3m": 10, "5m": 10, "15m": 15, "1h": 15, "1d": 30}
        min_bars = _MIN_BARS.get(timeframe, 15)
        if len(history) < min_bars:
            logger.debug(f"{timeframe} 数据不足 ({len(history)}/{min_bars}), 跳过指标计算")
            return

        # 转为 DataFrame（pandas，与 eth_ai_analysis.py 兼容格式）
        import pandas as pd
        df = pd.DataFrame(history)

        # 计算指标
        try:
            macd = _calc_macd(df)
            kdj = _calc_kdj(df)
            boll = _calc_boll(df)
        except Exception as e:
            logger.error(f"指标计算失败 [{timeframe}]: {e}")
            return

        self._latest_indicators[timeframe] = {
            "macd": macd,
            "kdj": kdj,
            "boll": boll,
            "close": bar["close"],
        }
        self._current_activity = f"🧮 计算 {timeframe} 指标 — " + self._indicator_summary(timeframe, macd, kdj, boll)
        self._last_activity_time = time.time()

        # 变化检测
        now = datetime.now(timezone.utc).timestamp()
        signals = self.change_detector.check(
            timeframe=timeframe,
            macd=macd,
            kdj=kdj,
            boll=boll,
            price=bar["close"],
            current_ts=now,
        )

        # 推送信号到 Queue A
        for sig in signals:
            urgency = sig.get("urgency", "medium")
            confidence = sig.get("confidence", 0.5)
            event = AgentEvent(
                type=AgentEventType.TECHNICAL_SIGNAL,
                source="agent1",
                data=sig,
                confidence=confidence,
                urgency=urgency,
            )
            # 跟踪异步发布任务，防止 task 泄漏
            task = asyncio.create_task(self.bus.publish_a(event))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
            self._stats["signals_pushed"] += 1
            self._signal_history.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "signal": sig.get("signal", ""),
                "timeframe": sig.get("timeframe", ""),
                "urgency": urgency,
                "confidence": confidence,
                "description": sig.get("description", ""),
                "price": sig.get("price", 0),
            })
            self._current_activity = f"📊 推送 {timeframe} {sig['description']} (⚡{urgency})"
            self._last_activity_time = time.time()
            logger.info(f"📊 Agent 1 push: {sig['description']} (urgency={urgency})")

    def _indicator_summary(self, tf: str, macd: dict, kdj: dict, boll: dict) -> str:
        """生成一行指标摘要（供 current_activity 使用）"""
        parts = []
        if macd:
            h = macd.get("histogram", 0)
            if isinstance(h, (int, float)):
                parts.append(f"MACD{'↑' if h > 0 else '↓' if h < 0 else '→'}")
        if kdj:
            if kdj.get("k_cross") == "golden":
                parts.append("KDJ金叉")
            elif kdj.get("k_cross") == "dead":
                parts.append("KDJ死叉")
            if kdj.get("overbought"):
                parts.append("⚠️超买")
            if kdj.get("oversold"):
                parts.append("🔥超卖")
        if boll:
            pos = boll.get("position", 0.5)
            if isinstance(pos, (int, float)):
                if pos > 0.9:
                    parts.append("上轨")
                elif pos < 0.1:
                    parts.append("下轨")
        return " ".join(parts) if parts else "无新信号"

    def get_status(self) -> dict:
        """返回当前状态（供监控用）"""
        return {
            "running": self._running,
            "current_activity": self._current_activity,
            "last_activity_time": self._last_activity_time,
            **self._stats,
            "bars_history": {
                tf: self.kline_builder.has_history(tf, 1)
                for tf in self.kline_builder.TIMEFRAMES
            },
            "latest_indicators": self._latest_indicators,
            "bar_counts": dict(self._bar_counts),
            "signal_history": list(self._signal_history),
        }

    def get_recent_signal_stats(self) -> dict:
        """返回近期信号统计数据（供 Agent 4 复盘使用）"""
        signals = list(self._signal_history)
        total = len(signals)
        if total == 0:
            return {"total_signals": 0, "by_timeframe": {},
                    "by_direction": {}, "by_urgency": {}}

        by_tf: dict[str, int] = {}
        by_dir: dict[str, int] = {}
        by_urg: dict[str, int] = {}
        for s in signals:
            tf = s.get("timeframe", "unknown")
            by_tf[tf] = by_tf.get(tf, 0) + 1
            desc = s.get("description", "")
            if "bullish" in desc or "buy" in desc or "金叉" in desc or "超卖" in desc:
                by_dir["buy"] = by_dir.get("buy", 0) + 1
            elif "bearish" in desc or "sell" in desc or "死叉" in desc or "超买" in desc:
                by_dir["sell"] = by_dir.get("sell", 0) + 1
            else:
                by_dir["neutral"] = by_dir.get("neutral", 0) + 1
            urg = s.get("urgency", "medium")
            by_urg[urg] = by_urg.get(urg, 0) + 1

        return {
            "total_signals": total,
            "by_timeframe": by_tf,
            "by_direction": by_dir,
            "by_urgency": by_urg,
        }
