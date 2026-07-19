"""LLM 影子决策（D12）测试 — schema / 记录 / 限频 / 异常兜底 / 统计"""
from __future__ import annotations

import json

import pytest

import agents.llm_shadow as ls_mod
from agents.llm_shadow import LLMShadow
from data.db_manager import ensure_sandbox_schema, DatabaseManager


class _SyncThread:
    """把 threading.Thread 替换为同步执行（确定性测试）"""

    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


class _FakeDeepSeek:
    def __init__(self, result=None, exc=None):
        self._result = result if result is not None else {
            "action": "buy", "confidence": 75, "reason": "看多", "_raw": '{"action":"buy"}'
        }
        self._exc = exc
        self.calls = 0

    def analyze(self, context):
        self.calls += 1
        if self._exc:
            raise self._exc
        return dict(self._result)


@pytest.fixture
def shadow(tmp_path, monkeypatch):
    monkeypatch.setattr(ls_mod.threading, "Thread", _SyncThread)
    ds = _FakeDeepSeek()
    s = LLMShadow(ds, str(tmp_path / "sandbox.db"), min_interval_s=300)
    yield s, ds
    s._db.close()


def _ctx():
    return {"current_price": 3000.5, "position_direction": "none"}


def _rule(action="buy", confidence=80):
    return {"action": action, "confidence": confidence, "reason": "规则信号"}


def _rows(shadow):
    cur = shadow._db.conn.execute(
        "SELECT * FROM sandbox_decisions ORDER BY id"
    )
    return [dict(r) for r in cur.fetchall()]


class TestSchema:
    def test_ensure_sandbox_schema_idempotent(self, tmp_path):
        db = DatabaseManager(str(tmp_path / "a.db"))
        ensure_sandbox_schema(db.conn)
        ensure_sandbox_schema(db.conn)  # 第二次不报错
        cols = {r[1] for r in db.conn.execute(
            "PRAGMA table_info(sandbox_decisions)").fetchall()}
        assert {"timestamp", "rule_action", "llm_action", "agree",
                "llm_latency_ms", "llm_error"} <= cols
        db.close()


class TestRecord:
    def test_record_agree(self, shadow):
        s, ds = shadow
        assert s.maybe_record(_ctx(), _rule("buy")) is True
        rows = _rows(s)
        assert len(rows) == 1
        r = rows[0]
        assert r["rule_action"] == "buy"
        assert r["llm_action"] == "buy"
        assert r["agree"] == 1
        assert r["price"] == 3000.5
        assert r["llm_error"] == ""
        assert ds.calls == 1
        # _raw 不进入 llm_decision JSON
        assert "_raw" not in json.loads(r["llm_decision"])
        assert r["llm_raw"].startswith('{"action"')

    def test_record_disagree(self, shadow):
        s, ds = shadow
        s.maybe_record(_ctx(), _rule("sell"))
        rows = _rows(s)
        assert rows[0]["agree"] == 0

    def test_llm_exception_recorded_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls_mod.threading, "Thread", _SyncThread)
        ds = _FakeDeepSeek(exc=ValueError("boom"))
        s = LLMShadow(ds, str(tmp_path / "b.db"))
        try:
            assert s.maybe_record(_ctx(), _rule()) is True
            rows = _rows(s)
            assert len(rows) == 1
            assert rows[0]["llm_action"] == "error"
            assert "boom" in rows[0]["llm_error"]
            assert rows[0]["agree"] == 0
        finally:
            s._db.close()


class TestRateLimit:
    def test_second_call_within_interval_skipped(self, shadow):
        s, ds = shadow
        assert s.maybe_record(_ctx(), _rule()) is True
        assert s.maybe_record(_ctx(), _rule()) is False
        assert ds.calls == 1
        assert s.total_skipped == 1
        assert len(_rows(s)) == 1

    def test_zero_interval_never_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls_mod.threading, "Thread", _SyncThread)
        ds = _FakeDeepSeek()
        s = LLMShadow(ds, str(tmp_path / "c.db"), min_interval_s=0)
        try:
            assert s.maybe_record(_ctx(), _rule()) is True
            assert s.maybe_record(_ctx(), _rule()) is True
            assert ds.calls == 2
        finally:
            s._db.close()


class TestStats:
    def test_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls_mod.threading, "Thread", _SyncThread)
        ds = _FakeDeepSeek()
        s = LLMShadow(ds, str(tmp_path / "d.db"), min_interval_s=0)
        try:
            s.maybe_record(_ctx(), _rule("buy"))   # agree
            s.maybe_record(_ctx(), _rule("sell"))  # disagree
            st = s.stats()
            assert st["total"] == 2
            assert st["agree_rate"] == 50.0
            assert st["llm_errors"] == 0
            assert st["recorded"] == 2
        finally:
            s._db.close()
