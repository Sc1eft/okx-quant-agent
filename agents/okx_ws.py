"""
OKX WebSocket 客户端 — 异步，自动重连

用于 Agent 1 获取实时行情 ticks
协议: wss://ws.okx.com:8443/ws/v5/public
"""
from __future__ import annotations

import asyncio
import hmac
import hashlib
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import websockets

logger = logging.getLogger("okx_ws")


class OKXWebSocketClient:
    """OKX WebSocket 客户端 — 支持自动重连与订阅管理"""

    WS_URL = "wss://ws.okx.com:8443/ws/v5/public"

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        passphrase: str = "",
        symbols: list[str] | None = None,
        reconnect_delay_base: float = 1.0,
        reconnect_delay_max: float = 60.0,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.symbols = symbols or ["ETH-USDT"]
        self.reconnect_delay_base = reconnect_delay_base
        self.reconnect_delay_max = reconnect_delay_max

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._subscribed_channels: list[dict] = []
        self._on_message: Optional[Callable] = None
        self._on_error: Optional[Callable] = None
        self._on_reconnect: Optional[Callable] = None

    def set_callbacks(
        self,
        on_message: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_reconnect: Optional[Callable] = None,
    ):
        """设置消息和错误回调

        on_reconnect: WebSocket 重连后调用（用于数据回填等恢复逻辑）
        """
        self._on_message = on_message
        self._on_error = on_error
        self._on_reconnect = on_reconnect

    async def connect(self):
        """建立 WebSocket 连接（自动重连循环）"""
        self._running = True
        delay = self.reconnect_delay_base

        while self._running:
            try:
                logger.info(f"正在连接 OKX WebSocket: {self.WS_URL}")
                async with websockets.connect(self.WS_URL, ping_interval=20) as ws:
                    self._ws = ws
                    logger.info("OKX WebSocket 已连接")
                    delay = self.reconnect_delay_base  # 重置重连延迟

                    # 订阅频道
                    await self._subscribe_all()

                    # 重连回调（用于数据回填等恢复逻辑）
                    if self._on_reconnect:
                        try:
                            await self._on_reconnect()
                        except Exception as e:
                            logger.error(f"重连回调异常: {e}")

                    # 消息循环
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            await self._handle_message(msg)
                        except json.JSONDecodeError:
                            logger.warning(f"WebSocket 消息解析失败: {raw[:100]}")

            except asyncio.CancelledError:
                logger.info("WebSocket 连接已取消")
                break
            except Exception as e:
                logger.error(f"WebSocket 连接异常: {e}")
                if self._on_error:
                    self._on_error(str(e))

            if not self._running:
                break

            # 指数退避重连
            logger.info(f"WebSocket 将在 {delay:.0f}s 后重连...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.reconnect_delay_max)

    async def disconnect(self):
        """断开 WebSocket 连接"""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("OKX WebSocket 已断开")

    async def subscribe(self, channel: str, inst_id: str, extra_params: Optional[dict] = None):
        """订阅频道"""
        arg = {"channel": channel, "instId": inst_id}
        if extra_params:
            arg.update(extra_params)
        sub_msg = {
            "op": "subscribe",
            "args": [arg],
        }
        self._subscribed_channels.append(arg)
        if self._ws:
            await self._ws.send(json.dumps(sub_msg))
            logger.info(f"已订阅: {channel} / {inst_id}")

    async def _subscribe_all(self):
        """订阅所有已注册的频道"""
        self._subscribed_channels.clear()
        for symbol in self.symbols:
            await self.subscribe("tickers", symbol)
            # 后续可扩展订阅 candles / books

    async def _handle_message(self, msg: dict):
        """处理收到的 WebSocket 消息"""
        # OKX WebSocket 心跳响应
        if msg.get("event") == "subscribe":
            logger.info(f"订阅成功: {msg}")
            return
        if msg.get("event") == "error":
            logger.error(f"WebSocket 错误: {msg}")
            return

        # 数据消息
        if "data" in msg and self._on_message:
            self._on_message(msg)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    @staticmethod
    def _sign(secret_key: str, timestamp: str) -> str:
        """OKX WebSocket 登录签名"""
        message = timestamp + "GET" + "/users/self/verify"
        mac = hmac.new(
            secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    async def login(self):
        """WebSocket 私有频道登录（Phase 2+ 需要）"""
        if not self.api_key or not self.secret_key or not self.passphrase:
            logger.warning("缺少 API 凭证，跳过 WebSocket 登录")
            return
        ts = datetime.now(timezone.utc).isoformat()[:-3] + "Z"
        sign = self._sign(self.secret_key, ts)
        login_msg = {
            "op": "login",
            "args": [{
                "apiKey": self.api_key,
                "passphrase": self.passphrase,
                "timestamp": ts,
                "sign": sign,
            }],
        }
        await self._ws.send(json.dumps(login_msg))
        logger.info("WebSocket 登录请求已发送")
