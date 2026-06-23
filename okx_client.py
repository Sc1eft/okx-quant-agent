"""
OKX REST API 客户端
第一版：公开行情查询（只读）
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import logging
import random
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx
from httpx import RemoteProtocolError, ConnectError, TimeoutException

from config import ExchangeConfig

logger = logging.getLogger("okx_client")


class OKXClient:
    """OKX API 客户端 — 第一版只接公开行情 REST"""

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            follow_redirects=True,
        )

    # ── 统一请求入口（带自动重试） ──

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        content: Optional[str] = None,
    ) -> httpx.Response:
        """
        统一 HTTP 请求入口，自动重试 transient 网络错误。

        重试策略：
        - 指数退避: 1s → 2s → 4s ...（最多 retry_count 次）
        - 每次退避加 ±20% jitter
        - 只重试: RemoteProtocolError（连接重置）、ConnectError（连接失败）、TimeoutException（超时）
        """
        max_retries = max(1, getattr(self.config, "retry_count", 3))
        last_exc = None

        for attempt in range(1, max_retries + 2):  # 第一次不算重试
            try:
                resp = self._client.request(
                    method=method, url=path, params=params,
                    headers=headers, content=content,
                )
                resp.raise_for_status()
                return resp

            except (RemoteProtocolError, ConnectError, TimeoutException) as e:
                last_exc = e
                if attempt <= max_retries:
                    sleep_sec = (2 ** (attempt - 1)) * random.uniform(0.8, 1.2)
                    logger.warning(
                        f"OKX 网络错误 ({type(e).__name__}), "
                        f"{max_retries - attempt + 1} 次重试剩余, "
                        f"等待 {sleep_sec:.1f}s..."
                    )
                    time.sleep(sleep_sec)
                else:
                    raise RuntimeError(
                        f"OKX 请求失败 (已重试 {max_retries} 次): {e}"
                    ) from e

    def _check_api_response(self, data: dict):
        """检查 OKX API 返回码"""
        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error: {data.get('msg', data)}")

    # ── 公开行情 ──

    def get_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        after: Optional[int] = None,
        before: Optional[int] = None,
    ) -> list[dict]:
        """
        获取 K 线数据
        https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks
        """
        params = {
            "instId": symbol,
            "bar": self._tf_to_bar(timeframe),
            "limit": min(limit, 300),
        }
        if after:
            params["after"] = str(after)
        if before:
            params["before"] = str(before)

        resp = self._request("GET", "/api/v5/market/candles", params=params)
        data = resp.json()
        self._check_api_response(data)
        return self._parse_klines(data.get("data", []))

    def get_ticker(self, symbol: str) -> dict:
        """获取最新 ticker"""
        resp = self._request("GET", "/api/v5/market/ticker", params={"instId": symbol})
        data = resp.json()
        self._check_api_response(data)
        return self._parse_ticker(data["data"][0])

    # ── 私有只读（阶段 9 启用） ──

    def get_balance(self) -> list[dict]:
        """查询账户余额（仅 Read 权限）"""
        ts = self._timestamp()
        body = ""
        headers = self._sign("GET", "/api/v5/account/balance", body, ts)
        resp = self._request("GET", "/api/v5/account/balance", headers=headers)
        data = resp.json()
        self._check_api_response(data)
        return data["data"]

    def get_positions(self, inst_type: str = "SPOT") -> list[dict]:
        """查询持仓"""
        ts = self._timestamp()
        path = f"/api/v5/account/positions?instType={inst_type}"
        body = ""
        headers = self._sign("GET", path, body, ts)
        resp = self._request("GET", path, headers=headers)
        data = resp.json()
        self._check_api_response(data)
        return data["data"]

    # ── 订单（阶段 10 启用） ──

    def place_order(self, symbol: str, side: str, sz: str, ord_type: str = "market", px: str = "") -> dict:
        """
        下单（需要 Trade 权限）
        side: "buy" / "sell"
        ord_type: "market" / "limit"
        px: 限价单价格，市价单留空
        """
        ts = self._timestamp()
        body = {
            "instId": symbol,
            "tdMode": "cash",
            "side": side,
            "ordType": ord_type,
            "sz": sz,
        }
        if px:
            body["px"] = px
        json_body = str(body).replace("'", '"')
        headers = self._sign("POST", "/api/v5/trade/order", json_body, ts)
        headers["Content-Type"] = "application/json"
        resp = self._request("POST", "/api/v5/trade/order", headers=headers, content=json_body)
        data = resp.json()
        self._check_api_response(data)
        return data["data"]

    # ── Phase 2: 订单管理（Task 1） ──

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """撤销订单

        https://www.okx.com/docs-v5/en/#rest-api-trade-cancel-order
        需要 Trade 权限。
        """
        ts = self._timestamp()
        body = {"instId": symbol, "ordId": order_id}
        json_body = str(body).replace("'", '"')
        headers = self._sign("POST", "/api/v5/trade/cancel-order", json_body, ts)
        headers["Content-Type"] = "application/json"
        resp = self._request("POST", "/api/v5/trade/cancel-order", headers=headers, content=json_body)
        data = resp.json()
        self._check_api_response(data)
        return self._normalize_order_data(data.get("data", []))

    def get_order(self, symbol: str, order_id: str) -> dict:
        """查询订单状态

        https://www.okx.com/docs-v5/en/#rest-api-trade-get-order-details
        需要 Trade 权限。
        返回字段: ordId, state(canceled/filled/partially_filled/live),
        fillPx, fillSz, accFillSz, side, instId
        """
        ts = self._timestamp()
        path = f"/api/v5/trade/order?{urlencode({'instId': symbol, 'ordId': order_id})}"
        headers = self._sign("GET", path, "", ts)
        resp = self._request("GET", path, headers=headers)
        data = resp.json()
        self._check_api_response(data)
        return self._normalize_order_data(data.get("data", []))

    def get_order_book(self, symbol: str, depth: int = 5) -> dict:
        """获取订单簿深度

        https://www.okx.com/docs-v5/en/#rest-api-market-data-get-order-book
        公开接口，无需签名。
        返回: {"asks": [[price, sz, ...], ...], "bids": [[price, sz, ...], ...], "ts": "..."}
        """
        params = {"instId": symbol, "sz": str(min(depth, 10))}
        resp = self._request("GET", "/api/v5/market/books", params=params)
        data = resp.json()
        self._check_api_response(data)
        raw = data.get("data", [{}])[0]
        return {
            "asks": raw.get("asks", []),
            "bids": raw.get("bids", []),
            "ts": raw.get("ts", ""),
        }

    @staticmethod
    def _normalize_order_data(raw: list) -> dict:
        """标准化订单 API 返回值"""
        if isinstance(raw, list) and len(raw) > 0:
            return raw[0]
        if isinstance(raw, dict):
            return raw
        return {}

    # ── 内部 ──

    def _sign(self, method: str, path: str, body: str, ts: str) -> dict:
        """OKX 签名"""
        message = ts + method.upper() + path + body
        mac = hmac.new(
            self.config.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        sign = base64.b64encode(mac.digest()).decode("utf-8")
        return {
            "OK-ACCESS-KEY": self.config.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.config.passphrase,
        }

    @staticmethod
    def _timestamp() -> str:
        return datetime.utcnow().isoformat()[:-3] + "Z"

    @staticmethod
    def _tf_to_bar(tf: str) -> str:
        mapping = {
            "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
            "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H",
            "6h": "6H", "12h": "12H", "1d": "1D", "2d": "2D", "1w": "1W",
        }
        return mapping.get(tf, "1H")

    @staticmethod
    def _parse_klines(raw: list[list[str]]) -> list[dict]:
        """OKX 原始 K 线转 dict"""
        return [
            {
                "timestamp": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "vol": float(item[5]),
                "vol_ccy": float(item[6]),
            }
            for item in raw
        ]

    @staticmethod
    def _parse_ticker(raw: dict) -> dict:
        last = float(raw["last"])
        open24h = float(raw.get("open24h", 0))
        change_24h = ((last - open24h) / open24h * 100) if open24h else 0.0
        return {
            "timestamp": int(raw["ts"]),
            "last": last,
            "bid": float(raw.get("bidPx", 0)),
            "ask": float(raw.get("askPx", 0)),
            "volume_24h": float(raw.get("vol24h", 0)),
            "high_24h": float(raw.get("high24h", 0)),
            "low_24h": float(raw.get("low24h", 0)),
            "change_24h": change_24h,
        }

    def close(self):
        self._client.close()
