"""
Agent 风险监控面板（只读）
读取 agents/trade_executor / risk_layer / position_monitor 的状态并展示
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

st.set_page_config(page_title="Agent 风控", page_icon="🛡", layout="wide")
st.title("🛡 Agent 风控监控面板")
st.markdown("实时展示三 Agent 系统的风控状态、交易记录和持仓监控。**只读面板**，不参与决策。")

# ============ 模拟/真实数据 ============

# 在正式运行中，这些数据通过 main.py 暴露的 dict 或 SQLite 获取
# 此处展示页面结构。生产运行时替换为真实数据源。

DEMO_MODE = st.sidebar.checkbox("演示模式（使用模拟数据）", value=True)

# ── 三个标签页 ──
tab1, tab2, tab3 = st.tabs(["📊 风控概览", "📋 交易日志", "📈 持仓监控"])

with tab1:
    st.subheader("Layer 1 — 交易前风控")

    if DEMO_MODE:
        risk_data = {
            "每日交易": "3 / 10",
            "每日亏损": "$25.50 / $100.00",
            "连续亏损": "1 / 3",
            "仓位乘数": "0.75x",
            "API 熔断": "未触发",
            "BTC 波动": "正常 (1.2%)",
        }
    else:
        # TODO: 从 main.py 暴露的 agent3.get_status()['risk_status'] 读取
        risk_data = {"状态": "等待 Agent 运行数据"}

    cols = st.columns(3)
    for i, (key, val) in enumerate(risk_data.items()):
        col = cols[i % 3]
        with col:
            st.metric(key, val)

    st.divider()
    st.subheader("Layer 2 — 交易中保护")

    l2_cols = st.columns(3)
    with l2_cols[0]:
        st.metric("限价单超时", "10s")
    with l2_cols[1]:
        st.metric("最大滑点", "0.3%")
    with l2_cols[2]:
        st.metric("部分成交等待", "10s")

    st.divider()
    st.subheader("Layer 3 — 交易后监控")

    l3_cols = st.columns(3)
    with l3_cols[0]:
        st.metric("止损触发", "0 次" if DEMO_MODE else "—")
    with l3_cols[1]:
        st.metric("止盈触发", "0 次" if DEMO_MODE else "—")
    with l3_cols[2]:
        st.metric("移动止损激活", "0 次" if DEMO_MODE else "—")


with tab2:
    st.subheader("最近交易记录")

    if DEMO_MODE:
        demo_trades = [
            {"时间": "2026-06-24 10:30:00", "方向": "买入", "数量": "0.01 ETH",
             "价格": "$3,450.00", "状态": "成交", "订单ID": "12345"},
            {"时间": "2026-06-24 11:15:00", "方向": "卖出", "数量": "0.01 ETH",
             "价格": "$3,480.50", "状态": "部分成交(50%)", "订单ID": "12346"},
        ]
        df = pd.DataFrame(demo_trades)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        try:
            import sqlite3
            conn = sqlite3.connect(PROJECT_ROOT / "data" / "agent_trades.db")
            df = pd.read_sql_query(
                "SELECT * FROM trades ORDER BY id DESC LIMIT 20", conn
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
            conn.close()
        except Exception as e:
            st.info(f"暂无交易数据: {e}")

    # 交易统计
    st.subheader("📊 交易统计")
    stat_cols = st.columns(4)
    with stat_cols[0]:
        st.metric("总交易次数", "2" if DEMO_MODE else "—")
    with stat_cols[1]:
        st.metric("成功", "2" if DEMO_MODE else "—")
    with stat_cols[2]:
        st.metric("失败", "0" if DEMO_MODE else "—")
    with stat_cols[3]:
        st.metric("成功率", "100%" if DEMO_MODE else "—")


with tab3:
    st.subheader("当前持仓")

    if DEMO_MODE:
        pos_data = {
            "方向": "多头 / Long",
            "数量": "0.01 ETH",
            "入场价": "$3,450.00",
            "当前价": "$3,500.00",
            "浮盈": "+$0.50 (+1.45%)",
            "止损位": "$3,380.00 (-2.0%)",
            "止盈位": "$3,620.00 (+4.9%)",
            "移动止损": "未激活",
        }
        cols = st.columns(4)
        for i, (key, val) in enumerate(pos_data.items()):
            with cols[i % 4]:
                st.metric(key, val)
    else:
        st.info("等待持仓数据")

    st.divider()
    st.subheader("止盈止损状态")

    sl_tp_cols = st.columns(3)
    with sl_tp_cols[0]:
        progress = 45  # 价格在 SL 和 TP 之间的位置百分比
        st.markdown("**SL ——— TP 位置**")
        st.progress(progress / 100, text=f"{progress}% 向 TP")
    with sl_tp_cols[1]:
        st.metric("距离止损", "$120.00 (3.5%)")
    with sl_tp_cols[2]:
        st.metric("距离止盈", "$120.00 (3.5%)")

st.divider()
st.caption(f"🕐 面板刷新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据延迟 ≤ 5s")
