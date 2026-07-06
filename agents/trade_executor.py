"""
交易执行器 — OKX 实盘下单封装

支持:
  - 限价单优先（10s 未成交撤单 → 市价单兜底）
  - 滑点保护、撤单重下
  - 重试机制（网络失败重试 3 次）
  - 部分成交处理
  - 合约模式（逐仓 USDT 永续合约模拟）
  - 交易日志
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from agents.config import AgentSystemConfig

logger = logging.getLogger("trade_executor")


class TradeExecutor:
    """交易执行器

    封装 OKXClient.place_order，添加保护逻辑。
    支持现货 (cash) 和合约 (isolated) 模拟模式。
    """

    def __init__(
        self,
        okx_client,
        symbol: str = "ETH-USDT",
        config: Optional[AgentSystemConfig] = None,
        market_mode: str = "spot",
        leverage: int = 10,
        futures_account=None,
    ):
        """
        Args:
            okx_client: OKXClient 实例（来自 okx_client.py）
            symbol: 交易对
            config: AgentSystemConfig 配置（可选）
            market_mode: "spot"(现货模拟) / "futures"(合约模拟)
            leverage: 合约杠杆倍数
            futures_account: FuturesAccount 实例（合约模式使用）
        """
        self._client = okx_client
        self.symbol = symbol
        self.max_retries = 3
        self.config = config or AgentSystemConfig()
        self.market_mode = market_mode
        self.leverage = leverage
        self.futures_account = futures_account

        # 统计
        self.total_orders = 0
        self.failed_orders = 0
        self.last_order: Optional[dict] = None

    @staticmethod
    def _normalize_result(result) -> dict:
        """将 OKX 下单返回结果规范化为 dict

        OKX place_order 返回 list[dict]（如 [{"ordId": "..."}]），
        此方法提取第一个元素以便统一访问字段。
        """
        if isinstance(result, list) and len(result) > 0:
            return result[0]
        if isinstance(result, dict):
            return result
        return {}

    async def execute_market(
        self,
        side: str,       # "buy" / "sell"
        size: str,       # ETH 数量（字符串，OKX API 要求）
    ) -> dict:
        """市价单执行

        返回:
            {"success": bool, "order_id": str, "fill_price": float,
             "filled_size": float, "error": str}
        """
        for attempt in range(self.max_retries):
            try:
                # 注意: place_order 是同步方法，用 asyncio 的线程池执行
                result = await asyncio.to_thread(
                    self._client.place_order,
                    symbol=self.symbol,
                    side=side,
                    sz=size,
                    ord_type="market",
                )
                self.total_orders += 1
                order_data = self._normalize_result(result)
                self.last_order = {
                    "side": side,
                    "size": size,
                    "order_id": order_data.get("ordId", ""),
                    "fill_price": self._extract_fill_price(result),
                    "filled_size": float(size),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                return {
                    "success": True,
                    "order_id": order_data.get("ordId", ""),
                    "fill_price": self._extract_fill_price(result),
                    "filled_size": float(size),
                    "error": "",
                }

            except Exception as e:
                logger.warning(f"市价单失败 (尝试 {attempt+1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1 * (2 ** attempt))

        self.failed_orders += 1
        return {
            "success": False,
            "order_id": "",
            "fill_price": 0.0,
            "filled_size": 0.0,
            "error": f"市价单失败，已重试 {self.max_retries} 次",
        }

    async def execute_limit(
        self,
        side: str,
        size: str,
        price: str,
        timeout_seconds: Optional[int] = None,
        signal_price: Optional[float] = None,
    ) -> dict:
        """限价单完整生命周期

        流程:
        1. 提交限价单
        2. 等待 timeout_seconds（默认从 config 读取）
        3. 调用 get_order 查询成交状态
        4a. 完全成交 → 检查滑点
        4b. 部分成交 → 撤销剩余
        4c. 未成交 → 撤销 → 市价单兜底
        5. 返回最终结果
        """
        timeout = timeout_seconds or self.config.limit_order_timeout_seconds

        # ── 1. 提交限价单 ──
        order_id = ""
        try:
            result = await asyncio.to_thread(
                self._client.place_order,
                symbol=self.symbol,
                side=side,
                sz=size,
                ord_type="limit",
                px=price,
            )
            order_data = self._normalize_result(result)
            order_id = order_data.get("ordId", "")
            self.total_orders += 1
        except Exception as e:
            logger.warning(f"限价单提交失败: {e}")
            # 转市价单兜底
            result = await self.execute_market(side, size)
            result["note"] = "限价单提交失败→市价单兜底"
            return result

        if not order_id:
            return await self.execute_market(side, size)

        # ── 2. 等待成交 ──
        await asyncio.sleep(timeout)

        # ── 3. 查询订单状态 ──
        try:
            order_status = await asyncio.to_thread(
                self._client.get_order, self.symbol, order_id
            )
        except Exception as e:
            logger.warning(f"查询订单失败: {e}")
            return {
                "success": True,
                "order_id": order_id,
                "fill_price": float(price),
                "filled_size": float(size),
                "error": "",
                "estimated": True,
                "note": "订单状态查询失败，使用挂牌价",
            }

        state = order_status.get("state", "")
        acc_fill_sz = float(order_status.get("accFillSz", "0"))
        fill_px_str = order_status.get("fillPx", "") or order_status.get("avgPx", "")

        # ── 4a. 完全成交 ──
        if state == "filled":
            fill_price = float(fill_px_str) if fill_px_str else float(price)

            # 滑点检查
            if signal_price and signal_price > 0:
                slippage = abs(fill_price - signal_price) / signal_price * 100
                if slippage > self.config.max_slippage_pct:
                    logger.warning(
                        f"滑点 {slippage:.2f}% 超过 {self.config.max_slippage_pct}% 上限"
                    )
                    self.last_order = {
                        "side": side, "size": size, "order_id": order_id,
                        "fill_price": fill_price, "filled_size": acc_fill_sz,
                        "slippage_pct": round(slippage, 2),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "note": f"滑点 {slippage:.2f}% 超过上限 {self.config.max_slippage_pct}%",
                    }
                    self.failed_orders += 1
                    return {
                        "success": False,
                        "order_id": order_id,
                        "fill_price": fill_price,
                        "filled_size": acc_fill_sz,
                        "slippage_pct": round(slippage, 2),
                        "error": f"滑点 {slippage:.2f}% 超过上限 {self.config.max_slippage_pct}%",
                    }

            # Update last_order
            self.last_order = {
                "side": side, "size": size, "order_id": order_id,
                "fill_price": fill_price, "filled_size": acc_fill_sz,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            return {
                "success": True,
                "order_id": order_id,
                "fill_price": fill_price,
                "filled_size": acc_fill_sz,
                "error": "",
            }

        # ── 4b. 部分成交 — 等待额外时间再撤单 ──
        if state == "partially_filled":
            # 等待额外时间让剩余部分成交
            extra_wait = self.config.partial_fill_timeout_seconds
            logger.info(f"部分成交，等待 {extra_wait}s 让剩余部分成交...")
            await asyncio.sleep(extra_wait)

            # 再次查询订单状态
            try:
                order_status = await asyncio.to_thread(
                    self._client.get_order, self.symbol, order_id
                )
                state = order_status.get("state", "")
                acc_fill_sz = float(order_status.get("accFillSz", "0"))
                fill_px_str = order_status.get("fillPx", "") or order_status.get("avgPx", "")
            except Exception:
                pass

            # 如果仍未完全成交，撤销剩余
            if state != "filled":
                try:
                    await asyncio.to_thread(self._client.cancel_order, self.symbol, order_id)
                except Exception as e:
                    logger.warning(f"部分成交后撤单失败: {e}")

            fill_price = float(fill_px_str) if fill_px_str else float(price)
            filled_pct = (acc_fill_sz / float(size)) * 100 if float(size) > 0 else 0
            logger.info(f"限价单部分成交: {acc_fill_sz}/{size} ({filled_pct:.0f}%)")

            # Update last_order
            self.last_order = {
                "side": side, "size": size, "order_id": order_id,
                "fill_price": fill_price, "filled_size": acc_fill_sz,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "note": f"部分成交—剩余已撤销 ({filled_pct:.0f}%)",
            }

            return {
                "success": True, "order_id": order_id,
                "fill_price": fill_price, "filled_size": acc_fill_sz,
                "filled_pct": round(filled_pct, 1), "error": "",
                "note": "部分成交—剩余已撤销",
            }

        # ── 4c. 未成交 → 撤销 → 确认 → 市价单兜底 ──
        try:
            await asyncio.to_thread(self._client.cancel_order, self.symbol, order_id)
        except Exception as e:
            logger.warning(f"撤单失败: {e}")

        # 确认订单状态（防止撤单瞬间已成交 → 双仓位）
        try:
            final_status = await asyncio.to_thread(
                self._client.get_order, self.symbol, order_id
            )
            final_state = final_status.get("state", "")
            if final_state == "filled":
                fill_price = float(final_status.get("fillPx", price))
                acc_fill_sz = float(final_status.get("accFillSz", "0"))
                logger.info(f"撤单时订单已成交: fill_price={fill_price}")
                self.last_order = {
                    "side": side, "size": size, "order_id": order_id,
                    "fill_price": fill_price, "filled_size": acc_fill_sz,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "note": "撤单时已成交—未发市价单",
                }
                return {
                    "success": True, "order_id": order_id,
                    "fill_price": fill_price, "filled_size": acc_fill_sz,
                    "error": "", "note": "撤单前订单已成交",
                }
        except Exception:
            pass

        logger.info("限价单未成交，撤销后转市价单")
        result = await self.execute_market(side, size)
        result["note"] = "限价单未成交→市价单兜底"
        return result

    async def cancel_and_check(self, order_id: str) -> dict:
        """撤销订单并查询最终状态"""
        try:
            await asyncio.to_thread(self._client.cancel_order, self.symbol, order_id)
        except Exception as e:
            logger.warning(f"cancel_and_check 撤单失败: {e}")
        try:
            return await asyncio.to_thread(self._client.get_order, self.symbol, order_id)
        except Exception as e:
            logger.warning(f"cancel_and_check 查询失败: {e}")
            return {}

    async def execute_safe(
        self,
        side: str,
        size_eth: float,
        signal_price: float,
        prefer_limit: bool = True,
    ) -> dict:
        """安全执行入口（自动处理size格式、限价→市价降级、滑点保护）

        当 exchange_permissions == "read" 时自动切换模拟模式，
        不调 OKX 真实 API，直接返回模拟成交结果。
        """
        # ── 合约模式：走 FuturesAccount 模拟 ──
        if self.market_mode == "futures" and self.futures_account is not None:
            if side == "buy":
                trade = self.futures_account.open_long(
                    price=signal_price, size=size_eth, leverage=self.leverage,
                )
            elif side == "sell":
                trade = self.futures_account.open_short(
                    price=signal_price, size=size_eth, leverage=self.leverage,
                )
            else:
                return {"success": False, "error": f"未知方向: {side}"}

            pos = self.futures_account.position
            order_id = f"fut_sim_{uuid.uuid4().hex[:12]}"
            fill_price = trade.get("price", signal_price)
            filled_size = trade.get("size", size_eth)

            self.total_orders += 1
            self.last_order = {
                "side": side, "size": f"{filled_size:.6f}", "order_id": order_id,
                "fill_price": fill_price, "filled_size": filled_size,
                "market_mode": "futures",
                "leverage": self.leverage,
                "margin": trade.get("margin", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            logger.info(
                f"📝 [合约模拟] {side} {filled_size:.4f} ETH × {self.leverage}x "
                f"@ ${fill_price:.2f}"
            )
            return {
                "success": True, "order_id": order_id,
                "fill_price": fill_price, "filled_size": filled_size,
                "leverage": self.leverage,
                "margin": trade.get("margin", 0),
                "liquidation_price": pos.liquidation_price if pos and pos.is_active else 0,
                "position_value": pos.position_value if pos and pos.is_active else 0,
                "margin_rate": pos.margin_rate(signal_price) if pos and pos.is_active else 0,
                "error": "", "simulated": True, "market_mode": "futures",
            }

        # ── 模拟模式：只读权限 / paper 模式，不调真实 API ──
        if self.config.exchange_permissions == "read":
            import random  # nosec

            # ±0.2% 随机滑点，模拟市价单立即成交
            simulated_fill_price = round(
                signal_price * random.uniform(0.998, 1.002), 2
            )
            order_id = f"sim_{uuid.uuid4().hex[:12]}"
            self.total_orders += 1
            self.last_order = {
                "side": side,
                "size": f"{size_eth:.6f}",
                "order_id": order_id,
                "fill_price": simulated_fill_price,
                "filled_size": size_eth,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            logger.info(
                f"📝 [模拟] {side} {size_eth:.4f} ETH @ ${simulated_fill_price:.2f}"
            )
            return {
                "success": True,
                "order_id": order_id,
                "fill_price": simulated_fill_price,
                "filled_size": size_eth,
                "error": "",
                "simulated": True,
            }

        size_str = f"{size_eth:.6f}"

        if prefer_limit:
            price_str = f"{signal_price:.2f}"
            result = await self.execute_limit(
                side, size_str, price_str,
                signal_price=signal_price,
            )
        else:
            result = await self.execute_market(side, size_str)

        return result

    def _extract_fill_price(self, order_result) -> float:
        """从 OKX 下单返回值中提取成交价"""
        item = self._normalize_result(order_result)
        if not item:
            return 0.0
        fill_px = item.get("fillPx", "")
        if fill_px:
            return float(fill_px)
        # 部分成交
        avg_px = item.get("avgPx", "")
        if avg_px:
            return float(avg_px)
        return 0.0

    def get_stats(self) -> dict:
        return {
            "total_orders": self.total_orders,
            "failed_orders": self.failed_orders,
            "symbol": self.symbol,
        }
