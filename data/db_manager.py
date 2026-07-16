"""
数据库连接管理器 — 共享 SQLite 连接，避免各 Agent 各自创建

WAL 模式让读写不互斥；busy_timeout 避免写冲突时的 immediate 报错。
同一 db_path 返回同一连接（模块级缓存），减少连接数。

用法:
    mgr = DatabaseManager("data/agent_trades.db")
    conn = mgr.conn
    conn.execute(...)
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Optional

logger = logging.getLogger("db_manager")


class DatabaseManager:
    """SQLite 连接管理器

    每个 db_path 对应一个共享连接，WAL 模式 + 忙等待超时。
    """

    _instances: dict[str, "DatabaseManager"] = {}
    _lock = threading.Lock()

    def __new__(cls, db_path: str) -> "DatabaseManager":
        # 模块级缓存：相同路径复用同一实例
        with cls._lock:
            if db_path not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[db_path] = instance
            return cls._instances[db_path]

    def __init__(self, db_path: str):
        if self._initialized:
            return
        self._initialized = True
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_lock = threading.Lock()
        self._ref_count = 0
        logger.debug(f"DatabaseManager 创建: {db_path}")

    @property
    def conn(self) -> sqlite3.Connection:
        """获取共享连接（懒加载 + WAL 模式）"""
        if self._conn is None:
            with self._conn_lock:
                if self._conn is None:  # double-check
                    self._conn = self._create_connection()
        return self._conn

    def _create_connection(self) -> sqlite3.Connection:
        """创建连接并启用 WAL 模式"""
        import os
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,  # 跨线程共享
        )
        conn.row_factory = sqlite3.Row

        # WAL 模式：写不阻塞读
        conn.execute("PRAGMA journal_mode=WAL;")
        # 忙等待 5 秒（而不是立即报 sqlite3.BusyError）
        conn.execute("PRAGMA busy_timeout=5000;")
        # 降低同步级别（提升写入速度）
        conn.execute("PRAGMA synchronous=NORMAL;")
        # 缓存 64MB
        conn.execute("PRAGMA cache_size=-65536;")

        logger.info(f"SQLite 连接已创建 (WAL): {self._db_path}")
        return conn

    def close(self):
        """关闭连接并清空缓存"""
        with self._conn_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
        with self._lock:
            self._instances.pop(self._db_path, None)
        logger.info(f"SQLite 连接已关闭: {self._db_path}")

    @property
    def db_path(self) -> str:
        return self._db_path

    def __del__(self):
        # 不用 _instances.pop — __del__ 在解释器退出时可能乱序执行
        pass

    # ── 便捷方法 ──

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()
