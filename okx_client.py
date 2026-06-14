"""
OKX REST API 客户端
第一版：公开行情查询（只读）
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import time
from datetime import datetime
from typing import Optional

import httpx

from config import ExchangeConfig


class OKXClient:
    """OKX API 客户端 — 第一版只接公开行情 REST"""

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            follow_redirects=True,
        )

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

        resp = self._client.get("/api/v5/market/candles", params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error: {data.get('msg', data)}")
        return self._parse_klines(data.get("data", []))

    def get_ticker(self, symbol: str) -> dict:
        """获取最新 ticker"""
        resp = self._client.get("/api/v5/market/ticker", params={"instId": symbol})
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error: {data.get('msg', data)}")
        return self._parse_ticker(data["data"][0])

    # ── 私有只读（阶段 9 启用） ──

    def get_balance(self) -> list[dict]:
        """查询账户余额（仅 Read 权限）"""
        ts = self._timestamp()
        method = "GET"
        path = "/api/v5/account/balance"
        body = ""
        headers = self._sign(method, path, body, ts)
        resp = self._client.get(path, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error: {data.get('msg', data)}")
        return data["data"]

    def get_positions(self, inst_type: str = "SPOT") -> list[dict]:
        """查询持仓"""
        ts = self._timestamp()
        method = "GET"
        path = f"/api/v5/account/positions?instType={inst_type}"
        body = ""
        headers = self._sign(method, path, body, ts)
        resp = self._client.get(path, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error: {data.get('msg', data)}")
        return data["data"]

    # ── 订单（阶段 10 启用） ──

    def place_order(self, symbol: str, side: str, sz: str, ord_type: str = "market") -> dict:
        """
        下单（需要 Trade 权限）
        side: "buy" / "sell"
        ord_type: "market" / "limit"
        """
        ts = self._timestamp()
        body = {
            "instId": symbol,
            "tdMode": "cash",
            "side": side,
            "ordType": ord_type,
            "sz": sz,
        }
        json_body = str(body).replace("'", '"')
        headers = self._sign("POST", "/api/v5/trade/order", json_body, ts)
        headers["Content-Type"] = "application/json"
        resp = self._client.post("/api/v5/trade/order", content=json_body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error: {data.get('msg', data)}")
        return data["data"]

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
            "6h": "6H", "12h": "12H", "1d": "1D", "1w": "1W",
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
        return {
            "timestamp": int(raw["ts"]),
            "last": float(raw["last"]),
            "bid": float(raw.get("bid", 0)),
            "ask": float(raw.get("ask", 0)),
            "volume_24h": float(raw.get("vol24h", 0)),
        }

    def close(self):
        self._client.close()
