"""Ethereum Live Data — real-time ETH-USDT market data from OKX.

Always-on candlestick chart (like stock K-line) with expanded timeframe options
from second-level (via heartbeat WebSocket collector) through 15-day candles.

No "start monitoring" button needed — data loads automatically.
Refactored: shared modules extracted to utils/ and components/.
"""

from __future__ import annotations
from frontend.components.layout import inject_mask_hider_js
from frontend.components.metrics_display import render_metric_card
from frontend.utils.data_provider import fetch_klines_with_agg, fetch_ticker
from frontend.utils.helpers import ss as _ss, fmt_change as _fmt_change, ETH_SYMBOL
from frontend.utils.session_state import get_config

# ── Extracted shared modules ──
from frontend.utils.eth_news import _fetch_crypto_news, _fmt_relative_time
from frontend.utils.eth_ai_analysis import (
    _AI_SYSTEM_PROMPT,
    _AI_CHAT_SYSTEM_PROMPT,
    _call_ai_analysis,
    _call_ai_chat,
    _sanitize_ai_text,
    _ticker_summary,
    _summarize_klines,
)
from frontend.components.eth_charts import (
    COLORS,
    TIMEFRAMES,
    TIMEFRAME_REFRESH_S,
    _build_sparkline,
    _build_candlestick_fig,
    _friendly_tf,
    _fmt_uptime,
)

import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Page-specific constants ──
DEFAULT_TF_LABEL = "15分钟"


# ════════════════════════════════════════════════════════════════
# PAGE
# ════════════════════════════════════════════════════════════════


st.markdown("""
    <div class="page-header">
        <h1>🟢 以太坊实时数据</h1>
        <p>ETH-USDT 实时行情 · K 线图表 · 秒级~15天 多周期 · 自动刷新</p>
    </div>
""", unsafe_allow_html=True)

inject_mask_hider_js()

cfg = get_config()

# ── Session state init ──
_ss("eth_ticker", None)
_ss("eth_data", None)
_ss("eth_timeframe", DEFAULT_TF_LABEL)
_ss("eth_data_count", 120)
_ss("eth_last_refresh", None)
_ss("eth_auto_refresh", True)

# ════════════════════════════════════════════════════════════════
# TOOLBAR
# ════════════════════════════════════════════════════════════════

st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-title">⚙ 控制面板</div>', unsafe_allow_html=True)

tf_labels = list(TIMEFRAMES.keys())
cur_label = st.session_state.eth_timeframe
default_idx = tf_labels.index(
    cur_label) if cur_label in tf_labels else tf_labels.index(DEFAULT_TF_LABEL)

ctrl_cols = st.columns([1.8, 1.5, 0.8, 1.0])

with ctrl_cols[0]:
    selected_tf = st.selectbox(
        "K 线周期",
        tf_labels,
        index=default_idx,
        key="eth_tf_sel")

with ctrl_cols[1]:
    dc = st.slider(
        "K 线数量",
        20,
        300,
        st.session_state.eth_data_count,
        step=10,
        key="eth_dc_slider")

with ctrl_cols[2]:
    st.caption("")
    if st.button("🔄", use_container_width=True):
        st.session_state.eth_data = None
        st.rerun()

with ctrl_cols[3]:
    auto = st.checkbox(
        "自动刷新",
        value=st.session_state.eth_auto_refresh,
        key="eth_auto_refresh")

st.markdown('</div>', unsafe_allow_html=True)

# ── Detect widget changes → reset data ──
tf_changed = selected_tf != st.session_state.eth_timeframe
count_changed = dc != st.session_state.eth_data_count
if tf_changed or count_changed:
    st.session_state.eth_timeframe = selected_tf
    st.session_state.eth_data_count = dc
    st.session_state.eth_data = None
    st.rerun()

# Resolve keys
tf_label = st.session_state.eth_timeframe
tf_key = TIMEFRAMES.get(tf_label, "1d")
data_count = st.session_state.eth_data_count
is_seconds_mode = tf_key == "1s"

# ════════════════════════════════════════════════════════════════
# TRADINGVIEW CHART (OKX mode only — outside fragment, no rerun)
# ════════════════════════════════════════════════════════════════

if not is_seconds_mode:
    import streamlit.components.v1 as components

    _TV_INTERVALS = {
        "1m": "1", "2m": "2", "15m": "15",
        "1h": "60", "6h": "360", "12h": "720",
        "1d": "1D", "2d": "2D", "15d": "1W",
    }
    tv_interval = _TV_INTERVALS.get(tf_key, "15")
    tv_theme = st.session_state.get("theme_mode", "light")

    st.markdown(
        '<div class="section-card" style="padding:0;overflow:hidden;border-radius:12px;">',
        unsafe_allow_html=True,
    )
    tv_html = f"""
    <div class="tradingview-widget-container" style="margin:0;line-height:1;">
        <div id="tv-eth-chart"></div>
        <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
        <script type="text/javascript">
        new TradingView.widget({{
            "width": "100%",
            "height": 420,
            "symbol": "OKX:ETHUSDT",
            "interval": "{tv_interval}",
            "timezone": "Asia/Shanghai",
            "theme": "{tv_theme}",
            "style": "1",
            "locale": "zh_CN",
            "toolbar_bg": "#f1f3f6",
            "enable_publishing": false,
            "allow_symbol_change": false,
            "hide_top_toolbar": false,
            "save_image": false,
            "container_id": "tv-eth-chart",
            "studies": [
                "MASimple@tv-basicstudies"
            ]
        }});
        </script>
    </div>
    """
    components.html(tv_html, height=450)
    st.markdown('</div>', unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# DISPLAY — 读 session_state 渲染，不在 fragment 内，刷新不闪烁
# ════════════════════════════════════════════════════════════════

_tf_label = st.session_state.eth_timeframe
_t_key_display = TIMEFRAMES.get(_tf_label, "1d")
_sec_mode_display = _t_key_display == "1s"
_cached_df_display = st.session_state.get("eth_data")
_ticker_data_display = st.session_state.get("eth_ticker")
_last_refresh_display = st.session_state.get("eth_last_refresh", "")

if _ticker_data_display:
    _tk = _ticker_data_display
    _change_24h = _tk.get("change_24h", 0) or 0
    _price_color = "green" if _change_24h >= 0 else "red"
    _lp = _tk.get("last", 0) or 0
    _bid = _tk.get("bid", 0) or 0
    _ask = _tk.get("ask", 0) or 0

    st.markdown(f"""
    <div class="ticker-bar">
    <div class="ticker-item">
    <span class="ticker-label">ETH-USDT</span>
    <span class="ticker-value {_price_color}">${_lp:,.2f} {_fmt_change(_change_24h)}</span>
    </div>
    <div class="ticker-item">
    <span class="ticker-label">买一 / 卖一</span>
    <span class="ticker-value">{f'${_bid:,.2f}' if _bid else 'N/A'} / {f'${_ask:,.2f}' if _ask else 'N/A'}</span>
    </div>
    <div class="ticker-item">
    <span class="ticker-label">24h 最高 / 最低</span>
    <span class="ticker-value">{f'${_tk.get("high_24h", 0) or 0:,.2f}' if _tk.get("high_24h") else "N/A"} / {f'${_tk.get("low_24h", 0) or 0:,.2f}' if _tk.get("low_24h") else "N/A"}</span>
    </div>
    <div class="ticker-item">
    <span class="ticker-label">24h 成交量</span>
    <span class="ticker-value">{f'{_tk.get("volume_24h", 0) or 0:,.0f} ETH'}</span>
    </div>
    <div style="margin-left:auto; display:flex; align-items:center; gap:0.5rem;">
    <span class="badge badge--green">✅ 实时数据</span>
    <span style="color:#94a3b8; font-size:0.8rem;">{_last_refresh_display}</span>
    </div>
    </div>
    """, unsafe_allow_html=True)
elif _cached_df_display is None:
    st.info("💡 加载后将显示实时行情")

if _sec_mode_display:
    # ── 心跳模式显示 ──
    from data.heartbeat_db import HeartbeatDB as _HB, is_collector_running as _hb_running, read_status as _hb_status

    _running = _hb_running()
    _status = _hb_status() if _running else None
    _tick_count = _status.get("tick_count", 0) if _status else 0
    _uptime_str = _fmt_uptime(_status.get("started_at") if _status else None)
    _tps = "0"
    _sa = _status.get("started_at") if _status else None
    if _sa and _tick_count:
        try:
            _elapsed = max(1, (datetime.now(timezone.utc) - datetime.fromisoformat(_sa)).total_seconds())
            _tps = f"{_tick_count / _elapsed:.1f}"
        except Exception:
            pass
    _dot_bg = "#22c55e" if _running else "#94a3b8"
    _status_icon = "💓 运行中" if _running else "⏸ 已停止"
    _pulse_style = "animation:pulse 1.5s infinite;" if _running else ""

    st.markdown(f"""
        <style>
        @keyframes pulse {{ 0%{{box-shadow:0 0 0 0 rgba(34,197,94,0.7);}} 70%{{box-shadow:0 0 0 10px rgba(34,197,94,0);}} 100%{{box-shadow:0 0 0 0 rgba(34,197,94,0);}} }}
        </style>
        <div class="section-card" style="padding:0.55rem 1rem;">
        <div style="display:flex;align-items:center;gap:1.2rem;flex-wrap:wrap;font-size:0.82rem;">
        <span style="display:flex;align-items:center;gap:5px;">
        <span style="width:10px;height:10px;border-radius:50%;background:{_dot_bg};{_pulse_style}display:inline-block;"></span>
        <span style="font-weight:600;">{_status_icon}</span>
        </span>
        <span style="color:var(--text-muted);">心跳 <strong style="color:var(--text-primary);">{_tick_count:,}</strong></span>
        <span style="color:var(--text-muted);">速率 <strong style="color:var(--text-primary);">{_tps} /s</strong></span>
        <span style="color:var(--text-muted);">运行时长 <strong style="color:var(--text-primary);">{_uptime_str}</strong></span>
        <span style="margin-left:auto;color:var(--text-muted);">周期 <strong style="color:var(--text-primary);">1秒</strong></span>
        </div>
        </div>
        """, unsafe_allow_html=True)

    if not _running:
        st.warning("💓 心跳采集器未运行。秒级数据需要 WebSocket 心跳采集器。")

    if _cached_df_display is not None and not _cached_df_display.empty:
        _last_price_hb = float(_cached_df_display["close"].iloc[-1])
        _prev_price_hb = st.session_state.get("eth_hb_prev_price", _last_price_hb)
        st.session_state.eth_hb_prev_price = _last_price_hb
        _price_dir = "up" if _last_price_hb >= _prev_price_hb else "down"
        _prev_close_hb = float(_cached_df_display["close"].iloc[-2]) if len(_cached_df_display) > 1 else _last_price_hb
        _chg_hb = (_last_price_hb - _prev_close_hb) / _prev_close_hb * 100 if _prev_close_hb else 0
        _price_color_hb = "#059669" if _price_dir == "up" else "#dc2626"
        _bg_chg = "#d1fae5" if _chg_hb >= 0 else "#fee2e2"
        _fg_chg = "#065f46" if _chg_hb >= 0 else "#991b1b"
        _bid_hb = _ticker_data_display.get("bid") if _ticker_data_display else None
        _ask_hb = _ticker_data_display.get("ask") if _ticker_data_display else None
        _spread_hb = (_ask_hb - _bid_hb) / _bid_hb * 100 if _bid_hb and _ask_hb else 0

        st.markdown(f"""
        <div style="text-align:center;padding:1.5rem 1rem 1rem;background:var(--bg-card);border-radius:12px;border:1px solid var(--border);margin-bottom:1rem;">
        <div style="font-size:3rem;font-weight:700;color:{_price_color_hb};font-variant-numeric:tabular-nums;line-height:1.1;">
        ${_last_price_hb:,.2f}
        </div>
        <div style="display:flex;justify-content:center;gap:1.5rem;margin-top:0.5rem;">
        <span style="font-size:0.95rem;color:var(--text-muted);">24h <span style="display:inline-block;padding:1px 12px;border-radius:999px;background:{_bg_chg};color:{_fg_chg};font-weight:600;">{_chg_hb:+.2f}%</span></span>
        <span style="font-size:0.95rem;color:var(--text-muted);">💓 {_tps} ticks/s</span>
        </div>
        <div style="display:flex;justify-content:center;gap:1rem;margin-top:0.75rem;font-size:0.85rem;color:var(--text-secondary);">
        <span style="background:var(--bg-input);padding:2px 10px;border-radius:6px;">买一 <strong>${_bid_hb:,.2f}</strong></span>
        <span style="background:var(--bg-input);padding:2px 10px;border-radius:6px;">卖一 <strong>${_ask_hb:,.2f}</strong></span>
        <span style="background:var(--bg-input);padding:2px 10px;border-radius:6px;">价差 <strong>{_spread_hb:.3f}%</strong></span>
        </div>
        </div>
        """, unsafe_allow_html=True)

        # Sparkline
        _hb_db = _HB()
        try:
            _recent_ticks = _hb_db.get_recent_ticks(limit=120)
        finally:
            _hb_db.close()
        if _recent_ticks:
            _spark_fig = _build_sparkline(_recent_ticks, height=110, theme=st.session_state.get("theme_mode", "light"))
            st.markdown('<div class="section-card" style="padding:0.5rem;">', unsafe_allow_html=True)
            st.plotly_chart(_spark_fig, use_container_width=True, config={"displayModeBar": False})
            st.markdown('</div>', unsafe_allow_html=True)

            _prices_hb = [t["price"] for t in _recent_ticks if t.get("price")]
            if _prices_hb:
                _kpi_cols = st.columns(5)
                with _kpi_cols[0]:
                    st.metric("时段最高", f"${max(_prices_hb):,.2f}")
                with _kpi_cols[1]:
                    st.metric("时段最低", f"${min(_prices_hb):,.2f}")
                with _kpi_cols[2]:
                    st.metric("时段波幅", f"${max(_prices_hb) - min(_prices_hb):,.2f}")
                with _kpi_cols[3]:
                    st.metric("心跳总数", f"{_tick_count:,}")
                with _kpi_cols[4]:
                    st.metric("采集速率", f"{_tps}/s")

                with st.expander("📋 最近心跳记录", expanded=True):
                    _rows = []
                    for t in _recent_ticks[:50]:
                        try:
                            _ts_str = datetime.fromisoformat(t["ts"]).strftime("%H:%M:%S.%f")[:10]
                        except Exception:
                            _ts_str = str(t.get("ts", ""))[:10]
                        _rows.append({
                            "时间": _ts_str,
                            "价格": f"${t['price']:,.2f}",
                            "买一": f"${t.get('bid', 0):,.2f}" if t.get("bid") else "-",
                            "卖一": f"${t.get('ask', 0):,.2f}" if t.get("ask") else "-",
                            "24h涨跌": f"{t.get('change_24h', 0):+.2f}%" if t.get("change_24h") is not None else "-",
                        })
                    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
                    st.caption(f"显示最近 {min(50, len(_recent_ticks))} / 共 {_HB().count_ticks():,} 条心跳记录")

                if _cached_df_display is not None and not _cached_df_display.empty:
                    with st.expander("📄 秒级 K 线数据"):
                        _dd = _cached_df_display.copy()
                        _dd.index = _dd.index.strftime("%Y-%m-%d %H:%M:%S")
                        _dd = _dd.rename(columns={"open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "笔数"})
                        st.dataframe(_dd.iloc[::-1], use_container_width=True)
                        st.download_button("📥 导出 CSV", _cached_df_display.to_csv(index=True).encode("utf-8"),
                                           "eth_heartbeat_candles.csv", "text/csv", use_container_width=True)
elif _cached_df_display is not None and not _cached_df_display.empty:
    # ── OKX 模式显示 ──
    _df = _cached_df_display
    _tf_display = _t_key_display
    _ticker_last = _ticker_data_display.get("last") if _ticker_data_display else None
    _last_price_val = _ticker_last if _ticker_last else float(_df["close"].iloc[-1])
    _prev_val = float(_df["close"].iloc[-2]) if len(_df) > 1 else _last_price_val
    _chg = (_last_price_val - _prev_val) / _prev_val * 100 if _prev_val else 0
    _hv = _ticker_data_display.get("high_24h", float(_df["high"].max())) if _ticker_data_display else float(_df["high"].max())
    _lv = _ticker_data_display.get("low_24h", float(_df["low"].min())) if _ticker_data_display else float(_df["low"].min())
    _ch24 = _ticker_data_display.get("change_24h") if _ticker_data_display else _chg

    st.markdown(f"""
    <div class="status-bar">
    <div class="status-item"><span class="status-dot"></span><span style="font-weight:600;color:#059669;">自动刷新</span></div>
    <div class="status-item"><span style="color:#64748b;">数据源</span><span style="font-weight:700;">OKX · TradingView</span></div>
    <div class="status-item"><span style="color:#64748b;">周期</span><span style="font-weight:600;">{_friendly_tf(_tf_display)}</span></div>
    <div class="status-item"><span style="color:#64748b;">K 线数</span><span style="font-weight:600;">{len(_df)}</span></div>
    <div class="status-item" style="margin-left:auto;"><span style="color:#64748b;">刷新 {TIMEFRAME_REFRESH_S.get(_tf_display, 10)}s</span></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📊 数据统计</div>', unsafe_allow_html=True)
    _kpi_cols = st.columns(6)
    with _kpi_cols[0]: render_metric_card("eth_price", _last_price_val)
    with _kpi_cols[1]: render_metric_card("eth_high_24h", _hv)
    with _kpi_cols[2]: render_metric_card("eth_low_24h", _lv)
    with _kpi_cols[3]: render_metric_card("eth_volume_24h", float(_df["volume"].sum()))
    with _kpi_cols[4]: render_metric_card("eth_change_24h", _ch24)
    with _kpi_cols[5]: render_metric_card("eth_range_24h", f"${float(_df['low'].min()):,.2f} ~ ${float(_df['high'].max()):,.2f}")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📋 市场详情</div>', unsafe_allow_html=True)
    _b = _ticker_data_display.get("bid") if _ticker_data_display else None
    _a = _ticker_data_display.get("ask") if _ticker_data_display else None
    _cols = st.columns(4)
    with _cols[0]: st.metric("最新价", f"${_last_price_val:,.2f}", f"{_chg:+.2f}%" if abs(_chg) > 0.01 else None)
    with _cols[1]: st.metric("买一价", f"${_b:,.2f}" if _b else "N/A")
    with _cols[2]: st.metric("卖一价", f"${_a:,.2f}" if _a else "N/A")
    with _cols[3]:
        _spread = (_a - _b) / _b * 100 if (_a and _b and _b) else 0
        st.metric("价差", f"{_spread:.4f}%" if _spread > 0 else "N/A")
    _cp = ((_last_price_val - float(_df["close"].iloc[0])) / float(_df["close"].iloc[0]) * 100) if len(_df) > 1 else 0
    _cols2 = st.columns(4)
    with _cols2[0]: st.metric("总成交量 (时段)", f"{float(_df['volume'].sum()):,.0f}")
    with _cols2[1]: st.metric("平均成交量", f"{float(_df['volume'].mean()):,.1f}")
    with _cols2[2]: st.metric("期间涨跌幅", f"{_cp:+.2f}%" if len(_df) > 1 else "N/A", delta_color="normal" if _cp >= 0 else "inverse")
    with _cols2[3]: st.metric("K 线数量", len(_df))
    st.markdown('</div>', unsafe_allow_html=True)

    with st.expander("📄 查看原始 K 线数据"):
        _dd = _df.copy()
        _dd.index = _dd.index.strftime("%Y-%m-%d %H:%M:%S")
        _dd = _dd.rename(columns={"open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "成交量"})
        st.dataframe(_dd.iloc[::-1], use_container_width=True)
        st.download_button("📥 导出 CSV", _df.to_csv(index=True).encode("utf-8"), "eth_usdt_klines.csv", "text/csv", use_container_width=True)

# ════════════════════════════════════════════════════════════════
# DATA FRAGMENT — 仅获取数据，无显示，全页刷新更新显示
# ════════════════════════════════════════════════════════════════

refresh_interval_s = TIMEFRAME_REFRESH_S.get(tf_key, 5) if auto else None


@st.fragment(run_every=refresh_interval_s)
def _data_fragment():
    """仅获取数据写入 session_state，然后 st.rerun() 全页刷新更新显示。
    显示代码在 fragment 外，故刷新期间旧数据显示始终不变，不产生蒙版。
    """
    # Guard: 如果是自己的 st.rerun() 触发的全页重跑，跳过
    if st.session_state.get("_eth_rerun_guard"):
        st.session_state._eth_rerun_guard = False
        return

    _tf_label = st.session_state.eth_timeframe
    _t_key = TIMEFRAMES.get(_tf_label, "1d")
    _d_count = st.session_state.eth_data_count
    _sec_mode = _t_key == "1s"

    # Ticker
    try:
        _ticker_data = fetch_ticker(cfg, symbol=ETH_SYMBOL)
        st.session_state.eth_ticker = _ticker_data
    except Exception:
        if st.session_state.get("eth_data") is None:
            st.warning("获取 ticker 失败")

    # K 线
    if _sec_mode:
        from data.heartbeat_db import HeartbeatDB, is_collector_running, start_collector
        if not is_collector_running():
            ok = start_collector()
            if ok:
                _time.sleep(1.5)
        _db = HeartbeatDB()
        try:
            _new_df = _db.get_second_candles(limit=_d_count)
            if _new_df is not None and not _new_df.empty:
                st.session_state.eth_data = _new_df
        except Exception as e:
            if st.session_state.get("eth_data") is None:
                st.error(f"读取心跳数据失败: {e}")
        finally:
            _db.close()
    else:
        try:
            _new_df = fetch_klines_with_agg(cfg, limit=_d_count, timeframe=_t_key, symbol=ETH_SYMBOL)
            if _new_df is not None and not _new_df.empty:
                st.session_state.eth_data = _new_df
        except Exception as e:
            if st.session_state.get("eth_data") is None:
                st.error(f"获取数据失败: {e}")

    st.session_state.eth_last_refresh = datetime.now().strftime("%H:%M:%S")
    _df = st.session_state.get("eth_data")

    # BacktestEngine 喂数据
    if st.session_state.get("ai_running") and _df is not None and not _df.empty:
        _engine = st.session_state.get("ai_executor")
        if _engine is not None:
            _buf = _engine.bar_buffer
            if not _buf.empty:
                _last_processed = _buf.index[-1]
                _new_bars = _df[_df.index > _last_processed]
            else:
                _new_bars = _df
            if not _new_bars.empty:
                for _, _bar in _new_bars.iterrows():
                    _engine.on_bar(_bar)
                st.session_state.ai_trade_state = _engine.get_state()

    # 触发全页重跑 → 更新 fragment 外的显示代码
    if auto:
        st.session_state._eth_rerun_guard = True
        st.rerun()


# ════════════════════════════════════════════════════════════════
# AI 多空分析 — 基于多维度数据的智能分析
# ════════════════════════════════════════════════════════════════

_ss("ai_analysis_result", None)
_ss("ai_analysis_error", None)
_ss("ai_news", None)
_ss("ai_running", False)
_ss("ai_use_live_mode", False)
_ss("ai_initial_balance", 10000.0)
_ss("ai_chat_context", None)
_ss("ai_chat_messages", [])
_ss("ai_chat_loading", False)

with st.container():
    st.markdown("---")
    st.markdown("### 🤖 AI 多空分析")
    st.markdown(
        "基于K线数据、成交量、历史走势、关联币种表现及最新新闻政策综合判断，"
        "点击按钮后自动采集数据+新闻并调用AI给出多空建议。"
    )

    btn_cols = st.columns([3, 1.5, 1.5])
    with btn_cols[1]:
        analyze_btn = st.button("📊 开始分析", type="primary", use_container_width=True)
    with btn_cols[2]:
        if st.button("🗑 清除结果", use_container_width=True):
            st.session_state.ai_analysis_result = None
            st.session_state.ai_analysis_error = None
            st.session_state.ai_news = None
            st.session_state.ai_chat_context = None
            st.session_state.ai_chat_messages = []
            st.session_state.ai_chat_loading = False
            st.rerun()

    # ── 执行分析 ──
    if analyze_btn:
        st.session_state.ai_analysis_result = None
        st.session_state.ai_analysis_error = None
        st.session_state.ai_chat_loading = False
        try:
            with st.status("🤖 正在分析…", expanded=True) as _status:
                _status.update(label="📡 获取 ETH 行情数据…")
                _tk = fetch_ticker(cfg, symbol="ETH-USDT")

                _status.update(label="📊 获取技术指标数据…")
                _k15 = fetch_klines_with_agg(cfg, limit=30, timeframe="15m", symbol="ETH-USDT")
                _k1h = fetch_klines_with_agg(cfg, limit=20, timeframe="1h", symbol="ETH-USDT")
                _k1d = fetch_klines_with_agg(cfg, limit=60, timeframe="1d", symbol="ETH-USDT")

                _status.update(label="🔄 获取关联币种行情…")
                _btc = fetch_ticker(cfg, symbol="BTC-USDT")
                _sol = fetch_ticker(cfg, symbol="SOL-USDT")
                _doge = fetch_ticker(cfg, symbol="DOGE-USDT")

                _status.update(label="📰 采集新闻与政策信息…")
                _news = _fetch_crypto_news()
                st.session_state.ai_news = _news

                # 保存对话上下文（市场数据快照）
                _mk = (
                    f"### 实时行情\n{_ticker_summary('ETH', _tk)}\n\n"
                    f"{_summarize_klines(_k15, '短期(15分钟)')}\n"
                    f"{_summarize_klines(_k1h, '中期(1小时)')}\n"
                    f"{_summarize_klines(_k1d, '长期(日线)')}\n\n"
                    f"### 关联币种\n{_ticker_summary('BTC', _btc)}\n"
                    f"{_ticker_summary('SOL', _sol)}\n"
                    f"{_ticker_summary('DOGE', _doge)}"
                )
                st.session_state.ai_chat_context = {
                    "market_summary": _mk,
                    "news": _news,
                    "analysis_result": None,
                }
                st.session_state.ai_chat_messages = []

                _status.update(label="🧠 AI 正在综合分析（技术面+基本面）…")
                _result = _call_ai_analysis(
                    ticker=_tk,
                    klines_15m=_k15,
                    klines_1h=_k1h,
                    klines_1d=_k1d,
                    btc_ticker=_btc,
                    sol_ticker=_sol,
                    doge_ticker=_doge,
                    cfg=cfg,
                    news=_news,
                )
                st.session_state.ai_analysis_result = _result
                if st.session_state.get("ai_chat_context"):
                    st.session_state.ai_chat_context["analysis_result"] = _result

                _status.update(label="✅ 分析完成", state="complete")
        except Exception as e:
            st.session_state.ai_analysis_error = str(e)
        st.rerun()

    # ── 错误状态 ──
    _err = st.session_state.get("ai_analysis_error")
    if _err:
        st.error(f"❌ 分析失败: {_err}")

    # ── 结果显示 ──
    _raw_news = st.session_state.get("ai_news", [])
    _res = st.session_state.get("ai_analysis_result")
    if _res:
        _dir = _res.get("direction", "neutral")
        _conf = _res.get("confidence", 0)
        if _dir == "long":
            _dir_color = "#059669"
            _dir_icon = "📈"
            _dir_text = "看多"
        elif _dir == "short":
            _dir_color = "#dc2626"
            _dir_icon = "📉"
            _dir_text = "看空"
        else:
            _dir_color = "#64748b"
            _dir_icon = "⚖️"
            _dir_text = "中性"
        _conf_color = "#059669" if _conf >= 70 else "#f59e0b" if _conf >= 40 else "#94a3b8"
        _ev_html = "".join(
            f'<li style="margin-bottom:0.3rem;">{_sanitize_ai_text(e)}</li>' for e in _res.get("key_evidence", []))
        _risk_html = "".join(
            f'<li style="margin-bottom:0.3rem;">{_sanitize_ai_text(r)}</li>' for r in _res.get("risk_warnings", []))

        _fund_news_html = ""
        if _raw_news:
            _news_items = "".join(
                f'<li style="margin-bottom:0.25rem;color:var(--text-muted);font-size:0.85rem;">'
                f'<span style="color:var(--text-primary);font-weight:500;">[{_sanitize_ai_text(n["source"])}]</span>'
                f'<span style="color:var(--text-muted);font-size:0.75rem;">{_fmt_relative_time(n.get("timestamp", ""))}</span> '
                f'{_sanitize_ai_text(n["title"])}</li>'
                for n in _raw_news
            )
            _fund_news_html = "\n".join([
                '<details style="margin-top:0.75rem;">',
                '<summary style="color:var(--text-muted);font-size:0.85rem;cursor:pointer;">',
                f'  📡 参考新闻（{len(_raw_news)}条）',
                '</summary>',
                f'<ul style="margin:0.5rem 0 0 0;padding-left:1.2rem;">{_news_items}</ul>',
                '</details>',
            ])

        st.markdown(f"""
        <div style="border:1px solid var(--border);border-radius:12px;padding:1.25rem;background:var(--bg-card);margin-top:0.5rem;">
            <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;">
                <span style="font-size:1.8rem;">{_dir_icon}</span>
                <span style="font-size:1.5rem;font-weight:700;color:{_dir_color};">{_dir_text}</span>
                <div style="margin-left:auto;display:flex;align-items:center;gap:0.5rem;">
                    <span style="color:var(--text-muted);font-size:0.85rem;">信心指数</span>
                    <span style="font-size:1.3rem;font-weight:700;color:{_conf_color};">{_conf}%</span>
                </div>
            </div>
            <p style="color:var(--text-secondary);font-size:0.95rem;margin-bottom:1rem;">{_sanitize_ai_text(_res.get("summary", ""))}</p>
            <div style="margin-bottom:1rem;">
                <p style="font-weight:600;color:var(--text-primary);margin-bottom:0.4rem;">📌 关键依据</p>
                <ul style="margin:0;padding-left:1.2rem;color:var(--text-secondary);font-size:0.9rem;">{_ev_html}</ul>
            </div>
            <div style="margin-bottom:1rem;">
                <p style="font-weight:600;color:var(--text-primary);margin-bottom:0.4rem;">⚠️ 风险提示</p>
                <ul style="margin:0;padding-left:1.2rem;color:var(--red);font-size:0.9rem;">{_risk_html}</ul>
            </div>
            <div style="display:flex;gap:1rem;flex-wrap:wrap;">
                <div style="flex:1;min-width:200px;background:var(--bg-card-hover);border-radius:8px;padding:0.75rem;">
                    <p style="font-weight:600;color:var(--text-primary);font-size:0.85rem;margin-bottom:0.3rem;">🔬 技术面</p>
                    <p style="color:var(--text-secondary);font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("technical_analysis", "")) or "—"}</p>
                </div>
                <div style="flex:1;min-width:200px;background:var(--bg-card-hover);border-radius:8px;padding:0.75rem;">
                    <p style="font-weight:600;color:var(--text-primary);font-size:0.85rem;margin-bottom:0.3rem;">🌊 市场情绪</p>
                    <p style="color:var(--text-secondary);font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("market_sentiment", "")) or "—"}</p>
                </div>
                <div style="flex:1;min-width:200px;background:var(--bg-card-hover);border-radius:8px;padding:0.75rem;">
                    <p style="font-weight:600;color:var(--text-primary);font-size:0.85rem;margin-bottom:0.3rem;">📰 基本面</p>
                    <p style="color:var(--text-secondary);font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("fundamental_analysis", "")) or "—"}</p>
                </div>
            </div>
            {_fund_news_html}
        </div>
        """, unsafe_allow_html=True)

    # ── AI 信号 → 交易执行 ──
    if not st.session_state.ai_running:
        if st.button("⚡ 按此信号交易", type="primary", use_container_width=True):
            # Inline ai_signal_to_rules — convert AI analysis to rules
            _dir = _res.get("direction", "neutral")
            _dir_label = "看多" if _dir == "long" else "看空" if _dir == "short" else "中性"
            rules = {
                "strategy_name": f"AI信号-{_dir_label}",
                "_strategy_type": "ai_signal",
                "timeframe_hint": "15m",
                "entry_conditions": [],
                "exit_conditions": [],
                "risk_params": {
                    "stop_loss_pct": 1.5, "take_profit_pct": 3.0,
                    "max_loss_pct": 3.0, "leverage": 1.0,
                    "position_timeout_bars": 96,
                    "trailing_stop_activation_pct": 2.0,
                    "trailing_stop_distance_pct": 1.25,
                },
                "ai_signal": {
                    "original_direction": _dir,
                    "confidence": _res.get("confidence", 0),
                    "summary": _res.get("summary", ""),
                    "key_evidence": _res.get("key_evidence", []),
                    "risk_warnings": _res.get("risk_warnings", []),
                    "technical_analysis": _res.get("technical_analysis", ""),
                    "market_sentiment": _res.get("market_sentiment", ""),
                    "fundamental_analysis": _res.get("fundamental_analysis", ""),
                },
                "_notes": f"AI信号: 信心指数{_res.get('confidence', 0)}%",
            }
            _df = st.session_state.get("eth_data")
            if _df is None or _df.empty:
                st.error("❌ 暂无K线数据，请等待数据加载")
                st.stop()
            from frontend.utils.backtest_engine import BacktestEngine
            executor = BacktestEngine(
                rules=rules,
                initial_balance=st.session_state.ai_initial_balance,
                mode="live" if st.session_state.ai_use_live_mode else "paper",
            )
            # 预热：跳过入场，只加载K线到缓冲区
            executor.ai_signal_skip_entry = True
            if _df is not None and not _df.empty:
                for _, bar in _df.iterrows():
                    executor.on_bar(bar)
            executor.ai_signal_skip_entry = False
            st.session_state.ai_strategy_rules = rules
            st.session_state.ai_executor = executor
            st.session_state.ai_running = True
            st.session_state.ai_trade_state = executor.get_state()
            st.rerun()
    else:
        st.info("⚠️ AI交易运行中，请先停止")

    # ── 💬 AI 追问对话 ──
    if _res:
        st.markdown("---")
        st.markdown("#### 💬 追问分析")

        chat_container = st.container()
        with chat_container:
            for i, msg in enumerate(st.session_state.ai_chat_messages):
                with st.chat_message(msg["role"]):
                    if msg["role"] == "assistant" and i == len(st.session_state.ai_chat_messages) - 1:
                        # 最新回答 — 带滑入动效
                        st.markdown(
                            f'<div class="fade-in-answer">{_sanitize_ai_text(msg["content"])}</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(_sanitize_ai_text(msg["content"]))

        if st.session_state.ai_chat_loading:
            with st.chat_message("assistant"):
                st.markdown(
                    '<div style="display:flex;align-items:center;gap:8px;">'
                    '<span>🤔</span>'
                    '<span class="typing-indicator">'
                    '<span class="dot"></span>'
                    '<span class="dot"></span>'
                    '<span class="dot"></span>'
                    '</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )

        user_input = st.chat_input("对当前市场分析提问…（例如：为什么看空？ETH支撑位在哪？）",
                                   disabled=st.session_state.ai_chat_loading)
        if user_input and not st.session_state.ai_chat_loading:
            st.session_state.ai_chat_messages.append(
                {"role": "user", "content": user_input}
            )
            st.session_state.ai_chat_loading = True
            st.rerun()

        # 两步渲染：问题先展示，回答好了再滑入
        if st.session_state.ai_chat_loading and st.session_state.ai_chat_messages and st.session_state.ai_chat_messages[-1]["role"] == "user":
            pending_question = st.session_state.ai_chat_messages[-1]["content"]
            context = st.session_state.get("ai_chat_context")
            answer = _call_ai_chat(
                pending_question, context,
                st.session_state.ai_chat_messages, cfg,
            )
            st.session_state.ai_chat_messages.append(
                {"role": "assistant", "content": answer}
            )
            st.session_state.ai_chat_loading = False
            st.rerun()


# ════════════════════════════════════════════════════════════════
# AI TRADING — natural-language strategy with auto-execution
# ════════════════════════════════════════════════════════════════

_ss("ai_strategy_text", "")
_ss("ai_strategy_rules", None)
_ss("ai_executor", None)
_ss("ai_trade_state", None)
_ss("ai_running", False)
_ss("ai_use_live_mode", False)
_ss("ai_initial_balance", 10000.0)
_ss("ai_interpret_error", None)

with st.expander("🤖 AI 交易", expanded=bool(st.session_state.ai_running)):
    st.markdown("输入交易策略的自然语言描述，AI 将解析为可执行规则并自动交易。")

    col_desc, col_ctrl = st.columns([3, 1])

    with col_desc:
        # 快捷模板
        tmpl_cols = st.columns([1, 1, 2])
        with tmpl_cols[0]:
            if st.button("📊 波动率反向", use_container_width=True,
                         disabled=st.session_state.ai_running):
                text = (
                    "15分钟K线实体大于15美元时反向开仓，"
                    "连续2根实体之和大于20美元也触发。"
                    "止损1.25%，多级移动止盈(1.25%保本/2.5%移动+1.25%/5%平半仓移动+2.5%)，"
                    "5倍杠杆，单笔最大亏损3%，同方向2小时冷却"
                )
                st.session_state.ai_strategy_text = text
                st.session_state.ai_text_input = text
                st.rerun()
        with tmpl_cols[1]:
            if st.button("📈 RSI均值回归", use_container_width=True,
                         disabled=st.session_state.ai_running):
                text = "RSI低于30买入，高于70卖出，止损2%，用10%资金"
                st.session_state.ai_strategy_text = text
                st.session_state.ai_text_input = text
                st.rerun()

        ai_text = st.text_area(
            "描述你的交易策略",
            value=st.session_state.ai_strategy_text,
            height=120,
            placeholder=(
                "例如：\n"
                "• RSI低于30买入，高于70卖出，止损2%，用10%资金\n"
                "• MA5金叉MA10买入，死叉卖出，止损3%\n"
                "• MACD金叉买入，移动止损2%，半仓\n"
                "• 价格突破60日均线买入，跌破60均线卖出，止盈5%\n"
                "• 连续3根阳线买入，连续2根阴线卖出\n"
                "• RSI低于30且MACD金叉买入\n"
                "• 布林带下轨买入，上轨卖出"
            ),
            key="ai_text_input",
        )

    with col_ctrl:
        st.markdown(
            "<div style='height:1.5rem'></div>",
            unsafe_allow_html=True)
        if st.button("🔮 解析策略", use_container_width=True, type="primary",
                     disabled=st.session_state.ai_running):
            if ai_text.strip():
                with st.spinner("🤖 AI 正在解析策略…"):
                    from frontend.utils.strategy_parser import parse_strategy_text
                    rules = parse_strategy_text(ai_text)
                if "parse_error" in rules:
                    st.session_state.ai_interpret_error = rules["parse_error"]
                    st.session_state.ai_strategy_rules = None
                else:
                    st.session_state.ai_strategy_rules = rules
                    st.session_state.ai_strategy_text = ai_text
                    st.session_state.ai_interpret_error = None
                st.rerun()
            else:
                st.warning("请先输入策略描述")

        # Mode toggle
        st.session_state.ai_use_live_mode = st.checkbox(
            "实盘模式", value=st.session_state.ai_use_live_mode,
            disabled=st.session_state.ai_running,
            help="启用时使用 OKX API 直接下单（需 Trade 权限）",
        )

    # ── 解析结果显示 ──
    ai_rules = st.session_state.ai_strategy_rules
    ai_err = st.session_state.ai_interpret_error

    if ai_err:
        st.error(f"❌ {ai_err}")

    if ai_rules and ai_err is None:
        st.markdown("---")
        st.markdown(f"**📋 {ai_rules.get('strategy_name', 'AI策略')}**")
        if ai_rules.get("timeframe_hint"):
            st.caption(f"⏱ 建议周期: {ai_rules['timeframe_hint']}")

        # 入场条件
        entry = ai_rules.get("entry_conditions", [])
        exit_c = ai_rules.get("exit_conditions", [])
        risk = ai_rules.get("risk_params", {})

        cond_cols = st.columns(3)
        with cond_cols[0]:
            st.markdown("**📈 入场条件**")
            cond_logic = ai_rules.get("_condition_logic", "any")
            if entry:
                label = "（需全部满足）" if cond_logic == "all" else "（任一满足即入）"
                st.caption(label)
                for i, c in enumerate(entry):
                    period = c.get('params', {}).get('period', '')
                    comp = c.get('comparison', '')
                    comp_symbol = {
                        'less_than': '<', 'greater_than': '>',
                        'crosses_above': '上穿', 'crosses_below': '下穿',
                        'greater_or_equal': '≥', 'less_or_equal': '≤',
                        'consecutive_gain': '连涨', 'consecutive_loss': '连跌',
                        'touches': '≈',
                    }.get(comp, comp)
                    val = c.get('value', '') or ''
                    cross = c.get('cross_with', '')
                    if cross:
                        pnl = f" {comp_symbol} {cross.upper()}"
                    elif val:
                        pnl = f" {comp_symbol} {val}"
                    else:
                        pnl = f" {comp_symbol}"
                    st.markdown(
                        f"{i + 1}. {c.get('indicator', '').upper()}{period}{pnl}"
                        f" → {'买入' if c.get('action') == 'buy' else '卖出'}"
                    )
            else:
                st.markdown("*(无入场条件)*")
        with cond_cols[1]:
            st.markdown("**📉 出场条件**")
            if exit_c:
                for i, c in enumerate(exit_c):
                    period = c.get('params', {}).get('period', '')
                    comp = c.get('comparison', '')
                    comp_symbol = {
                        'less_than': '<', 'greater_than': '>',
                        'crosses_above': '上穿', 'crosses_below': '下穿',
                        'greater_or_equal': '≥', 'less_or_equal': '≤',
                        'consecutive_gain': '连涨', 'consecutive_loss': '连跌',
                    }.get(comp, comp)
                    val = c.get('value', '') or ''
                    cross = c.get('cross_with', '')
                    if cross:
                        pnl = f" {comp_symbol} {cross.upper()}"
                    elif val:
                        pnl = f" {comp_symbol} {val}"
                    else:
                        pnl = f" {comp_symbol}"
                    st.markdown(
                        f"{i + 1}. {c.get('indicator', '').upper()}{period}{pnl}"
                        f" → {'卖出' if c.get('action') == 'sell' else '买入'}"
                    )
            else:
                st.markdown("*(止盈/止损硬出场)*")
        with cond_cols[2]:
            st.markdown("**🛡 风控参数**")
            sl = risk.get("stop_loss_pct")
            tp = risk.get("take_profit_pct")
            ps = risk.get("position_size_pct", 10.0)
            ts_act = risk.get("trailing_stop_activation_pct")
            ts_dist = risk.get("trailing_stop_distance_pct")
            timeout = risk.get("position_timeout_bars")

            st.markdown(f"- 仓位: {ps:.0f}%")
            st.markdown(f"- 止损: {f'{sl:.1f}%' if sl else '无'}")
            st.markdown(f"- 止盈: {f'{tp:.1f}%' if tp else '无'}")
            if ts_dist:
                st.markdown(f"- 移动止损: 激活+{ts_act:.1f}% / 回落{ts_dist:.1f}%")
            if timeout:
                st.markdown(f"- 持仓超时: {timeout}根K线")

            # 波动率策略专属参数显示
            if ai_rules.get("_strategy_type") == "volatility_contrarian":
                st.markdown("---")
                st.markdown("**⚡ 波动率策略参数**")
                lev = risk.get("leverage", 5)
                max_loss = risk.get("max_loss_pct", 3)
                body_t = risk.get("volatility_body_threshold", 15)
                sum_t = risk.get("volatility_sum_threshold", 20)
                cool = risk.get("cooldown_bars", 8)
                st.markdown(
                    f"- 杠杆: {lev}x | 单笔最大亏损: {max_loss}%\n"
                    f"- 触发: 单根实体 > ${body_t} 或 连续2根之和 > ${sum_t}\n"
                    f"- 方向: 反向开仓（阴线→做多，阳线→做空）\n"
                    f"- 同向冷却: {cool}根K线 | 连亏: 仓位减半"
                )

            # 策略备注
            notes = ai_rules.get("_notes", "")
            if notes:
                st.caption(f"📝 {notes}")

        # 控制按钮
        st.markdown("---")
        btn_cols = st.columns([2, 1, 2])
        with btn_cols[0]:
            bal = st.number_input(
                "初始资金 (USDT)",
                min_value=100.0, max_value=10_000_000.0,
                value=st.session_state.ai_initial_balance,
                step=1000.0,
                disabled=st.session_state.ai_running,
                key="ai_balance_input",
            )

        with btn_cols[1]:
            st.markdown(
                "<div style='height:1.5rem'></div>",
                unsafe_allow_html=True)
            if not st.session_state.ai_running:
                if st.button("▶ 启动", use_container_width=True, type="primary"):
                    _df = st.session_state.get("eth_data")
                    if _df is None or _df.empty:
                        st.error("暂无K线数据，请等待数据加载")
                        st.stop()

                    from frontend.utils.backtest_engine import BacktestEngine
                    executor = BacktestEngine(
                        rules=ai_rules,
                        initial_balance=st.session_state.ai_initial_balance,
                        mode="live" if st.session_state.ai_use_live_mode else "paper",
                    )

                    # 预热：加载已有K线数据
                    if _df is not None and not _df.empty:
                        for _, bar in _df.iterrows():
                            executor.on_bar(bar)

                    st.session_state.ai_executor = executor
                    st.session_state.ai_running = True
                    st.session_state.ai_trade_state = executor.get_state()
                    st.rerun()
            else:
                if st.button("⏹ 停止", use_container_width=True, type="primary"):
                    st.session_state.ai_running = False
                    state = st.session_state.get("ai_trade_state")
                    if state:
                        st.success(
                            f"已停止 | 交易 {
                                state.get(
                                    'total_trades',
                                    0)} 笔 | " f"权益 ${
                                state.get(
                                    'account',
                                    {}).get(
                                    'equity',
                                    0):,.2f}")
                    st.rerun()

        # ── 运行状态 ──
        if st.session_state.ai_running and st.session_state.ai_trade_state:
            state = st.session_state.ai_trade_state
            acc = state.get("account", {})

            st.markdown("---")
            st.markdown(f"**🤖 运行中 — {state.get('strategy_name', 'AI策略')}**")

            # KPI
            kpi = st.columns(6)
            with kpi[0]:
                st.metric("状态", "🟢 运行中" if state.get("running") else "⏸ 已停止")
            with kpi[1]:
                sig = state.get("signal", "hold")
                sig_emoji = {
                    "buy": "🟢",
                    "sell": "🔴",
                    "short": "🔴",
                    "hold": "⚪",
                    "blocked": "🟡"}
                st.metric("最新信号", f"{sig_emoji.get(sig, '⚪')} {sig.upper()}")
            with kpi[2]:
                ip = acc.get("in_position", False)
                ps = state.get("position_side", "")
                pos_label = "✅ 持仓中"
                if ip and ps == "long":
                    pos_label = "🟢 多头"
                elif ip and ps == "short":
                    pos_label = "🔴 空头"
                elif not ip:
                    pos_label = "⬜ 空仓"
                st.metric("持仓", pos_label)
            with kpi[3]:
                ep = state.get("entry_price", 0)
                st.metric("入场价", f"${ep:,.2f}" if ep > 0 else "-")
            with kpi[4]:
                equity = acc.get("equity", 0)
                init_bal = acc.get("initial_balance", 0)
                pnl = equity - init_bal
                pnl_color = "green" if pnl >= 0 else "red"
                st.markdown(
                    f'<div style="padding:0.5rem 0;"><span style="color:#64748b;font-size:0.8rem;">总 P&L</span>'
                    f'<div style="font-size:1.3rem;font-weight:700;color:{pnl_color};">${pnl:+,.2f}</div></div>',
                    unsafe_allow_html=True,
                )
            with kpi[5]:
                st.metric("交易次数", state.get("total_trades", 0))

            # 信号原因
            if state.get("signal_reason"):
                st.info(
                    f"📡 最近信号: {
                        state['signal'].upper()} — {
                        state['signal_reason']}")

            # 多级止盈 + 冷却状态（波动率策略专属）
            if state.get("position_side") and state.get("in_position"):
                mtp = state.get("multi_tp_level", 0)
                dsp = state.get("dynamic_stop_price", 0)
                pcd = state.get("partial_close_done", False)
                mtp_labels = {
                    0: "未激活",
                    1: "保本",
                    2: "+1.25%",
                    3: "+2.5%/+2.5%-2.5%"}
                mtp_info = []
                if mtp > 0:
                    mtp_info.append(f"止盈级别: {mtp_labels.get(mtp, str(mtp))}")
                if dsp > 0:
                    mtp_info.append(f"动态止损: ${dsp:.2f}")
                if pcd:
                    mtp_info.append("✅ 已部分平仓")
                if mtp_info:
                    st.caption(" | ".join(mtp_info))

            # 冷却状态
            cr = acc.get("cooldown_remaining", 0)
            ptl = acc.get("prev_trade_loss", False)
            cool_info = []
            if cr > 0:
                cool_info.append(f"⏳ 冷却剩余: {cr}根K线")
            if ptl:
                cool_info.append("⚠️ 前笔亏损（仓位减半）")
            if cool_info:
                st.caption(" | ".join(cool_info))

            # 交易记录
            trades = acc.get("trades", [])
            if trades:
                with st.expander("📋 交易记录"):
                    import pandas as _pd
                    df_trades = _pd.DataFrame(
                        trades).iloc[::-1].reset_index(drop=True)
                    cols_show = ["time", "side", "price", "size", "pnl", "fee"]
                    cols_exist = [
                        c for c in cols_show if c in df_trades.columns]
                    df_trades_display = df_trades[cols_exist]
                    if "pnl" in df_trades_display.columns:
                        df_trades_display["pnl"] = df_trades_display["pnl"].apply(
                            lambda x: f"${x:+,.2f}" if isinstance(x, (int, float)) and x != 0 else "-"
                        )
                    st.dataframe(
                        df_trades_display,
                        use_container_width=True,
                        hide_index=True)

                    # 统计
                    wins = [t for t in trades if t.get("pnl", 0) > 0]
                    losses = [t for t in trades if t.get("pnl", 0) < 0]
                    win_rate = len(wins) / len(trades) * 100 if trades else 0
                    total_pnl = sum(t.get("pnl", 0) for t in trades)
                    stat_cols = st.columns(4)
                    stat_cols[0].metric("总盈亏", f"${total_pnl:+,.2f}")
                    stat_cols[1].metric("胜率", f"{win_rate:.1f}%")
                    stat_cols[2].metric("盈利次数", len(wins))
                    stat_cols[3].metric("亏损次数", len(losses))

        elif st.session_state.ai_running and not st.session_state.ai_trade_state:
            st.info("⏳ 等待数据…")

# ════════════════════════════════════════════════════════════════
# DATA FRAGMENT — 放在页面末尾，避免 st.rerun() 吞掉按钮点击事件
# ════════════════════════════════════════════════════════════════
_data_fragment()
