"""测试复盘报告生成器 — Phase 4"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.review_generator import ReviewGenerator
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig(
        review_generator_enabled=True,
        review_report_dir=tempfile.mkdtemp(),
        review_report_min_trades=3,
    )


@pytest.fixture
def temp_db():
    """创建临时数据库, 自动清理"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # 确保所有连接已关闭再删除
    import gc
    gc.collect()
    try:
        os.unlink(path)
    except PermissionError:
        pass


def _populate_trades(db_path: str, trades: list[dict]):
    """向测试数据库写入交易记录"""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, side TEXT, size REAL, price REAL,
            pnl REAL, order_id TEXT, symbol TEXT, decision TEXT,
            pnl_close REAL DEFAULT 0,
            trade_group_id TEXT DEFAULT '',
            trade_type TEXT DEFAULT 'open'
        )
    """)
    for t in trades:
        conn.execute(
            "INSERT INTO trades (timestamp, side, size, price, pnl, order_id, symbol, decision, "
            "pnl_close, trade_group_id, trade_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t.get("timestamp", datetime.now(timezone.utc).isoformat()),
                t.get("side", "buy"),
                t.get("size", 0.01),
                t.get("price", 3500.0),
                t.get("pnl", 0),
                t.get("order_id", ""),
                "ETH-USDT",
                "{}",
                t.get("pnl_close", 0),
                t.get("trade_group_id", ""),
                t.get("trade_type", "open"),
            ),
        )
    conn.commit()
    conn.close()


class TestReviewGenerator:

    def test_empty_db(self, config, temp_db):
        """空数据库返回零值"""
        # 使用不存在的文件路径让 ReviewGenerator 创建空表
        gen = ReviewGenerator(config, temp_db)
        stats = gen.compute_monthly_stats()
        assert stats["trades"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["total_pnl"] == 0.0

    def test_win_rate_calculation(self, config, temp_db):
        """胜率计算"""
        _populate_trades(temp_db, [
            {"pnl_close": 10, "trade_type": "close"},
            {"pnl_close": 20, "trade_type": "close"},
            {"pnl_close": -5, "trade_type": "close"},
        ])
        gen = ReviewGenerator(config, temp_db)
        stats = gen.compute_monthly_stats()
        assert stats["trades"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["win_rate"] == pytest.approx(66.7, abs=0.1)  # 2/3
        assert stats["total_pnl"] == 25.0

    def test_max_drawdown(self, config, temp_db):
        """最大回撤计算"""
        now = datetime.now(timezone.utc)
        _populate_trades(temp_db, [
            {"pnl_close": 100, "timestamp": (now - timedelta(days=4)).isoformat(), "trade_type": "close"},
            {"pnl_close": 200, "timestamp": (now - timedelta(days=3)).isoformat(), "trade_type": "close"},
            {"pnl_close": -150, "timestamp": (now - timedelta(days=2)).isoformat(), "trade_type": "close"},
            {"pnl_close": 50, "timestamp": (now - timedelta(days=1)).isoformat(), "trade_type": "close"},
        ])
        gen = ReviewGenerator(config, temp_db)
        stats = gen.compute_monthly_stats()
        # peak = 300 (day3), trough = 150 (day4 after -150)
        # dd = (300-150)/300 * 100 = 50%
        assert stats["max_drawdown_pct"] == pytest.approx(50.0, abs=1.0)
        assert stats["total_pnl"] == 200.0

    def test_daily_report_generates_file(self, config, temp_db):
        """每日报告生成 JSON 文件"""
        _populate_trades(temp_db, [
            {"pnl_close": 10, "trade_type": "close"},
            {"pnl_close": 5, "trade_type": "close"},
            {"pnl_close": -3, "trade_type": "close"},
        ])
        gen = ReviewGenerator(config, temp_db)
        report = gen.generate_daily_report()
        assert report["type"] == "daily"
        assert report["stats"]["trades"] >= 1

        # 验证文件存在
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        report_path = Path(config.review_report_dir) / f"daily_{today}.json"
        assert report_path.exists()
        with open(str(report_path), encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["type"] == "daily"

    def test_weekly_report_generates_file(self, config, temp_db):
        """每周报告生成 JSON 文件"""
        _populate_trades(temp_db, [
            {"pnl_close": 10, "trade_type": "close"},
            {"pnl_close": 5, "trade_type": "close"},
            {"pnl_close": -3, "trade_type": "close"},
        ])
        gen = ReviewGenerator(config, temp_db)
        report = gen.generate_weekly_report()
        assert report["type"] == "weekly"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        report_path = Path(config.review_report_dir) / f"weekly_{today}.json"
        assert report_path.exists()

    def test_report_skipped_below_min_trades(self, config, temp_db):
        """低于最小交易次数时生成摘要说明"""
        config.review_report_min_trades = 10  # 提高阈值
        gen = ReviewGenerator(config, temp_db)
        stats = gen.compute_monthly_stats()
        report = gen._build_report(stats, "daily", "2026-06-24")
        assert "暂不生成总结" in report["summary"]

    def test_fallback_to_pnl(self, config, temp_db):
        """当 pnl_close 全为空时回退到 pnl 字段"""
        _populate_trades(temp_db, [
            {"pnl": 10, "pnl_close": 0, "trade_type": "open"},
            {"pnl": 20, "pnl_close": 0, "trade_type": "open"},
            {"pnl": -5, "pnl_close": 0, "trade_type": "open"},
        ])
        gen = ReviewGenerator(config, temp_db)
        stats = gen.compute_monthly_stats()
        assert stats["trades"] == 3
        assert stats["total_pnl"] == 25.0
        assert stats.get("_fallback") is True

    def test_by_side_breakdown(self, config, temp_db):
        """按方向拆分统计"""
        _populate_trades(temp_db, [
            {"side": "buy", "pnl_close": 10, "trade_type": "close"},
            {"side": "buy", "pnl_close": -5, "trade_type": "close"},
            {"side": "sell", "pnl_close": 15, "trade_type": "close"},
        ])
        gen = ReviewGenerator(config, temp_db)
        stats = gen.compute_monthly_stats()
        assert "buy" in stats["by_side"]
        assert "sell" in stats["by_side"]
        assert stats["by_side"]["buy"]["trades"] == 2
        assert stats["by_side"]["buy"]["pnl"] == 5.0
