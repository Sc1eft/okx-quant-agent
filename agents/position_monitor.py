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
        self._entry_time: Optional[datetime] = None  # 开仓时间（用于最小持仓时间保护）

        # 止盈止损（从外部传入）
        self._stop_loss: float = 0.0
        self._take_profit: float = 0.0

        # 移动止损状态
        self._trailing_stop_active: bool = False
        self._trailing_high: float = 0.0        # 多头：最高价
        self._trailing_low: float = 0.0         # 空头：最低价
        self._current_stop_loss: float = 0.0    # 当前实际止损位

        # 开仓是否用了限价单（maker 费率）
        self._opened_with_limit: bool = False

        # 累计已支付的开仓手续费（补仓场景）
        self._total_open_fees: float = 0.0

        # 开仓时的 DeepSeek 信心度与仓位比例（用于平仓记录追溯）
        self._open_confidence: float = 0.0
        self._open_position_size_pct: float = 0.0

        # 开平配对 ID（由 update_position 赋值）
        self._trade_group_id: str = ""

        # 连续平仓失败次数（>0 时说明交易所有仓位未被正确平掉）
        self._close_failures: int = 0

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
        opened_with_limit: bool = True,
        accumulate: bool = False,
        confidence: float = 0.0,
        position_size_pct: float = 0.0,
    ):
        """更新持仓信息（由 Agent 3 在新开仓后调用）

        如果当前有持仓且新方向不同（反转）或清仓，
        先自动记录平仓盈亏再更新。

        Args:
            accumulate: True=同方向补仓（累加 size，加权均价），
                        False=覆盖新开仓（默认）
            confidence: 开仓时 DeepSeek 的信心度（用于平仓记录追溯）
            position_size_pct: 开仓时的仓位比例（用于平仓记录追溯）
        """
        # ── 补仓模式：同方向累加 ──
        if accumulate and self._has_position and side == self._position_side and size > 0:
            total_size = self._position_size + size
            self._entry_price = (
                self._position_size * self._entry_price + size * entry_price
            ) / total_size
            self._position_size = total_size

            # 累计开仓手续费
            fee_rate = self._maker_fee_rate if opened_with_limit else self._taker_fee_rate
            self._total_open_fees += size * entry_price * fee_rate

            # 用最新 SL/TP 覆盖（DeepSeek 看到的是全量上下文）
            if stop_loss > 0:
                self._stop_loss = stop_loss
            if take_profit > 0:
                self._take_profit = take_profit

            # _opened_with_limit：如果这次用了 maker 且之前是 taker，升级
            if opened_with_limit and not self._opened_with_limit:
                self._opened_with_limit = True

            # 用最新的信心度和仓位比例覆盖
            self._open_confidence = confidence
            self._open_position_size_pct = position_size_pct

            # 重置移动止损状态（以当前累计持仓为准重新跟踪）
            self._trailing_stop_active = False
            self._trailing_high = entry_price if side == "long" else 0.0
            self._trailing_low = entry_price if side == "short" else float("inf")

            self._entry_time = datetime.now(timezone.utc)

            logger.info(
                f"补仓: +{size:.4f} ETH @ ${entry_price:.2f} → "
                f"总持仓 {self._position_size:.4f} ETH, 均价 ${self._entry_price:.2f}, "
                f"SL=${self._stop_loss:.2f} TP=${self._take_profit:.2f}"
            )
            return

        # ── 覆盖 / 反转模式（已有逻辑） ──
        # 检测反转/平仓 → 先记 PnL
        if self._has_position and self._position_size > 0:
            is_reversal = (size == 0) or (side != self._position_side)
            if is_reversal and entry_price > 0:
                # 最小持仓时间保护：持仓不足 N 秒时跳过平仓记录（防零持仓反转刷单）
                min_hold = getattr(self.config, 'agent3_min_holding_time_seconds', 120)
                hold_secs = (datetime.now(timezone.utc) - self._entry_time).total_seconds() if self._entry_time else 999
                if hold_secs < min_hold:
                    logger.info(
                        f"持仓仅 {hold_secs:.0f}s，低于最小持仓 {min_hold}s，跳过反转平仓记录。 "
                        f"方向: {self._position_side} → {side}"
                    )
                else:
                    self._record_close_pnl(entry_price)

        self._has_position = size > 0
        self._position_side = side if self._has_position else "none"
        self._position_size = size
        self._entry_price = entry_price
        self._entry_time = datetime.now(timezone.utc) if self._has_position else None
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        self._trade_group_id = trade_group_id
        self._opened_with_limit = opened_with_limit if self._has_position else False
        self._total_open_fees = (
            size * entry_price * (self._maker_fee_rate if opened_with_limit else self._taker_fee_rate)
        ) if size > 0 else 0.0  # 覆盖模式下重置为本次单笔费用

        # 记录开仓时的原始决策信心与仓位比例
        self._open_confidence = confidence if self._has_position else 0.0
        self._open_position_size_pct = position_size_pct if self._has_position else 0.0

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

    def _record_close_pnl(self, close_price: float):
        """记录现有持仓的平仓盈亏（反转或清仓时调用）"""
        close_side = "sell" if self._position_side == "long" else "buy"

        if self._position_side == "long":
            gross_pnl = (close_price - self._entry_price) * self._position_size
        else:
            gross_pnl = (self._entry_price - close_price) * self._position_size

        # 开仓费用：补仓场景用累计值，单次开仓按实际费率
        open_fee = self._total_open_fees if self._total_open_fees > 0 else (
            self._position_size * self._entry_price *
            (self._maker_fee_rate if self._opened_with_limit else self._taker_fee_rate)
        )
        close_fee = self._position_size * close_price * self._taker_fee_rate
        total_fee = open_fee + close_fee
        net_pnl = gross_pnl - total_fee

        # 平仓原因：反转 or 清仓
        reason = f"平{self._position_side}反手" if self._position_side else "平仓"

        self.risk.record_trade({
            "side": close_side,
            "size": self._position_size,
            "price": close_price,
            "pnl": round(net_pnl, 2),
            "pnl_close": round(net_pnl, 2),
            "fee": round(close_fee, 2),
            "trade_group_id": self._trade_group_id or "",
            "trade_type": "close",
            "order_id": "",
            "symbol": self.executor.symbol,
            "decision": {"action": close_side, "reason": reason},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "confidence": self._open_confidence,
            "position_size_pct": self._open_position_size_pct,
        })

        logger.info(
            f"📝 反转平仓: {self._position_side} {self._position_size:.4f} ETH "
            f"@ ${close_price:.2f} → PnL={net_pnl:+.2f} USDT"
        )

        # 通知 Agent 3 重置仓位状态
        if self.close_callback:
            self.close_callback(
                side=close_side,
                size=self._position_size,
                fill_price=close_price,
                pnl=round(net_pnl, 2),
            )

    def clear_position(self):
        """清空持仓（外部调用，如手动平仓后）"""
        self._total_open_fees = 0.0
        self.update_position("none", 0, 0, 0, 0)

    def restore_from_db(self) -> bool:
        """启动时从 SQLite 回放交易记录，重建持仓状态

        回放规则:
        - trade_type='open': side=buy → 多头, side=sell → 空头;
          同方向 → 加权累加，反方向 → 视为反转（替换）
        - trade_type='close': 清仓
        - 止损/止盈取开仓记录中的持久化值（P2-3 起入库）；
          旧记录无此值时按配置默认百分比重建（日志告警）

        Returns:
            True = 恢复出了持仓；False = 无持仓
        """
        if not getattr(self.risk, "_db_conn", None):
            return False
        try:
            rows = self.risk._db_conn.execute(
                "SELECT * FROM trades ORDER BY id"
            ).fetchall()
        except Exception as e:
            logger.error(f"持仓恢复查询失败: {e}")
            return False

        side = "none"
        size = 0.0
        entry = 0.0
        group_id = ""
        saved_sl = 0.0
        saved_tp = 0.0
        for r in rows:
            t_side = r["side"] or ""
            t_size = float(r["size"] or 0)
            t_price = float(r["price"] or 0)
            if t_size <= 0 or t_price <= 0:
                continue
            if r["trade_type"] == "close":
                side, size, entry, group_id = "none", 0.0, 0.0, ""
                saved_sl = saved_tp = 0.0
                continue
            # open 记录
            direction = "long" if t_side == "buy" else "short"
            if side == direction and size > 0:
                entry = (size * entry + t_size * t_price) / (size + t_size)
                size += t_size
            else:
                side, size, entry = direction, t_size, t_price
            group_id = r["trade_group_id"] or ""
            # 开仓时持久化的止损止盈（旧记录无此列则为 0）
            keys = r.keys()
            saved_sl = float(r["stop_loss"]) if "stop_loss" in keys and r["stop_loss"] else 0.0
            saved_tp = float(r["take_profit"]) if "take_profit" in keys and r["take_profit"] else 0.0

        if side == "none" or size <= 0:
            logger.info("持仓恢复: 无未平仓记录，空仓启动")
            return False

        if saved_sl > 0 and saved_tp > 0:
            # 开仓时入库的止损止盈原样还原（含 DeepSeek 定制值）
            stop_loss, take_profit = saved_sl, saved_tp
            logger.warning(
                f"持仓已从 DB 恢复: {side} {size:.4f} ETH @ ${entry:.2f}, "
                f"SL=${stop_loss:.2f} TP=${take_profit:.2f}（开仓时保存值）"
            )
        else:
            # 旧记录无 SL/TP，用配置默认值重建
            sl_pct = self.config.agent3_default_stop_loss_pct / 100
            tp_pct = self.config.agent3_default_take_profit_pct / 100
            if side == "long":
                stop_loss = entry * (1 - sl_pct)
                take_profit = entry * (1 + tp_pct)
            else:
                stop_loss = entry * (1 + sl_pct)
                take_profit = entry * (1 - tp_pct)
            logger.warning(
                f"持仓已从 DB 恢复: {side} {size:.4f} ETH @ ${entry:.2f}, "
                f"止损/止盈按配置默认重建 SL=${stop_loss:.2f} TP=${take_profit:.2f}"
            )

        self._has_position = True
        self._position_side = side
        self._position_size = size
        self._entry_price = entry
        self._entry_time = datetime.now(timezone.utc)
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        self._current_stop_loss = stop_loss
        self._trade_group_id = group_id
        self._trailing_stop_active = False
        self._trailing_high = entry if side == "long" else 0.0
        self._trailing_low = entry if side == "short" else float("inf")

        return True

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

    @property
    def _maker_fee_rate(self) -> float:
        """Maker 费率（限价单吃深度）"""
        mode = getattr(self.executor, 'market_mode', 'spot')
        if mode == "futures":
            return self.config.futures_maker_fee_rate
        return self.config.maker_fee_rate

    @property
    def _taker_fee_rate(self) -> float:
        """Taker 费率（市价单立即成交）"""
        mode = getattr(self.executor, 'market_mode', 'spot')
        if mode == "futures":
            return self.config.futures_taker_fee_rate
        return self.config.taker_fee_rate

    async def _close_position(self, reason: str, current_price: float = 0.0):
        """平仓（按市价卖出/买入）

        失败时保留持仓状态并交由下一轮检查重试——绝不出现
        "系统以为平了、交易所仓位裸奔"的状态分叉。

        Args:
            reason: 平仓原因描述
            current_price: 触发平仓时的当前价格（用于 execute_safe 模拟模式）
        """
        side = "sell" if self._position_side == "long" else "buy"

        logger.info(f"平仓: {self._position_side} {self._position_size:.4f} ETH (原因: {reason})")

        # 1. 执行平仓（最多 3 次尝试，指数退避）
        success = False
        fill_price = 0.0
        order_id = ""
        last_error = ""
        for attempt in range(3):
            try:
                result = await self.executor.execute_safe(
                    side=side,
                    size_eth=self._position_size,
                    signal_price=current_price if current_price > 0 else self._entry_price,
                    prefer_limit=False,  # 平仓用市价单
                    close_only=True,     # 只平仓，不反转开新仓
                )
                if result.get("success"):
                    success = True
                    fill_price = result.get("fill_price", 0.0) or 0.0
                    order_id = result.get("order_id", "")
                    break
                last_error = result.get("error", "未知错误")
                logger.warning(f"平仓失败 (尝试 {attempt + 1}/3): {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"平仓异常 (尝试 {attempt + 1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(2 * (2 ** attempt))

        # 2. 平仓未成功 → 保留持仓，下轮检查自动重试
        if not success:
            self._close_failures += 1
            logger.error(
                f"平仓未成功 ({reason})，保留持仓状态，下轮重试。"
                f"累计失败 {self._close_failures} 次。最后错误: {last_error}"
            )
            return

        self._close_failures = 0
        # 成交价兜底：模拟/查询异常时可能返回 0，用触发价或入场价替代
        if fill_price <= 0:
            fill_price = current_price if current_price > 0 else self._entry_price
            logger.warning(f"平仓成交价缺失，使用兜底价 {fill_price:.2f} 记账")

        # 3. 计算 PnL
        if self._position_side == "long":
            gross_pnl = (fill_price - self._entry_price) * self._position_size
        else:
            gross_pnl = (self._entry_price - fill_price) * self._position_size

        # 开仓费用：补仓场景用累计值，单次开仓按实际费率
        open_fee = self._total_open_fees if self._total_open_fees > 0 else (
            self._position_size * self._entry_price *
            (self._maker_fee_rate if self._opened_with_limit else self._taker_fee_rate)
        )
        close_fee = self._position_size * fill_price * self._taker_fee_rate
        total_fee = open_fee + close_fee
        net_pnl = gross_pnl - total_fee

        # 4. 记录平仓到风控系统（trade_type='close'，Agent 4 复盘依赖此记录）
        self.risk.record_trade({
            "side": side,
            "size": self._position_size,
            "price": fill_price,
            "pnl": round(net_pnl, 2),
            "pnl_close": round(net_pnl, 2),
            "fee": round(close_fee, 2),
            "trade_group_id": self._trade_group_id or "",
            "trade_type": "close",
            "order_id": order_id,
            "symbol": self.executor.symbol,
            "decision": {"action": side, "reason": reason},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "confidence": self._open_confidence,
            "position_size_pct": self._open_position_size_pct,
        })

        # 5. 通知 Agent 3 更新仓位状态
        if self.close_callback:
            self.close_callback(
                side=side,
                size=self._position_size,
                fill_price=fill_price,
                pnl=round(net_pnl, 2),
            )

        # 6. 清空持仓标记（先清状态标记，防止 clear_position → update_position 重复记账）
        self._has_position = False
        self.clear_position()

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "has_position": self._has_position,
            "position_side": self._position_side,
            "position_size": self._position_size,
            "entry_price": self._entry_price,
            "entry_time": self._entry_time.isoformat() if self._entry_time else "",
            "stop_loss": self._current_stop_loss,
            "take_profit": self._take_profit,
            "trailing_stop_active": self._trailing_stop_active,
            "trailing_high": self._trailing_high,
            "total_open_fees": round(self._total_open_fees, 4),
            "close_failures": self._close_failures,
            **self._stats,
        }
