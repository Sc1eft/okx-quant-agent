"""
持仓监控器 — 止盈 / 止损 / 移动止损

职责:
  1. 每 N 秒检查持仓状态
  2. 价格达到止损位 → 触发市价平仓
  3. 价格达到止盈位 → 触发市价平仓
  4. 浮动止损（trailing stop）：价格朝有利方向移动时上移止损位

被 Agent 3 启动，独立协程运行。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from agents.config import AgentSystemConfig

logger = logging.getLogger("position_monitor")


class PositionMonitor:
    """持仓监控器 — 止盈/止损/移动止损"""

    def __init__(
        self,
        config: AgentSystemConfig,
        risk_manager,
        executor,
        okx_client,
        close_callback=None,  # 平仓回调: close_callback(side, size, fill_price, pnl)
    ):
        self.config = config
        self.risk = risk_manager
        self.executor = executor
        self.okx = okx_client
        self.close_callback = close_callback

        # 当前持仓信息
        self._has_position: bool = False
        self._position_side: str = "none"       # "long" / "short" / "none"
        self._position_size: float = 0.0        # ETH
        self._entry_price: float = 0.0

        # 止盈止损（从外部传入）
        self._stop_loss: float = 0.0
        self._take_profit: float = 0.0

        # 移动止损状态
        self._trailing_stop_active: bool = False
        self._trailing_high: float = 0.0        # 多头：最高价
        self._trailing_low: float = 0.0         # 空头：最低价
        self._current_stop_loss: float = 0.0    # 当前实际止损位

        # 运行状态
        self._running: bool = False
        self._stats = {
            "stop_loss_triggered": 0,
            "take_profit_triggered": 0,
            "trailing_stop_activated": 0,
            "trailing_stop_triggered": 0,
            "start_time": "",
        }

    # ── 公开接口 ──

    def update_position(
        self,
        side: str,
        size: float,
        entry_price: float,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        trade_group_id: str = "",
    ):
        """更新持仓信息（由 Agent 3 在新开仓后调用）"""
        self._has_position = size > 0
        self._position_side = side if self._has_position else "none"
        self._position_size = size
        self._entry_price = entry_price
        self._stop_loss = stop_loss
        self._take_profit = take_profit

        # 重置移动止损状态
        self._trailing_stop_active = False
        self._trailing_high = entry_price if side == "long" else 0.0
        self._trailing_low = entry_price if side == "short" else float("inf")
        self._current_stop_loss = stop_loss

        if self._has_position:
            logger.info(
                f"持仓更新: {side} {size:.4f} ETH @ ${entry_price:.2f} "
                f"SL=${stop_loss:.2f} TP=${take_profit:.2f}"
            )
        else:
            logger.info("持仓已清空，停止监控")

    def clear_position(self):
        """清空持仓（外部调用，如手动平仓后）"""
        self.update_position("none", 0, 0, 0, 0)

    async def run(self):
        """启动持仓监控主循环"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("持仓监控器启动")

        while self._running:
            try:
                await self._check_once()
            except Exception as e:
                logger.error(f"持仓检查异常: {e}")

            await asyncio.sleep(self.config.position_monitor_interval)

    async def stop(self):
        """停止监控"""
        self._running = False
        logger.info("持仓监控器已停止")

    # ── 内部逻辑 ──

    async def _check_once(self) -> bool:
        """执行一次持仓检查

        返回: True=触发了平仓操作
        """
        if not self._has_position or self._position_size <= 0:
            return False

        # 获取当前价格
        try:
            ticker = await asyncio.to_thread(self.okx.get_ticker, self.executor.symbol)
            current_price = float(ticker.get("last", 0))
        except Exception as e:
            logger.warning(f"获取当前价格失败: {e}")
            return False

        if current_price <= 0:
            return False

        # 多头逻辑
        if self._position_side == "long":
            return await self._check_long(current_price)

        # 空头逻辑
        if self._position_side == "short":
            return await self._check_short(current_price)

        return False

    async def _check_long(self, current_price: float) -> bool:
        """检查多头持仓"""
        # 更新移动止损跟踪的最高价
        if current_price > self._trailing_high:
            self._trailing_high = current_price

            # 检查是否激活移动止损
            pnl_pct = (current_price - self._entry_price) / self._entry_price * 100
            if pnl_pct >= self.config.trailing_stop_activation_pct and not self._trailing_stop_active:
                self._trailing_stop_active = True
                self._stats["trailing_stop_activated"] += 1
                logger.info(f"移动止损激活 @ ${current_price:.2f} (浮盈 {pnl_pct:.1f}%)")

            # 更新移动止损位
            if self._trailing_stop_active:
                new_sl = self._trailing_high * (1 - self.config.trailing_stop_distance_pct / 100)
                if new_sl > self._current_stop_loss:
                    self._current_stop_loss = new_sl

        # 检查是否触发止损（含移动止损）
        if current_price <= self._current_stop_loss:
            logger.warning(
                f"多头止损触发: ${current_price:.2f} <= SL ${self._current_stop_loss:.2f}"
            )
            was_trailing = self._trailing_stop_active
            await self._close_position("多头止损", current_price)
            self._stats["stop_loss_triggered"] += 1
            if was_trailing:
                self._stats["trailing_stop_triggered"] += 1
            return True

        # 检查是否触发止盈
        if self._take_profit > 0 and current_price >= self._take_profit:
            logger.info(
                f"多头止盈触发: ${current_price:.2f} >= TP ${self._take_profit:.2f}"
            )
            await self._close_position("多头止盈", current_price)
            self._stats["take_profit_triggered"] += 1
            return True

        return False

    async def _check_short(self, current_price: float) -> bool:
        """检查空头持仓"""
        # 更新移动止损跟踪的最低价
        if current_price < self._trailing_low:
            self._trailing_low = current_price

            pnl_pct = (self._entry_price - current_price) / self._entry_price * 100
            if pnl_pct >= self.config.trailing_stop_activation_pct and not self._trailing_stop_active:
                self._trailing_stop_active = True
                self._stats["trailing_stop_activated"] += 1
                logger.info(f"空头移动止损激活 @ ${current_price:.2f} (浮盈 {pnl_pct:.1f}%)")

            if self._trailing_stop_active:
                new_sl = self._trailing_low * (1 + self.config.trailing_stop_distance_pct / 100)
                if new_sl < self._current_stop_loss:
                    self._current_stop_loss = new_sl

        # 止损（价格上涨）
        if self._current_stop_loss > 0 and current_price >= self._current_stop_loss:
            logger.warning(
                f"空头止损触发: ${current_price:.2f} >= SL ${self._current_stop_loss:.2f}"
            )
            was_trailing = self._trailing_stop_active
            await self._close_position("空头止损", current_price)
            self._stats["stop_loss_triggered"] += 1
            if was_trailing:
                self._stats["trailing_stop_triggered"] += 1
            return True

        # 止盈（价格下跌）
        if self._take_profit > 0 and current_price <= self._take_profit:
            logger.info(
                f"空头止盈触发: ${current_price:.2f} <= TP ${self._take_profit:.2f}"
            )
            await self._close_position("空头止盈", current_price)
            self._stats["take_profit_triggered"] += 1
            return True

        return False

    async def _close_position(self, reason: str, current_price: float = 0.0):
        """平仓（按市价卖出/买入）

        Args:
            reason: 平仓原因描述
            current_price: 触发平仓时的当前价格（用于 execute_safe 模拟模式）
        """
        side = "sell" if self._position_side == "long" else "buy"
        size_str = f"{self._position_size:.6f}"

        logger.info(f"平仓: {self._position_side} {self._position_size:.4f} ETH (原因: {reason})")

        try:
            result = await self.executor.execute_safe(
                side=side,
                size_eth=self._position_size,
                signal_price=current_price if current_price > 0 else self._entry_price,
                prefer_limit=False,  # 平仓用市价单
            )
            if result["success"]:
                # Calculate actual PnL
                fill_price = result.get("fill_price", 0)
                if self._position_side == "long":
                    pnl = (fill_price - self._entry_price) * self._position_size
                else:
                    pnl = (self._entry_price - fill_price) * self._position_size

                self.risk.record_trade({
                    "side": side,
                    "size": self._position_size,
                    "price": fill_price,
                    "pnl": round(pnl, 2),
                    "order_id": result["order_id"],
                    "symbol": self.executor.symbol,
                    "decision": {"action": side, "reason": reason},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                # 通知 Agent 3 更新仓位状态
                if self.close_callback:
                    self.close_callback(
                        side=side,
                        size=self._position_size,
                        fill_price=fill_price,
                        pnl=round(pnl, 2),
                    )
            else:
                logger.error(f"平仓失败: {result.get('error', '')}")
        except Exception as e:
            logger.error(f"平仓异常: {e}")

        # 清空持仓标记（不管成功与否都标记）
        self.clear_position()

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "has_position": self._has_position,
            "position_side": self._position_side,
            "position_size": self._position_size,
            "entry_price": self._entry_price,
            "stop_loss": self._current_stop_loss,
            "take_profit": self._take_profit,
            "trailing_stop_active": self._trailing_stop_active,
            "trailing_high": self._trailing_high,
            **self._stats,
        }
