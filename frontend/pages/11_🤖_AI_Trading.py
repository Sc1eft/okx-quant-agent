"""🤖 AI 自动交易 — 基于 DeepSeek AI 多空分析的自动交易执行

将 AI 多空分析结果与 AIStrategyExecutor 打通，实现半自动/全自动交易。

流程：
  用户点击「开始AI交易」
    ↓
  自动采集：行情 + 技术指标 + 关联币种 + 新闻
    ↓
  调用 DeepSeek AI 分析（复用 eth_ai_analysis 模块）
    ↓
  AI 返回：direction / confidence / 入场区间 / 止损 / 止盈
    ↓
  组装为 AIStrategyExecutor 可消费的 rules JSON
    ↓
  Executor 在实时 K 线数据流中等待入场条件
    ↓
  入场 → 持仓监控（止盈止损/移动止盈）→ 出场 → 记录
"""

from __future__ import annotations
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from frontend.components.metrics_display import render_metric_card
from frontend.components.charts import equity_curve_chart
from frontend.utils.data_provider import fetch_klines_with_agg, fetch_ticker
from frontend.utils.session_state import get_config

from frontend.utils.eth_news import _fetch_crypto_news, _fmt_relative_time
from frontend.utils.eth_ai_analysis import (
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
    _build_candlestick_fig,
    _friendly_tf,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════

ETH_SYMBOL = "ETH-USDT"
DEFAULT_TF_LABEL = "15分钟"

# ════════════════════════════════════════════════════════════════
# SESSION STATE HELPERS
# ════════════════════════════════════════════════════════════════


def _ss(key: str, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _init_state():
    """Initialize all session state variables for this page."""
    _ss("ai_running", False)
    _ss("ai_executor", None)
    _ss("ai_trade_state", None)
    _ss("ai_analysis_result", None)
    _ss("ai_news", None)
    _ss("ai_data", None)
    _ss("ai_ticker", None)
    _ss("ai_trades", [])
    _ss("ai_equity_curve", [])
    _ss("ai_timeframe", DEFAULT_TF_LABEL)
    _ss("ai_data_count", 120)
    _ss("ai_auto_refresh", True)
    _ss("ai_initial_balance", 10000.0)
    _ss("ai_use_live_mode", False)
    _ss("ai_chat_context", None)
    _ss("ai_chat_messages", [])
    _ss("ai_chat_loading", False)
    _ss("ai_entry_markers", [])
    _ss("ai_exit_markers", [])
    _ss("ai_error", None)


def _fmt_change(c: float | None) -> str:
    if c is None:
        return ""
    return f"{c:+.2f}%"


# ════════════════════════════════════════════════════════════════
# PAGE LAYOUT
# ════════════════════════════════════════════════════════════════

st.markdown("""
    <div class="page-header">
        <h1>🤖 AI 自动交易</h1>
        <p>DeepSeek AI 多空分析 · 自动执行 · 实时监控</p>
    </div>
""", unsafe_allow_html=True)

cfg = get_config()
_init_state()

# ════════════════════════════════════════════════════════════════
# CONTROL BAR
# ════════════════════════════════════════════════════════════════

st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-title">⚙ 控制面板</div>', unsafe_allow_html=True)

ctrl_cols = st.columns([1.5, 1.5, 1.0, 1.0, 1.2])

with ctrl_cols[0]:
    tf_labels = list(TIMEFRAMES.keys())
    cur_label = st.session_state.ai_timeframe
    default_idx = tf_labels.index(cur_label) if cur_label in tf_labels else tf_labels.index(DEFAULT_TF_LABEL)
    selected_tf = st.selectbox("K 线周期", tf_labels, index=default_idx, key="ai_tf_sel")

with ctrl_cols[1]:
    dc = st.slider("K 线数量", 20, 300, st.session_state.ai_data_count, step=10, key="ai_dc_slider")

with ctrl_cols[2]:
    st.caption("")
    refresh_btn = st.button("🔄 刷新", use_container_width=True)

with ctrl_cols[3]:
    auto = st.checkbox("自动刷新", value=st.session_state.ai_auto_refresh, key="ai_auto_refresh")

with ctrl_cols[4]:
    st.caption("")
    st.session_state.ai_use_live_mode = st.checkbox(
        "实盘模式", value=st.session_state.ai_use_live_mode,
        disabled=st.session_state.ai_running,
        help="启用时使用 OKX API 直接下单（需 Trade 权限）",
    )

st.markdown('</div>', unsafe_allow_html=True)

# ── Detect widget changes ──
tf_changed = selected_tf != st.session_state.ai_timeframe
count_changed = dc != st.session_state.ai_data_count
if tf_changed or count_changed or refresh_btn:
    st.session_state.ai_timeframe = selected_tf
    st.session_state.ai_data_count = dc
    st.session_state.ai_data = None
    st.rerun()

tf_label = st.session_state.ai_timeframe
tf_key = TIMEFRAMES.get(tf_label, "1d")
data_count = st.session_state.ai_data_count

# ════════════════════════════════════════════════════════════════
# CONTROL BUTTONS (Start / Stop / Clear)
# ════════════════════════════════════════════════════════════════

st.markdown('<div class="section-card">', unsafe_allow_html=True)
btn_cols = st.columns([2, 2, 2, 4])

with btn_cols[0]:
    start_disabled = st.session_state.ai_running
    if st.button("🚀 开始AI交易", type="primary", use_container_width=True, disabled=start_disabled):
        st.session_state.ai_analysis_result = None
        st.session_state.ai_news = None
        st.session_state.ai_error = None
        st.session_state.ai_trades = []
        st.session_state.ai_equity_curve = []
        st.session_state.ai_entry_markers = []
        st.session_state.ai_exit_markers = []

        try:
            with st.status("🤖 AI 交易启动中…", expanded=True) as status:
                status.update(label="📡 获取 ETH 行情数据…")
                tk = fetch_ticker(cfg, symbol=ETH_SYMBOL)
                st.session_state.ai_ticker = tk

                status.update(label="📊 获取技术指标数据…")
                k15 = fetch_klines_with_agg(cfg, limit=30, timeframe="15m", symbol=ETH_SYMBOL)
                k1h = fetch_klines_with_agg(cfg, limit=20, timeframe="1h", symbol=ETH_SYMBOL)
                k1d = fetch_klines_with_agg(cfg, limit=7, timeframe="1d", symbol=ETH_SYMBOL)

                status.update(label="🔄 获取关联币种行情…")
                btc = fetch_ticker(cfg, symbol="BTC-USDT")
                sol = fetch_ticker(cfg, symbol="SOL-USDT")
                doge = fetch_ticker(cfg, symbol="DOGE-USDT")

                status.update(label="📰 采集新闻与政策信息…")
                news = _fetch_crypto_news()
                st.session_state.ai_news = news

                status.update(label="🧠 AI 正在综合分析（技术面+基本面）…")
                result = _call_ai_analysis(
                    ticker=tk, klines_15m=k15, klines_1h=k1h, klines_1d=k1d,
                    btc_ticker=btc, sol_ticker=sol, doge_ticker=doge,
                    cfg=cfg, news=news,
                )
                st.session_state.ai_analysis_result = result

                # ── 保存对话上下文 ──
                mk = (
                    f"### 实时行情\n{_ticker_summary('ETH', tk)}\n\n"
                    f"{_summarize_klines(k15, '短期(15分钟)')}\n"
                    f"{_summarize_klines(k1h, '中期(1小时)')}\n"
                    f"{_summarize_klines(k1d, '长期(日线)')}\n\n"
                    f"### 关联币种\n{_ticker_summary('BTC', btc)}\n"
                    f"{_ticker_summary('SOL', sol)}\n"
                    f"{_ticker_summary('DOGE', doge)}"
                )
                st.session_state.ai_chat_context = {
                    "market_summary": mk,
                    "news": news,
                    "analysis_result": result,
                }
                st.session_state.ai_chat_messages = []

                # ── 组装 AI 信号 → Executor ──
                if result.get("direction") in ("long", "short"):
                    from agent.signal_bridge import ai_signal_to_rules
                    rules = ai_signal_to_rules(result, initial_balance=st.session_state.ai_initial_balance)

                    status.update(label="📥 加载市场数据…")
                    # 获取当前时间段的 K 线数据用于预热
                    current_df = fetch_klines_with_agg(cfg, limit=data_count, timeframe=tf_key, symbol=ETH_SYMBOL)
                    st.session_state.ai_data = current_df

                    from execution.ai_executor import AIStrategyExecutor
                    executor = AIStrategyExecutor(
                        rules=rules, cfg=cfg,
                        initial_balance=st.session_state.ai_initial_balance,
                        mode="live" if st.session_state.ai_use_live_mode else "paper",
                    )

                    # 预热：跳过入场，只加载 K 线到缓冲区
                    executor.ai_signal_skip_entry = True
                    if current_df is not None and not current_df.empty:
                        for _, bar in current_df.iterrows():
                            executor.on_bar(bar)
                    executor.ai_signal_skip_entry = False

                    st.session_state.ai_executor = executor
                    st.session_state.ai_running = True
                    st.session_state.ai_trade_state = executor.get_state()

                    status.update(label="✅ AI 交易已启动", state="complete")
                    st.success(f"🎯 AI 信号: {result.get('direction')} (信心指数 {result.get('confidence', 0)}%)")
                else:
                    st.warning(f"⚠ AI 信号为中性 (neutral)，不启动交易。{result.get('summary', '')}")
                    st.session_state.ai_running = False
        except Exception as e:
            st.session_state.ai_error = str(e)
            st.session_state.ai_running = False
        st.rerun()

with btn_cols[1]:
    stop_disabled = not st.session_state.ai_running
    if st.button("⏹ 停止", use_container_width=True, type="secondary", disabled=stop_disabled):
        st.session_state.ai_running = False
        state = st.session_state.get("ai_trade_state")
        if state:
            st.success(
                f"已停止 | 交易 {state.get('total_trades', 0)} 笔 | "
                f"权益 ${state.get('account', {}).get('equity', 0):,.2f}"
            )
        st.rerun()

with btn_cols[2]:
    if st.button("🗑 清除", use_container_width=True,
                 disabled=st.session_state.ai_running):
        for k in ["ai_running", "ai_executor", "ai_trade_state",
                  "ai_analysis_result", "ai_news", "ai_data", "ai_ticker",
                  "ai_trades", "ai_equity_curve", "ai_entry_markers",
                  "ai_exit_markers", "ai_chat_context", "ai_chat_messages",
                  "ai_error"]:
            st.session_state[k] = None
        st.session_state.ai_running = False
        st.rerun()

with btn_cols[3]:
    st.session_state.ai_initial_balance = st.number_input(
        "初始资金 (USDT)",
        min_value=100.0, max_value=10_000_000.0,
        value=st.session_state.ai_initial_balance,
        step=1000.0,
        disabled=st.session_state.ai_running,
    )

st.markdown('</div>', unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# STATUS CARDS
# ════════════════════════════════════════════════════════════════

state = st.session_state.get("ai_trade_state")
acc = state.get("account", {}) if state else {}
running = st.session_state.ai_running
analysis = st.session_state.get("ai_analysis_result")

st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-title">📊 交易状态</div>', unsafe_allow_html=True)

kpi_cols = st.columns(6)
with kpi_cols[0]:
    equity_val = acc.get("equity", 0) if acc else 0
    init_val = acc.get("initial_balance", st.session_state.ai_initial_balance) if acc else st.session_state.ai_initial_balance
    pnl_val = equity_val - init_val
    pnl_pct = (pnl_val / init_val * 100) if init_val > 0 else 0
    st.metric("总权益", f"${equity_val:,.2f}", f"{pnl_val:+,.2f}")

with kpi_cols[1]:
    pos_label = "⬜ 空仓"
    ip = state.get("in_position", False) if state else False
    ps = state.get("position_side", "") if state else ""
    if ip and ps == "long":
        pos_label = "🟢 多头"
    elif ip and ps == "short":
        pos_label = "🔴 空头"
    st.metric("持仓", pos_label)

with kpi_cols[2]:
    ep = state.get("entry_price", 0) if state else 0
    st.metric("入场价", f"${ep:,.2f}" if ep > 0 else "-")

with kpi_cols[3]:
    if analysis:
        dir_text = {"long": "📈 看多", "short": "📉 看空", "neutral": "⚖️ 中性"}.get(analysis.get("direction", ""), "-")
        conf = analysis.get("confidence", 0)
        st.metric("AI 信号", dir_text, f"{conf}%")
    else:
        st.metric("AI 信号", "等待启动")

with kpi_cols[4]:
    trades = acc.get("trades", []) if acc else []
    total_trades = len(trades)
    st.metric("交易次数", total_trades)

with kpi_cols[5]:
    status_text = "🟢 运行中" if running else "⏸ 已停止"
    st.metric("状态", status_text)

if running and state:
    # 附加状态行
    extra_cols = st.columns(6)
    with extra_cols[0]:
        sig = state.get("signal", "hold")
        sig_emoji = {"buy": "🟢", "sell": "🔴", "short": "🔴", "hold": "⚪", "blocked": "🟡"}
        st.caption(f"最新信号: {sig_emoji.get(sig, '⚪')} {sig.upper()}")

    with extra_cols[1]:
        reason = state.get("signal_reason", "")
        if reason:
            st.caption(f"📡 {reason}")

    with extra_cols[2]:
        mtp = state.get("multi_tp_level", 0)
        if mtp:
            st.caption(f"止盈级别: {mtp}")

    with extra_cols[3]:
        dsp = state.get("dynamic_stop_price", 0)
        if dsp > 0:
            st.caption(f"动态止损: ${dsp:.2f}")

    with extra_cols[4]:
        cr = state.get("cooldown_remaining", 0)
        if cr > 0:
            st.caption(f"⏳ 冷却: {cr}根K线")

    with extra_cols[5]:
        ptl = state.get("prev_trade_loss", False)
        if ptl:
            st.caption("⚠️ 前笔亏损")

    # 信号原因详情
    if state.get("signal_reason"):
        st.info(f"📡 {state['signal_reason']}")

st.markdown('</div>', unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# DATA FRAGMENT — 自动刷新 + 喂数据给 Executor
# ════════════════════════════════════════════════════════════════

refresh_interval_s = TIMEFRAME_REFRESH_S.get(tf_key, 5) if auto else None


@st.fragment(run_every=refresh_interval_s)
def _data_fragment():
    """数据获取 + 喂给 Executor + 更新 UI"""

    _tf_label = st.session_state.ai_timeframe
    _t_key = TIMEFRAMES.get(_tf_label, "1d")
    _d_count = st.session_state.ai_data_count

    _cached_df = st.session_state.get("ai_data")

    # Ticker
    try:
        _ticker_data = fetch_ticker(cfg, symbol=ETH_SYMBOL)
        st.session_state.ai_ticker = _ticker_data
    except Exception as e:
        _ticker_data = st.session_state.get("ai_ticker")
        if _cached_df is None:
            st.warning(f"获取 ticker 失败: {e}")

    # K 线
    try:
        _new_df = fetch_klines_with_agg(
            cfg, limit=_d_count, timeframe=_t_key, symbol=ETH_SYMBOL,
        )
        if _new_df is not None and not _new_df.empty:
            st.session_state.ai_data = _new_df
            _cached_df = _new_df
    except Exception as e:
        if _cached_df is None:
            st.error(f"获取数据失败: {e}")

    _df = _cached_df

    # ── 喂数据给 Executor ──
    if st.session_state.get("ai_running") and _df is not None and not _df.empty:
        _executor = st.session_state.get("ai_executor")
        if _executor is not None:
            _buf = _executor.bar_buffer
            if _buf is not None and not _buf.empty:
                _last_processed = _buf.index[-1]
                _new_bars = _df[_df.index > _last_processed]
            else:
                _new_bars = _df
            if not _new_bars.empty:
                for _, _bar in _new_bars.iterrows():
                    _executor.on_bar(_bar)
                st.session_state.ai_trade_state = _executor.get_state()

                # 收集交易记录和权益曲线
                _acc = _executor.account
                if _acc.trades:
                    st.session_state.ai_trades = _acc.trades

                # 权益曲线点
                _eq = {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "equity": _acc.equity,
                }
                _curve = st.session_state.get("ai_equity_curve") or []
                _curve.append(_eq)
                st.session_state.ai_equity_curve = _curve

                # 入场/出场标记
                if _executor.in_position:
                    _entry_markers = st.session_state.get("ai_entry_markers") or []
                    _ep = _executor.entry_price
                    _et = _executor.entry_time or ""
                    # 避免重复标记
                    if not _entry_markers or _entry_markers[-1].get("price") != _ep:
                        _entry_markers.append({"time": _et, "price": _ep, "side": _executor.position_side})
                        st.session_state.ai_entry_markers = _entry_markers

    # ── Ticker bar ──
    if _ticker_data:
        _ch24 = _ticker_data.get("change_24h", 0) or 0
        _pc = "green" if _ch24 >= 0 else "red"
        _lp = _ticker_data.get("last", 0) or 0

        st.markdown(f"""
        <div class="ticker-bar">
        <div class="ticker-item">
        <span class="ticker-label">ETH-USDT</span>
        <span class="ticker-value {_pc}">${_lp:,.2f} {_fmt_change(_ch24)}</span>
        </div>
        <div class="ticker-item">
        <span class="ticker-label">买一 / 卖一</span>
        <span class="ticker-value">{f'${_ticker_data.get("bid", 0):,.2f}' if _ticker_data.get("bid") else "N/A"} / {f'${_ticker_data.get("ask", 0):,.2f}' if _ticker_data.get("ask") else "N/A"}</span>
        </div>
        <div class="ticker-item">
        <span class="ticker-label">24h 最高 / 最低</span>
        <span class="ticker-value">{f'${_ticker_data.get("high_24h", 0):,.2f}' if _ticker_data.get("high_24h") else "N/A"} / {f'${_ticker_data.get("low_24h", 0):,.2f}' if _ticker_data.get("low_24h") else "N/A"}</span>
        </div>
        <div class="ticker-item">
        <span class="ticker-label">24h 成交量</span>
        <span class="ticker-value">{f'{_ticker_data.get("volume_24h", 0):,.0f} ETH'}</span>
        </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("💡 加载后将显示实时行情")

    # ════════════════════════════════════════════════════════════════
    # K 线图 + 交易标记
    # ════════════════════════════════════════════════════════════════

    if _df is not None and not _df.empty:
        _fig = _build_candlestick_fig(
            _df, ticker_data=_ticker_data, tf_key=_t_key, height=450,
        )

        # 叠加入场标记
        _entry_markers = st.session_state.get("ai_entry_markers") or []
        if _entry_markers:
            _entry_df = pd.DataFrame(_entry_markers)
            _entry_df["time"] = pd.to_datetime(_entry_df["time"])
            _fig.add_trace(go.Scatter(
                x=_entry_df["time"], y=_entry_df["price"],
                mode="markers",
                name="入场",
                marker=dict(
                    color="#059669", size=14,
                    symbol="triangle-up",
                    line=dict(color="white", width=2),
                ),
            ))

        # 叠加出场标记
        _exit_markers = st.session_state.get("ai_exit_markers") or []
        if _exit_markers:
            _exit_df = pd.DataFrame(_exit_markers)
            _exit_df["time"] = pd.to_datetime(_exit_df["time"])
            _fig.add_trace(go.Scatter(
                x=_exit_df["time"], y=_exit_df["price"],
                mode="markers",
                name="出场",
                marker=dict(
                    color="#dc2626", size=14,
                    symbol="triangle-down",
                    line=dict(color="white", width=2),
                ),
            ))

        st.plotly_chart(_fig, use_container_width=True, config={"displayModeBar": False})

        # KPI row
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        _kpi_sub = st.columns(4)
        with _kpi_sub[0]:
            st.metric("当前价", f"${float(_df['close'].iloc[-1]):,.2f}")
        with _kpi_sub[1]:
            st.metric("时段最高", f"${float(_df['high'].max()):,.2f}")
        with _kpi_sub[2]:
            st.metric("时段最低", f"${float(_df['low'].min()):,.2f}")
        with _kpi_sub[3]:
            vol = float(_df['volume'].sum())
            st.metric("时段成交量", f"{vol:,.0f}")
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("⏳ 加载K线数据…")


_data_fragment()

# ════════════════════════════════════════════════════════════════
# AI 分析结果展示
# ════════════════════════════════════════════════════════════════

_err = st.session_state.get("ai_error")
if _err:
    st.error(f"❌ {_err}")

_raw_news = st.session_state.get("ai_news")
_res = st.session_state.get("ai_analysis_result")
if _res:
    st.markdown("---")
    st.markdown("### 🤖 AI 分析结果")

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
            f'<li style="margin-bottom:0.25rem;color:#64748b;font-size:0.85rem;">'
            f'<span style="color:#0f172a;font-weight:500;">[{_sanitize_ai_text(n["source"])}]</span>'
            f'<span style="color:#94a3b8;font-size:0.75rem;">{_fmt_relative_time(n.get("timestamp", ""))}</span> '
            f'{_sanitize_ai_text(n["title"])}</li>'
            for n in _raw_news
        )
        _fund_news_html = "\n".join([
            '<details style="margin-top:0.75rem;">',
            '<summary style="color:#64748b;font-size:0.85rem;cursor:pointer;">',
            f'  📡 参考新闻（{len(_raw_news)}条）',
            '</summary>',
            f'<ul style="margin:0.5rem 0 0 0;padding-left:1.2rem;">{_news_items}</ul>',
            '</details>',
        ])

    st.markdown(f"""
    <div style="border:1px solid #e2e8f0;border-radius:12px;padding:1.25rem;background:white;margin-top:0.5rem;">
        <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;">
            <span style="font-size:1.8rem;">{_dir_icon}</span>
            <span style="font-size:1.5rem;font-weight:700;color:{_dir_color};">{_dir_text}</span>
            <div style="margin-left:auto;display:flex;align-items:center;gap:0.5rem;">
                <span style="color:#64748b;font-size:0.85rem;">信心指数</span>
                <span style="font-size:1.3rem;font-weight:700;color:{_conf_color};">{_conf}%</span>
            </div>
        </div>
        <p style="color:#475569;font-size:0.95rem;margin-bottom:1rem;">{_sanitize_ai_text(_res.get("summary", ""))}</p>
        <div style="margin-bottom:1rem;">
            <p style="font-weight:600;color:#0f172a;margin-bottom:0.4rem;">📌 关键依据</p>
            <ul style="margin:0;padding-left:1.2rem;color:#475569;font-size:0.9rem;">{_ev_html}</ul>
        </div>
        <div style="margin-bottom:1rem;">
            <p style="font-weight:600;color:#0f172a;margin-bottom:0.4rem;">⚠️ 风险提示</p>
            <ul style="margin:0;padding-left:1.2rem;color:#dc2626;font-size:0.9rem;">{_risk_html}</ul>
        </div>
        <div style="display:flex;gap:1rem;flex-wrap:wrap;">
            <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">🔬 技术面</p>
                <p style="color:#475569;font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("technical_analysis", "")) or "—"}</p>
            </div>
            <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">🌊 市场情绪</p>
                <p style="color:#475569;font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("market_sentiment", "")) or "—"}</p>
            </div>
            <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">📰 基本面</p>
                <p style="color:#475569;font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("fundamental_analysis", "")) or "—"}</p>
            </div>
        </div>
        {_fund_news_html}
    </div>
    """, unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════
    # AI 追问对话
    # ════════════════════════════════════════════════════════════════

    st.markdown("---")
    st.markdown("#### 💬 追问分析")

    chat_container = st.container()
    with chat_container:
        for msg in st.session_state.ai_chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(_sanitize_ai_text(msg["content"]))

    if st.session_state.get("ai_chat_loading"):
        with st.chat_message("assistant"):
            st.markdown("🤔 思考中…")

    user_input = st.chat_input("对当前市场分析提问…（例如：为什么看空？ETH支撑位在哪？）",
                               disabled=st.session_state.get("ai_chat_loading", False))
    if user_input and not st.session_state.get("ai_chat_loading", False):
        st.session_state.ai_chat_messages.append({"role": "user", "content": user_input})
        context = st.session_state.get("ai_chat_context")
        st.session_state.ai_chat_loading = True
        answer = _call_ai_chat(
            user_input, context,
            st.session_state.ai_chat_messages, cfg,
        )
        st.session_state.ai_chat_messages.append({"role": "assistant", "content": answer})
        st.session_state.ai_chat_loading = False
        st.rerun()


# ════════════════════════════════════════════════════════════════
# 交易记录
# ════════════════════════════════════════════════════════════════

_trades = st.session_state.get("ai_trades") or []
if _trades:
    st.markdown("---")
    st.markdown("### 📋 交易记录")

    import pandas as _pd
    df_trades = _pd.DataFrame(_trades).iloc[::-1].reset_index(drop=True)
    cols_show = ["time", "side", "price", "size", "pnl", "fee"]
    cols_exist = [c for c in cols_show if c in df_trades.columns]
    df_display = df_trades[cols_exist]
    if "pnl" in df_display.columns:
        df_display["pnl"] = df_display["pnl"].apply(
            lambda x: f"${x:+,.2f}" if isinstance(x, (int, float)) and x != 0 else "-"
        )
    st.dataframe(df_display, use_container_width=True, hide_index=True)

    # 统计
    wins = [t for t in _trades if t.get("pnl", 0) > 0]
    losses = [t for t in _trades if t.get("pnl", 0) < 0]
    win_rate = len(wins) / len(_trades) * 100 if _trades else 0
    total_pnl = sum(t.get("pnl", 0) for t in _trades)
    stat_cols = st.columns(5)
    stat_cols[0].metric("总盈亏", f"${total_pnl:+,.2f}")
    stat_cols[1].metric("胜率", f"{win_rate:.1f}%")
    stat_cols[2].metric("盈利次数", len(wins))
    stat_cols[3].metric("亏损次数", len(losses))
    stat_cols[4].metric("总交易", len(_trades))


# ════════════════════════════════════════════════════════════════
# 权益曲线
# ════════════════════════════════════════════════════════════════

_curve = st.session_state.get("ai_equity_curve") or []
if len(_curve) >= 2:
    st.markdown("---")
    st.markdown("### 📈 权益曲线")
    fig_eq = equity_curve_chart(_curve, title="AI 交易权益曲线")
    st.plotly_chart(fig_eq, use_container_width=True, config={"displayModeBar": False})
