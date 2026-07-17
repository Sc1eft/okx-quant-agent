"""
ETH 实时心跳 — 💓 秒级 ETH-USDT 实时行情监控

后台采集器通过 OKX WebSocket 接收实时 ticker 数据，
本页面每秒读取 status.json + SQLite 展示。

使用方式:
    1. 点击「▶ 启动心跳采集」— 启动后台 WebSocket 采集器
    2. 页面自动每秒刷新，显示最新 ET H 价格
    3. 点击「⏹ 停止」关闭采集器

数据流:
    OKX WebSocket → eth_heartbeat.py (常驻) → SQLite ← 本页面 (每秒读取)
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.heartbeat_db import (
    HeartbeatDB,
    read_status,
    is_collector_running,
    start_collector,
    stop_collector,
)
from frontend.utils.data_provider import fetch_ticker
from frontend.utils.helpers import ss as _ss, fmt_change as _fmt_change, fmt_uptime as _fmt_uptime, fmt_price as _fmt_price, ETH_SYMBOL
from frontend.components.eth_charts import COLORS, _build_sparkline

# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def _calc_ticks_per_second(status: dict) -> str:
    """根据 tick_count 和 started_at 算每秒平均 ticks。"""
    tc = status.get("tick_count", 0)
    sa = status.get("started_at")
    if not sa or not tc:
        return "0"
    try:
        start = datetime.fromisoformat(sa)
        elapsed = max(1, (datetime.now(timezone.utc) - start).total_seconds())
        return f"{tc / elapsed:.1f}"
    except Exception:
        return "0"


# ════════════════════════════════════════════════════════════════
# PAGE STYLING
# ════════════════════════════════════════════════════════════════

st.markdown("""
<style>
/* ── Pulse animation for heartbeat dot ── */
@keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7); }
    70%  { box-shadow: 0 0 0 14px rgba(34, 197, 94, 0); }
    100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
}
.heartbeat-dot {
    display: inline-block;
    width: 14px; height: 14px;
    border-radius: 50%;
    margin-right: 8px;
    vertical-align: middle;
    animation: pulse 1.5s infinite;
}
.dot-green { background: #22c55e; }
.dot-red   { background: #ef4444; }
.dot-gray  { background: #94a3b8; }

/* ── Large price display ── */
.price-value {
    font-size: 3.2rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
    transition: color 0.2s ease;
}
.price-up   { color: #059669; }
.price-down { color: #dc2626; }
.price-flat { color: #0f172a; }

/* ── Change badge ── */
.change-badge {
    display: inline-block;
    padding: 2px 12px;
    border-radius: 999px;
    font-size: 0.95rem;
    font-weight: 600;
}
.change-up   { background: #d1fae5; color: #065f46; }
.change-down { background: #fee2e2; color: #991b1b; }

/* ── Status bar ── */
.hb-status-bar {
    display: flex; flex-wrap: wrap; gap: 1.5rem;
    padding: 0.75rem 1rem;
    background: #f8fafc;
    border-radius: 10px;
    border: 1px solid #e2e8f0;
    align-items: center;
    font-size: 0.9rem;
    margin-bottom: 1rem;
}
.hb-status-item {
    display: flex; align-items: center; gap: 6px;
}
.hb-status-label { color: #64748b; font-size: 0.8rem; }
.hb-status-value { color: #0f172a; font-weight: 600; }

/* ── Control buttons row ── */
.hb-controls {
    display: flex; gap: 0.5rem; align-items: center;
}

/* ── Price container card ── */
.price-card {
    text-align: center;
    padding: 2rem 1rem 1.5rem;
    background: white;
    border-radius: 16px;
    border: 1px solid #e2e8f0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    margin-bottom: 1rem;
}
.price-sub {
    display: flex; justify-content: center; gap: 1.5rem;
    margin-top: 0.5rem; color: #64748b; font-size: 0.9rem;
}
.price-bba {
    display: flex; justify-content: center; gap: 1rem;
    margin-top: 0.75rem; font-size: 0.85rem; color: #475569;
}
.price-bba span { background: #f1f5f9; padding: 2px 10px; border-radius: 6px; }

/* ── Sparkline container ── */
.sparkline-box {
    background: white;
    border-radius: 12px;
    border: 1px solid #e2e8f0;
    padding: 0.5rem;
    margin-bottom: 1rem;
}

/* ── Mobile responsive ── */
@media (max-width: 767px) {
    .price-value { font-size: 2rem !important; }
    .price-card { padding: 1rem 0.5rem !important; }
    .price-bba { flex-wrap: wrap; gap: 0.4rem; }
    .price-bba span { flex: 1 1 calc(50% - 0.4rem); text-align: center; }
    .price-sub { flex-wrap: wrap; gap: 0.5rem; }
    .hb-status-bar { gap: 0.4rem; font-size: 0.78rem; }
}
@media (max-width: 479px) {
    .price-value { font-size: 1.6rem !important; }
}
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# PAGE HEADER
# ════════════════════════════════════════════════════════════════

st.markdown("""
<div style="display:flex; align-items:center; gap:0.75rem; margin-bottom:0.25rem;">
    <span style="font-size:1.8rem;">💓</span>
    <div>
        <h1 style="margin:0; font-size:1.5rem;">ETH 实时心跳</h1>
        <p style="margin:0; color:#64748b; font-size:0.85rem;">
            ETH-USDT 秒级实时行情 · 通过 OKX WebSocket
        </p>
    </div>
</div>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# SESSION STATE
# ════════════════════════════════════════════════════════════════

_ss("hb_user_started", False)  # 用户主动点击过启动

# ════════════════════════════════════════════════════════════════
# 1. STATUS BAR + CONTROLS
# ════════════════════════════════════════════════════════════════

running = is_collector_running()

# If user started but it's not running, try restart
if st.session_state.hb_user_started and not running:
    st.info("⏳ 采集器正在启动……")
    start_collector()
    time.sleep(1.5)
    running = is_collector_running()

# ── Status bar ──
dot_class = "dot-green" if running else "dot-gray"
status_text = "🟢 运行中" if running else "⏸ 已停止"

status = read_status()
tick_count = status.get("tick_count", 0) if status else 0
uptime_str = _fmt_uptime(status.get("started_at") if status else None)
tps = _calc_ticks_per_second(status) if status else "0"

st.markdown(f"""
<div class="hb-status-bar">
    <div class="hb-status-item">
        <span class="heartbeat-dot {dot_class}"></span>
        <span class="hb-status-value">{status_text}</span>
    </div>
    <div class="hb-status-item">
        <span class="hb-status-label">心跳</span>
        <span class="hb-status-value">{tick_count:,}</span>
    </div>
    <div class="hb-status-item">
        <span class="hb-status-label">速率</span>
        <span class="hb-status-value">{tps} ticks/s</span>
    </div>
    <div class="hb-status-item">
        <span class="hb-status-label">运行时长</span>
        <span class="hb-status-value">{uptime_str}</span>
    </div>
    <div class="hb-status-item" style="margin-left:auto; display:flex; gap:0.5rem;">
        <span class="hb-status-label">ETH-USDT</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Controls ──
ctrl_cols = st.columns([1, 1, 1, 3])
with ctrl_cols[0]:
    if running:
        if st.button("⏹ 停止采集", use_container_width=True, type="primary"):
            stop_collector()
            st.session_state.hb_user_started = False
            st.rerun()
    else:
        if st.button("▶ 启动心跳采集", use_container_width=True, type="primary"):
            ok = start_collector()
            st.session_state.hb_user_started = True
            if ok:
                time.sleep(0.5)
            st.rerun()

with ctrl_cols[1]:
    # Clear data button — 点击后需二次确认，防止误删
    if st.button("🗑 清空数据", use_container_width=True):
        st.session_state.hb_confirm_clear = True

with ctrl_cols[2]:
    auto = st.checkbox("自动刷新", value=True, key="hb_auto_refresh")

# ── 清空数据二次确认 ──
if st.session_state.get("hb_confirm_clear"):
    st.warning("⚠️ 此操作将删除全部心跳数据，且不可恢复。")
    confirm_cols = st.columns([1, 1, 4])
    with confirm_cols[0]:
        if st.button("✅ 确认清空", key="hb_clear_yes", type="primary"):
            try:
                db = HeartbeatDB()
                db.conn.execute("DELETE FROM ticks")
                db.conn.commit()
                db.close()
                st.toast("✅ 心跳数据已清空", icon="🗑")
            except Exception as e:
                st.error(f"清空失败: {e}")
            st.session_state.hb_confirm_clear = False
            time.sleep(0.3)
            st.rerun()
    with confirm_cols[1]:
        if st.button("取消", key="hb_clear_no"):
            st.session_state.hb_confirm_clear = False
            st.rerun()

if not running:
    st.markdown("""
    <div style="text-align:center; padding:3rem 1rem; color:#94a3b8;">
        <div style="font-size:3rem; margin-bottom:0.5rem;">💓</div>
        <div style="font-size:1.1rem; font-weight:600; color:#475569; margin-bottom:0.5rem;">
            心跳采集未启动
        </div>
        <div style="font-size:0.9rem;">
            点击「▶ 启动心跳采集」开始实时接收 ETH-USDT ticker 数据
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ════════════════════════════════════════════════════════════════
# 2. LIVE DISPLAY — 动态行情区（独立 fragment，每秒刷新，不闪全页）
# ════════════════════════════════════════════════════════════════

@st.fragment(run_every=1 if auto else None)
def _live_fragment():
    """价格卡 / sparkline / KPI / 最近心跳记录 — 数据获取与渲染都在 fragment 内"""
    # 采集器停止时退回整页重跑，走顶部「已停止 / 自动重启」逻辑
    if not is_collector_running():
        st.rerun()

    status = read_status()
    if not status:
        st.warning("⏳ 等待采集器就绪……")
        return

    tick_count = status.get("tick_count", 0)
    tps = _calc_ticks_per_second(status)

    last_price = status.get("last_price", 0) or 0
    change_24h = status.get("change_24h")
    bid = status.get("last_bid")
    ask = status.get("last_ask")
    volume_24h = status.get("volume_24h")

    # Determine direction for coloring
    prev_price = _ss("hb_prev_price", last_price)
    if last_price > prev_price:
        price_dir = "up"
    elif last_price < prev_price:
        price_dir = "down"
    else:
        price_dir = "flat"
    st.session_state.hb_prev_price = last_price

    # Pre-compute display values
    change_class = "change-up" if (change_24h or 0) >= 0 else "change-down"
    price_value_class = f"price-{price_dir}"
    bid_str = _fmt_price(bid) if bid else "N/A"
    ask_str = _fmt_price(ask) if ask else "N/A"
    spread_str = f"{(ask - bid) / bid * 100:.3f}%" if bid and ask else "N/A"
    vol_str = f"{volume_24h:,.0f}" if volume_24h else "N/A"

    # ── Price card ──
    st.markdown(f"""
<div class="price-card">
    <div class="price-value {price_value_class}">
        {_fmt_price(last_price)}
    </div>
    <div class="price-sub">
        <span>
            24h <span class="change-badge {change_class}">{_fmt_change(change_24h)}</span>
        </span>
        <span>💓 {tps} ticks/s</span>
    </div>
    <div class="price-bba">
        <span>买一 <strong>{bid_str}</strong></span>
        <span>卖一 <strong>{ask_str}</strong></span>
        <span>价差 <strong>{spread_str}</strong></span>
        <span>24h 量 <strong>{vol_str}</strong></span>
    </div>
</div>
""", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    # 3. SPARKLINE
    # ════════════════════════════════════════════════════════════

    db = HeartbeatDB()
    recent_ticks = db.get_recent_ticks(limit=120)  # last ~10-20s of data

    if recent_ticks:
        sparkline = _build_sparkline(recent_ticks, height=110)
        st.markdown('<div class="sparkline-box">', unsafe_allow_html=True)
        st.plotly_chart(sparkline, use_container_width=True, config={"displayModeBar": False})
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("⏳ 等待心跳数据……")

    # ════════════════════════════════════════════════════════════
    # 4. KPI ROW
    # ════════════════════════════════════════════════════════════

    kpi_cols = st.columns(5)

    # Price stats from recent ticks
    if recent_ticks:
        prices = [t["price"] for t in recent_ticks if t.get("price")]
        if prices:
            with kpi_cols[0]:
                st.metric("时段最高", f"${max(prices):,.2f}",
                          f"+{(max(prices) - prices[0]) / prices[0] * 100:+.2f}%")
            with kpi_cols[1]:
                st.metric("时段最低", f"${min(prices):,.2f}",
                          f"-{(prices[0] - min(prices)) / prices[0] * 100:+.2f}%")
            with kpi_cols[2]:
                st.metric("时段波幅", f"${max(prices) - min(prices):,.2f}")
            with kpi_cols[3]:
                st.metric("心跳总数", f"{tick_count:,}")
            with kpi_cols[4]:
                sample_rate = f"{tps}/s"
                st.metric("采集速率", sample_rate)

    # ════════════════════════════════════════════════════════════
    # 5. RECENT TICKS TABLE
    # ════════════════════════════════════════════════════════════

    if recent_ticks:
        st.markdown("### 📋 最近心跳记录")

        # Prepare display data
        rows = []
        for t in recent_ticks[:50]:  # last 50 ticks
            try:
                ts = datetime.fromisoformat(t["ts"]).strftime("%H:%M:%S.%f")[:10]
            except Exception:
                ts = str(t.get("ts", ""))[:10]
            rows.append({
                "时间": ts,
                "价格": f"${t['price']:,.2f}",
                "买一": f"${t['bid']:,.2f}" if t.get("bid") else "-",
                "卖一": f"${t['ask']:,.2f}" if t.get("ask") else "-",
                "24h 涨跌": f"{t.get('change_24h', 0):+.2f}%" if t.get("change_24h") is not None else "-",
            })

        df_display = pd.DataFrame(rows)

        # Color the price column
        def _color_price(val):
            return ""  # Keep default — we use the price-value class above

        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "时间": st.column_config.TextColumn("时间", width="small"),
                "价格": st.column_config.TextColumn("价格 💰", width="small"),
                "买一": st.column_config.TextColumn("买一", width="small"),
                "卖一": st.column_config.TextColumn("卖一", width="small"),
                "24h 涨跌": st.column_config.TextColumn("24h 涨跌", width="small"),
            },
        )

        total = db.count_ticks()
        st.caption(f"显示最近 {min(50, len(recent_ticks))} / 共 {total:,} 条心跳记录")
    else:
        st.info("💡 启动后实时心跳将在此显示")

    db.close()


_live_fragment()
