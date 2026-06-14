"""
数据层测试
"""

from __future__ import annotations

import pytest
import os
import tempfile
from pathlib import Path

from config import Config, DataConfig
from data.storage import DataStore
from data.quality import DataQualityChecker


@pytest.fixture
def temp_db_config():
    """使用临时数据库的配置"""
    cfg = Config()
    tmpdir = tempfile.mkdtemp()
    cfg.data.db_path = os.path.join(tmpdir, "test_market.db")
    return cfg


def test_store_creates_database(temp_db_config):
    store = DataStore(temp_db_config)
    _ = store.conn  # 触发惰性连接创建
    assert Path(temp_db_config.db_path).exists()
    store.close()


def test_store_insert_klines(temp_db_config):
    store = DataStore(temp_db_config)
    rows = [
        {"timestamp": 1000, "open": 50000, "high": 50100, "low": 49900, "close": 50050, "vol": 100},
        {"timestamp": 2000, "open": 50050, "high": 50200, "low": 50000, "close": 50100, "vol": 150},
    ]
    inserted = store.insert_klines("BTC-USDT", "1h", rows)
    assert inserted == 2
    assert store.count_klines("BTC-USDT", "1h") == 2
    store.close()


def test_store_dedup(temp_db_config):
    """按 symbol+timeframe+timestamp 去重"""
    store = DataStore(temp_db_config)
    rows = [
        {"timestamp": 1000, "open": 50000, "high": 50100, "low": 49900, "close": 50050, "vol": 100},
    ]
    inserted1 = store.insert_klines("BTC-USDT", "1h", rows)
    inserted2 = store.insert_klines("BTC-USDT", "1h", rows)
    assert inserted2 == 0  # 重复，不插入
    assert store.count_klines("BTC-USDT", "1h") == 1
    store.close()


def test_store_load_klines(temp_db_config):
    store = DataStore(temp_db_config)
    rows = [
        {"timestamp": 1000, "open": 50000, "high": 50100, "low": 49900, "close": 50050, "vol": 100},
        {"timestamp": 2000, "open": 50050, "high": 50200, "low": 50000, "close": 50100, "vol": 150},
    ]
    store.insert_klines("BTC-USDT", "1h", rows)
    df = store.load_klines("BTC-USDT", "1h")
    assert len(df) == 2
    assert "close" in df.columns
    assert "volume" in df.columns
    store.close()


def test_store_load_with_date_filter(temp_db_config):
    store = DataStore(temp_db_config)
    rows = [
        {"timestamp": 1000, "open": 50000, "high": 50100, "low": 49900, "close": 50050, "vol": 100},
        {"timestamp": 2000, "open": 50050, "high": 50200, "low": 50000, "close": 50100, "vol": 150},
        {"timestamp": 3000, "open": 50100, "high": 50300, "low": 50050, "close": 50200, "vol": 200},
    ]
    store.insert_klines("BTC-USDT", "1h", rows)
    df = store.load_klines("BTC-USDT", "1h", start_ts=1500)
    assert len(df) == 2  # 时间戳 >= 1500 的有 2 条
    store.close()


def test_wal_mode_enabled(temp_db_config):
    """验证 WAL 模式已启用"""
    store = DataStore(temp_db_config)
    cursor = store.conn.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    assert mode == "wal", f"期望 wal，实际 {mode}"
    store.close()


def test_data_quality_continuity_check(temp_db_config):
    """数据连续性检测"""
    store = DataStore(temp_db_config)

    # 插入连续 K 线
    rows = [
        {"timestamp": ts, "open": 50000, "high": 50100, "low": 49900, "close": 50050, "vol": 100}
        for ts in [1000, 3601000, 7201000]  # 模拟 1h K 线（间隔 3600s = 3600000ms）
    ]
    # 注意：1h = 3600000ms

    for i, r in enumerate(rows):
        r["timestamp"] = 1000 + i * 3600000  # 正确对齐

    store.insert_klines("BTC-USDT", "1h", rows)

    checker = DataQualityChecker(temp_db_config)
    result = checker.check_continuity("BTC-USDT", "1h")
    assert "status" in result
    checker.close()
    store.close()


def test_data_quality_price_check(temp_db_config):
    """异常价格检测"""
    store = DataStore(temp_db_config)
    # 插入正常 + 异常 K 线
    normal_rows = [
        {"timestamp": 1000 + i * 3600000, "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50050.0, "vol": 100.0}
        for i in range(10)
    ]
    # 加一条价格异常
    normal_rows.append(
        {"timestamp": 1000 + 20 * 3600000, "open": 500000.0, "high": 510000.0, "low": 490000.0, "close": 505000.0, "vol": 100.0}
    )
    store.insert_klines("BTC-USDT", "1h", normal_rows)

    checker = DataQualityChecker(temp_db_config)
    result = checker.check_price_anomalies("BTC-USDT", "1h")
    checker.close()
    store.close()
