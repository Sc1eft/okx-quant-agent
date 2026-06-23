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


class Agent1:
    """Agent 1 — 技术分析师"""

    def __init__(self, config: AgentSystemConfig, event_bus: EventBus):
        self.config = config
        self.bus = event_bus
        self.kline_builder = KlineBuilder()
        self.change_detector = ChangeDetector()
        self.ws_client = OKXWebSocketClient(
            symbols=[config.ws_symbol],
            reconnect_delay_base=config.ws_reconnect_delay_base,
            reconnect_delay_max=config.ws_reconnect_delay_max,
        )

        # 指标缓存（用于在 on_bar 中快速获取最新值）
        self._latest_indicators: dict[str, dict] = {}

        # 回调绑定
        self.kline_builder.on_completed_bar = self._on_bar
        self.ws_client.set_callbacks(on_message=self._on_tick)

        # 运行状态
        self._running = False
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
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"tick 解析失败: {e} | msg={msg}")

    def _on_bar(self, timeframe: str, bar: dict):
        """处理新完成的 K 线"""
        self._stats["bars_completed"] += 1
        logger.debug(f"新K线完成: {timeframe} @ {bar['close']:.2f}")

        # 收集该周期所有历史 K 线
        history = self.kline_builder.get_history(timeframe)
        history.append(bar)  # 把刚完成的这根也算进去

        # 需要至少 30 根 K 线才能计算可靠指标
        if len(history) < 30:
            logger.debug(f"{timeframe} 数据不足 ({len(history)}/{30}), 跳过指标计算")
            return

        # 转为 DataFrame（pandas，与 eth_ai_analysis.py 兼容格式）
        import pandas as pd
        df = pd.DataFrame(history)
        df.rename(columns={
            "timestamp": "timestamp",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }, inplace=True)

        # 计算指标
        macd = _calc_macd(df)
        kdj = _calc_kdj(df)
        boll = _calc_boll(df)

        self._latest_indicators[timeframe] = {
            "macd": macd,
            "kdj": kdj,
            "boll": boll,
            "close": bar["close"],
        }

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
            # 非阻塞发布
            asyncio.ensure_future(self.bus.publish_a(event))
            self._stats["signals_pushed"] += 1
            logger.info(f"📊 Agent 1 push: {sig['description']} (urgency={urgency})")

    def get_status(self) -> dict:
        """返回当前状态（供监控用）"""
        return {
            "running": self._running,
            **self._stats,
            "bars_history": {
                tf: self.kline_builder.has_history(tf, 1)
                for tf in self.kline_builder.TIMEFRAMES
            },
            "latest_indicators": self._latest_indicators,
        }
