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
                t.get("decision", "{}"),
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

        # 验证文件存在（新目录结构）
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        report_path = Path(config.report_dir) / "daily" / f"daily_{today}.json"
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
        report_path = Path(config.report_dir) / "weekly" / f"weekly_{today}.json"
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
        """按持仓方向拆分统计（close 的 side 是平仓单方向：sell=平多, buy=平空）"""
        _populate_trades(temp_db, [
            {"side": "buy", "pnl_close": 10, "trade_type": "close"},   # 平空 → short
            {"side": "buy", "pnl_close": -5, "trade_type": "close"},   # 平空 → short
            {"side": "sell", "pnl_close": 15, "trade_type": "close"},  # 平多 → long
        ])
        gen = ReviewGenerator(config, temp_db)
        stats = gen.compute_monthly_stats()
        assert "short" in stats["by_side"]
        assert "long" in stats["by_side"]
        assert stats["by_side"]["short"]["trades"] == 2
        assert stats["by_side"]["short"]["pnl"] == 5.0
        assert stats["by_side"]["long"]["trades"] == 1

    # ── Task 3: 交易报告扩展测试 ──

    def test_extract_wins_and_losses(self, config, temp_db):
        """提取盈亏交易（含持仓方向与开/平仓价还原）"""
        _populate_trades(temp_db, [
            {"pnl_close": 10, "side": "sell", "price": 3600.0,
             "decision": '{"reason": "MACD golden cross"}',
             "trade_type": "close", "trade_group_id": "g1"},
            {"pnl_close": -5, "side": "buy", "price": 3400.0,
             "decision": '{"reason": "Resistance break"}',
             "trade_type": "close", "trade_group_id": "g2"},
            # 关联的开仓行：g1 开多 @3500, g2 开空 @3500
            {"side": "buy", "price": 3500.0, "trade_type": "open", "trade_group_id": "g1"},
            {"side": "sell", "price": 3500.0, "trade_type": "open", "trade_group_id": "g2"},
        ])
        gen = ReviewGenerator(config, temp_db)
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trades WHERE trade_type='close'").fetchall()
        wins, losses = gen.extract_wins_and_losses(rows, conn)
        conn.close()
        assert len(wins) == 1, f"expected 1 win, got {len(wins)}"
        assert len(losses) == 1, f"expected 1 loss, got {len(losses)}"
        assert wins[0]["pnl"] == 10, f"expected pnl 10, got {wins[0]['pnl']}"
        assert losses[0]["pnl"] == -5, f"expected pnl -5, got {losses[0]['pnl']}"
        assert wins[0]["reason"], f"expected non-empty reason, got {repr(wins[0]['reason'])}"
        assert "golden cross" in wins[0]["reason"]
        # 方向与价格还原：sell 平仓 + 开仓 buy → long；entry=开仓价, exit=平仓价
        assert wins[0]["side"] == "long"
        assert wins[0]["entry_price"] == 3500.0
        assert wins[0]["exit_price"] == 3600.0
        assert losses[0]["side"] == "short"
        assert losses[0]["entry_price"] == 3500.0
        assert losses[0]["exit_price"] == 3400.0

    def test_monthly_report_no_trades(self, config, temp_db):
        """无交易时月度报告返回零值"""
        gen = ReviewGenerator(config, temp_db)
        report = gen.generate_monthly_report()
        assert report["type"] == "monthly"
        assert report["stats"]["trades"] == 0
        assert report["pushed"] is False
        assert "trades" in report

    def test_monthly_report_with_trades(self, config, temp_db):
        """月度报告包含交易明细"""
        _populate_trades(temp_db, [
            {"pnl_close": 10, "side": "buy", "decision": json.dumps({"reason": "MACD cross"}), "trade_type": "close"},
            {"pnl_close": -3, "side": "sell", "decision": "{}", "trade_type": "close"},
        ])
        gen = ReviewGenerator(config, temp_db)
        report = gen.generate_monthly_report()
        assert report["stats"]["trades"] >= 1
        assert len(report["trades"]["wins"]) >= 1

    def test_report_writes_to_new_dir(self, config, temp_db):
        """报告写入新目录结构 data/reports/{type}/"""
        _populate_trades(temp_db, [
            {"pnl_close": 10, "trade_type": "close"},
        ])
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        gen = ReviewGenerator(config, temp_db)
        gen.generate_daily_report()
        path = Path(config.report_dir) / "daily" / f"daily_{today}.json"
        assert path.exists()

    def test_ai_analysis_not_called_when_no_deepseek(self, config, temp_db):
        """不传 deepseek 时不调用 AI 分析"""
        _populate_trades(temp_db, [
            {"pnl_close": 10, "trade_type": "close"},
            {"pnl_close": -5, "trade_type": "close"},
        ])
        gen = ReviewGenerator(config, temp_db)  # no deepseek passed
        report = gen.generate_daily_report()
        assert "ai_analysis" not in report  # deepseek=None 时不添加
