"""
风控管理器（RiskManager）— 交易后状态与记录

交易前/交易中检查已统一收敛到 agents/rule_engine/（RuleEngine 唯一风控入口）。
本模块保留：
  - 仓位查询（委托 PositionMonitor，唯一仓位事实源）
  - API 错误熔断计数
  - 交易后记录：写入 SQLite、连亏/日亏损统计、仓位乘数
  - 每日状态重置（北京时间 00:00）与启动状态恢复
"""
from __future__ import annotations

import json
import logging
import sqlite3
import os
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Tuple

from agents.config import AgentSystemConfig
from data.db_manager import DatabaseManager

logger = logging.getLogger("risk_layer")


class RiskManager:
    """风控管理器 — 三层风控"""

    def __init__(self, config: AgentSystemConfig):
        self.config = config

        # ── Layer 1 状态 ──
        self._last_trade_time: Optional[datetime] = None
        self._daily_trade_count: int = 0
        self._daily_loss_usdt: float = 0.0
        self._daily_realized_pnl: float = 0.0  # 今日净盈亏（正=盈利, 负=亏损）
        self._consecutive_losses: int = 0
        self._current_date: date = datetime.now(timezone.utc).date()
        self._current_cst_date: date = self._utc_to_cst_date(datetime.now(timezone.utc))
        # 仓位唯一事实源是 PositionMonitor（由 main.py 接线）；
        # RiskManager 不再自行记账仓位（旧记账逻辑方向处理有误且与 PM 分叉）
        self.position_monitor = None

        # ── Layer 2 状态 ──
        self._consecutive_api_errors: int = 0
        self._api_breaker_until: Optional[datetime] = None

        # ── Phase 2 状态 ──
        self._volatility_delay_until: Optional[datetime] = None

        # ── Layer 3 状态 ──
        self._daily_trades: list[dict] = []

        # ── SQLite 持久化 ──
        self._init_db()
        # 启动恢复：从 DB 重建当日风控状态（重启不清零，防绕过日内限制）
        self._restore_daily_state()

    # ── Layer 1: 交易前检查已迁移至 agents/rule_engine/（波动/深度/限额/频率规则） ──

    def get_position(self) -> Tuple[Optional[str], float]:
        """当前持仓 (side, size_eth)，side 为 None 表示无持仓

        PositionMonitor 是唯一仓位事实源；未接入时视为无持仓。
        """
        pm = self.position_monitor
        if pm is not None:
            st = pm.get_status()
            side = st.get("position_side", "none")
            size = st.get("position_size", 0.0)
            if side in (None, "none") or size <= 0:
                return None, 0.0
            return side, size
        return None, 0.0

    # ── Layer 2: 交易中保护 ──

    def report_api_error(self):
        """报告 API 错误（用于熔断）"""
        self._consecutive_api_errors += 1
        if self._consecutive_api_errors >= 3:
            self._api_breaker_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            logger.warning(f"连续 {self._consecutive_api_errors} 次 API 错误，触发熔断 5 分钟")
        # 实际熔断时间在 check 里计算

    def reset_api_errors(self):
        """重置 API 错误计数"""
        self._consecutive_api_errors = 0
        self._api_breaker_until = None

    # ── Layer 3: 交易后记录 ──

    def record_trade(self, trade_data: dict):
        """记录一笔交易（写入内存 + SQLite）

        Phase 4 新增字段:
            trade_group_id (str): 开平配对 ID
            trade_type (str): 'open' / 'close'
            pnl_close (float): 平仓时实际盈亏
            fee (float): 该笔交易的手续费，开仓和平仓各记一次
        """
        self._last_trade_time = datetime.now(timezone.utc)
        self._daily_trade_count += 1
        self._daily_trades.append(trade_data)
        self._log_trade_sync(trade_data)

        # Phase 4: 平仓时更新对应开仓记录的 pnl_close
        if trade_data.get("trade_type") == "close" and trade_data.get("trade_group_id"):
            self._update_pnl_close(trade_data)

        pnl = trade_data.get("pnl", 0)
        size = trade_data.get("size", 0)
        _is_small_position = size < self.config.agent3_min_position_for_loss_tracking

        if pnl < 0:
            if _is_small_position:
                logger.info(
                    f"小仓亏损不计入风控: {size:.4f} ETH (阈值"
                    f" {self.config.agent3_min_position_for_loss_tracking} ETH), "
                    f"亏损 ${abs(pnl):.2f}"
                )
            else:
                self._record_loss(abs(pnl))
        elif pnl > 0:
            self._consecutive_losses = 0  # 盈利后重置连亏

        # 追踪今日净盈亏（不论开/平仓，有 pnl 都计入）
        if pnl != 0:
            self._daily_realized_pnl = round(self._daily_realized_pnl + pnl, 2)

    def _update_pnl_close(self, trade_data: dict):
        """平仓时更新对应开仓记录的 pnl_close"""
        if not self._db_conn:
            return
        try:
            with self._db.write_lock:
                self._db_conn.execute(
                    "UPDATE trades SET pnl_close = ? "
                    "WHERE trade_group_id = ? AND trade_type = 'open'",
                    (trade_data.get("pnl", 0), trade_data["trade_group_id"])
                )
                self._db_conn.commit()
        except Exception as e:
            logger.debug(f"更新 pnl_close 失败: {e}")

    def _record_loss(self, loss_usdt: float):
        """记录亏损"""
        self._consecutive_losses += 1
        self._daily_loss_usdt += loss_usdt

    def record_loss(self, loss_usdt: float):
        """公开的亏损记录接口，代理 _record_loss"""
        self._record_loss(loss_usdt)

    def get_position_size_multiplier(self) -> float:
        """返回仓位乘数（连亏后减半）"""
        if self._consecutive_losses > 0:
            return max(0.1, 1.0 - self._consecutive_losses * 0.25)
        return 1.0

    @staticmethod
    def _utc_to_cst_date(utc_dt: datetime) -> date:
        """UTC 时间转北京时间（CST, UTC+8）的日期"""
        from time_utils import utc_to_cst
        return utc_to_cst(utc_dt).date()

    def _check_date_reset(self, now: datetime):
        """每日重置（北京时间午夜 00:00 CST = UTC 16:00）"""
        cst_today = self._utc_to_cst_date(now)
        if cst_today != self._current_cst_date:
            logger.info(f"每日风控重置 (CST): {self._current_cst_date} → {cst_today}")
            self._daily_trade_count = 0
            self._daily_loss_usdt = 0.0
            self._daily_realized_pnl = 0.0
            self._consecutive_losses = 0
            self._current_cst_date = cst_today
            self._current_date = now.date()
            self._daily_trades = []
            self._consecutive_api_errors = 0
            self._api_breaker_until = None

    def is_daily_limit_reached(self) -> bool:
        """已达每日交易上限？（含跨日自动重置）"""
        self._check_date_reset(datetime.now(timezone.utc))
        return self._daily_trade_count >= self.config.agent3_max_daily_trades

    def get_status(self) -> dict:
        """返回风控状态摘要"""
        pos_side, pos_eth = self.get_position()
        return {
            "daily_trade_count": self._daily_trade_count,
            "max_daily_trades": self.config.agent3_max_daily_trades,
            "daily_loss_usdt": round(self._daily_loss_usdt, 2),
            "daily_realized_pnl": round(self._daily_realized_pnl, 2),
            "max_daily_loss_usdt": self.config.agent3_max_daily_loss_usdt,
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive_losses": self.config.agent3_max_consecutive_losses,
            "position_size_multiplier": self.get_position_size_multiplier(),
            "position_eth": round(pos_eth, 6),
            "position_side": pos_side,
            "max_trades_per_hour": self.config.max_trades_per_hour,
        }

    # ── SQLite 持久化 ──

    def _init_db(self):
        """初始化 SQLite 数据库和表（使用 DatabaseManager 共享连接）"""
        db_path = self.config.db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        try:
            self._db = DatabaseManager(db_path)
            self._db_conn = self._db.conn
            # 建表/迁移统一走 db_manager.ensure_trades_schema（单一 schema 权威）
            from data.db_manager import ensure_trades_schema
            ensure_trades_schema(self._db_conn)
        except Exception as e:
            logger.error(f"SQLite 初始化失败: {e}")
            self._db = None
            self._db_conn = None

    def _restore_daily_state(self):
        """启动时从 DB 回放当日交易，重建内存风控状态

        恢复规则与 record_trade 的记账规则保持一致：
        - 每行（open/close）都计入当日交易次数
        - pnl<0 且仓位 >= 最小跟踪仓位 → 连亏+1、日亏损累加
        - pnl>0 → 连亏清零
        - 仅处理北京时间当日（CST 00:00 = UTC 16:00）以来的记录
        """
        if not self._db_conn:
            return
        try:
            from time_utils import now_cst
            cst_now = now_cst()
            cst_midnight = cst_now.replace(hour=0, minute=0, second=0, microsecond=0)
            utc_cutoff = cst_midnight.astimezone(timezone.utc).isoformat()

            rows = self._db_conn.execute(
                "SELECT * FROM trades WHERE timestamp >= ? ORDER BY id",
                (utc_cutoff,),
            ).fetchall()
            if not rows:
                return

            min_size = self.config.agent3_min_position_for_loss_tracking
            for r in rows:
                trade = dict(r)
                self._daily_trade_count += 1
                self._daily_trades.append(trade)

                pnl = trade.get("pnl") or 0
                size = trade.get("size") or 0
                if pnl < 0 and size >= min_size:
                    self._consecutive_losses += 1
                    self._daily_loss_usdt += abs(pnl)
                elif pnl > 0:
                    self._consecutive_losses = 0
                if pnl != 0:
                    self._daily_realized_pnl = round(self._daily_realized_pnl + pnl, 2)

            last_ts = rows[-1]["timestamp"]
            if last_ts:
                try:
                    self._last_trade_time = datetime.fromisoformat(last_ts)
                except ValueError:
                    pass

            self._daily_loss_usdt = round(self._daily_loss_usdt, 2)
            logger.info(
                f"风控状态已恢复: 今日 {self._daily_trade_count} 笔, "
                f"亏损 {self._daily_loss_usdt:.2f} USDT, 连亏 {self._consecutive_losses} 次"
            )
        except Exception as e:
            logger.error(f"风控状态恢复失败（按零状态启动）: {e}")

    def _log_trade_sync(self, trade_data: dict):
        """同步写入交易到 SQLite（含 Phase 4 P&L 列 + 手续费 + 信心度）"""
        if not self._db_conn:
            return
        try:
            with self._db.write_lock:
                self._db_conn.execute(
                    "INSERT INTO trades (timestamp, side, size, price, pnl, order_id, symbol, decision, "
                    "pnl_close, trade_group_id, trade_type, fee, confidence, position_size_pct, "
                    "stop_loss, take_profit) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trade_data.get("timestamp", ""),
                        trade_data.get("side", ""),
                        trade_data.get("size", 0),
                        trade_data.get("price", 0),
                        trade_data.get("pnl", 0),
                        trade_data.get("order_id", ""),
                        trade_data.get("symbol", ""),
                        json.dumps(trade_data.get("decision", {})),
                        trade_data.get("pnl_close", 0),
                        trade_data.get("trade_group_id", ""),
                        trade_data.get("trade_type", "open"),
                        trade_data.get("fee", 0.0),
                        trade_data.get("confidence", 0),
                        trade_data.get("position_size_pct", 0.0),
                        trade_data.get("stop_loss", 0),
                        trade_data.get("take_profit", 0),
                    )
                )
                self._db_conn.commit()
        except Exception as e:
            logger.error(f"SQLite 写入失败: {e}")
