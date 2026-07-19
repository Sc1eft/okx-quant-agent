"""A4 过拟合统计护栏测试 — Sharpe 显著性 / 参数扫描护栏 / OOS 判定"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import backtest.analyzer as analyzer_mod
from backtest.analyzer import WalkForwardAnalyzer
from backtest.metrics import compute_metrics
from config import Config


def _fake_df(n=200, start="2025-01-01"):
    idx = pd.date_range(start, periods=n, freq="1h")
    close = 3000 + np.cumsum(np.random.default_rng(7).normal(0, 5, n))
    return pd.DataFrame({
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": 100.0,
    }, index=idx)


class _FakeEngine:
    """按预设序列轮询返回 metrics 的假回测引擎"""

    def __init__(self, metrics_seq):
        self._seq = list(metrics_seq)
        self.calls = []
        self._i = 0

    def run(self, df, strategy_name=None, params=None, **kw):
        self.calls.append({"params": params, "n_bars": len(df)})
        m = self._seq[self._i % len(self._seq)]
        self._i += 1
        return SimpleNamespace(metrics=m)


def _mk_metrics(ret=10.0, sharpe=1.0, trades=10, dd=5.0):
    return {"total_return_pct": ret, "sharpe": sharpe,
            "max_drawdown_pct": dd, "total_trades": trades}


@pytest.fixture
def cfg():
    return Config()


# ───────────────────────────── metrics: Sharpe 显著性 ─────────────────────────────

class TestSharpeSignificance:
    def _trades(self, n):
        base = pd.Timestamp("2025-01-01")
        return [
            SimpleNamespace(pnl=10.0, entry_time=base, exit_time=base + pd.Timedelta(hours=1))
            for _ in range(n)
        ]

    def test_psr_high_for_steady_uptrend(self):
        idx = pd.date_range("2025-01-01", periods=500, freq="1h")
        equity = pd.Series(np.linspace(10000, 11000, 500), index=idx)
        m = compute_metrics(equity, self._trades(25), 10000, _fake_df(500))
        assert m["psr"] > 0.99
        assert m["sharpe_se"] >= 0
        assert m["min_trades_ok"] is True

    def test_min_trades_flag(self):
        idx = pd.date_range("2025-01-01", periods=100, freq="1h")
        equity = pd.Series(np.linspace(10000, 10050, 100), index=idx)
        m = compute_metrics(equity, self._trades(3), 10000, _fake_df(100))
        assert m["min_trades_ok"] is False

    def test_empty_equity_untouched(self):
        m = compute_metrics(pd.Series(dtype=float), [], 10000, _fake_df(10))
        assert m == {"total_trades": 0}


# ───────────────────────────── 参数扫描护栏 ─────────────────────────────

class TestSweepGuardrails:
    def test_macd_agent_param_space(self, cfg, monkeypatch):
        """macd_agent 必须扫自己真实的参数（score_threshold/min_confidence）"""
        engine = _FakeEngine([_mk_metrics()])
        monkeypatch.setattr(analyzer_mod, "BacktestEngine", lambda _cfg: engine)
        WalkForwardAnalyzer(cfg).parameter_sweep(_fake_df(100), "macd_agent", n_iterations=8)
        swept = [c["params"] for c in engine.calls if c["params"]]
        assert swept, "应按参数空间采样"
        for p in swept:
            assert set(p) == {"score_threshold", "min_confidence"}

    def test_low_trades_excluded_from_ranking(self, cfg, monkeypatch):
        """交易次数不足的组合不进 top-N，且计入 n_skipped"""
        # 交替：高收益但 0 笔交易 vs 低收益但 10 笔交易
        engine = _FakeEngine([
            _mk_metrics(ret=999.0, trades=0),
            _mk_metrics(ret=5.0, trades=10),
        ])
        monkeypatch.setattr(analyzer_mod, "BacktestEngine", lambda _cfg: engine)
        r = WalkForwardAnalyzer(cfg).parameter_sweep(_fake_df(100), "ma_cross", n_iterations=10)
        assert r.n_valid == 5
        assert r.n_skipped_low_trades == 5
        assert r.best_return == 5.0  # 999% 的 0 交易组合被排除
        assert all(e["trades"] >= 5 for e in r.top_10pct_params)

    def test_all_low_trades_fails(self, cfg, monkeypatch):
        engine = _FakeEngine([_mk_metrics(ret=50.0, trades=1)])
        monkeypatch.setattr(analyzer_mod, "BacktestEngine", lambda _cfg: engine)
        r = WalkForwardAnalyzer(cfg).parameter_sweep(_fake_df(100), "ma_cross", n_iterations=6)
        assert r.verdict == "FAIL"
        assert r.n_valid == 0
        assert "交易次数均不足" in r.details

    def test_top_params_oos_revalidated(self, cfg, monkeypatch):
        """top 组合自动在留出段复验（oos_return / oos_retention）"""
        engine = _FakeEngine([_mk_metrics(ret=10.0, trades=10)])
        monkeypatch.setattr(analyzer_mod, "BacktestEngine", lambda _cfg: engine)
        r = WalkForwardAnalyzer(cfg).parameter_sweep(_fake_df(100), "ma_cross", n_iterations=10)
        assert r.top_10pct_params
        for e in r.top_10pct_params:
            assert "oos_return" in e
            assert "oos_retention" in e
        # IS/OOS 切分：复验调用发生在 30 根 OOS 段上
        assert any(c["n_bars"] == 30 for c in engine.calls)

    def test_multiple_testing_note_in_details(self, cfg, monkeypatch):
        engine = _FakeEngine([_mk_metrics(ret=10.0, trades=10)])
        monkeypatch.setattr(analyzer_mod, "BacktestEngine", lambda _cfg: engine)
        r = WalkForwardAnalyzer(cfg).parameter_sweep(_fake_df(100), "ma_cross", n_iterations=6)
        assert "多次试验" in r.details


# ───────────────────────────── OOS 判定 ─────────────────────────────

class TestOOSVerdict:
    def test_low_retention_fails(self, cfg, monkeypatch):
        """OOS 保留率 <50% 不得 PASS（旧逻辑只看 oos_ret>0 + sharpe>0.5）"""
        # 第一次调用=IS（高收益），第二次=OOS（大幅退化但 sharpe 仍 >0.5）
        engine = _FakeEngine([
            _mk_metrics(ret=100.0, sharpe=2.0, trades=20),
            _mk_metrics(ret=10.0, sharpe=1.0, trades=5),
        ])
        monkeypatch.setattr(analyzer_mod, "BacktestEngine", lambda _cfg: engine)
        r = WalkForwardAnalyzer(cfg).out_of_sample_test(_fake_df(100), "ma_cross")
        assert r["retention_ratio"] == 10.0
        assert r["verdict"] == "FAIL"

    def test_good_retention_passes(self, cfg, monkeypatch):
        engine = _FakeEngine([
            _mk_metrics(ret=100.0, sharpe=2.0, trades=20),
            _mk_metrics(ret=80.0, sharpe=1.5, trades=8),
        ])
        monkeypatch.setattr(analyzer_mod, "BacktestEngine", lambda _cfg: engine)
        r = WalkForwardAnalyzer(cfg).out_of_sample_test(_fake_df(100), "ma_cross")
        assert r["retention_ratio"] == 80.0
        assert r["verdict"] == "PASS"


# ───────────────────────────── WF n_windows 传递（runner bug 回归） ─────────────────────────────

class TestWFWindows:
    def test_run_accepts_n_windows(self, cfg, monkeypatch):
        engine = _FakeEngine([_mk_metrics()])
        monkeypatch.setattr(analyzer_mod, "BacktestEngine", lambda _cfg: engine)
        r = WalkForwardAnalyzer(cfg).run(_fake_df(200), "ma_cross", n_windows=2)
        assert len(r.windows) == 2
