"""模拟盘无头 runner 测试 — 配置/状态文件协议 + 引擎工厂 + 辅助函数"""
from __future__ import annotations

import pandas as pd
import pytest

import execution.paper_runner as pr
from config import Config


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    """把 runner 的配置/状态/PID 文件重定向到临时目录"""
    cfg_p = tmp_path / "paper_runner_config.json"
    state_p = tmp_path / "paper_runner_state.json"
    pid_p = tmp_path / "paper_runner.pid"
    monkeypatch.setattr(pr, "CONFIG_PATH", cfg_p)
    monkeypatch.setattr(pr, "STATE_PATH", state_p)
    monkeypatch.setattr(pr, "PID_PATH", pid_p)
    return cfg_p, state_p, pid_p


class TestConfigStateFiles:
    def test_write_read_config(self, tmp_paths):
        pr.write_config({"mode": "futures", "strategy": "macd_agent", "leverage": 10})
        cfg = pr.read_config()
        assert cfg["mode"] == "futures"
        assert cfg["leverage"] == 10

    def test_read_config_missing(self, tmp_paths):
        assert pr.read_config() is None

    def test_read_config_corrupt(self, tmp_paths):
        tmp_paths[0].write_text("{bad json", encoding="utf-8")
        assert pr.read_config() is None

    def test_clear_config(self, tmp_paths):
        pr.write_config({"mode": "spot"})
        pr.clear_config()
        assert pr.read_config() is None

    def test_read_state_missing(self, tmp_paths):
        assert pr.read_state() is None

    def test_state_roundtrip(self, tmp_paths):
        pr._write_state(phase="running", bars_processed=100,
                        paper_state={"signal": "hold"}, config={"mode": "spot"})
        st = pr.read_state()
        assert st["phase"] == "running"
        assert st["bars_processed"] == 100
        assert st["paper_state"]["signal"] == "hold"
        assert "updated_at" in st

    def test_state_atomic_write_no_tmp_left(self, tmp_paths):
        pr._write_state(phase="running")
        assert not tmp_paths[1].with_suffix(".tmp").exists()


class TestHelpers:
    def test_poll_interval(self):
        assert pr._poll_interval_s("15m") == 5
        assert pr._poll_interval_s("1h") == 10
        assert pr._poll_interval_s("4h") == 30
        assert pr._poll_interval_s("unknown") == 10

    def test_is_runner_running_no_pid(self, tmp_paths):
        assert pr.is_runner_running() is False

    def test_make_tick_exit_state(self):
        from execution.futures_paper import FuturesPaperEngine
        engine = FuturesPaperEngine(Config(), wallet_balance=10000, leverage=10)
        trade = {"side": "close_long", "price": 3000, "pnl": -50, "reason": "stop_loss"}
        st = pr._make_tick_exit_state(3000.0, "2025-01-01T00:00:00", trade, engine)
        assert st["signal"] == "tick_exit"
        assert st["liquidation"] is None
        assert st["trade"] is trade
        assert "account" in st

    def test_make_tick_exit_state_liquidation(self):
        from execution.futures_paper import FuturesPaperEngine
        engine = FuturesPaperEngine(Config(), wallet_balance=10000, leverage=10)
        liq = {"side": "liquidation", "price": 2700}
        st = pr._make_tick_exit_state(2700.0, None, liq, engine)
        assert st["liquidation"] is liq


class TestMaybeTickExit:
    def _engine(self):
        from execution.futures_paper import FuturesPaperEngine
        return FuturesPaperEngine(Config(), wallet_balance=10000, leverage=10)

    def test_triggered_uses_last_tick_at(self, monkeypatch):
        """触发时 ts 必须取自状态文件的 last_tick_at 字段（防字段名回归）"""
        engine = self._engine()
        called = {}

        def fake_check(price, ts=None, risk_engine=None):
            called["price"] = price
            called["ts"] = ts
            return {"side": "close_long", "price": price, "pnl": -10, "reason": "stop_loss"}

        monkeypatch.setattr(engine, "check_tick_exit", fake_check)
        monkeypatch.setattr(pr, "_read_heartbeat", lambda: {
            "last_price": 2900.5, "last_tick_at": "2026-07-18T15:00:00+00:00"})

        result = pr._maybe_tick_exit(engine, None)
        assert result is not None
        state, price = result
        assert price == 2900.5
        assert state["signal"] == "tick_exit"
        assert called["price"] == 2900.5
        assert called["ts"] == "2026-07-18T15:00:00+00:00"

    def test_no_heartbeat(self, monkeypatch):
        monkeypatch.setattr(pr, "_read_heartbeat", lambda: None)
        assert pr._maybe_tick_exit(object(), None) is None

    def test_no_price(self, monkeypatch):
        monkeypatch.setattr(pr, "_read_heartbeat", lambda: {"last_price": None})
        assert pr._maybe_tick_exit(object(), None) is None

    def test_no_trigger(self, monkeypatch):
        engine = self._engine()
        monkeypatch.setattr(engine, "check_tick_exit", lambda *a, **k: None)
        monkeypatch.setattr(pr, "_read_heartbeat",
                            lambda: {"last_price": 3000.0, "last_tick_at": "t"})
        assert pr._maybe_tick_exit(engine, None) is None


class TestBuildEngine:
    def test_spot_engine(self):
        from execution.paper import PaperEngine
        engine = pr._build_engine(
            {"mode": "spot", "wallet_balance": 5000, "position_size_pct": 0.2},
            Config(),
        )
        assert isinstance(engine, PaperEngine)

    def test_futures_engine_with_funding(self, monkeypatch):
        from execution.futures_paper import FuturesPaperEngine
        monkeypatch.setattr(pr, "_fetch_funding_rate", lambda _cfg: 0.0001)
        engine = pr._build_engine(
            {"mode": "futures", "wallet_balance": 5000, "leverage": 5,
             "position_size_pct": 0.2, "exit_params": {"stop_loss_pct": 3.0}},
            Config(),
        )
        assert isinstance(engine, FuturesPaperEngine)
        assert engine.leverage == 5
        assert engine._funding_rate == 0.0001

    def test_futures_engine_funding_failure_tolerated(self, monkeypatch):
        """资金费率获取失败 → None（引擎不结算），不阻断启动"""
        from execution.futures_paper import FuturesPaperEngine
        monkeypatch.setattr(pr, "_fetch_funding_rate", lambda _cfg: None)
        engine = pr._build_engine(
            {"mode": "futures", "wallet_balance": 5000, "leverage": 5,
             "position_size_pct": 0.2},
            Config(),
        )
        assert isinstance(engine, FuturesPaperEngine)
        assert engine._funding_rate is None


def _slot_cfg(label, **over):
    cfg = {"label": label, "strategy": label, "strategy_params": {},
           "exit_params": {}, "leverage": 5, "wallet_balance": 5000.0,
           "position_size_pct": 0.2}
    cfg.update(over)
    return cfg


class TestBuildSlots:
    def test_legacy_config_single_slot(self, monkeypatch):
        """无 strategies 键 → 单 slot，行为与旧单策略一致"""
        monkeypatch.setattr(pr, "_fetch_funding_rate", lambda _cfg: None)
        slots = pr._build_slots(
            {"mode": "spot", "strategy": "ma_cross", "strategy_params": {}},
            Config(),
        )
        assert len(slots) == 1
        assert slots[0]["label"] == "ma_cross"

    def test_multi_slot_independent_engines(self, monkeypatch):
        """多 slot：策略/引擎/风控实例各自独立（连亏/日亏统计互不污染）"""
        monkeypatch.setattr(pr, "_fetch_funding_rate", lambda _cfg: 0.0001)
        slots = pr._build_slots(
            {"mode": "futures", "timeframe": "1h",
             "strategies": [_slot_cfg("ma_cross", leverage=5, wallet_balance=5000.0),
                            _slot_cfg("breakout", leverage=10, wallet_balance=8000.0)]},
            Config(),
        )
        assert [s["label"] for s in slots] == ["ma_cross", "breakout"]
        assert slots[0]["engine"] is not slots[1]["engine"]
        assert slots[0]["strategy"] is not slots[1]["strategy"]
        assert slots[0]["risk"] is not slots[1]["risk"]
        assert slots[0]["engine"].leverage == 5
        assert slots[1]["engine"].leverage == 10
        assert slots[0]["engine"]._funding_rate == 0.0001
        assert slots[1]["engine"]._funding_rate == 0.0001

    def test_funding_fetched_once_for_all_slots(self, monkeypatch):
        """资金费率共享一次拉取，注入每个引擎"""
        calls = []
        monkeypatch.setattr(pr, "_fetch_funding_rate",
                            lambda _cfg: calls.append(1) or 0.0002)
        slots = pr._build_slots(
            {"mode": "futures",
             "strategies": [_slot_cfg(n) for n in ("ma_cross", "breakout", "rsi_mean_reversion")]},
            Config(),
        )
        assert len(calls) == 1
        assert all(s["engine"]._funding_rate == 0.0002 for s in slots)


class TestSlotStateShape:
    def test_single_slot_plain_state(self):
        st = pr._slot_state({"ma_cross": {"signal": "hold"}}, ["ma_cross"])
        assert st == {"signal": "hold"}  # 单 slot 保持旧 state 结构

    def test_multi_slot_bucketed(self):
        st = pr._slot_state({"a": {"signal": "hold"}, "b": {"signal": "buy"}}, ["a", "b"])
        assert st == {"a": {"signal": "hold"}, "b": {"signal": "buy"}}

    def test_empty(self):
        assert pr._slot_state({}, ["ma_cross"]) is None
        assert pr._slot_state({}, ["a", "b"]) == {}


class TestMainChain:
    """链路：config 写入 → main() 建引擎回放 → state.json 结构（mock 数据获取与退出循环）"""

    def _fake_klines(self, n=30):
        idx = pd.date_range("2026-01-01 09:00", periods=n, freq="h", tz="Asia/Shanghai")
        close = [3000.0 + i for i in range(n)]
        return pd.DataFrame({
            "open": close, "high": [c + 5 for c in close],
            "low": [c - 5 for c in close], "close": close,
            "volume": [100.0] * n,
        }, index=idx)

    class _StopLoop(Exception):
        pass

    def _run_main_once(self, monkeypatch):
        """回放完成后进入实时循环，第一轮写完状态由 sleep 抛异常跳出无限循环"""
        df = self._fake_klines()
        monkeypatch.setattr(pr, "fetch_okx_data", lambda *a, **k: df)
        monkeypatch.setattr(pr, "_fetch_funding_rate", lambda _cfg: None)
        stop = self._StopLoop

        def _boom(_s):
            raise stop()

        monkeypatch.setattr(pr.time, "sleep", _boom)
        with pytest.raises(stop):
            pr.main()

    def test_multi_slot_state_bucketed_by_label(self, tmp_paths, monkeypatch):
        pr.write_config({
            "mode": "futures", "timeframe": "1h", "initial_bars": 30, "tick_exit": False,
            "strategy": "ma_cross",  # 兼容字段（主策略）
            "strategies": [_slot_cfg("ma_cross", leverage=5, wallet_balance=5000.0),
                           _slot_cfg("breakout", leverage=10, wallet_balance=8000.0)],
        })
        self._run_main_once(monkeypatch)
        st = pr.read_state()
        assert st["phase"] == "running"
        assert st["slots"] == ["ma_cross", "breakout"]
        ps = st["paper_state"]
        assert set(ps.keys()) == {"ma_cross", "breakout"}
        for lb in ("ma_cross", "breakout"):
            assert "account" in ps[lb]
            assert ps[lb]["account"]["equity"] > 0
        assert ps["ma_cross"]["account"]["initial_balance"] == 5000.0
        assert ps["breakout"]["account"]["initial_balance"] == 8000.0

    def test_single_slot_keeps_legacy_state(self, tmp_paths, monkeypatch):
        """旧单策略 config（无 strategies 键）→ state.json 结构一字不动"""
        pr.write_config({
            "mode": "spot", "strategy": "ma_cross", "strategy_params": {},
            "wallet_balance": 5000, "position_size_pct": 0.2,
            "timeframe": "1h", "initial_bars": 30, "tick_exit": False,
        })
        self._run_main_once(monkeypatch)
        st = pr.read_state()
        assert st["phase"] == "running"
        assert "slots" not in st
        assert "account" in st["paper_state"]  # paper_state 即单引擎 state


class TestMultiEquityChart:
    def test_traces_per_slot(self):
        import plotly.graph_objects as go
        from frontend.components.charts import multi_equity_chart
        curve = [{"time": "2026-01-01", "equity": 10000.0},
                 {"time": "2026-01-02", "equity": 10010.0}]
        fig = multi_equity_chart({"ma_cross": curve, "breakout": curve, "empty": []})
        assert isinstance(fig, go.Figure)
        assert len(fig.data) == 2  # 空曲线不画
        assert {t.name for t in fig.data} == {"ma_cross", "breakout"}
