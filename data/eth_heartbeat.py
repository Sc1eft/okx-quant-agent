"""
ETH-USDT WebSocket 心跳采集器 — 常驻后台进程

用 OKX 公开 WebSocket 接收 ETH-USDT 实时 ticker，写入 SQLite。
前端通过 status.json + SQLite 读取数据。

用法:
    python data/eth_heartbeat.py                  # 启动（前台）
    python data/eth_heartbeat.py --stop           # 停止

设计:
    - WebSocket 自动重连，断线后 3s 重试
    - 每 tick 写入 SQLite (WAL) + 原子写 status.json
    - 每秒更新心跳状态（即使无价格更新也标记 alive）
    - 自动清理 24h 前旧数据
    - PID 文件 + Windows taskkill 管理生命周期
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.heartbeat_db import HeartbeatDB, PID_PATH, _atomic_json_write

# ── 配置 ──
OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
ETH_SYMBOL = "ETH-USDT"
RECONNECT_DELAY = 3  # 秒
HEARTBEAT_INTERVAL = 1  # 秒（status 更新间隔）

logger = logging.getLogger("eth_heartbeat")


class ETHHeartbeatCollector:
    """ETH-USDT 心跳采集器 — 通过 OKX WebSocket 接收 ticker。"""

    def __init__(self):
        self.db = HeartbeatDB()
        self.ws: websocket.WebSocketApp | None = None
        self.running = True
        self.connected = False
        self.tick_count = 0
        self.last_tick_time: float = 0.0
        self.last_price: float = 0.0
        self.last_bid: float | None = None
        self.last_ask: float | None = None
        self.volume_24h: float | None = None
        self.change_24h: float | None = None
        self.started_at = datetime.now(timezone.utc)
        self._monitor_thread: threading.Thread | None = None
        self._ws_thread: threading.Thread | None = None

    # ── WebSocket 回调 ──

    def _on_message(self, ws, message: str):
        if message == "pong":
            return

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        # 订阅确认事件
        if data.get("event") == "subscribe":
            return

        # ticker 数据推送
        arg = data.get("arg", {})
        if arg.get("channel") == "tickers" and "data" in data:
            for tick in data["data"]:
                self._handle_tick(tick)

    def _handle_tick(self, tick: dict):
        now = datetime.now(timezone.utc)
        ts_ms = int(tick.get("ts", now.timestamp() * 1000))
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()

        # OKX WebSocket 用 bidPx/askPx（REST API 用 bid/ask）
        bid = _safe_float(tick.get("bidPx")) or _safe_float(tick.get("bid"))
        ask = _safe_float(tick.get("askPx")) or _safe_float(tick.get("ask"))

        # 保存到实例变量（monitor 线程用）
        self.last_price = _safe_float(tick.get("last")) or 0.0
        self.last_bid = bid
        self.last_ask = ask
        self.volume_24h = _safe_float(tick.get("vol24h"))
        self.change_24h = _safe_float(tick.get("change24h"))

        # 写入 DB（不写 status.json，monitor 线程统一写）
        self.db._insert_tick_only(
            ts=ts,
            ts_ms=ts_ms,
            price=self.last_price,
            bid=bid,
            ask=ask,
            volume_24h=self.volume_24h,
            high_24h=_safe_float(tick.get("high24h")),
            low_24h=_safe_float(tick.get("low24h")),
            change_24h=self.change_24h,
        )

        self.tick_count += 1
        self.last_tick_time = time.time()

    def _on_error(self, ws, error):
        logger.warning(f"WebSocket 错误: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.connected = False
        logger.info(f"WebSocket 关闭 ({close_status_code})")

    def _on_open(self, ws):
        self.connected = True
        logger.info("WebSocket 已连接，订阅 ETH-USDT ticker...")
        ws.send(json.dumps({
            "op": "subscribe",
            "args": [{"channel": "tickers", "instId": ETH_SYMBOL}],
        }))
        logger.info("✅ 已订阅 ETH-USDT ticker")

    # ── 后台心跳监控 ──

    def _monitor_loop(self):
        """每秒写入 status.json（唯一写入者），供前端读取。"""
        while self.running:
            status = {
                "connected": self.connected,
                "tick_count": self.tick_count,
                "started_at": self.started_at.isoformat(),
                "last_tick_at": (
                    datetime.fromtimestamp(self.last_tick_time, tz=timezone.utc).isoformat()
                    if self.last_tick_time else None
                ),
                "last_price": self.last_price,
                "last_bid": self.last_bid,
                "last_ask": self.last_ask,
                "volume_24h": self.volume_24h,
                "change_24h": self.change_24h,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "is_running": True,
            }
            _atomic_json_write(PID_PATH.parent / "eth_heartbeat_status.json", status)
            time.sleep(HEARTBEAT_INTERVAL)

    # ── 主循环 ──

    def run(self):
        logger.info("🚀 ETH-USDT 心跳采集器启动")

        # 写 PID 文件
        PID_PATH.write_text(str(os.getpid()))

        # 启动秒级心跳监控
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

        # 清理 24h 前旧数据
        self.db.cleanup_old()

        # 重连主循环
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    OKX_WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )

                # WebSocket 在独立线程运行
                self._ws_thread = threading.Thread(
                    target=self.ws.run_forever,
                    kwargs={"ping_interval": 20, "ping_timeout": 5},
                    daemon=True,
                )
                self._ws_thread.start()

                # 等待连接结束
                while self.running and (self._ws_thread and self._ws_thread.is_alive()):
                    time.sleep(0.5)

                if self.running:
                    logger.info(f"⏳ {RECONNECT_DELAY}s 后重连...")
                    time.sleep(RECONNECT_DELAY)

            except Exception as e:
                logger.error(f"连接异常: {e}")
                if self.running:
                    time.sleep(RECONNECT_DELAY)

    def stop(self):
        self.running = False
        self.connected = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.db.close()

        _atomic_json_write(
            PID_PATH.parent / "eth_heartbeat_status.json",
            {
                "connected": False,
                "is_running": False,
                "tick_count": self.tick_count,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        PID_PATH.unlink(missing_ok=True)
        logger.info("🛑 采集器已停止")


# ── 工具 ──

def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ── CLI ──

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                str(PROJECT_ROOT / "logs" / "eth_heartbeat.log"),
                encoding="utf-8",
            ),
        ],
    )

    if "--stop" in sys.argv:
        from data.heartbeat_db import stop_collector as sc
        sc()
        return

    if "--status" in sys.argv:
        from data.heartbeat_db import read_status as rs
        s = rs()
        if s:
            print(json.dumps(s, indent=2, ensure_ascii=False))
        else:
            print("采集器未运行")
        return

    collector = ETHHeartbeatCollector()

    def _signal_handler(sig, frame):
        logger.info("收到退出信号，正在关闭...")
        collector.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    collector.run()


if __name__ == "__main__":
    main()
