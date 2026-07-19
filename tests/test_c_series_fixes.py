"""C 系列修复测试 — 密钥保护 / 时区统一 / 资金费率单位"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

import config as config_mod
from config import Config


# ───────────────────────────── C1: 密钥保护 ─────────────────────────────

class TestSaveSensitiveFields:
    def test_save_preserves_existing_file_keys(self, tmp_path):
        """文件已有 key + 内存被 env 注入新 key → 保存后文件保留原 key"""
        cfg_file = tmp_path / "default.json"
        cfg_file.write_text(json.dumps({
            "exchange": {"api_key": "FILE_KEY", "secret_key": "FILE_SEC",
                         "passphrase": "FILE_PP"},
            "agent": {"api_key": "FILE_DS"},
        }), encoding="utf-8")
        cfg = Config()
        cfg.exchange.api_key = "ENV_KEY"  # 模拟 env 注入
        cfg.exchange.secret_key = "ENV_SEC"
        cfg.exchange.passphrase = "ENV_PP"
        cfg.agent.api_key = "ENV_DS"
        cfg.trading.symbol = "ETH-USDT"
        cfg.save(str(cfg_file))
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved["exchange"]["api_key"] == "FILE_KEY"
        assert saved["exchange"]["secret_key"] == "FILE_SEC"
        assert saved["exchange"]["passphrase"] == "FILE_PP"
        assert saved["agent"]["api_key"] == "FILE_DS"
        assert saved["trading"]["symbol"] == "ETH-USDT"  # 非敏感字段正常保存

    def test_save_never_writes_env_keys_to_empty_file(self, tmp_path):
        """文件原本没有 key → env 注入的 key 绝不落明文"""
        cfg_file = tmp_path / "new.json"
        cfg = Config()
        cfg.exchange.api_key = "ENV_KEY"
        cfg.exchange.secret_key = "ENV_SEC"
        cfg.exchange.passphrase = "ENV_PP"
        cfg.agent.api_key = "ENV_DS"
        cfg.save(str(cfg_file))
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved["exchange"]["api_key"] == ""
        assert saved["exchange"]["secret_key"] == ""
        assert saved["exchange"]["passphrase"] == ""
        assert saved["agent"]["api_key"] == ""


class TestDotenv:
    def test_load_dotenv_sets_and_no_override(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config_mod, "_DOTENV_LOADED", False)
        monkeypatch.delenv("TEST_KEY_A", raising=False)
        monkeypatch.setenv("TEST_KEY_B", "existing")
        env_file = tmp_path / ".env"
        env_file.write_text(
            'TEST_KEY_A="quoted_value"\n# comment\nTEST_KEY_B=plain\n\n',
            encoding="utf-8",
        )
        config_mod._load_dotenv(env_file)
        import os
        assert os.environ["TEST_KEY_A"] == "quoted_value"
        assert os.environ["TEST_KEY_B"] == "existing"  # 已有 env 不被覆盖

    def test_load_dotenv_missing_file_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config_mod, "_DOTENV_LOADED", False)
        config_mod._load_dotenv(tmp_path / "nonexistent.env")  # 不抛异常


class TestServerchanEnv:
    def test_sendkey_from_env(self, monkeypatch):
        monkeypatch.setenv("SERVERCHAN_SENDKEY", "sct_test_key")
        from agents.config import AgentSystemConfig
        assert AgentSystemConfig().serverchan_sendkey == "sct_test_key"


# ───────────────────────────── C2: 时区 ─────────────────────────────

class TestBarTime:
    def test_bar_now_iso_uses_bar_time(self):
        from frontend.utils.backtest_engine import BacktestEngine
        eng = BacktestEngine(rules={})
        ts = pd.Timestamp("2025-06-01 10:00", tz="Asia/Shanghai")
        eng._current_bar_ts = ts
        assert eng._bar_now_iso() == ts.isoformat()

    def test_bar_now_iso_fallback_utc(self):
        from frontend.utils.backtest_engine import BacktestEngine
        eng = BacktestEngine(rules={})
        assert eng._current_bar_ts is None
        assert eng._bar_now_iso().endswith("+00:00")


class TestTickTzNormalize:
    def test_utc_iso_converted_to_shanghai(self):
        from execution.futures_paper import _to_bar_tz
        out = _to_bar_tz("2026-07-18T15:00:00+00:00")
        assert out == "2026-07-18T23:00:00+08:00"

    def test_naive_treated_as_utc(self):
        from execution.futures_paper import _to_bar_tz
        assert _to_bar_tz("2026-07-18T15:00:00") == "2026-07-18T23:00:00+08:00"

    def test_none_and_garbage_passthrough(self):
        from execution.futures_paper import _to_bar_tz
        assert _to_bar_tz(None) is None
        assert _to_bar_tz("") == ""
        assert _to_bar_tz("not-a-timestamp") == "not-a-timestamp"


class TestCstBoundary:
    def test_utc_to_cst_date_midnight_boundary(self):
        from agents.risk_layer import RiskManager
        # UTC 16:00 = CST 次日 00:00
        assert RiskManager._utc_to_cst_date(
            datetime(2025, 1, 1, 16, 0, tzinfo=timezone.utc)) == date(2025, 1, 2)
        assert RiskManager._utc_to_cst_date(
            datetime(2025, 1, 1, 15, 59, tzinfo=timezone.utc)) == date(2025, 1, 1)


# ───────────────────────────── C3: 资金费率单位 ─────────────────────────────

class TestFundingRateThreshold:
    def _collector(self, funding_rate: str):
        from agents.onchain_collector import OnchainCollector
        from agents.config import AgentSystemConfig
        okx = SimpleNamespace(get_funding_rate=lambda: {
            "funding_rate": funding_rate, "next_funding_rate": ""})
        bus = SimpleNamespace(publish_b=AsyncMock())
        return OnchainCollector(okx, AgentSystemConfig(), bus,
                                http_client=MagicMock()), bus

    def test_high_rate_triggers(self):
        """0.0001 (0.01%) 即达阈值 —— 修复前 100× 偏差导致永不触发"""
        coll, bus = self._collector("0.0001")
        asyncio.run(coll._fetch_and_push_funding())
        event = bus.publish_b.call_args[0][0]
        assert event.data["is_high"] is True
        assert event.urgency == "high"
        assert event.data["funding_rate_pct"] == 0.01

    def test_normal_rate_not_high(self):
        coll, bus = self._collector("0.00005")  # 0.005% 正常水平
        asyncio.run(coll._fetch_and_push_funding())
        event = bus.publish_b.call_args[0][0]
        assert event.data["is_high"] is False
        assert event.urgency == "medium"
