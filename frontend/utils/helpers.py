"""共享前端工具函数

从 page_modules/8-11 提取的重复函数（_ss, _fmt_change, _fmt_uptime, _fmt_price）
以及共享的图表主题色常量。
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

__all__ = [
    "ETH_SYMBOL",
    "THEME_COLORS",
    "ss",
    "fmt_change",
    "fmt_uptime",
    "fmt_price",
]

# ── 常量 ──

ETH_SYMBOL = "ETH-USDT"

# 图表主题色（与 charts.py 的 _default_layout 和 eth_charts.py 共享）
THEME_COLORS: dict[str, dict[str, str]] = {
    "light": {
        "bg_plot": "#ffffff",
        "bg_paper": "#f8fafc",
        "font": "#475569",
        "title": "#0f172a",
        "grid": "#e2e8f0",
    },
    "dark": {
        "bg_plot": "#1e293b",
        "bg_paper": "#1e293b",
        "font": "#94a3b8",
        "title": "#f1f5f9",
        "grid": "#334155",
    },
}


# ── Session State ──


def ss(key: str, default=None):
    """st.session_state 快捷读写，带默认值。"""
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


# ── 格式化工具 ──


def fmt_change(c: float | None) -> str:
    """涨跌幅百分比，如 '+3.45%' 或 '-1.23%'。"""
    if c is None:
        return ""
    return f"{c:+.2f}%"


def fmt_uptime(started_at_str: str | None) -> str:
    """ISO 时间字符串 → 可读运行时间，如 '1h 23m 45s'。"""
    if not started_at_str:
        return "-"
    try:
        start = datetime.fromisoformat(started_at_str)
        delta = datetime.now(timezone.utc) - start
        total_sec = int(delta.total_seconds())
        h, r = divmod(total_sec, 3600)
        m, s = divmod(r, 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"
    except Exception:
        return "-"


def fmt_price(p: float) -> str:
    """带 $ 符号的价格格式。"""
    return f"${p:,.2f}"
