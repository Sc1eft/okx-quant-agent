"""测试参数自适应 — Phase 4"""
from __future__ import annotations

import os
import sys
import tempfile
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.param_adapter import ParamAdapter
from agents.config import AgentSystemConfig


def _make_db(trades: list[dict]) -> str:
    """创建临时数据库并写入交易"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE trades (
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
            "INSERT INTO trades (pnl_close) VALUES (?)",
            (t["pnl_close"],),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def config():
    return AgentSystemConfig(
        param_adapter_enabled=True,
        param_adapter_min_trades_for_adjust=3,
        param_adapter_adjust_interval_hours=24,
        param_adapter_max_trades_range=[5, 20],
        param_adapter_win_rate_target=0.50,
        agent3_max_daily_trades=10,
        agent3_debounce_seconds=30.0,
        agent3_min_interval_between_trades=300,
        agent3_max_consecutive_losses=3,
    )


class TestParamAdapter:

    def test_no_adjustment_below_min_trades(self, config):
        """数据不足时不调整"""
        db_path = _make_db([{"pnl_close": 10}])  # only 1 trade
        try:
            adapter = ParamAdapter(config, db_path)
            result = adapter.adjust(now=datetime.now(timezone.utc))
            assert result["adjusted"] is False
            assert "数据不足" in result["reason"]
        finally:
            os.unlink(db_path)

    def test_adjust_on_high_win_rate(self, config):
        """高胜率 → 增加日交易次数, 缩短间隔"""
        db_path = _make_db([
            {"pnl_close": 10}, {"pnl_close": 15}, {"pnl_close": 20},
            {"pnl_close": 8}, {"pnl_close": -5},
        ])  # 4/5 = 80% win rate
        try:
            adapter = ParamAdapter(config, db_path)
            assert config.agent3_max_daily_trades == 10
            result = adapter.adjust(now=datetime.now(timezone.utc))
            assert result["adjusted"] is True
            assert config.agent3_max_daily_trades == 12  # +2
            assert config.agent3_debounce_seconds == 25.0  # -5s
        finally:
            os.unlink(db_path)

    def test_adjust_on_low_win_rate(self, config):
        """低胜率 → 减少日交易次数, 延长间隔"""
        db_path = _make_db([
            {"pnl_close": -10}, {"pnl_close": -15}, {"pnl_close": 20},
            {"pnl_close": -8}, {"pnl_close": -5},
        ])  # 1/5 = 20% win rate
        try:
            adapter = ParamAdapter(config, db_path)
            assert config.agent3_max_daily_trades == 10
            result = adapter.adjust(now=datetime.now(timezone.utc))
            assert result["adjusted"] is True
            assert config.agent3_max_daily_trades == 8  # -2
            assert config.agent3_debounce_seconds == 40.0  # +10s
        finally:
            os.unlink(db_path)

    def test_bounds_enforced(self, config):
        """调整不超过安全边界"""
        config.agent3_max_daily_trades = 5  # 已经是最小值
        db_path = _make_db([
            {"pnl_close": -10}, {"pnl_close": -15}, {"pnl_close": -20},
            {"pnl_close": -8}, {"pnl_close": -5},
        ])  # 0/5 = 0% win rate
        try:
            adapter = ParamAdapter(config, db_path)
            result = adapter.adjust(now=datetime.now(timezone.utc))
            assert result["adjusted"] is True
            # 不应低于 5
            assert config.agent3_max_daily_trades >= 5
        finally:
            os.unlink(db_path)

    def test_should_adjust_timing(self, config):
        """调整间隔检查"""
        db_path = _make_db([{"pnl_close": 10}, {"pnl_close": 15}, {"pnl_close": 20}])
        try:
            adapter = ParamAdapter(config, db_path)
            now = datetime.now(timezone.utc)

            # 第一次: 应该调整
            assert adapter.should_adjust(now) is True

            # 调整
            adapter.adjust(now=now)
            assert adapter._last_adjust_time is not None

            # 立即再检查: 不应调整
            assert adapter.should_adjust(now) is False

            # 24小时后: 应调整
            later = now + timedelta(hours=25)
            assert adapter.should_adjust(later) is True
        finally:
            os.unlink(db_path)

    def test_get_recent_win_rate_empty(self, config):
        """空数据库返回 None"""
        db_path = _make_db([])
        try:
            adapter = ParamAdapter(config, db_path)
            conn = sqlite3.connect(db_path)
            try:
                rate = adapter._get_recent_win_rate(conn)
                assert rate is None
            finally:
                conn.close()
        finally:
            os.unlink(db_path)

    def test_adjustment_log_append(self, config):
        """调整记录被保存到日志"""
        db_path = _make_db([
            {"pnl_close": 10}, {"pnl_close": 15}, {"pnl_close": 20},
            {"pnl_close": 8}, {"pnl_close": -5},
        ])
        try:
            adapter = ParamAdapter(config, db_path)

            # 第一次调整: 应执行并记录
            result = adapter.adjust(now=datetime.now(timezone.utc))
            assert result["adjusted"] is True
            assert len(adapter._adjustment_log) == 1

            # 第二次 (不同时间, 但间隔未到): 不执行
            result = adapter.adjust(now=datetime.now(timezone.utc) + timedelta(hours=1))
            assert result["adjusted"] is False
            # 间隔未到不记录到日志
            assert len(adapter._adjustment_log) == 1
        finally:
            os.unlink(db_path)
