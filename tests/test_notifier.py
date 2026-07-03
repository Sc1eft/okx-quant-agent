"""测试 ServerChan 通知"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from agents.notifier import ServerChanNotifier


class TestServerChanNotifier:

    def test_push_report_empty_sendkey_returns_false(self):
        """空 sendkey 返回 False"""
        n = ServerChanNotifier(sendkey="")
        result = n.push_report("daily", "2026-07-03", {
            "stats": {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
                      "total_pnl": 0, "max_drawdown_pct": 0},
            "ai_analysis": {"wins": {"patterns": []}, "losses": {"patterns": []}},
            "summary": "",
        })
        assert result is False

    def test_push_text_invalid_key_returns_false(self):
        """无效 sendkey 返回 False（网络失败）"""
        n = ServerChanNotifier(sendkey="SCT_invalid_test_key")
        result = n.push_text("test", "test content")
        assert result is False  # 网络请求会失败

    def test_push_report_with_wins_and_losses(self):
        """包含盈亏数据的报告推送"""
        n = ServerChanNotifier(sendkey="")
        report = {
            "stats": {"trades": 5, "wins": 3, "losses": 2, "win_rate": 60.0,
                      "total_pnl": 25.0, "max_drawdown_pct": 1.5},
            "ai_analysis": {
                "wins": {
                    "patterns": [
                        {"pattern": "MACD金叉做多", "wins_count": 2, "avg_profit": 15.0,
                         "takeaway": "信号可靠"}
                    ]
                },
                "losses": {
                    "patterns": [
                        {"pattern": "追高做多", "loss_count": 1, "avg_loss": -5.0,
                         "cause": "假突破", "suggestion": "等待回踩"}
                    ]
                },
                "summary": "信号共振策略有效",
            },
        }
        result = n.push_report("weekly", "2026-W27", report)
        assert result is False  # 空 sendkey = False
