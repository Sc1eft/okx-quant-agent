"""
三层风控系统（阶段一基础版）

Layer 1 — 交易前检查:
  - 最小交易间隔（距上次交易 > 5 分钟）
  - 单笔上限 ≤ 0.5 ETH
  - 每日交易次数 ≤ 10
  - 每日亏损上限 ≤ 100 USDT
  - 连续亏损 ≤ 3 次（连亏后仓位减半）
  - 方向冲突（已有同方向仓位时累加不超上限）

Layer 2 — 交易中保护:
  - 限价单优先
  - 滑点 > 0.3% 取消

Layer 3 — 交易后监控:
  - 记录交易到 SQLite
  - 更新风控状态
"""
from __future__ import annotations

import json
import logging
import sqlite3
import os
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Tuple

from agents.config import AgentSystemConfig

logger = logging.getLogger("risk_layer")


class RiskManager:
    """风控管理器 — 三层风控"""

    def __init__(self, config: AgentSystemConfig):
        self.config = config

        # ── Layer 1 状态 ──
        self._last_trade_time: Optional[datetime] = None
        self._daily_trade_count: int = 0
        self._daily_loss_usdt: float = 0.0
        self._consecutive_losses: int = 0
        self._current_date: date = datetime.now(timezone.utc).date()
        self._current_cst_date: date = self._utc_to_cst_date(datetime.now(timezone.utc))
        self._current_position_eth: float = 0.0
        self._current_position_side: Optional[str] = None  # "long" / "short"

        # ── Layer 2 状态 ──
        self._consecutive_api_errors: int = 0
        self._api_breaker_until: Optional[datetime] = None

        # ── Phase 2 状态 ──
        self._btc_delay_until: Optional[datetime] = None

        # ── Layer 3 状态 ──
        self._daily_trades: list[dict] = []

        # ── SQLite 持久化 ──
        self._init_db()

    # ── Layer 1: 交易前检查 ──

    def check_layer1(
        self,
        side: str,  # "buy" / "sell"
        size_eth: float,
        price: float,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """交易前全项检查，返回 (通过?, 原因)"""
        now = now or datetime.now(timezone.utc)
        self._check_date_reset(now)

        # 1. 最小交易间隔
        if self._last_trade_time:
            elapsed = (now - self._last_trade_time).total_seconds()
            if elapsed < self.config.agent3_min_interval_between_trades:
                remaining = self.config.agent3_min_interval_between_trades - int(elapsed)
                return False, f"交易间隔未到，还需 {remaining}s"

        # 2. 单笔上限
        if size_eth > self.config.agent3_max_position_eth:
            return False, f"单笔 {size_eth:.4f} ETH 超过上限 {self.config.agent3_max_position_eth} ETH"

        # 3. 每日交易次数
        if self._daily_trade_count >= self.config.agent3_max_daily_trades:
            return False, f"今日交易已达上限 ({self._daily_trade_count} 次)"

        # 4. 每日亏损上限
        if self._daily_loss_usdt >= self.config.agent3_max_daily_loss_usdt:
            return False, f"今日亏损已达上限 ({self._daily_loss_usdt:.2f} USDT)"

        # 5. 连续亏损
        if self._consecutive_losses >= self.config.agent3_max_consecutive_losses:
            return False, f"连续亏损 {self._consecutive_losses} 次，交易暂停"

        # 6. 方向冲突（同方向累加检查）
        direction = "long" if side == "buy" else "short"
        if self._current_position_side == direction:
            new_total = self._current_position_eth + size_eth
            if new_total > self.config.agent3_max_position_eth:
                return False, f"同方向累加 {new_total:.4f} ETH 超过上限"

        # 7. API 熔断检查
        if self._api_breaker_until and now < self._api_breaker_until:
            remaining = (self._api_breaker_until - now).total_seconds()
            return False, f"API 熔断中，剩余 {remaining:.0f}s"

        # 8. HFT 防护：每小时交易频率上限
        if not self._check_hft(now):
            return False, f"交易频率过高（每小时上限 {self.config.max_trades_per_hour} 笔）"

        return True, ""

    def _check_hft(self, now: datetime) -> bool:
        """检查过去一小时内交易是否超过上限"""
        if not self._db_conn:
            return True  # 无数据库时不拦截
        try:
            hour_ago = (now - timedelta(hours=1)).isoformat()
            cur = self._db_conn.execute(
                "SELECT COUNT(*) FROM trades WHERE timestamp >= ? AND trade_type = 'open'",
                (hour_ago,)
            )
            count = cur.fetchone()[0] or 0
            return count < self.config.max_trades_per_hour
        except Exception as e:
            logger.warning(f"HFT 检查异常，保守拦截: {e}")
            return False  # DB 异常时保守拦截，不放行

    # ── Layer 2: 交易中保护 ──

    def check_layer1_pre(
        self,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """交易前快速预检（方向无关项，在 DeepSeek API 调用前执行）

        检查项:
          1. API 熔断
          2. 每日交易次数上限
          3. 每日亏损上限
          4. 连续亏损
          5. 最小交易间隔
          6. HFT 防护（每小时频率上限）

        返回 (通过?, 原因)
        """
        now = now or datetime.now(timezone.utc)
        self._check_date_reset(now)

        # 1. API 熔断
        if self._api_breaker_until and now < self._api_breaker_until:
            remaining = (self._api_breaker_until - now).total_seconds()
            return False, f"API 熔断中，剩余 {remaining:.0f}s"

        # 2. 每日交易次数
        if self._daily_trade_count >= self.config.agent3_max_daily_trades:
            return False, f"今日交易已达上限 ({self._daily_trade_count} 次)"

        # 3. 每日亏损上限
        if self._daily_loss_usdt >= self.config.agent3_max_daily_loss_usdt:
            return False, f"今日亏损已达上限 ({self._daily_loss_usdt:.2f} USDT)"

        # 4. 连续亏损
        if self._consecutive_losses >= self.config.agent3_max_consecutive_losses:
            return False, f"连续亏损 {self._consecutive_losses} 次，交易暂停"

        # 5. 最小交易间隔
        if self._last_trade_time:
            elapsed = (now - self._last_trade_time).total_seconds()
            if elapsed < self.config.agent3_min_interval_between_trades:
                remaining = self.config.agent3_min_interval_between_trades - int(elapsed)
                return False, f"交易间隔未到，还需 {remaining}s"

        # 6. HFT 防护
        if not self._check_hft(now):
            return False, f"交易频率过高（每小时上限 {self.config.max_trades_per_hour} 笔）"

        return True, ""

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

        # 更新仓位信息
        side = trade_data.get("side", "")
        size = trade_data.get("size", 0)
        if side == "buy":
            self._current_position_side = "long"
            self._current_position_eth += size
        elif side == "sell":
            if trade_data.get("short"):
                self._current_position_side = "short"
            else:
                # closing a long / reducing long position
                self._current_position_eth = max(0, self._current_position_eth - size)
                if self._current_position_eth <= 0:
                    self._current_position_side = None

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

    def _update_pnl_close(self, trade_data: dict):
        """平仓时更新对应开仓记录的 pnl_close"""
        if not self._db_conn:
            return
        try:
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
        cst_dt = utc_dt + timedelta(hours=8)
        return cst_dt.date()

    def _check_date_reset(self, now: datetime):
        """每日重置（北京时间午夜 00:00 CST = UTC 16:00）"""
        cst_today = self._utc_to_cst_date(now)
        if cst_today != self._current_cst_date:
            logger.info(f"每日风控重置 (CST): {self._current_cst_date} → {cst_today}")
            self._daily_trade_count = 0
            self._daily_loss_usdt = 0.0
            self._consecutive_losses = 0
            self._current_cst_date = cst_today
            self._current_date = now.date()
            self._daily_trades = []
            self._consecutive_api_errors = 0
            self._api_breaker_until = None

    def get_status(self) -> dict:
        """返回风控状态摘要"""
        return {
            "daily_trade_count": self._daily_trade_count,
            "max_daily_trades": self.config.agent3_max_daily_trades,
            "daily_loss_usdt": round(self._daily_loss_usdt, 2),
            "max_daily_loss_usdt": self.config.agent3_max_daily_loss_usdt,
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive_losses": self.config.agent3_max_consecutive_losses,
            "position_size_multiplier": self.get_position_size_multiplier(),
            "position_eth": round(self._current_position_eth, 6),
            "position_side": self._current_position_side,
            "max_trades_per_hour": self.config.max_trades_per_hour,
        }

    # ── Phase 2: BTC 波动检查 ──

    async def check_btc_volatility_async(self, okx_client) -> tuple[bool, str]:
        """检查 BTC 15m 波动率，超阈值则拒绝交易

        Args:
            okx_client: OKXClient 实例（用于获取 BTC K线）

        Returns:
            (通过?, 原因)
        """
        # 先检查是否在延迟期内
        now = datetime.now(timezone.utc)
        if hasattr(self, '_btc_delay_until') and self._btc_delay_until and now < self._btc_delay_until:
            remaining = (self._btc_delay_until - now).total_seconds()
            return False, f"BTC 波动延迟中，剩余 {remaining:.0f}s"

        # 获取最后两根 BTC 15m K线
        try:
            import asyncio
            klines = await asyncio.to_thread(
                okx_client.get_klines, "BTC-USDT", "15m", 2
            )
        except Exception as e:
            logger.warning(f"BTC 波动检查失败（API 异常）: {e}")
            return True, ""  # API 异常不阻塞交易

        if len(klines) < 2:
            return True, ""

        prev_close = klines[0]["close"] if isinstance(klines[0], dict) else float(klines[0][4])
        curr_close = klines[1]["close"] if isinstance(klines[1], dict) else float(klines[1][4])

        if prev_close <= 0:
            return True, ""

        change_pct = abs(curr_close - prev_close) / prev_close * 100
        if change_pct > self.config.btc_volatility_threshold_pct:
            self._btc_delay_until = now + timedelta(seconds=self.config.btc_volatility_delay_seconds)
            logger.warning(
                f"BTC 15m 波动 {change_pct:.1f}% > {self.config.btc_volatility_threshold_pct}%"
                f"，延迟 {self.config.btc_volatility_delay_seconds}s"
            )
            return False, f"BTC 15m 波动 {change_pct:.1f}%，超过阈值 {self.config.btc_volatility_threshold_pct}%"

        # 波动恢复正常 → 清除延迟
        self._btc_delay_until = None
        return True, ""

    # ── Phase 2: 市场深度检查 ──

    async def check_market_depth_async(
        self,
        okx_client,
        side: str,       # "buy" / "sell"
        size_eth: float,  # 交易数量（ETH）
    ) -> tuple[bool, str, bool]:
        """检查市场深度是否足够

        Args:
            okx_client: OKXClient 实例
            side: 交易方向
            size_eth: 交易数量（ETH）

        Returns:
            (检查通过?, 消息, 是否强制限价单)
        """
        try:
            import asyncio
            order_book = await asyncio.to_thread(
                okx_client.get_order_book, self.config.ws_symbol, depth=5
            )
        except Exception as e:
            logger.warning(f"市场深度检查失败: {e}")
            return True, "深度检查跳过", True  # 失败则保守地走限价单

        asks = order_book.get("asks", [])
        bids = order_book.get("bids", [])

        if not asks or not bids:
            return True, "深度数据为空", True

        # 计算买卖价差（基点）
        best_ask = float(asks[0][0])
        best_bid = float(bids[0][0])
        mid_price = (best_ask + best_bid) / 2

        if mid_price <= 0:
            return True, "", True

        spread_bps = (best_ask - best_bid) / mid_price * 10000

        # 检查深度是否足够完成交易
        if side == "buy":
            available_depth = sum(float(ask[1]) for ask in asks if float(ask[0]) <= best_ask * 1.005)
        else:
            available_depth = sum(float(bid[1]) for bid in bids if float(bid[0]) >= best_bid * 0.995)

        if available_depth < size_eth:
            return False, (
                f"卖方深度不足: 可用 {available_depth:.4f} ETH < 需求 {size_eth} ETH"
            ), True

        # 价差过大 → 强制走限价单
        if spread_bps > self.config.market_depth_spread_bps:
            return True, f"价差 {spread_bps:.1f}bps > {self.config.market_depth_spread_bps}bps，走限价单", True

        return True, "", False  # 深度充足，可以市价单

    # ── SQLite 持久化 ──

    def _init_db(self):
        """初始化 SQLite 数据库和表（含 Phase 4 迁移）"""
        db_path = self.config.db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        try:
            self._db_conn = sqlite3.connect(db_path, check_same_thread=False)
            self._db_conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    side TEXT,
                    size REAL,
                    price REAL,
                    pnl REAL,
                    order_id TEXT,
                    symbol TEXT,
                    decision TEXT
                )
            """)
            # Phase 4 迁移: 新增 P&L 跟踪列 + 手续费列
            for col, col_def in [
                ("pnl_close", "REAL DEFAULT 0"),
                ("trade_group_id", "TEXT DEFAULT ''"),
                ("trade_type", "TEXT DEFAULT 'open'"),
                ("fee", "REAL DEFAULT 0.0"),
                ("confidence", "INTEGER DEFAULT 0"),
                ("position_size_pct", "REAL DEFAULT 0.0"),
            ]:
                try:
                    self._db_conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_def}")
                except sqlite3.OperationalError:
                    pass  # 列已存在
            self._db_conn.commit()
        except Exception as e:
            logger.error(f"SQLite 初始化失败: {e}")
            self._db_conn = None

    def _log_trade_sync(self, trade_data: dict):
        """同步写入交易到 SQLite（含 Phase 4 P&L 列 + 手续费 + 信心度）"""
        if not self._db_conn:
            return
        try:
            self._db_conn.execute(
                "INSERT INTO trades (timestamp, side, size, price, pnl, order_id, symbol, decision, "
                "pnl_close, trade_group_id, trade_type, fee, confidence, position_size_pct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                )
            )
            self._db_conn.commit()
        except Exception as e:
            logger.error(f"SQLite 写入失败: {e}")
