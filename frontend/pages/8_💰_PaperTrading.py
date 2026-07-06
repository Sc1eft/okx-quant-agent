"""Paper Trading page - 实盘数据模拟交易（建仓金额 + 策略参数可调）

Real-time flow:
  启动监控 →
    Phase 1: 自动获取初始 K 线数据 →
    Phase 2: 逐根回放已缓存数据 →
    Phase 3: 持续从 OKX 拉取最新 K 线 + Ticker →
    Phase 4: 有新 K 线 → 策略处理 → 风控 → 模拟成交 → 更新界面
"""

import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Force reload strategy modules to bypass Streamlit caching
for _mod in list(sys.modules.keys()):
    if _mod.startswith("strategies") or _mod.startswith("execution") or _mod.startswith("risk"):
        import importlib
        importlib.reload(sys.modules[_mod])

import strategies.base as _sb
import execution.paper as _paper
import risk.rules as _risk

from frontend.utils.session_state import get_config
from frontend.utils.data_provider import fetch_latest_klines, fetch_ticker
from frontend.components.charts import equity_curve_chart
from frontend.components.metrics_display import render_metric_card
from frontend.utils.helpers import ss as _ss
from execution.futures_paper import FuturesPaperEngine


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def _refresh_interval_s(tf: str) -> int:
    return {"15m": 5, "1h": 10, "4h": 30, "1d": 60}.get(tf, 10)


def _friendly_tf(tf: str) -> str:
    return {"15m": "15 分钟", "1h": "1 小时", "4h": "4 小时", "1d": "1 天"}.get(tf, tf)


def _render_strategy_params(strategy_name: str, default_params: dict) -> dict:
    """渲染策略参数编辑界面，返回用户修改后的参数字典。"""
    params = dict(default_params)

    # ── 策略特有参数 ──
    if strategy_name == "ma_cross":
        col1, col2 = st.columns(2)
        with col1:
            params["short_window"] = st.number_input(
                "短期均线窗口", min_value=2, max_value=100,
                value=int(params.get("short_window", 7)), step=1,
                key=f"param_{strategy_name}_short_window",
                help="短期移动平均线周期数，值越小对价格变化越敏感")
        with col2:
            params["long_window"] = st.number_input(
                "长期均线窗口", min_value=5, max_value=200,
                value=int(params.get("long_window", 25)), step=1,
                key=f"param_{strategy_name}_long_window",
                help="长期移动平均线周期数，值越大趋势越平滑")

    elif strategy_name == "rsi_mean_reversion":
        col1, col2, col3 = st.columns(3)
        with col1:
            params["rsi_period"] = st.number_input(
                "RSI 周期", min_value=2, max_value=50,
                value=int(params.get("rsi_period", 14)), step=1,
                key=f"param_{strategy_name}_rsi_period")
        with col2:
            params["oversold"] = st.number_input(
                "超卖阈值", min_value=5, max_value=50,
                value=int(params.get("oversold", 30)), step=1,
                key=f"param_{strategy_name}_oversold",
                help="RSI 低于此值视为超卖，触发买入信号")
        with col3:
            params["overbought"] = st.number_input(
                "超买阈值", min_value=50, max_value=95,
                value=int(params.get("overbought", 70)), step=1,
                key=f"param_{strategy_name}_overbought",
                help="RSI 高于此值视为超买，触发卖出信号")

    elif strategy_name == "breakout":
        col1, col2 = st.columns(2)
        with col1:
            params["period"] = st.number_input(
                "突破周期", min_value=5, max_value=100,
                value=int(params.get("period", 20)), step=1,
                key=f"param_{strategy_name}_period",
                help="突破 N 周期高点/低点")
        with col2:
            params["atr_multiplier"] = st.number_input(
                "ATR 倍数", min_value=0.5, max_value=5.0,
                value=float(params.get("atr_multiplier", 2.0)), step=0.1,
                key=f"param_{strategy_name}_atr_mult",
                help="ATR 自适应止损倍数")

    st.markdown("---")
    st.markdown("**🛡 止盈止损设置**")
    col1, col2, col3 = st.columns(3)
    with col1:
        params["stop_loss_pct"] = st.slider(
            "止损 (%)", 0.0, 15.0,
            value=float(params.get("stop_loss_pct", 2.0)), step=0.1,
            key=f"param_{strategy_name}_sl",
            help="持仓亏损达到此比例时强制平仓（0=不使用）")
    with col2:
        params["take_profit_pct"] = st.slider(
            "止盈 (%)", 0.0, 30.0,
            value=float(params.get("take_profit_pct", 6.0)), step=0.1,
            key=f"param_{strategy_name}_tp",
            help="持仓盈利达到此比例时强制平仓（0=不使用）")
    with col3:
        params["position_timeout_bars"] = st.number_input(
            "持仓超时 (K线)", min_value=1, max_value=200,
            value=int(params.get("position_timeout_bars", 48)), step=1,
            key=f"param_{strategy_name}_timeout",
            help="持仓超过此数量 K 线后强制平仓")

    col1, col2 = st.columns(2)
    with col1:
        params["trailing_stop_activation"] = st.slider(
            "移动止损激活 (%)", 0.0, 20.0,
            value=float(params.get("trailing_stop_activation", 3.0)), step=0.1,
            key=f"param_{strategy_name}_ts_act",
            help="浮盈达到此比例后激活移动止损（0=不使用）")
    with col2:
        params["trailing_stop_distance"] = st.slider(
            "移动止损距离 (%)", 0.0, 10.0,
            value=float(params.get("trailing_stop_distance", 1.5)), step=0.1,
            key=f"param_{strategy_name}_ts_dist",
            help="从最高点回落此比例时触发移动止损")

    st.caption("💡 修改参数后点击「启动监控」生效，中途不能修改")
    return params


# ════════════════════════════════════════════════════════════════
# PAGE HEADER
# ════════════════════════════════════════════════════════════════

st.markdown("""
    <div class="page-header">
        <h1>💰 模拟交易</h1>
        <p>设置建仓金额 → 选择策略并调参 → 连接 OKX 真实行情 → 模拟成交 → 实时监控</p>
    </div>
""", unsafe_allow_html=True)

cfg = get_config()
BASE_CURRENCY = cfg.trading.symbol.split("-")[0]  # "ETH-USDT" → "ETH"
strategies = _sb.get_available_strategies()

if not strategies:
    st.warning("无可用的策略")
    st.stop()

strategy_names = list(strategies.keys())

# ── Init Engine (默认空，启动时创建) ──
if "paper_engine" not in st.session_state or st.session_state.paper_engine is None:
    st.session_state.paper_engine = _paper.PaperEngine(cfg)

engine: _paper.PaperEngine = st.session_state.paper_engine
risk_engine = st.session_state.get("risk_engine") or _risk.RiskEngine(cfg.risk)

paper_running = _ss("paper_running", False)
ticker_data = st.session_state.get("paper_ticker")

# ════════════════════════════════════════════════════════════════
# 1. TICKER BAR — dark gradient, shows live price
# ════════════════════════════════════════════════════════════════

if ticker_data:
    tk = ticker_data
    price_color = "green" if tk.get("change_24h", 0) >= 0 else "red"
    change_str = f"{tk.get('change_24h', 0):+.2f}%" if tk.get("change_24h") is not None else ""
    bid_str = f"${tk['bid']:,.2f}" if tk.get("bid") else "N/A"
    ask_str = f"${tk['ask']:,.2f}" if tk.get("ask") else "N/A"

    st.markdown(f"""
    <div class="ticker-bar">
        <div class="ticker-item">
            <span class="ticker-label">{cfg.trading.symbol}</span>
            <span class="ticker-value {price_color}">${tk['last']:,.2f} {change_str}</span>
        </div>
        <div class="ticker-item">
            <span class="ticker-label">买一 / 卖一</span>
            <span class="ticker-value">{bid_str} / {ask_str}</span>
        </div>
        <div class="ticker-item">
            <span class="ticker-label">24h 成交量</span>
            <span class="ticker-value">{tk.get('volume_24h', 0):,.0f} {BASE_CURRENCY}</span>
        </div>
        <div style="margin-left:auto; display:flex; align-items:center; gap:0.5rem;">
            <span class="badge badge--green">✅ 实时数据</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
else:
    st.info("💡 启动监控后实时行情将在此显示")

# ════════════════════════════════════════════════════════════════
# 2. CONTROL PANEL — white card with funding + params
# ════════════════════════════════════════════════════════════════

st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-title">⚙ 控制面板</div>', unsafe_allow_html=True)

# ── Row 1: Strategy + K 线设置 ──
row1 = st.columns([2.5, 1.5, 1.5])

with row1[0]:
    paper_strategy = _ss("paper_strategy", strategy_names[0])
    idx = strategy_names.index(paper_strategy) if paper_strategy in strategy_names else 0
    selected_strategy = st.selectbox(
        "📈 交易策略", strategy_names, index=idx,
        disabled=paper_running,
        format_func=lambda n: f"{n} — {strategies[n]['description']}",
        key="paper_strategy_sel")

with row1[1]:
    _ss("paper_data_count", 100)
    data_count = st.slider("📊 初始 K 线数", 50, 300,
                           st.session_state.paper_data_count,
                           step=50, disabled=paper_running,
                           key="paper_trade_data_count",
                           help="首次加载的历史 K 线数量")

with row1[2]:
    _ss("paper_tf", "1h")
    tf_options = ["1h", "15m", "4h", "1d"]
    def_tf_idx = tf_options.index(st.session_state.paper_tf) if st.session_state.paper_tf in tf_options else 0
    timeframe = st.selectbox("⏱ K 线周期", tf_options, index=def_tf_idx,
                              disabled=paper_running,
                              format_func=_friendly_tf,
                              key="paper_trade_tf")

# ── Row 2: 资金设置 ──
st.markdown('<div style="height:0.5rem"></div>', unsafe_allow_html=True)
row2 = st.columns([1, 1])

with row2[0]:
    _ss("paper_initial_balance", 10000.0)
    initial_balance = st.number_input(
        "💰 初始资金 (USDT)",
        min_value=100.0, max_value=10_000_000.0,
        value=st.session_state.paper_initial_balance, step=1000.0,
        disabled=paper_running, key="paper_initial_balance_input",
        help="模拟交易的起始总资金（建仓金额）")

with row2[1]:
    _ss("paper_position_size_pct", 10.0)
    _ss("paper_market_mode", "spot")
    _ss("paper_leverage", 10)
    position_size_pct = st.slider(
        "📊 单笔仓位比例",
        1.0, 100.0, st.session_state.paper_position_size_pct,
        step=1.0, disabled=paper_running,
        key="paper_position_size_pct_input",
        help="每次开仓使用的资金比例（占当前余额的百分比）")

# ── Row 3: 策略参数 (expander) ──
with st.expander("⚙ 策略参数", expanded=False):
    st.markdown(f"""
    <div style="margin-bottom:0.75rem; color:#475569; font-size:0.9rem;">
        当前策略: <strong>{selected_strategy}</strong> — {strategies[selected_strategy]['description']}
    </div>
    """, unsafe_allow_html=True)

    strategy_info = strategies[selected_strategy]
    default_params = dict(strategy_info["params"])
    # Merge in common risk params that all strategies share
    if "stop_loss_pct" not in default_params:
        default_params["stop_loss_pct"] = 2.0
        default_params["take_profit_pct"] = 6.0
        default_params["trailing_stop_activation"] = 3.0
        default_params["trailing_stop_distance"] = 1.5
        default_params["position_timeout_bars"] = 48

    modified_params = _render_strategy_params(selected_strategy, default_params)
    st.session_state.paper_strategy_params = modified_params

# ── Hint about current settings ──
if not paper_running:
    _params = st.session_state.get("paper_strategy_params", {})
    _fund_hint = (
        f"资金: ${initial_balance:,.0f} | "
        f"仓位: {position_size_pct:.0f}% | "
        f"策略: {selected_strategy}"
    )
    if _params:
        if selected_strategy == "ma_cross":
            _fund_hint += f" | MA{_params.get('short_window', 7)}/{_params.get('long_window', 25)}"
        elif selected_strategy == "rsi_mean_reversion":
            _fund_hint += f" | RSI{_params.get('rsi_period', 14)} ({_params.get('oversold', 30)}/{_params.get('overbought', 70)})"
        elif selected_strategy == "breakout":
            _fund_hint += f" | N{_params.get('period', 20)} ATR×{_params.get('atr_multiplier', 2.0)}"
        _fund_hint += f" | 止盈{_params.get('take_profit_pct', 6.0)}% 止损{_params.get('stop_loss_pct', 2.0)}%"
    st.caption(f"💡 当前配置: {_fund_hint}")

# ── Row 3.5: 市场模式 + 杠杆 ──
st.markdown('<div style="height:0.25rem"></div>', unsafe_allow_html=True)
mode_cols = st.columns([1.5, 2, 3])
with mode_cols[0]:
    market_mode = st.selectbox(
        "📊 交易模式",
        ["spot", "futures"],
        index=0 if st.session_state.paper_market_mode == "spot" else 1,
        disabled=paper_running,
        key="paper_market_selector",
        format_func=lambda m: {"spot": "💵 现货", "futures": "📈 合约"}[m],
    )
    st.session_state.paper_market_mode = market_mode

with mode_cols[1]:
    if market_mode == "futures":
        leverage = st.slider(
            "🔧 杠杆倍数",
            min_value=1, max_value=125,
            value=st.session_state.paper_leverage,
            step=1, disabled=paper_running,
            key="paper_leverage_slider",
            help="OKX ETH-USDT 永续合约最大 125x")
        st.session_state.paper_leverage = leverage
    else:
        st.markdown("""<div style="padding:1.5rem 0; color:#94a3b8; font-size:0.85rem;">现货全额交易</div>""", unsafe_allow_html=True)

# ── Row 4: Buttons ──
st.markdown('<div style="height:0.5rem"></div>', unsafe_allow_html=True)
row4 = st.columns([1.3, 1, 1, 1.2])

with row4[0]:
    if paper_running:
        st.button("⏹ 停止监控", use_container_width=True, type="primary",
                  on_click=lambda: st.session_state.update(paper_running=False))
    else:
        if st.button("▶ 启动监控", use_container_width=True, type="primary"):
            st.session_state.paper_running = True
            st.session_state.paper_strategy = selected_strategy

            if market_mode == "futures":
                engine = FuturesPaperEngine(
                    cfg,
                    wallet_balance=initial_balance,
                    leverage=leverage,
                    position_size_pct=position_size_pct / 100.0,
                )
            else:
                engine = _paper.PaperEngine(
                    cfg,
                    initial_balance=initial_balance,
                    position_size_pct=position_size_pct / 100.0,
                )
            st.session_state.paper_engine = engine

            # Create strategy with user-modified params
            strategy_params = st.session_state.get("paper_strategy_params", {})
            strategy = _sb.create_strategy(selected_strategy, params=strategy_params)
            strategy._bar_buffer = None
            strategy.position = None
            st.session_state.paper_strategy_instance = strategy

            # Reset state
            st.session_state.paper_state = None
            st.session_state.paper_data = None
            st.session_state.paper_ticker = None
            st.session_state.paper_last_refresh = None
            st.rerun()

with row4[1]:
    st.button("📥 获取数据", use_container_width=True, disabled=paper_running)

with row4[2]:
    _ss("paper_auto_refresh", True)
    st.checkbox("自动刷新", value=st.session_state.paper_auto_refresh,
                key="paper_auto_refresh")

with row4[3]:
    if not paper_running:
        st.markdown("""
        <div style="padding:0.4rem 0; color:#94a3b8; font-size:0.85rem;">
        设置好参数后点击「启动监控」
        </div>
        """, unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# 3. STATUS BAR — only when monitoring
# ════════════════════════════════════════════════════════════════

if paper_running:
    last_refresh = st.session_state.get("paper_last_refresh")
    strategy_inst = st.session_state.get("paper_strategy_instance")
    buffer_len = 0
    if strategy_inst and hasattr(strategy_inst, "_bar_buffer") and strategy_inst._bar_buffer is not None:
        buffer_len = len(strategy_inst._bar_buffer)

    st.markdown(f"""
    <div class="status-bar">
        <div class="status-item">
            <span class="status-dot"></span>
            <span style="font-weight:600; color:#059669;">监控中</span>
        </div>
        <div class="status-item">
            <span style="color:#64748b;">已处理</span>
            <span style="font-weight:700;">{buffer_len}</span>
            <span style="color:#64748b;">根 K 线</span>
        </div>
        <div class="status-item">
            <span style="color:#64748b;">周期</span>
            <span style="font-weight:600;">{_friendly_tf(timeframe)}</span>
            <span style="color:#64748b;">模式</span>
            <span style="font-weight:600;">{'📈 合约 ' + str(st.session_state.paper_leverage) + 'x' if st.session_state.paper_market_mode == 'futures' else '💵 现货'}</span>
        </div>
        <div class="status-item">
            <span style="color:#64748b;">策略</span>
            <span style="font-weight:600;">{selected_strategy}</span>
        </div>
        <div class="status-item" style="margin-left:auto;">
            <span style="color:#64748b;">最后更新</span>
            <span style="font-weight:600;">{last_refresh or '-'}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.progress(min(buffer_len / max(data_count, 1), 1.0), text="处理进度")

# ════════════════════════════════════════════════════════════════
# 4. PROCESSING CORE — real-time loop
# ════════════════════════════════════════════════════════════════

df = st.session_state.get("paper_data")
strategy = st.session_state.get("paper_strategy_instance")

# 4a. Always fetch ticker when monitoring
if paper_running:
    try:
        ticker = fetch_ticker(cfg)
        st.session_state.paper_ticker = ticker
        st.session_state.paper_last_refresh = datetime.now().strftime("%H:%M:%S")
    except Exception:
        pass

# 4b. Main processing
if paper_running and strategy is not None:
    if df is None or df.empty:
        try:
            with st.spinner("正在从 OKX 获取历史 K 线..."):
                df = fetch_latest_klines(cfg, limit=data_count, timeframe=timeframe)
                if df is not None and not df.empty:
                    st.session_state.paper_data = df
                    st.success(f"✅ 获取 {len(df)} 根 K 线成功")
                else:
                    st.warning("OKX 返回空数据，请稍后重试")
                    st.session_state.paper_running = False
                    st.rerun()
        except Exception as e:
            st.error(f"获取数据失败: {e}")
            st.session_state.paper_running = False
            st.rerun()

    if df is not None and not df.empty:
        processed = len(strategy._bar_buffer) if hasattr(strategy, "_bar_buffer") and strategy._bar_buffer is not None else 0
        total = len(df)

        if processed < total:
            # ── Phase 2: replay cached bars ──
            bar = df.iloc[processed]
            state = engine.run_bar(bar, strategy, risk_engine)
            st.session_state.paper_state = state
            if processed % 10 == 0 and processed > 0:
                pct = processed / total * 100
                st.toast(f"⏳ 回放中: {processed}/{total} ({pct:.0f}%)", icon="📊")
            time.sleep(0.05)
            st.rerun()

        else:
            # ── Phase 3: real-time polling ──
            fetch_failures = _ss("paper_fetch_failures", 0)
            try:
                latest_df = fetch_latest_klines(cfg, limit=5, timeframe=timeframe)
                st.session_state.paper_fetch_failures = 0  # reset on success
            except Exception as e:
                st.session_state.paper_fetch_failures = fetch_failures + 1
                # 连续 3 次以上失败才显示 warning，避免网络波动时频繁弹窗
                if st.session_state.paper_fetch_failures >= 3:
                    st.warning(f"📡 获取最新 K 线失败 ({st.session_state.paper_fetch_failures} 次): {e}")
                time.sleep(_refresh_interval_s(timeframe))
                st.rerun()

            if latest_df is not None and not latest_df.empty:
                last_known = df.index[-1]
                new_bars = latest_df[latest_df.index > last_known]

                if not new_bars.empty:
                    df = pd.concat([df, new_bars])
                    df = df[~df.index.duplicated(keep="last")].sort_index()
                    df = df.tail(500)
                    st.session_state.paper_data = df

                    bar = new_bars.iloc[0]
                    state = engine.run_bar(bar, strategy, risk_engine)
                    st.session_state.paper_state = state
                    st.toast(f"📡 新 K 线: ${float(bar['close']):,.2f} | 信号: {state.get('signal','')}")
                    st.rerun()
                else:
                    time.sleep(_refresh_interval_s(timeframe))
                    st.rerun()
            else:
                time.sleep(_refresh_interval_s(timeframe))
                st.rerun()

# ════════════════════════════════════════════════════════════════
# 5. DISPLAY — KPIs, Charts, Positions, Trades
# ════════════════════════════════════════════════════════════════

paper_state = st.session_state.get("paper_state")

if df is not None and paper_state is not None:
    account = paper_state.get("account", {})

    # ── 5a. KPI Row ──
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📊 当前状态</div>', unsafe_allow_html=True)

    is_futures = st.session_state.paper_market_mode == "futures"

    if is_futures:
        kpi_cols = st.columns(8)
        with kpi_cols[0]:
            render_metric_card("price", float(paper_state.get("price", 0)))
        with kpi_cols[1]:
            render_metric_card("equity", float(account.get("equity", 0)))
        with kpi_cols[2]:
            wb = account.get("wallet_balance", 0)
            st.metric("钱包余额", f"${wb:,.2f}")
        with kpi_cols[3]:
            upnl = float(account.get("unrealized_pnl_pct", 0))
            render_metric_card("unrealized_pnl_pct", upnl)
        with kpi_cols[4]:
            rpnl = float(account.get("total_realized_pnl", 0))
            render_metric_card("total_pnl", rpnl)
        with kpi_cols[5]:
            direction = account.get("direction", "flat")
            pos_val = float(account.get("position", 0))
            dir_emoji = {"long": "🟢", "short": "🔴", "flat": "⚪"}
            st.metric(f"持仓 {dir_emoji.get(direction, '')}",
                      f"{pos_val:.6f}" if pos_val > 0 else "-")
        with kpi_cols[6]:
            lev = account.get("leverage", 0)
            st.metric("杠杆", f"{lev}x" if lev else "-")
        with kpi_cols[7]:
            sig = paper_state.get("signal", "hold")
            sig_emoji = {"buy": "🟢", "sell": "🔴", "exit": "⚫", "hold": "⚪"}
            render_metric_card("signal", f"{sig_emoji.get(sig, '⚪')} {sig.upper()}")
    else:
        kpi_cols = st.columns(7)
        with kpi_cols[0]:
            render_metric_card("price", float(paper_state.get("price", 0)))
        with kpi_cols[1]:
            render_metric_card("equity", float(account.get("equity", 0)))
        with kpi_cols[2]:
            render_metric_card("balance", float(account.get("balance", 0)))
        with kpi_cols[3]:
            upnl = float(account.get("unrealized_pnl_pct", 0))
            render_metric_card("unrealized_pnl_pct", upnl)
        with kpi_cols[4]:
            rpnl = float(account.get("total_realized_pnl", 0))
            render_metric_card("total_pnl", rpnl)
        with kpi_cols[5]:
            pos_val = float(account.get("position", 0))
            st.metric(f"持仓 ({BASE_CURRENCY})", f"{pos_val:.6f}")
        with kpi_cols[6]:
            sig = paper_state.get("signal", "hold")
            sig_emoji = {"buy": "🟢", "sell": "🔴", "exit": "⚫", "hold": "⚪"}
            render_metric_card("signal", f"{sig_emoji.get(sig, '⚪')} {sig.upper()}")

    st.markdown('</div>', unsafe_allow_html=True)

    # ── 5b. Risk, Liquidation & Trade Notification ──
    if not paper_state.get("risk_ok", True):
        st.warning(f"⚠️ 风控拒绝: {paper_state.get('risk_reason', '未知')}")

    liq = paper_state.get("liquidation")
    if liq:
        st.error(f"💥 **强平触发!** {liq.get('direction', '').upper()} @ ${liq.get('price', 0):,.2f} | 损失 ${liq.get('margin_lost', 0):,.2f}")

    trade = paper_state.get("trade")
    if trade:
        side = trade.get("side", "")
        price = trade.get("price", 0)
        note = trade.get("note", "")
        if note and note.startswith("no_"):
            pass  # 忽略无效信号
        elif side == "buy":
            st.success(f"✅ **买入执行** ${price:,.2f} | 数量 {trade.get('size', 0):.6f} {BASE_CURRENCY} | 资金 ${account.get('balance', 0):,.2f}")
        elif side == "sell" and trade.get("pnl"):
            pnl = trade.get("pnl", 0)
            icon = "📈" if pnl > 0 else "📉"
            st.info(f"{icon} **卖出执行** ${price:,.2f} | PnL ${pnl:.2f} | 资金 ${account.get('balance', 0):,.2f}")
        elif side == "open_long":
            st.success(f"🟢 **开多** ${price:,.2f} | 数量 {trade.get('size', 0):.6f} {BASE_CURRENCY} | 保证金 ${trade.get('margin', 0):,.2f}")
        elif side == "open_short":
            st.info(f"🔴 **开空** ${price:,.2f} | 数量 {trade.get('size', 0):.6f} {BASE_CURRENCY} | 保证金 ${trade.get('margin', 0):,.2f}")
        elif side in ("close_long", "close_short"):
            pnl = trade.get("pnl", 0)
            icon = "📈" if pnl > 0 else "📉"
            dir_label = "平多" if side == "close_long" else "平空"
            st.info(f"{icon} **{dir_label}** ${price:,.2f} | PnL ${pnl:,.2f} | 钱包 ${account.get('wallet_balance', 0):,.2f}")
        elif side == "liquidation" and trade.get("pnl"):
            st.error(f"💥 **强平** {trade.get('direction', '').upper()} @ ${price:,.2f} | PnL ${trade['pnl']:,.2f}")

    st.divider()

    # ── 5c. Charts ──
    chart_tab1, chart_tab2 = st.tabs(["📈 K 线 + 信号", "📊 权益曲线"])

    with chart_tab1:
        fig = go.Figure()
        df_display = df.tail(50).copy()
        fig.add_trace(go.Candlestick(
            x=df_display.index,
            open=df_display["open"],
            high=df_display["high"],
            low=df_display["low"],
            close=df_display["close"],
            name="K 线",
            increasing_line_color="#059669",
            decreasing_line_color="#dc2626",
            increasing_fillcolor="#059669",
            decreasing_fillcolor="#dc2626",
        ))

        # Current price from ticker
        if ticker_data:
            fig.add_hline(
                y=ticker_data["last"],
                line_dash="dash",
                line_color="#2563eb",
                line_width=1.5,
                annotation_text=f"实时 ${ticker_data['last']:,.2f}",
                annotation_position="right",
                annotation=dict(font=dict(size=11, color="#2563eb")),
            )

        # Trade markers
        if hasattr(engine, "account") and engine.account.trades:
            for t in engine.account.trades:
                s = t.get("side", "")
                if s == "buy" and t.get("price"):
                    fig.add_trace(go.Scatter(
                        x=[t["time"]], y=[t["price"]],
                        mode="markers", name="买入",
                        showlegend=False,
                        marker=dict(
                            color="#059669", size=14,
                            symbol="triangle-up",
                            line=dict(color="white", width=2),
                        ),
                    ))
                elif s == "sell" and t.get("price"):
                    fig.add_trace(go.Scatter(
                        x=[t["time"]], y=[t["price"]],
                        mode="markers", name="卖出",
                        showlegend=False,
                        marker=dict(
                            color="#dc2626", size=14,
                            symbol="triangle-down",
                            line=dict(color="white", width=2),
                        ),
                    ))

        fig.update_layout(
            title=dict(
                text=f"{cfg.trading.symbol} — {_friendly_tf(timeframe)} (实时)",
                font=dict(size=14, color="#0f172a"),
                x=0, xanchor="left",
            ),
            xaxis_title="时间",
            yaxis_title="价格 ($)",
            plot_bgcolor="#ffffff",
            paper_bgcolor="#f8fafc",
            font=dict(color="#475569", family="-apple-system, BlinkMacSystemFont, sans-serif"),
            hovermode="x unified",
            hoverlabel=dict(
                bgcolor="#1e293b",
                font=dict(color="white", size=12),
                bordercolor="#334155",
            ),
            xaxis_rangeslider_visible=False,
            height=380,
            margin=dict(l=40, r=20, t=50, b=40),
        )
        fig.update_xaxes(
            gridcolor="#e2e8f0",
            zeroline=False,
            showgrid=True,
            linecolor="#e2e8f0",
        )
        fig.update_yaxes(
            gridcolor="#e2e8f0",
            zeroline=False,
            showgrid=True,
            linecolor="#e2e8f0",
        )
        st.plotly_chart(fig, use_container_width=True)

    with chart_tab2:
        eq_hist = account.get("equity_history", [])
        if eq_hist:
            fig = equity_curve_chart(eq_hist, title="模拟盘权益曲线", theme=st.session_state.get("theme_mode", "light"))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("尚无权益历史数据")

    st.divider()

    # ── 5d. Position Details ──
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📋 持仓详情</div>', unsafe_allow_html=True)

    is_futures = st.session_state.paper_market_mode == "futures"

    if is_futures:
        pos_cols = st.columns(5)
        with pos_cols[0]:
            direction = account.get("direction", "flat")
            pos_val = float(account.get("position", 0))
            dir_emoji = {"long": "🟢", "short": "🔴", "flat": "⚪"}
            st.metric(f"持仓 {dir_emoji.get(direction, '')}",
                      f"{pos_val:.6f}" if pos_val > 0 else "-")
        with pos_cols[1]:
            st.metric("钱包余额", f"${account.get('wallet_balance', 0):,.2f}")
        with pos_cols[2]:
            st.metric("可用余额", f"${account.get('available_balance', 0):,.2f}")
        with pos_cols[3]:
            liq = float(account.get("liquidation_price", 0))
            price = float(paper_state.get("price", 0))
            if liq > 0 and price > 0:
                dist = abs(price - liq) / price * 100
                danger = "🔴" if dist < 5 else "🟡" if dist < 15 else "⚪"
                st.metric(f"强平价 {danger}", f"${liq:,.2f}",
                          delta=f"{dist:.1f}%" if price > liq else "⚠️ 已触发",
                          delta_color="inverse" if price > liq else "off")
            else:
                st.metric("强平价", "-")
        with pos_cols[4]:
            render_metric_card("total_pnl", float(account.get("total_realized_pnl", 0)))
    else:
        pos_cols = st.columns(4)
        with pos_cols[0]:
            pos_val = float(account.get("position", 0))
            st.metric(f"持仓 ({BASE_CURRENCY})", f"{pos_val:.6f}")
        with pos_cols[1]:
            render_metric_card("balance", float(account.get("balance", 0)))
        with pos_cols[2]:
            rpnl = float(account.get("total_realized_pnl", 0))
            render_metric_card("total_pnl", rpnl)
        with pos_cols[3]:
            render_metric_card("equity", float(account.get("equity", 0)))

    st.markdown('</div>', unsafe_allow_html=True)

    # ── 5e. Trade History ──
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📋 交易记录</div>', unsafe_allow_html=True)

    if hasattr(engine, "account") and engine.account.trades:
        df_trades = pd.DataFrame(engine.account.trades)
        df_trades = df_trades.iloc[::-1].reset_index(drop=True)
        is_fut = st.session_state.paper_market_mode == "futures"
        display_cols = ["time", "side", "price", "size", "pnl", "fee"]
        if is_fut:
            display_cols += ["margin", "leverage", "wallet_after"]
        else:
            display_cols += ["balance_after"]
        existing = [c for c in display_cols if c in df_trades.columns]
        df_display = df_trades[existing]

        def _color_pnl(val):
            if isinstance(val, (int, float)):
                return "color: #059669" if val and val > 0 else ("color: #dc2626" if val and val < 0 else "")
            return ""

        if "pnl" in df_display.columns:
            styled = df_display.style.map(_color_pnl, subset=["pnl"])
        else:
            styled = df_display

        st.dataframe(styled, use_container_width=True, hide_index=True)

        csv = df_trades.to_csv(index=False).encode("utf-8")
        st.download_button("📥 导出交易记录", csv, "paper_trades.csv", "text/csv")
    else:
        st.info("暂无交易记录，启动监控后将在此显示")

    st.markdown('</div>', unsafe_allow_html=True)

    # ── 5f. Strategy & Account Info ──
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📖 账户与策略信息</div>', unsafe_allow_html=True)

    is_futures = st.session_state.paper_market_mode == "futures"
    total_bars = len(strategy._bar_buffer) if strategy and hasattr(strategy, "_bar_buffer") and strategy._bar_buffer is not None else 0
    info_cols = st.columns(5)
    info_cols[0].metric("策略", selected_strategy)
    info_cols[1].metric("已处理 K 线", total_bars)
    info_cols[2].metric("交易次数", len(engine.account.trades))
    if is_futures:
        info_cols[3].metric("钱包余额", f"${getattr(engine.account, 'wallet_balance', 0):,.2f}")
        info_cols[4].metric("总权益", f"${engine.account.total_equity:,.2f}")
    else:
        info_cols[3].metric("初始资金", f"${engine.account.initial_balance:,.0f}")
        info_cols[4].metric("当前权益", f"${engine.account.equity:,.2f}")

    # 合约强平预警
    if is_futures:
        liq_price = float(account.get("liquidation_price", 0))
        curr_price = float(paper_state.get("price", 0))
        if liq_price > 0 and curr_price > 0:
            direction = account.get("direction", "")
            dist = abs(curr_price - liq_price) / curr_price * 100
            if direction == "long" and curr_price > liq_price:
                if dist < 10:
                    st.warning(f"⚠️ 距强平价仅 {dist:.1f}% (强平 ${liq_price:,.2f})")
            elif direction == "short" and curr_price < liq_price:
                if dist < 10:
                    st.warning(f"⚠️ 距强平价仅 {dist:.1f}% (强平 ${liq_price:,.2f})")

    if ticker_data:
        st.caption(f"OKX 实时价格: ${ticker_data['last']:,.2f} | "
                   f"更新时间: {st.session_state.get('paper_last_refresh', '-')}")

    st.markdown('</div>', unsafe_allow_html=True)

    # ── 5g. Trade Statistics (collapsible) ──
    if engine.account.trades:
        with st.expander("📊 交易统计"):
            wins = [t for t in engine.account.trades if t.get("pnl", 0) > 0]
            losses = [t for t in engine.account.trades if t.get("pnl", 0) < 0]
            total_pnl = sum(t.get("pnl", 0) for t in engine.account.trades)
            win_rate = len(wins) / len(engine.account.trades) * 100 if engine.account.trades else 0
            avg_win = sum(t.get("pnl", 0) for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t.get("pnl", 0) for t in losses) / len(losses) if losses else 0

            stats_cols = st.columns(6)
            stats_cols[0].metric("总盈亏", f"${total_pnl:+.2f}")
            stats_cols[1].metric("胜率", f"{win_rate:.1f}%")
            stats_cols[2].metric("盈利次数", len(wins))
            stats_cols[3].metric("亏损次数", len(losses))
            stats_cols[4].metric("平均盈利", f"${avg_win:+.2f}")
            stats_cols[5].metric("平均亏损", f"${avg_loss:+.2f}")

            # ROI
            init_cap = engine.account.initial_balance if hasattr(engine.account, 'initial_balance') else engine.account.initial_wallet
            roi = total_pnl / init_cap * 100 if init_cap > 0 else 0
            roi_color = "#059669" if roi > 0 else "#dc2626"
            st.markdown(
                f"**投资回报率 (ROI):** "
                f"<span style='color:{roi_color}; font-weight:700; font-size:1.2rem;'>"
                f"{roi:+.2f}%</span>"
                f"&nbsp;&nbsp;(初始 ${init_cap:,.0f} → "
                f"当前 ${engine.account.total_equity if hasattr(engine.account, 'total_equity') else engine.account.equity:,.2f})",
                unsafe_allow_html=True)

else:
    if df is None:
        st.markdown("""
        <div style="text-align:center; padding:3rem 1rem; color:#94a3b8;">
            <div style="font-size:3rem; margin-bottom:0.5rem;">💰</div>
            <div style="font-size:1.1rem; font-weight:600; color:#475569; margin-bottom:0.25rem;">
                模拟交易尚未启动
            </div>
            <div style="font-size:0.9rem;">
                设置建仓金额和策略参数后，点击「启动监控」自动获取 OKX 实时数据并开始模拟交易
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("数据已加载，点击「启动监控」开始逐根处理 K 线信号")
