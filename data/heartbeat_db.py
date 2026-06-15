"""
ETH 心跳数据存储 — SQLite WAL 模式

设计要点:
- WAL 模式：后台采集器持续写入，前端同时读取，互不阻塞
- 每 tick 同时写 status.json（供前端秒级读取，避免高频查 DB）
- 自动清理 24h 前的旧数据
- 原子写入 status.json，防止前端读到不完整的文件
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("heartbeat.db")

# ── 默认路径（都在 data/ 目录下） ──
DB_DIR = Path(__file__).resolve().parent
DB_PATH = DB_DIR / "eth_heartbeat.db"
STATUS_PATH = DB_DIR / "eth_heartbeat_status.json"
PID_PATH = DB_DIR / "eth_heartbeat.pid"


class HeartbeatDB:
    """心跳 tick 数据库 — 高频写入优化"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    # ── 连接管理 ──

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA cache_size=-16384;")  # 16MB
        self._init_tables(conn)
        return conn

    @staticmethod
    def _init_tables(conn: sqlite3.Connection):
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ticks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,  -- ISO 8601
                ts_ms       INTEGER NOT NULL,  -- Unix ms
                price       REAL    NOT NULL,
                bid         REAL,
                ask         REAL,
                volume_24h  REAL,
                high_24h    REAL,
                low_24h     REAL,
                change_24h  REAL
            );

            CREATE INDEX IF NOT EXISTS idx_ticks_ts
                ON ticks(ts_ms DESC);

            -- 采集器 session 记录（每条 = 一次启动到停止）
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT NOT NULL,
                stopped_at  TEXT,
                tick_count  INTEGER DEFAULT 0
            );
        """)
        conn.commit()

    # ── 写入 ──

    def insert_tick(
        self,
        ts: str,
        ts_ms: int,
        price: float,
        bid: float | None = None,
        ask: float | None = None,
        volume_24h: float | None = None,
        high_24h: float | None = None,
        low_24h: float | None = None,
        change_24h: float | None = None,
    ):
        """写入一条 tick + 更新 status.json（原子写入）。"""
        self._insert_tick_only(ts, ts_ms, price, bid, ask, volume_24h, high_24h, low_24h, change_24h)

        # 原子写入 status.json
        status = {
            "last_tick_ts": ts,
            "last_price": price,
            "last_bid": bid,
            "last_ask": ask,
            "volume_24h": volume_24h,
            "change_24h": change_24h,
            "connected": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json_write(STATUS_PATH, status)

    def _insert_tick_only(
        self,
        ts: str,
        ts_ms: int,
        price: float,
        bid: float | None = None,
        ask: float | None = None,
        volume_24h: float | None = None,
        high_24h: float | None = None,
        low_24h: float | None = None,
        change_24h: float | None = None,
    ):
        """仅写入 DB，不写 status.json（给采集器内部用，monitor 线程统一写 status）。"""
        self.conn.execute(
            """INSERT INTO ticks (ts, ts_ms, price, bid, ask,
                                  volume_24h, high_24h, low_24h, change_24h)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, ts_ms, price, bid, ask,
             volume_24h, high_24h, low_24h, change_24h),
        )
        self.conn.commit()

    # ── 读取 ──

    def get_recent_ticks(self, limit: int = 100) -> list[dict]:
        """获取最近的 N 条 tick（按时间倒序）。"""
        cursor = self.conn.execute(
            "SELECT * FROM ticks ORDER BY ts_ms DESC LIMIT ?", (limit,),
        )
        return [dict(zip(row.keys(), row)) for row in cursor.fetchall()]

    def get_ticks_since(self, since_ms: int, limit: int = 5000) -> list[dict]:
        """获取某个时间戳以来的所有 tick（正序）。"""
        cursor = self.conn.execute(
            "SELECT * FROM ticks WHERE ts_ms > ? ORDER BY ts_ms ASC LIMIT ?",
            (since_ms, limit),
        )
        return [dict(zip(row.keys(), row)) for row in cursor.fetchall()]

    def count_ticks(self) -> int:
        cursor = self.conn.execute("SELECT COUNT(*) FROM ticks")
        return cursor.fetchone()[0]

    def get_price_range(self, since_ms: int) -> tuple[float, float]:
        """获取某时间以来的最低/最高价。"""
        cursor = self.conn.execute(
            "SELECT MIN(price), MAX(price) FROM ticks WHERE ts_ms > ?",
            (since_ms,),
        )
        row = cursor.fetchone()
        return (row[0] or 0.0, row[1] or 0.0)  # type:ignore

    def get_second_candles(self, limit: int = 100) -> "pd.DataFrame":
        """Aggregate ticks into 1-second OHLCV candles.

        将 tick 数据按秒分组，生成 open/high/low/close/volume（volume = tick 计数）。
        需要 pandas，延迟导入以避免顶层依赖。

        Returns:
            DataFrame with columns [open, high, low, close, volume], index=datetime
        """
        import pandas as pd

        cursor = self.conn.execute(
            """SELECT ts_ms, price
               FROM ticks
               ORDER BY ts_ms ASC
               LIMIT ?""",
            (limit * 10,),  # ~6 ticks/s × 10 = generous buffer
        )
        rows = [dict(zip(row.keys(), row)) for row in cursor.fetchall()]
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        # ts_ms 是 UTC 毫秒时间戳 → 转为 Asia/Shanghai 时区
        df["datetime"] = (
            pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
            .tz_convert("Asia/Shanghai")
        )
        df = df.set_index("datetime")

        # Resample to 1-second OHLC + tick count as volume
        ohlc = df["price"].resample("1s").ohlc()
        vol = df["price"].resample("1s").count()
        result = ohlc.copy()
        result["volume"] = vol
        result = result.dropna(subset=["open"])
        result.index.name = "timestamp"
        return result.tail(limit)

    # ── 维护 ──

    def cleanup_old(self, max_age_hours: int = 24):
        """清理超过 max_age_hours 的旧数据。"""
        cutoff_ms = int(datetime.now().timestamp() * 1000) - max_age_hours * 3600 * 1000
        deleted = self.conn.execute(
            "DELETE FROM ticks WHERE ts_ms < ?", (cutoff_ms,),
        ).rowcount
        if deleted:
            logger.info(f"清理了 {deleted} 条过期 tick")
        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ── 工具 ──

def _atomic_json_write(path: Path, data: dict):
    """原子写入 JSON：先写 .tmp 再 replace，防止读到一半的文件。"""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(path)


def read_status() -> dict | None:
    """读取当前采集器状态（前端用）。"""
    if not STATUS_PATH.exists():
        return None
    try:
        with open(STATUS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def is_collector_running() -> bool:
    """检查 PID 文件以判断采集器是否在运行。"""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
        # Windows: 用 tasklist 检查进程是否存在
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def start_collector() -> bool:
    """启动后台采集器进程。"""
    if is_collector_running():
        logger.info("采集器已在运行")
        return True

    script = Path(__file__).resolve().parent / "eth_heartbeat.py"
    try:
        import subprocess, sys
        subprocess.Popen(
            [sys.executable, str(script)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("已启动 ETH 心跳采集器")
        return True
    except Exception as e:
        logger.error(f"启动采集器失败: {e}")
        return False


def stop_collector() -> bool:
    """停止后台采集器。"""
    if not PID_PATH.exists():
        logger.info("没有运行中的采集器")
        return True

    try:
        import subprocess
        pid = int(PID_PATH.read_text().strip())
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=5,
        )
        PID_PATH.unlink(missing_ok=True)
        logger.info(f"已停止采集器 (PID {pid})")
        return True
    except Exception as e:
        logger.error(f"停止采集器失败: {e}")
        return False
