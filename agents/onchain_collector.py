"""
链上数据收集器 — Phase 3 的 Agent 2 扩展模块

采集链上和市场数据:
  1. Gas 费监控（Etherscan API / 回退跳过）
  2. 巨鲸转账监控（Whale Alert API / 回退跳过）
  3. 吃单比 (Taker Buy/Sell Ratio) — OKX 公开 API
  4. 永续合约资金费率 (Funding Rate) — OKX 公开 API

所有采集到的数据都通过 EventBus Queue B 推送，与新闻数据同一通道。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.config import AgentSystemConfig

logger = logging.getLogger("onchain_collector")


# ── 辅助函数 ──

def _cst_now_iso() -> str:
    """返回北京时间 ISO 字符串"""
    return (datetime.now(timezone.utc).timestamp())


def _parse_gas_from_api_response(data: dict | None) -> dict | None:
    """解析 Etherscan Gas Tracker API 响应"""
    if not data or not isinstance(data, dict):
        return None
    try:
        result = data.get("result", {})
        if not isinstance(result, dict):
            return None
        safe_gwei = float(result.get("SafeGasPrice", 0))
        propose_gwei = float(result.get("ProposeGasPrice", 0))
        fast_gwei = float(result.get("FastGasPrice", 0))
        if safe_gwei <= 0 and propose_gwei <= 0 and fast_gwei <= 0:
            return None
        return {
            "safe_gwei": safe_gwei,
            "propose_gwei": propose_gwei,
            "fast_gwei": fast_gwei,
            "base_fee": result.get("suggestBaseFee", ""),
        }
    except (KeyError, ValueError, TypeError):
        return None


def _categorize_gas(gwei: float, cfg: AgentSystemConfig) -> str:
    """将 Gas 费归类"""
    if gwei >= cfg.agent2_gas_extreme_threshold_gwei:
        return "extreme"
    elif gwei >= cfg.agent2_gas_high_threshold_gwei:
        return "high"
    elif gwei >= 50:
        return "medium"
    return "low"


def _parse_whale_from_response(data: list) -> list[dict]:
    """解析 Whale Alert API 响应"""
    results = []
    try:
        for tx in data:
            if not isinstance(tx, dict):
                continue
            results.append({
                "hash": tx.get("hash", ""),
                "blockchain": tx.get("blockchain", ""),
                "symbol": tx.get("symbol", ""),
                "amount_usdt": float(tx.get("amount_usd", 0)),
                "amount": float(tx.get("amount", 0)),
                "from_owner": tx.get("from", {}).get("owner", ""),
                "to_owner": tx.get("to", {}).get("owner", ""),
                "from_address": tx.get("from", {}).get("address", ""),
                "to_address": tx.get("to", {}).get("address", ""),
            })
    except (KeyError, ValueError, TypeError):
        pass
    return results


# ── 主要收集器类 ──

class OnchainCollector:
    """链上数据收集器 — 作为 Agent 2 的子协程运行

    用法:
        collector = OnchainCollector(okx_client, config, event_bus)
        # — 在 Agent 2 中启动:
        await collector.run()  # 内部 gather 多个协程
        # — 或者单独跑某个监控:
        await collector._gas_monitor_loop()
    """

    def __init__(
        self,
        okx_client,
        config: AgentSystemConfig,
        event_bus: EventBus,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.okx = okx_client
        self.cfg = config
        self.bus = event_bus
        self._http = http_client or httpx.AsyncClient(timeout=15.0)

        self._running = False

        # 缓存，避免重复推送相同数据
        self._last_taker_ratio: float = 0.0
        self._last_funding_rate: float = 0.0
        self._last_gas_level: str = ""
        self._last_whale_hashes: set[str] = set()

        self._stats = {
            "gas_fetches": 0,
            "whale_fetches": 0,
            "taker_fetches": 0,
            "funding_fetches": 0,
            "events_pushed": 0,
            "last_gas_gwei": 0,
            "last_taker_buy_ratio": 0,
            "last_funding_rate": 0,
            "last_whale_count": 0,
        }

    # ── 主入口 ──

    async def run(self):
        """启动所有启用的监控协程"""
        self._running = True
        tasks = []

        if self.cfg.agent2_gas_enabled:
            tasks.append(asyncio.create_task(self._gas_monitor_loop(), name="gas_monitor"))
        if self.cfg.agent2_whale_enabled:
            tasks.append(asyncio.create_task(self._whale_monitor_loop(), name="whale_monitor"))
        if self.cfg.agent2_taker_volume_enabled:
            tasks.append(asyncio.create_task(self._taker_volume_loop(), name="taker_volume"))
        if self.cfg.agent2_funding_rate_enabled:
            tasks.append(asyncio.create_task(self._funding_rate_loop(), name="funding_rate"))

        if not tasks:
            logger.info("OnchainCollector: 所有监控均已禁用")
            return

        logger.info(f"OnchainCollector 启动: {len(tasks)} 个监控任务")
        await asyncio.gather(*tasks)

    async def stop(self):
        self._running = False
        await self._http.aclose()
        logger.info("OnchainCollector 已停止")

    # ── Gas 费监控 ──

    async def _gas_monitor_loop(self):
        """定时获取 ETH Gas 费"""
        logger.info("Gas 费监控已启动")
        while self._running:
            try:
                await self._fetch_and_push_gas()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Gas 费获取异常: {e}")
            await asyncio.sleep(self.cfg.agent2_onchain_interval_seconds)

    async def _fetch_and_push_gas(self):
        """获取 Gas 费并推送事件"""
        self._stats["gas_fetches"] += 1
        gas_data = None

        # 尝试 Etherscan API（使用专用的 etherscan API key）
        api_key = self.cfg.agent2_etherscan_api_key
        if api_key:
            try:
                resp = await self._http.get(
                    "https://api.etherscan.io/api",
                    params={"module": "gastracker", "action": "gasoracle", "apikey": api_key},
                )
                if resp.status_code == 200:
                    parsed = _parse_gas_from_api_response(resp.json())
                    if parsed:
                        gas_data = parsed
            except Exception as e:
                logger.debug(f"Etherscan Gas API 请求失败: {e}")

        if not gas_data:
            logger.debug("Gas 数据不可用（跳过本轮）")
            return

        propose_gwei = gas_data["propose_gwei"]
        level = _categorize_gas(propose_gwei, self.cfg)

        # 只推送级别变化
        if level == self._last_gas_level and level in ("low", "medium"):
            return  # 低级别无变化不重复推送
        self._last_gas_level = level
        self._stats["last_gas_gwei"] = propose_gwei

        urgency = "high" if level in ("extreme",) else ("medium" if level == "high" else "low")
        event = AgentEvent(
            type=AgentEventType.NEWS_EVENT,
            source="agent2_gas",
            data={
                "type": "gas",
                "gas_gwei": propose_gwei,
                "level": level,
                "safe_gwei": gas_data["safe_gwei"],
                "fast_gwei": gas_data["fast_gwei"],
                "base_fee": gas_data["base_fee"],
                "description": f"ETH Gas: {propose_gwei} Gwei ({level})",
            },
            confidence=0.7 if level in ("high", "extreme") else 0.4,
            urgency=urgency,
        )
        await self.bus.publish_b(event)
        self._stats["events_pushed"] += 1
        logger.info(f"⛽ Gas: {propose_gwei} Gwei ({level})")

    # ── 巨鲸转账监控 ──

    async def _whale_monitor_loop(self):
        """定时获取巨鲸转账"""
        logger.info("巨鲸监控已启动")
        while self._running:
            try:
                await self._fetch_and_push_whale()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"巨鲸获取异常: {e}")
            await asyncio.sleep(self.cfg.agent2_onchain_interval_seconds)

    async def _fetch_and_push_whale(self):
        """获取 Whale Alert 数据并推送"""
        self._stats["whale_fetches"] += 1
        api_key = self.cfg.agent2_whale_alert_api_key

        if not api_key:
            return  # 无 API key 不工作

        try:
            resp = await self._http.get(
                "https://api.whale-alert.io/v1/transactions",
                params={"api_key": api_key, "min_value": self.cfg.agent2_whale_min_value_usdt},
            )
            if resp.status_code != 200:
                logger.debug(f"Whale Alert API 返回 {resp.status_code}")
                return

            body = resp.json()
            txs = _parse_whale_from_response(body.get("transactions", body.get("data", [])))
        except Exception as e:
            logger.debug(f"Whale Alert 请求失败: {e}")
            return

        if not txs:
            return

        # 去重
        new_txs = [tx for tx in txs if tx["hash"] not in self._last_whale_hashes]
        if not new_txs:
            return

        # 保留最近 hash
        self._last_whale_hashes.update(tx["hash"] for tx in new_txs)
        if len(self._last_whale_hashes) > 200:
            self._last_whale_hashes = set(list(self._last_whale_hashes)[-100:])

        for tx in new_txs:
            is_to_exchange = "exchange" in tx.get("to_owner", "").lower()
            is_from_exchange = "exchange" in tx.get("from_owner", "").lower()
            direction = "→ 交易所" if is_to_exchange else ("← 出交易所" if is_from_exchange else "")

            weight = min(1.0, tx["amount_usdt"] / 5_000_000 * 0.8 + 0.2)  # $5M → 1.0
            urgency = "high" if weight >= 0.7 else "medium"

            event = AgentEvent(
                type=AgentEventType.NEWS_EVENT,
                source="agent2_whale",
                data={
                    "type": "whale",
                    "hash": tx["hash"],
                    "symbol": tx["symbol"],
                    "amount": tx["amount"],
                    "amount_usdt": tx["amount_usdt"],
                    "from_owner": tx["from_owner"],
                    "to_owner": tx["to_owner"],
                    "direction": direction,
                    "description": (
                        f"🐋 {tx['amount']:.1f} {tx['symbol']} (${tx['amount_usdt']:,.0f}) "
                        f"{direction} {tx['from_owner']} → {tx['to_owner']}"
                    ),
                },
                confidence=weight,
                urgency=urgency,
            )
            await self.bus.publish_b(event)
            self._stats["events_pushed"] += 1
            logger.info(f"🐋 Whale: {tx['amount']:.1f} {tx['symbol']} {direction}")

        self._stats["last_whale_count"] = len(new_txs)

    # ── 吃单比监控 ──

    async def _taker_volume_loop(self):
        """定时获取吃单买卖比"""
        logger.info("吃单比监控已启动")
        while self._running:
            try:
                await self._fetch_and_push_taker()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"吃单比获取异常: {e}")
            await asyncio.sleep(self.cfg.agent2_onchain_interval_seconds)

    async def _fetch_and_push_taker(self):
        """获取 OKX 吃单量数据并推送"""
        self._stats["taker_fetches"] += 1

        try:
            data = await asyncio.to_thread(self.okx.get_taker_volume)
        except Exception as e:
            logger.debug(f"吃单比获取失败: {e}")
            return

        buy_vol = float(data.get("buy_vol_ccy", "0") or "0")
        sell_vol = float(data.get("sell_vol_ccy", "0") or "0")
        total = buy_vol + sell_vol

        if total <= 0:
            return

        buy_ratio = buy_vol / total
        sell_ratio = sell_vol / total

        # 检测变化：超过阈值或方向翻转
        threshold = self.cfg.agent2_taker_volume_buy_ratio_threshold
        is_bullish = buy_ratio >= threshold
        is_bearish = sell_ratio >= threshold

        # 避免重复推送相近值
        if abs(buy_ratio - self._last_taker_ratio) < 0.05 and not (is_bullish or is_bearish):
            return
        self._last_taker_ratio = buy_ratio
        self._stats["last_taker_buy_ratio"] = round(buy_ratio, 4)

        sentiment = "bullish" if is_bullish else ("bearish" if is_bearish else "neutral")
        urgency = "high" if sentiment != "neutral" else "low"

        event = AgentEvent(
            type=AgentEventType.NEWS_EVENT,
            source="agent2_taker",
            data={
                "type": "taker_volume",
                "buy_ratio": round(buy_ratio, 4),
                "sell_ratio": round(sell_ratio, 4),
                "buy_vol_ccy": buy_vol,
                "sell_vol_ccy": sell_vol,
                "sentiment": sentiment,
                "description": (
                    f"📊 吃单比: 买 {buy_ratio:.1%} / 卖 {sell_ratio:.1%} ({sentiment})"
                ),
            },
            confidence=0.65 if sentiment != "neutral" else 0.35,
            urgency=urgency,
        )
        await self.bus.publish_b(event)
        self._stats["events_pushed"] += 1
        logger.info(f"📊 吃单比: 买 {buy_ratio:.1%} / 卖 {sell_ratio:.1%} ({sentiment})")

    # ── 资金费率监控 ──

    async def _funding_rate_loop(self):
        """定时获取资金费率"""
        logger.info("资金费率监控已启动")
        while self._running:
            try:
                await self._fetch_and_push_funding()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"资金费率获取异常: {e}")
            await asyncio.sleep(self.cfg.agent2_onchain_interval_seconds)

    async def _fetch_and_push_funding(self):
        """获取 OKX 资金费率数据并推送"""
        self._stats["funding_fetches"] += 1

        try:
            data = await asyncio.to_thread(self.okx.get_funding_rate)
        except Exception as e:
            logger.debug(f"资金费率获取失败: {e}")
            return

        rate_str = data.get("funding_rate", "0")
        next_rate_str = data.get("next_funding_rate", "")

        try:
            rate = float(rate_str) * 100  # 转百分比
        except (ValueError, TypeError):
            return

        try:
            next_rate = float(next_rate_str) * 100 if next_rate_str else rate
        except (ValueError, TypeError):
            next_rate = rate

        # 检测显著变化（ETH 正常费率在 0.000x%，变化 0.0001% 即推送）
        if abs(rate - self._last_funding_rate) < 0.0001 and abs(rate) < self.cfg.agent2_funding_rate_high_threshold * 100:
            return
        self._last_funding_rate = rate
        self._stats["last_funding_rate"] = round(rate, 4)

        is_high = abs(rate) >= self.cfg.agent2_funding_rate_high_threshold * 100
        sentiment = "bearish" if rate > 0 else ("bullish" if rate < 0 else "neutral")
        urgency = "high" if is_high else "medium"

        event = AgentEvent(
            type=AgentEventType.NEWS_EVENT,
            source="agent2_funding",
            data={
                "type": "funding_rate",
                "funding_rate_pct": round(rate, 4),
                "next_funding_rate_pct": round(next_rate, 4),
                "sentiment": sentiment,
                "is_high": is_high,
                "description": (
                    f"💰 资金费率: {rate:+.4f}% ({sentiment})"
                    + (" ⚠️ 高" if is_high else "")
                ),
            },
            confidence=0.6 if is_high else 0.4,
            urgency=urgency,
        )
        await self.bus.publish_b(event)
        self._stats["events_pushed"] += 1
        logger.info(f"💰 资金费率: {rate:+.4f}% ({sentiment})")

    # ── 状态 ──

    def get_status(self) -> dict:
        return {
            "running": self._running,
            **self._stats,
        }
