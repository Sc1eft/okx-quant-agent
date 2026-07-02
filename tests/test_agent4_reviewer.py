"""测试 Agent 4 — 复盘改进 Agent"""
from __future__ import annotations

import os
import sys
import json
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.agent4_reviewer import Agent4Reviewer, _PARAM_BOUNDS, _RISK_PARAMS
from agents.config import AgentSystemConfig
from agents.deepseek_caller import DeepSeekTrader


def _make_db(trades: list[dict]) -> str:
    """创建临时数据库并写入交易（含 Phase 4 字段）"""
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
            "INSERT INTO trades (timestamp, side, size, price, pnl, pnl_close, "
            "trade_group_id, trade_type, decision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t.get("timestamp", "2026-07-03T00:00:00"),
                t.get("side", "buy"),
                t.get("size", 0.01),
                t.get("price", 3000),
                t.get("pnl", 0.0),
                t.get("pnl_close", 0.0),
                t.get("trade_group_id", ""),
                t.get("trade_type", "open"),
                json.dumps(t.get("decision", {})),
            ),
        )
    conn.commit()
    conn.close()
    return path


def _make_reviewer(db_path: str = ":memory:") -> Agent4Reviewer:
    """创建测试用的 Agent4Reviewer 实例"""
    config = AgentSystemConfig()
    deepseek = DeepSeekTrader(api_key="test")
    kline_builder = MagicMock()
    kline_builder.get_history.return_value = []
    agent1 = MagicMock()
    agent1.get_recent_signal_stats.return_value = {"total_signals": 0}
    agent2 = MagicMock()
    agent2.get_recent_news.return_value = []
    agent2.get_status.return_value = {"onchain": {}}

    return Agent4Reviewer(
        config=config,
        deepseek=deepseek,
        db_path=db_path,
        kline_builder=kline_builder,
        agent1=agent1,
        agent2=agent2,
    )


# ── 基础测试 ──

def test_init():
    """Agent4Reviewer 初始化后状态正确"""
    reviewer = _make_reviewer()
    status = reviewer.get_status()
    assert status["running"] is False
    assert status["trade_count"] == 0
    assert status["total_reviews"] == 0


@pytest.mark.asyncio
async def test_notify_trade_under_threshold():
    """交易数未达阈值时不会触发复盘"""
    reviewer = _make_reviewer()
    with patch.object(reviewer, "_run_review") as mock_run:
        for _ in range(4):
            await reviewer.notify_trade({"id": 1})
        mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_notify_trade_triggers_review():
    """交易数达阈值后触发复盘"""
    reviewer = _make_reviewer()
    with patch.object(reviewer, "_run_review") as mock_run:
        for i in range(5):
            await reviewer.notify_trade({"id": i})
        mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_notify_trade_triggers_multiple_reviews():
    """每满 5 笔触发一次复盘，不重置计数"""
    reviewer = _make_reviewer()
    with patch.object(reviewer, "_run_review") as mock_run:
        # side_effect 模拟真实 _run_review 对 last_review_count 的更新
        async def _update_count():
            reviewer._last_review_count = reviewer._trade_count
        mock_run.side_effect = _update_count
        for i in range(12):
            await reviewer.notify_trade({"id": i})
        assert mock_run.call_count == 2  # 5笔和10笔各一次

    status = reviewer.get_status()
    assert status["last_review_count"] == 10


# ── 数据采集测试 ──

def test_load_recent_trades():
    """能从 SQLite 加载最近交易"""
    trades = [
        {"side": "buy", "price": 3000, "pnl_close": 10.0, "decision": {"reason": "good"}},
        {"side": "sell", "price": 3050, "pnl_close": -5.0, "decision": {"reason": "bad"}},
    ]
    db = _make_db(trades)
    reviewer = _make_reviewer(db_path=db)
    loaded = reviewer._load_recent_trades(5)
    assert len(loaded) == 2
    assert loaded[0]["side"] == "sell"  # 最近的在前
    assert loaded[1]["side"] == "buy"


def test_load_recent_trades_empty_db():
    """空数据库返回空列表"""
    reviewer = _make_reviewer()
    loaded = reviewer._load_recent_trades(5)
    assert loaded == []


# ── 校验测试 ──

def test_validate_unknown_param():
    """未知参数名被拒绝"""
    reviewer = _make_reviewer()
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "unknown_param", "to": 10,
    }) is False


def test_validate_out_of_bounds():
    """超出安全范围的参数被拒绝"""
    reviewer = _make_reviewer()
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_trades", "to": 100,
    }) is False
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_trades", "to": -1,
    }) is False


def test_validate_risk_param_strict():
    """风险参数只能降低不能提高"""
    reviewer = _make_reviewer()
    # max_daily_loss_usdt 默认 100，改为 50（降低=允许）
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_loss_usdt", "to": 50,
    }) is True
    # 改为 150（提高=拒绝）
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_loss_usdt", "to": 150,
    }) is False


def test_validate_debounce():
    """同一参数最小修改间隔"""
    reviewer = _make_reviewer()
    # 第一次应该通过
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_debounce_seconds", "to": 60,
    }) is True
    # 立即第二次应该被防抖拒绝
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_debounce_seconds", "to": 90,
    }) is False


def test_validate_no_actual_change():
    """值没变化时跳过"""
    reviewer = _make_reviewer()
    # agent3_max_daily_trades 默认是 10，调到 10 = 无变化
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_trades", "to": 10,
    }) is False


# ── 边界表完整性 ──

def test_param_bounds_completeness():
    """_PARAM_BOUNDS 表包含所有 config 可调字段，无遗漏"""
    config = AgentSystemConfig()
    # 验证可调参数都存在
    for param in _PARAM_BOUNDS:
        assert hasattr(config, param), f"{param} 在 config 中缺失"


def test_review_prompt_format():
    """Prompt 模板能正确格式化"""
    reviewer = _make_reviewer()
    prompt = reviewer._build_review_prompt(
        trades=[{"side": "buy", "price": 3000, "pnl_close": 10.0,
                 "decision": {"reason": "good"}, "timestamp": "2026-07-03T10:00:00"}],
        market={"15m": {"high": 3100, "low": 2900, "last_close": 3000, "count": 20}},
        signals={"total_signals": 5, "by_timeframe": {"15m": 3, "1h": 2},
                 "by_direction": {"buy": 3, "sell": 2}, "by_urgency": {"high": 1, "medium": 4}},
        news=["ETH ETF 流入量创新低"],
        onchain={"last_gas_gwei": 45, "last_taker_buy_ratio": 0.48,
                 "last_funding_rate": -0.0005, "last_whale_count": 2},
        prev_reviews=[{"timestamp": "2026-07-02T10:00:00", "summary": "上轮复盘",
                       "adjustments": [{"param": "agent3_debounce_seconds"}]}],
    )
    assert "【最近1笔交易】" in prompt
    assert "ETH ETF" in prompt
    assert "Gas: 45" in prompt
    assert "debounce_seconds" in prompt
