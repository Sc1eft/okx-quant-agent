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

        幂等设计：
        - 每笔逻辑订单生成唯一 clOrdId，重试沿用同一 ID（交易所侧去重）
        - 下单异常后先按 clOrdId 查证订单是否已发出，避免重复下单
        - 成交后调用 get_order 补查真实成交价（place_order 响应不含 fillPx）

        返回:
            {"success": bool, "order_id": str, "fill_price": float,
             "filled_size": float, "error": str}
        """
        clord_id = f"qa{uuid.uuid4().hex[:20]}"

        for attempt in range(self.max_retries):
            try:
                # 注意: place_order 是同步方法，用 asyncio 的线程池执行
                result = await asyncio.to_thread(
                    self._client.place_order,
                    symbol=self.symbol,
                    side=side,
                    sz=size,
                    ord_type="market",
                    clord_id=clord_id,
                )
                self.total_orders += 1
                order_data = self._normalize_result(result)
                order_id = order_data.get("ordId", "")

                # 补查真实成交价与成交量
                fill_price, filled_size = await self._fetch_fill(
                    order_id, clord_id, fallback_size=float(size)
                )
                self.last_order = {
                    "side": side,
                    "size": size,
                    "order_id": order_id,
                    "clord_id": clord_id,
                    "fill_price": fill_price,
                    "filled_size": filled_size,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                return {
                    "success": True,
                    "order_id": order_id,
                    "fill_price": fill_price,
                    "filled_size": filled_size,
                    "error": "",
                }

            except Exception as e:
                logger.warning(f"市价单失败 (尝试 {attempt+1}/{self.max_retries}): {e}")
                # 订单可能已发出但响应丢失 — 按 clOrdId 查证，防止重试造成重复下单
                placed = await self._query_by_clord(clord_id)
                if placed:
                    order_id = placed.get("ordId", "")
                    logger.info(f"订单实际已成交/存在 (clOrdId={clord_id})，按已发出处理")
                    fill_price, filled_size = await self._fetch_fill(
                        order_id, clord_id, fallback_size=float(size), known=placed
                    )
                    self.total_orders += 1
                    return {
                        "success": True,
                        "order_id": order_id,
                        "fill_price": fill_price,
                        "filled_size": filled_size,
                        "error": "",
                        "recovered": True,
                    }
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

    async def _query_by_clord(self, clord_id: str) -> Optional[dict]:
        """按 clOrdId 查询订单（用于异常后的幂等恢复），查不到返回 None"""
        try:
            result = await asyncio.to_thread(
                self._client.get_order, self.symbol, "", clord_id
            )
            return result if result.get("ordId") else None
        except Exception as e:
            logger.debug(f"按 clOrdId 查询失败: {e}")
            return None

    async def _fetch_fill(
        self,
        order_id: str,
        clord_id: str,
        fallback_size: float,
        known: Optional[dict] = None,
    ) -> Tuple[float, float]:
        """查询订单的真实成交价/量（市价单成交快，最多等 ~1.5s）

        Returns:
            (fill_price, filled_size)；查不到时返回 (0.0, fallback_size)，
            调用方需对 fill_price <= 0 做兜底处理。
        """
        for wait in (0.5, 1.0):
            status = known
            if status is None:
                try:
                    status = await asyncio.to_thread(
                        self._client.get_order, self.symbol, order_id, clord_id
                    )
                except Exception as e:
                    logger.debug(f"查询成交信息失败: {e}")
                    status = None
            if status:
                px_str = status.get("avgPx", "") or status.get("fillPx", "")
                sz_str = status.get("accFillSz", "")
                if px_str:
                    return (
                        float(px_str),
                        float(sz_str) if sz_str else fallback_size,
                    )
            if wait > 0:
                await asyncio.sleep(wait)
            known = None
        logger.warning("未能查到真实成交价，返回 0.0（调用方应兜底）")
        return 0.0, fallback_size

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
            # 不能假定成交（仓位可能根本不存在）。撤单并查询最终状态，
            # 把"未知"收敛为确定结果。
            logger.warning(f"查询订单失败: {e}，撤单并确认最终状态")
            final = await self.cancel_and_check(order_id)
            final_state = final.get("state", "")
            final_fill = float(final.get("accFillSz", "0") or 0)
            if final_state == "filled" or final_fill > 0:
                fill_px = final.get("fillPx", "") or final.get("avgPx", "")
                return {
                    "success": True,
                    "order_id": order_id,
                    "fill_price": float(fill_px) if fill_px else float(price),
                    "filled_size": final_fill,
                    "error": "",
                    "note": f"状态查询失败，撤单确认{'已成交' if final_state == 'filled' else '部分成交'}",
                }
            return {
                "success": False,
                "order_id": order_id,
                "fill_price": 0.0,
                "filled_size": 0.0,
                "error": f"订单状态查询失败: {e}",
                "note": f"已撤单未成交（状态: {final_state or '未知'}）",
            }

        state = order_status.get("state", "")
        acc_fill_sz = float(order_status.get("accFillSz", "0"))
        fill_px_str = order_status.get("fillPx", "") or order_status.get("avgPx", "")

        # ── 4a. 完全成交 ──
        if state == "filled":
            fill_price = float(fill_px_str) if fill_px_str else float(price)

            # 滑点仅记录不拒绝：订单已在交易所成交，报 success=False
            # 会让调用方误以为没有持仓 → 仓位失控。限价单的价格保护
            # 由挂牌价本身保证。
            slippage = 0.0
            if signal_price and signal_price > 0:
                slippage = abs(fill_price - signal_price) / signal_price * 100
                if slippage > self.config.max_slippage_pct:
                    logger.warning(
                        f"限价单成交价偏离信号价 {slippage:.2f}% "
                        f"(上限 {self.config.max_slippage_pct}%)，仓位已建立照常跟踪"
                    )

            # Update last_order
            self.last_order = {
                "side": side, "size": size, "order_id": order_id,
                "fill_price": fill_price, "filled_size": acc_fill_sz,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            result = {
                "success": True,
                "order_id": order_id,
                "fill_price": fill_price,
                "filled_size": acc_fill_sz,
                "error": "",
            }
            if slippage > 0:
                result["slippage_pct"] = round(slippage, 2)
            return result

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
            except Exception as e:
                logger.warning(f"限价单状态查询失败: {e}")
                state = ""
                acc_fill_sz = 0.0

            # 如果仍未完全成交，撤销剩余
            cancel_failed = False
            if state != "filled":
                try:
                    await asyncio.to_thread(self._client.cancel_order, self.symbol, order_id)
                except Exception as e:
                    cancel_failed = True
                    logger.warning(f"部分成交后撤单失败: {e}，剩余挂单可能仍有效")

            fill_price = float(fill_px_str) if fill_px_str else float(price)
            filled_pct = (acc_fill_sz / float(size)) * 100 if float(size) > 0 else 0
            logger.info(f"限价单部分成交: {acc_fill_sz}/{size} ({filled_pct:.0f}%)")

            note = "部分成交—剩余已撤销"
            if cancel_failed:
                note = "部分成交—剩余撤单失败，挂单可能仍有效，请人工核对"

            # Update last_order
            self.last_order = {
                "side": side, "size": size, "order_id": order_id,
                "fill_price": fill_price, "filled_size": acc_fill_sz,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "note": f"{note} ({filled_pct:.0f}%)",
            }

            return {
                "success": True, "order_id": order_id,
                "fill_price": fill_price, "filled_size": acc_fill_sz,
                "filled_pct": round(filled_pct, 1), "error": "",
                "note": note,
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
        except Exception as e:
            # 无法确认订单是否还活着：发市价兜底可能双仓，如实失败让人工核对
            logger.error(f"撤单后无法确认订单状态: {e}，跳过市价兜底")
            return {
                "success": False,
                "order_id": order_id,
                "fill_price": 0.0,
                "filled_size": 0.0,
                "error": f"撤单后无法确认订单状态: {e}",
                "note": "限价单可能仍有效，未发市价兜底单，请人工核对",
            }
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
        if final_state != "canceled":
            # 撤单未生效，限价单可能仍挂在盘口 — 发市价兜底会双仓
            logger.error(f"限价单撤单未生效 (state={final_state})，跳过市价兜底")
            return {
                "success": False,
                "order_id": order_id,
                "fill_price": 0.0,
                "filled_size": float(final_status.get("accFillSz", "0") or 0),
                "error": f"撤单未生效 (state={final_state})",
                "note": "限价单可能仍有效，未发市价兜底单，请人工核对",
            }

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
        close_only: bool = False,
    ) -> dict:
        """安全执行入口（自动处理size格式、限价→市价降级、滑点保护）

        当 exchange_permissions == "read" 时自动切换模拟模式，
        不调 OKX 真实 API，直接返回模拟成交结果。

        Args:
            close_only: 仅平仓不开新仓（PositionMonitor 止损/止盈用）。
                        合约模式下 side 指平仓订单方向（平多=sell, 平空=buy）。
        """
        # ── 合约模式：走 FuturesAccount 模拟 ──
        if self.market_mode == "futures" and self.futures_account is not None:
            acct = self.futures_account
            pos = acct.position

            # ── 平仓专用通道（不开新仓） ──
            if close_only:
                close_dir = "long" if side == "sell" else "short"
                if not pos or not pos.is_active or pos.direction != close_dir:
                    return {
                        "success": False, "order_id": "", "fill_price": 0.0,
                        "filled_size": 0.0,
                        "error": f"无 {close_dir} 持仓可平（当前: {pos.direction if pos and pos.is_active else 'flat'}）",
                    }
                trade = acct.close_position(price=signal_price, prefer_limit=prefer_limit)
                if trade.get("note"):
                    return {
                        "success": False, "order_id": "", "fill_price": 0.0,
                        "filled_size": 0.0, "error": f"平仓失败: {trade['note']}",
                    }
                order_id = f"fut_sim_{uuid.uuid4().hex[:12]}"
                self.total_orders += 1
                logger.info(
                    f"📝 [合约模拟] 平{close_dir} {trade['size']:.4f} ETH "
                    f"@ ${trade['price']:.2f} PnL={trade.get('pnl', 0):+.2f}"
                )
                return {
                    "success": True, "order_id": order_id,
                    "fill_price": trade["price"], "filled_size": trade["size"],
                    "realized_pnl": trade.get("pnl", 0), "closed": True,
                    "error": "", "simulated": True, "market_mode": "futures",
                }

            # ── 开仓 / 反转（有反向持仓时先平后开） ──
            want_dir = "long" if side == "buy" else "short" if side == "sell" else None
            if want_dir is None:
                return {"success": False, "error": f"未知方向: {side}"}

            if pos and pos.is_active and pos.direction != want_dir:
                close_trade = acct.close_position(price=signal_price)
                if close_trade.get("note"):
                    return {
                        "success": False, "order_id": "", "fill_price": 0.0,
                        "filled_size": 0.0,
                        "error": f"反转平仓失败: {close_trade['note']}",
                    }
                logger.info(
                    f"📝 [合约模拟] 反转先平{pos.direction if pos else '?'} "
                    f"@ ${close_trade['price']:.2f} PnL={close_trade.get('pnl', 0):+.2f}"
                )

            if side == "buy":
                trade = acct.open_long(
                    price=signal_price, size=size_eth, leverage=self.leverage,
                    prefer_limit=prefer_limit,
                )
            else:
                trade = acct.open_short(
                    price=signal_price, size=size_eth, leverage=self.leverage,
                    prefer_limit=prefer_limit,
                )

            # FuturesAccount 以 note 字段报告拒绝原因，必须如实上抛，不能伪造成功
            note = trade.get("note")
            if note:
                return {
                    "success": False, "order_id": "", "fill_price": 0.0,
                    "filled_size": 0.0, "error": f"开仓被拒: {note}",
                }

            pos = acct.position
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

    def get_stats(self) -> dict:
        return {
            "total_orders": self.total_orders,
            "failed_orders": self.failed_orders,
            "symbol": self.symbol,
        }
