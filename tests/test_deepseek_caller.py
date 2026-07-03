"""测试 DeepSeek 调用器"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from agents.deepseek_caller import DeepSeekTrader


class TestDeepSeekTrader:

    def test_analyze_trade_report_returns_expected_keys(self):
        """trade report 分析返回正确的 key 结构"""
        trader = DeepSeekTrader(api_key="sk-placeholder")
        ctx = {
            "period_type": "daily",
            "period_start": "2026-07-03T00:00:00Z",
            "period_end": "2026-07-03T16:00:00Z",
            "stats": {
                "trades": 5, "wins": 3, "losses": 2, "win_rate": 60.0,
                "total_pnl": 25.0, "max_drawdown_pct": 1.5,
            },
            "win_trades": [
                {
                    "pnl": 10, "side": "buy", "reason": "MACD bullish cross",
                    "entry_price": 3400, "exit_price": 3410,
                }
            ],
            "loss_trades": [],
        }
        result = trader.analyze_trade_report(ctx)
        assert "wins" in result
        assert "losses" in result
        assert "summary" in result

    def test_analyze_trade_report_fallback_on_error(self):
        """API 调用失败时回退默认值"""
        trader = DeepSeekTrader(api_key="sk-placeholder")
        result = trader.analyze_trade_report({
            "period_type": "daily",
            "period_start": "2026-07-03T00:00:00Z",
            "period_end": "2026-07-03T16:00:00Z",
            "stats": {},
            "win_trades": [],
            "loss_trades": [],
        })
        assert result["wins"]["count"] == 0
        assert result["losses"]["count"] == 0
        assert result["summary"] == "AI 分析暂不可用"
