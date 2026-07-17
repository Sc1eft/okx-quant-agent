"""
交易报告页面 — 浏览日/周/月交易报告
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import streamlit as st

# 项目根路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_DIR = PROJECT_ROOT / "data" / "reports"


def _load_reports(report_type: str | None = None) -> list[dict]:
    """加载报告 JSON 文件列表，按时间倒序"""
    reports = []
    types = [report_type] if report_type else ["daily", "weekly", "monthly"]
    for rt in types:
        rt_dir = REPORT_DIR / rt
        if not rt_dir.exists():
            continue
        for f in sorted(rt_dir.glob(f"{rt}_*.json"), reverse=True):
            try:
                with open(str(f), encoding="utf-8") as fh:
                    data = json.load(fh)
                    data["_file"] = str(f)
                    data["_type_label"] = {
                        "daily": "📅 日报", "weekly": "📅 周报", "monthly": "📅 月报",
                    }.get(rt, rt)
                    reports.append(data)
            except (json.JSONDecodeError, OSError):
                continue
    reports.sort(key=lambda r: r.get("date", ""), reverse=True)
    return reports


def _render_report_card(report: dict):
    """渲染单条报告卡片"""
    stats = report.get("stats", {})
    ai = report.get("ai_analysis", {})
    pushed = report.get("pushed", False)
    pushed_label = "✅ 已推送微信" if pushed else "⏳ 待推送"
    total_pnl = stats.get("total_pnl", 0)
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    trades = stats.get("trades", 0)
    win_rate = stats.get("win_rate", 0)

    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(
                f"**{report.get('_type_label', '')} | {report.get('date', '')}**"
            )
            st.markdown(
                f"{pnl_emoji} 交易 {trades} 笔 | 胜率 {win_rate}% | "
                f"总盈亏 {total_pnl:+.2f} USDT"
            )
        with col2:
            st.markdown(f"**{pushed_label}**")
            if pushed:
                pt = report.get("push_time", "")
                if pt:
                    st.caption(f"推送于 {pt[:19]}")

        # AI 分析摘要
        if ai:
            wins = ai.get("wins", {})
            losses = ai.get("losses", {})
            tabs = st.tabs(["🟢 盈利", "🔴 亏损", "💡 总结"])
            with tabs[0]:
                if wins.get("patterns"):
                    for p in wins["patterns"]:
                        st.markdown(
                            f"- **{p['pattern']}**: {p.get('wins_count', 0)}笔 "
                            f"均盈 +{p.get('avg_profit', 0):.1f}"
                        )
                        if p.get("takeaway"):
                            st.caption(f"  → {p['takeaway']}")
                else:
                    st.caption("无盈利交易")
            with tabs[1]:
                if losses.get("patterns"):
                    for p in losses["patterns"]:
                        st.markdown(
                            f"- **{p['pattern']}**: {p.get('loss_count', 0)}笔 "
                            f"均亏 {p.get('avg_loss', 0):.1f}"
                        )
                        if p.get("cause"):
                            st.caption(f"  原因: {p['cause']}")
                        if p.get("suggestion"):
                            st.caption(f"  建议: {p['suggestion']}")
                else:
                    st.caption("无亏损交易")
            with tabs[2]:
                summary = ai.get("summary", "") or report.get("summary", "")
                if summary:
                    st.info(summary)
                else:
                    st.caption("暂无总结")
        else:
            st.caption(report.get("summary", ""))

        # 展开查看原始数据
        with st.expander("📄 完整数据"):
            st.json(report)


st.title("📋 交易报告")

# ── 顶部操作栏 ──
col1, _ = st.columns([1, 3])
with col1:
    filter_type = st.selectbox("报告类型", ["全部", "日报", "周报", "月报"])

st.info("报告由 Agent 定时自动生成（日报：每日 UTC 16:00；周报：每周日；月报：每月 1 日），本页面仅用于浏览历史报告。")

# ── 报告列表 ──
type_map = {"全部": None, "日报": "daily", "周报": "weekly", "月报": "monthly"}
reports = _load_reports(type_map.get(filter_type))

if not reports:
    st.info("暂无交易报告。Agent 3 会在交易时段自动生成报告。")
else:
    st.markdown(f"**共 {len(reports)} 份报告**")
    for report in reports:
        _render_report_card(report)
