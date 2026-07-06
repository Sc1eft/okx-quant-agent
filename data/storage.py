"""
数据存储层 — SQLite
🔧 优化：WAL 模式 + 写入性能调优
"""

from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from config import Config

logger = logging.getLogger("data.storage")


class DataStore:
    """
    SQLite 数据存储
    WAL 模式确保读写不互斥，回测与数据采集可同时进行
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db_path = Path(cfg.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._create_connection()
        return self._conn

    def _create_connection(self) -> sqlite3.Connection:
        """创建连接并应用 WAL 模式"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        # ── WAL 模式优化（P1） ──
        # WAL = Write-Ahead Logging，写操作不阻塞读操作
        # 回测读取大量数据的同时，数据采集可以继续写入
        conn.execute("PRAGMA journal_mode=WAL;")
        # 降低同步级别提升写入速度（牺牲崩溃安全换性能）
        conn.execute("PRAGMA synchronous=NORMAL;")
        # 忙等待 5 秒而不是立即报错
        conn.execute("PRAGMA busy_timeout=5000;")
        # 缓存大小提升到 64MB
        conn.execute("PRAGMA cache_size=-65536;")
        # 启用内存映射（大查询提速）
        conn.execute("PRAGMA mmap_size=268435456;")  # 256MB
        # 临时表放内存
        conn.execute("PRAGMA temp_store=MEMORY;")

        self._init_tables(conn)
        return conn

    def _init_tables(self, conn: sqlite3.Connection):
        """初始化表结构"""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS klines (
                symbol      TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                timestamp   INTEGER NOT NULL,
                open        REAL NOT NULL,
                high        REAL NOT NULL,
                low         REAL NOT NULL,
                close       REAL NOT NULL,
                volume      REAL NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (symbol, timeframe, timestamp)
            );

            CREATE INDEX IF NOT EXISTS idx_klines_lookup
                ON klines(symbol, timeframe, timestamp);

            CREATE TABLE IF NOT EXISTS tickers (
                symbol      TEXT NOT NULL,
                timestamp   INTEGER NOT NULL,
                last        REAL NOT NULL,
                bid         REAL,
                ask         REAL,
                volume_24h  REAL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (symbol, timestamp)
            );

            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                side        TEXT NOT NULL,
                price       REAL NOT NULL,
                size        REAL NOT NULL,
                fee         REAL,
                fee_ccy     TEXT,
                timestamp   INTEGER NOT NULL,
                strategy    TEXT,
                mode        TEXT NOT NULL DEFAULT 'backtest',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS data_quality_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                timeframe   TEXT,
                check_type  TEXT NOT NULL,
                status      TEXT NOT NULL,
                detail      TEXT,
                checked_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        conn.commit()

    # ── K 线写入 ──

    def insert_klines(self, symbol: str, timeframe: str, rows: list[dict]) -> int:
        """
        批量插入 K 线（按 symbol+timeframe+timestamp 去重）
        返回新增行数
        """
        if not rows:
            return 0
        sql = """
            INSERT OR IGNORE INTO klines (symbol, timeframe, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        data = [
            (symbol, timeframe, r["timestamp"], r["open"], r["high"], r["low"], r["close"], r.get("vol", 0))
            for r in rows
        ]
        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany(sql, data)
        inserted = self.conn.total_changes - before
        logger.debug(f"写入 {len(rows)} 行 K 线 ({symbol} {timeframe})，新增 {inserted}")
        return inserted

    # ── K 线读取 ──

    def load_klines(
        self,
        symbol: str,
        timeframe: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
        descending: bool = False,
    ) -> pd.DataFrame:
        """加载 K 线为 DataFrame（索引为 datetime）

        Args:
            symbol: 交易对
            timeframe: 周期
            start_ts: 起始时间戳（毫秒）
            end_ts: 结束时间戳（毫秒）
            limit: 最大返回条数
            descending: 是否按时间戳降序取最新 N 条（默认 False = ASC）
        """
        conditions = ["symbol = ?", "timeframe = ?"]
        params = [symbol, timeframe]

        if start_ts:
            conditions.append("timestamp >= ?")
            params.append(start_ts)
        if end_ts:
            conditions.append("timestamp <= ?")
            params.append(end_ts)

        order = "DESC" if descending else "ASC"
        sql = f"SELECT * FROM klines WHERE {' AND '.join(conditions)} ORDER BY timestamp {order}"
        if limit:
            sql += f" LIMIT {limit}"

        df = pd.read_sql_query(sql, self.conn, params=params)
        if df.empty:
            return df

        # DESC → 翻转为升序（调用方期望正序）
        if descending:
            df = df.iloc[::-1]

        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        return df

    def count_klines(self, symbol: str, timeframe: str) -> int:
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM klines WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        )
        return cursor.fetchone()[0]

    def get_date_range(self, symbol: str, timeframe: str) -> tuple:
        cursor = self.conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM klines WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        )
        row = cursor.fetchone()
        return (row[0], row[1]) if row[0] else (None, None)

    # ── 日志/质量记录 ──

    def log_quality_check(self, symbol: str, timeframe: str | None, check_type: str, status: str, detail: str = ""):
        self.conn.execute(
            "INSERT INTO data_quality_log (symbol, timeframe, check_type, status, detail) VALUES (?, ?, ?, ?, ?)",
            (symbol, timeframe, check_type, status, detail),
        )
        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
