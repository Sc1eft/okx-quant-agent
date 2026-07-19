"""Paper Trading page - 实盘数据模拟交易（建仓金额 + 策略参数可调）

Real-time flow:
  启动监控 →
    Phase 1: 自动获取初始 K 线数据 →
    Phase 2: 逐根回放已缓存数据 →
    Phase 3: 持续从 OKX 拉取最新 K 线 + Ticker →
    Phase 4: 有新 K 线 → 策略处理 → 风控 → 模拟成交 → 更新界面
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Force reload strategy modules to bypass Streamlit caching（每次会话仅执行一次）
if not st.session_state.get("_paper_modules_reloaded"):
    for _mod in list(sys.modules.keys()):
        if _mod.startswith("strategies") or _mod.startswith("execution") or _mod.startswith("risk"):
            import importlib
            importlib.reload(sys.modules[_mod])
    st.session_state._paper_modules_reloaded = True

import strategies.base as _sb
import execution.paper_runner as _pr
import risk.rules as _risk

from frontend.utils.session_state import get_config, save_config
from frontend.utils.data_provider import fetch_latest_klines, fetch_ticker
from frontend.components.charts import equity_curve_chart, multi_equity_chart
from frontend.components.tv_lightweight import build_kline_tv_html
from frontend.components.metrics_display import render_metric_card
from frontend.components.layout import (
    page_header, section_card, status_bar, ticker_bar, empty_state, metric_row,
)
from frontend.utils.helpers import ss as _ss


@st.cache_resource
def _paper_runtime() -> dict:
    """模拟盘运行时（进程级缓存）：浏览器刷新/新会话不丢失，重启 Streamlit 进程才清空"""
    return {}


def _stop_paper_monitor():
    """停止监控：停掉无头 runner 进程，同时清 session 与进程级运行时的运行标记"""
    _pr.stop_runner()
    st.session_state.paper_running = False
    _paper_runtime()["running"] = False


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def _refresh_interval_s(tf: str) -> int:
    return {"15m": 5, "1h": 10, "4h": 30, "1d": 60}.get(tf, 10)


def _friendly_tf(tf: str) -> str:
    return {"15m": "15 分钟", "1h": "1 小时", "4h": "4 小时", "1d": "1 天"}.get(tf, tf)


def _is_multi_state(ps) -> bool:
    """paper_state 是否为多 slot 结构（{label: state}；单策略 state 必有 account 键）。"""
    return isinstance(ps, dict) and bool(ps) and "account" not in ps


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

page_header(
    "💰 模拟交易",
    "用 OKX 真实行情跑假钱：设置资金与策略 → 启动监控 → 实时观察策略表现",
    badge="模拟盘 · 无真实资金",
    badge_type="blue",
)

cfg = get_config()
BASE_CURRENCY = cfg.trading.symbol.split("-")[0]  # "ETH-USDT" → "ETH"
strategies = _sb.get_available_strategies()

if not strategies:
    st.warning("无可用的策略")
    st.stop()

strategy_names = list(strategies.keys())

# ── Init: 无头 runner 进程为真相源；浏览器刷新/新会话/Streamlit 重启后从这里接管现场 ──
_rt = _paper_runtime()
if _pr.is_runner_running():
    st.session_state.paper_running = True
    _rt["running"] = True
    _rs = _pr.read_state()
    if _rs:
        if _rs.get("paper_state") and st.session_state.get("paper_state") is None:
            st.session_state.paper_state = _rs["paper_state"]
        _rc = _rs.get("config") or {}
        if _rc:  # 恢复控制面板显示（与运行中的 runner 配置对齐）
            st.session_state.paper_market_mode = _rc.get("mode", st.session_state.get("paper_market_mode", "futures"))
            st.session_state.paper_leverage = _rc.get("leverage", st.session_state.get("paper_leverage", 10))
            st.session_state.paper_strategy = _rc.get("strategy", st.session_state.get("paper_strategy"))
            st.session_state.paper_tf = _rc.get("timeframe", st.session_state.get("paper_tf", "1h"))
            _slots = _rc.get("strategies") or []
            if _slots:  # 多策略配置 → 恢复策略多选框选中项（label 即策略名）
                st.session_state.paper_strategies_sel = [
                    s.get("label") or s.get("strategy") for s in _slots]
elif st.session_state.get("paper_running") or _rt.get("running"):
    # runner 已退出（被停止或崩溃）→ 落回停止态
    st.session_state.paper_running = False
    _rt["running"] = False

# 沙盒风控引擎持久化到 session_state（原 5_🛡_Risk 页共享实例，现由本页托管）
if st.session_state.get("risk_engine") is None:
    st.session_state.risk_engine = _risk.RiskEngine(cfg.risk)
risk_engine = st.session_state.risk_engine

paper_running = _ss("paper_running", False)

# 「自动刷新」开启且监控中时，fragment 定时刷新间隔（秒）；否则不自动刷新
_auto_refresh = st.session_state.get("paper_auto_refresh", True)
_live_refresh_s = (
    _refresh_interval_s(st.session_state.get("paper_trade_tf", "1h"))
    if (paper_running and _auto_refresh) else None
)

# ════════════════════════════════════════════════════════════════
# 1. TICKER BAR — dark gradient, shows live price（独立 fragment，不闪烁）
# ════════════════════════════════════════════════════════════════

@st.fragment(run_every=_live_refresh_s)
def _ticker_fragment():
    """独立获取并渲染实时价格条，不触发全页重渲染"""
    if st.session_state.get("paper_running", False):
        try:
            st.session_state.paper_ticker = fetch_ticker(cfg)
            _paper_runtime()["paper_ticker"] = st.session_state.paper_ticker
            st.session_state.paper_last_refresh = datetime.now().strftime("%H:%M:%S")
        except Exception:
            pass

    tk = st.session_state.get("paper_ticker")
    if tk:
        price_color = "green" if tk.get("change_24h", 0) >= 0 else "red"
        change_str = f"{tk.get('change_24h', 0):+.2f}%" if tk.get("change_24h") is not None else ""
        bid_str = f"${tk['bid']:,.2f}" if tk.get("bid") else "N/A"
        ask_str = f"${tk['ask']:,.2f}" if tk.get("ask") else "N/A"
        ticker_bar([
            {"label": cfg.trading.symbol,
             "value": f"${tk['last']:,.2f} {change_str}", "color": price_color},
            {"label": "买一 / 卖一", "value": f"{bid_str} / {ask_str}"},
            {"label": "24h 成交量", "value": f"{tk.get('volume_24h', 0):,.0f} {BASE_CURRENCY}"},
        ])
    else:
        st.info("💡 启动监控后实时行情将在此显示")

_ticker_fragment()

# ════════════════════════════════════════════════════════════════
# 2. CONTROL PANEL — 基础设置外露，高级参数折叠
# ════════════════════════════════════════════════════════════════

with section_card("控制面板", "⚙"):
    # ── 基础设置：策略 / 周期 / 初始资金 / 杠杆 ──
    base_cols = st.columns(4)

    with base_cols[0]:
        if "paper_strategies_sel" not in st.session_state:
            st.session_state.paper_strategies_sel = [_ss("paper_strategy", strategy_names[0])]
        st.session_state.paper_strategies_sel = (
            [s for s in st.session_state.paper_strategies_sel if s in strategy_names]
            or [strategy_names[0]])
        selected_strategies = st.multiselect(
            "📈 交易策略（可多选并行）", strategy_names,
            disabled=paper_running,
            format_func=lambda n: f"{n} — {strategies[n]['description']}",
            key="paper_strategies_sel",
            help="多选时每个策略独立账户并行模拟，权益曲线叠加对比")
        if not selected_strategies:
            selected_strategies = [strategy_names[0]]
        selected_strategy = selected_strategies[0]  # 主策略：参数 hint / 兼容字段用

    with base_cols[1]:
        _ss("paper_tf", "1h")
        tf_options = ["1h", "15m", "4h", "1d"]
        def_tf_idx = tf_options.index(st.session_state.paper_tf) if st.session_state.paper_tf in tf_options else 0
        timeframe = st.selectbox("⏱ K 线周期", tf_options, index=def_tf_idx,
                                  disabled=paper_running,
                                  format_func=_friendly_tf,
                                  key="paper_trade_tf")

    with base_cols[2]:
        _ss("paper_initial_balance", 10000.0)
        initial_balance = st.number_input(
            "💰 初始资金 (USDT)",
            min_value=100.0, max_value=10_000_000.0,
            value=st.session_state.paper_initial_balance, step=1000.0,
            disabled=paper_running, key="paper_initial_balance_input",
            help="模拟交易的起始总资金（建仓金额）")

    with base_cols[3]:
        _ss("paper_market_mode", "futures")
        _ss("paper_leverage", 10)
        if st.session_state.paper_market_mode == "futures":
            leverage = st.slider(
                "🔧 杠杆倍数",
                min_value=1, max_value=125,
                value=st.session_state.paper_leverage,
                step=1, disabled=paper_running,
                key="paper_leverage_slider",
                help="OKX ETH-USDT 永续合约最大 125x（交易模式在下方「高级设置」中切换）")
            st.session_state.paper_leverage = leverage
        else:
            st.markdown("""<div style="padding:1.5rem 0; color:#94a3b8; font-size:0.85rem;">现货模式 · 全额交易</div>""", unsafe_allow_html=True)

    # ── 策略参数 (expander)：每个选中策略各一组（widget key 按策略名前缀，互不冲突）──
    with st.expander("⚙ 策略参数", expanded=False):
        params_map = {}
        for _name in selected_strategies:
            if len(selected_strategies) > 1:
                st.markdown(f"""
                <div style="margin-bottom:0.25rem; color:#475569; font-size:0.9rem;">
                    <strong>{_name}</strong> — {strategies[_name]['description']}
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="margin-bottom:0.75rem; color:#475569; font-size:0.9rem;">
                    当前策略: <strong>{_name}</strong> — {strategies[_name]['description']}
                </div>
                """, unsafe_allow_html=True)

            strategy_info = strategies[_name]
            default_params = dict(strategy_info["params"])
            # Merge in common risk params that all strategies share
            if "stop_loss_pct" not in default_params:
                default_params["stop_loss_pct"] = 5.0
                default_params["take_profit_pct"] = 10.0
                default_params["trailing_stop_activation"] = 6.0
                default_params["trailing_stop_distance"] = 3.0
                default_params["position_timeout_bars"] = 72

            params_map[_name] = _render_strategy_params(_name, default_params)
        st.session_state.paper_strategy_params_map = params_map
        st.session_state.paper_strategy_params = params_map[selected_strategy]

    # ── 高级设置 (expander)：数据量 / 仓位 / 交易模式 / 秒级止损 ──
    with st.expander("🔧 高级设置", expanded=False):
        adv1 = st.columns(2)
        with adv1[0]:
            _ss("paper_data_count", 100)
            data_count = st.slider("📊 初始 K 线数", 50, 300,
                                   st.session_state.paper_data_count,
                                   step=50, disabled=paper_running,
                                   key="paper_trade_data_count",
                                   help="首次加载的历史 K 线数量")
        with adv1[1]:
            _ss("paper_position_size_pct", 10.0)
            position_size_pct = st.slider(
                "📊 单笔仓位比例",
                1.0, 100.0, st.session_state.paper_position_size_pct,
                step=1.0, disabled=paper_running,
                key="paper_position_size_pct_input",
                help="每次开仓使用的资金比例（占当前余额的百分比）")

        adv2 = st.columns(2)
        with adv2[0]:
            market_mode = st.selectbox(
                "📊 交易模式",
                ["futures", "spot"],
                index=0 if st.session_state.paper_market_mode == "futures" else 1,
                disabled=paper_running,
                key="paper_market_selector",
                format_func=lambda m: {"spot": "💵 现货", "futures": "📈 合约"}[m],
            )
            st.session_state.paper_market_mode = market_mode
        with adv2[1]:
            tick_exit = st.checkbox(
                "⚡ 秒级止损（心跳 tick 驱动）",
                value=st.session_state.get("paper_tick_exit", True),
                disabled=paper_running,
                key="paper_tick_exit_checkbox",
                help="持仓期间用秒级心跳价格检查止损/止盈/移动止损，触及立即平仓，不等 K 线收盘；"
                     "依赖心跳采集器（启动监控时自动拉起）")
            st.session_state.paper_tick_exit = tick_exit

    # ── Hint about current settings ──
    if not paper_running:
        _params = st.session_state.get("paper_strategy_params", {})
        _fund_hint = (
            f"资金: ${initial_balance:,.0f} | "
            f"仓位: {position_size_pct:.0f}% | "
            f"策略: {'+'.join(selected_strategies)}"
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

    # ── Buttons ──
    btn_cols = st.columns([1.3, 1, 2.2])

    with btn_cols[0]:
        if paper_running:
            st.button("⏹ 停止监控", use_container_width=True, type="primary",
                      on_click=_stop_paper_monitor)
        else:
            if st.button("▶ 启动监控", use_container_width=True, type="primary"):
                _exit_keys = ("stop_loss_pct", "take_profit_pct",
                              "trailing_stop_activation", "trailing_stop_distance")
                _params_map = st.session_state.get("paper_strategy_params_map") or \
                    {selected_strategy: st.session_state.get("paper_strategy_params", {})}

                # 每个选中策略一个独立 slot（label=策略名；杠杆/钱包/仓位比例为共享控件值）
                slot_cfgs = []
                for _name in selected_strategies:
                    _sp = _params_map.get(_name, {})
                    slot_cfgs.append({
                        "label": _name,
                        "strategy": _name,
                        "strategy_params": _sp,
                        "exit_params": {k: _sp[k] for k in _exit_keys if k in _sp},
                        "leverage": st.session_state.get("paper_leverage", 10),
                        "wallet_balance": initial_balance,
                        "position_size_pct": position_size_pct / 100.0,
                    })

                # 主策略参数（含止盈止损等退出参数，同时喂给策略与 tick 级退出）
                strategy_params = _params_map.get(selected_strategy, {})
                exit_params = {k: strategy_params[k] for k in _exit_keys if k in strategy_params}

                # 写入运行配置并拉起无头 runner 进程（浏览器关闭/刷新不影响交易循环）
                _pr.write_config({
                    "mode": market_mode,
                    "strategy": selected_strategy,
                    "strategy_params": strategy_params,
                    "exit_params": exit_params,
                    "wallet_balance": initial_balance,
                    "leverage": st.session_state.get("paper_leverage", 10),
                    "position_size_pct": position_size_pct / 100.0,
                    "timeframe": timeframe,
                    "initial_bars": data_count,
                    "tick_exit": st.session_state.get("paper_tick_exit", True),
                    "strategies": slot_cfgs,
                })
                if not _pr.start_runner():
                    st.error("模拟盘进程启动失败，请查看 logs/paper_runner.log")
                    st.stop()

                # ⚡ 秒级止损依赖心跳采集器（幂等，已在运行则直接返回）
                if st.session_state.get("paper_tick_exit", True):
                    try:
                        from data.heartbeat_db import start_collector as _start_hb
                        _start_hb()
                    except Exception as _e:
                        st.warning(f"心跳采集器启动失败，秒级止损不可用: {_e}")

                st.session_state.paper_running = True
                st.session_state.paper_strategy = selected_strategy

                # Reset state（展示数据由 runner 状态文件 + 页面独立拉取填充）
                st.session_state.paper_state = None
                st.session_state.paper_data = None
                st.session_state.paper_ticker = None
                st.session_state.paper_last_refresh = None

                _rt.update(running=True)
                st.rerun()

    with btn_cols[1]:
        _ss("paper_auto_refresh", True)
        st.checkbox("自动刷新", key="paper_auto_refresh")

# ── 风控设置与状态（原 5_🛡_Risk 页并入） ──
with st.expander("🛡 风控设置与状态", expanded=False):
    _rstate = risk_engine.state
    _paused = st.session_state.get("risk_paused", False)

    rs_cols = st.columns(4)
    with rs_cols[0]:
        st.metric("当前状态", "🔴 已暂停" if _paused else "🟢 运行中")
    with rs_cols[1]:
        st.metric("连续亏损", f"{_rstate.consecutive_losses}/{cfg.risk.max_consecutive_losses}")
    with rs_cols[2]:
        st.metric("当日亏损", f"{_rstate.daily_loss_pct:.2f}% / {cfg.risk.max_daily_loss_pct}%")
    with rs_cols[3]:
        st.metric("当日交易", f"{_rstate.daily_trades} / 20")

    if _rstate.is_paused:
        st.warning(f"⏸ 风控已暂停: {_rstate.pause_reason}")

    rc_cols = st.columns(2)
    with rc_cols[0]:
        if _paused:
            if st.button("▶ 恢复交易", key="risk_resume", use_container_width=True):
                st.session_state.risk_paused = False
                _rstate.is_paused = False
                _rstate.pause_reason = ""
                st.rerun()
        else:
            if st.button("⏸ 暂停交易", key="risk_pause", use_container_width=True):
                st.session_state.risk_paused = True
                _rstate.is_paused = True
                _rstate.pause_reason = "手动暂停"
                st.rerun()
    with rc_cols[1]:
        if st.button("🔄 重置当日", key="risk_reset_daily", use_container_width=True):
            _rstate.reset_daily(10000)
            _rstate.consecutive_losses = 0
            st.session_state.risk_paused = False
            st.success("当日风控状态已重置")

    st.divider()
    _rcfg = cfg.risk
    rk_cols = st.columns(3)
    with rk_cols[0]:
        st.markdown("**仓位限制**")
        new_max_pos = st.slider("最大仓位 (%)", 5.0, 100.0, _rcfg.max_position_pct * 100, key="risk_max_pos")
        new_max_order = st.slider("单笔最大 (%)", 1.0, 50.0, _rcfg.max_single_order_pct * 100, key="risk_max_order")
    with rk_cols[1]:
        st.markdown("**亏损限制**")
        new_max_loss = st.slider("日最大亏损 (%)", 0.5, 20.0, _rcfg.max_daily_loss_pct, step=0.5, key="risk_max_loss")
        new_max_cons = st.slider("连续止损次数", 1, 10, _rcfg.max_consecutive_losses, key="risk_max_cons")
    with rk_cols[2]:
        st.markdown("**冷却/恢复**")
        new_cooldown = st.slider("冷却 (根K线)", 0, 50, _rcfg.cooldown_bars, key="risk_cooldown")
        new_expiry = st.slider("信号过期 (根K线)", 0, 10, _rcfg.signal_expiry_bars, key="risk_expiry")

    rec_cols = st.columns(3)
    with rec_cols[0]:
        new_recovery = st.selectbox(
            "恢复模式", ["manual", "auto_cool", "switch_strategy"],
            index=["manual", "auto_cool", "switch_strategy"].index(_rcfg.recovery_mode),
            key="risk_recovery",
        )
    with rec_cols[1]:
        new_rec_cooldown = st.number_input("恢复冷却 (根K线)", 0, 100, _rcfg.recovery_cooldown_bars, key="risk_rec_cooldown")
    with rec_cols[2]:
        new_max_restarts = st.number_input("日最大重启次数", 1, 10, _rcfg.max_daily_starts, key="risk_max_restarts")

    if st.button("💾 应用风控设置", type="primary", key="risk_apply"):
        _rcfg.max_position_pct = new_max_pos / 100.0
        _rcfg.max_single_order_pct = new_max_order / 100.0
        _rcfg.max_daily_loss_pct = new_max_loss
        _rcfg.max_consecutive_losses = new_max_cons
        _rcfg.cooldown_bars = new_cooldown
        _rcfg.signal_expiry_bars = new_expiry
        _rcfg.recovery_mode = new_recovery
        _rcfg.recovery_cooldown_bars = new_rec_cooldown
        _rcfg.max_daily_starts = new_max_restarts
        # 用新配置重建风控引擎
        st.session_state.risk_engine = _risk.RiskEngine(cfg.risk)
        save_config()
        st.success("风控参数已更新并保存!")

# ════════════════════════════════════════════════════════════════
# 3-5. MONITOR — 状态栏 + 实时处理 + 动态数据区（独立 fragment，防闪烁）
# ════════════════════════════════════════════════════════════════

@st.fragment(run_every=_live_refresh_s)
def _paper_monitor_fragment():
    """监控状态栏 + K 线处理 + 动态数据展示（独立刷新，不触发全页重渲染）"""
    running = st.session_state.get("paper_running", False)
    df = st.session_state.get("paper_data")
    ticker_data = st.session_state.get("paper_ticker")

    # ── 无头 runner 状态（单一真相源；页面只读，浏览器关闭不影响交易循环）──
    rs = _pr.read_state() if running else None
    prog = (rs or {}).get("progress") or {}
    rs_phase = (rs or {}).get("phase", "")

    # ── 3. STATUS BAR — only when monitoring ──
    if running:
        last_refresh = st.session_state.get("paper_last_refresh")
        if rs_phase == "replaying":
            kline_info = f"回放 {prog.get('processed', 0)}/{prog.get('total', 0)}"
        else:
            _ps = (rs or {}).get("paper_state") or {}
            _ps0 = next(iter(_ps.values()), {}) if _is_multi_state(_ps) else _ps
            _ps_ts = _ps0.get("timestamp", "")
            kline_info = f"最新 {_ps_ts[5:16] if _ps_ts else '-'}"

        _mode_txt = (
            "📈 合约 " + str(st.session_state.paper_leverage) + "x"
            if st.session_state.paper_market_mode == "futures" else "💵 现货"
        )
        status_bar("监控中", [
            ("K 线", kline_info),
            ("周期", _friendly_tf(timeframe)),
            ("模式", _mode_txt),
            ("策略", "+".join(selected_strategies)),
            ("最后更新", last_refresh or "-"),
        ], state="ok")

        if rs_phase == "replaying":
            st.progress(min(prog.get("processed", 0) / max(prog.get("total", 1), 1), 1.0),
                        text="回放历史 K 线")

    # ── 4. RUNNER SYNC — 进程状态同步 + K 线展示数据拉取（与 runner 解耦）──
    if running:
        if not _pr.is_runner_running():
            st.session_state.paper_running = False
            _rt["running"] = False
            st.warning("⚠️ 模拟盘进程已退出，请重新启动监控")
        elif rs:
            if rs_phase == "error":
                st.error(f"模拟盘进程错误: {rs.get('error', '未知')}")
            ps = rs.get("paper_state")
            if ps:
                _prev_ps = st.session_state.paper_state or {}
                st.session_state.paper_state = ps
                _rt["paper_state"] = ps
                # 多 slot（{label: state}）逐 slot 检测新成交；单策略走原逻辑
                _items = ps.items() if _is_multi_state(ps) else [(None, ps)]
                for _lb, _st in _items:
                    _prev_slot = (_prev_ps.get(_lb) or {}) if (_lb and isinstance(_prev_ps, dict)) else _prev_ps
                    _prev_trade = (_prev_slot.get("trade") or {}).get("time")
                    _new_trade = (_st.get("trade") or {}).get("time")
                    if _new_trade and _new_trade != _prev_trade:
                        _tag = f"[{_lb}] " if _lb else ""
                        st.toast(f"📡 {_tag}{_st.get('signal', '').upper()} @ ${float(_st.get('price', 0)):,.2f}", icon="📡")

        # K 线展示数据（页面独立拉取，与 runner 解耦）
        try:
            _df = fetch_latest_klines(cfg, limit=data_count, timeframe=timeframe)
            if _df is not None and not _df.empty:
                df = _df
                st.session_state.paper_data = _df
                _rt["paper_data"] = _df
                st.session_state.paper_fetch_failures = 0
        except Exception as e:
            fetch_failures = _ss("paper_fetch_failures", 0)
            st.session_state.paper_fetch_failures = fetch_failures + 1
            # 连续 3 次以上失败才显示 warning，避免网络波动时频繁弹窗
            if st.session_state.paper_fetch_failures >= 3:
                st.warning(f"📡 获取最新 K 线失败 ({st.session_state.paper_fetch_failures} 次): {e}")

    # ════════════════════════════════════════════════════════════════
    # 5. DISPLAY — KPIs, Charts, Positions, Trades
    # ════════════════════════════════════════════════════════════════

    paper_state = st.session_state.get("paper_state")

    if df is not None and paper_state is not None:
        # ── 多 slot：selectbox 切换查看单策略（KPI/持仓/交易/K 线标记只画选中 slot），权益曲线仍叠加全部 ──
        multi_states = paper_state if _is_multi_state(paper_state) else None
        if multi_states is not None:
            _slot = st.selectbox("🔀 查看策略 Slot", list(multi_states.keys()), key="paper_slot_sel")
            paper_state = multi_states[_slot]

        account = paper_state.get("account", {})
        is_futures = st.session_state.paper_market_mode == "futures"

        # ── 5a. 核心 KPI（4 个最关键的指标，其余收进 expander）──
        sig = paper_state.get("signal", "hold")
        sig_emoji = {"buy": "🟢", "sell": "🔴", "exit": "⚫", "hold": "⚪"}
        sig_color = {"buy": "green", "sell": "red", "exit": "amber", "hold": "gray"}
        upnl = float(account.get("unrealized_pnl_pct", 0))
        direction = account.get("direction", "flat")
        dir_label = {"long": "多头持仓", "short": "空头持仓", "flat": "无方向"}
        pos_val = float(account.get("position", 0))
        equity = float(account.get("equity", 0))
        rpnl = float(account.get("total_realized_pnl", 0))

        metric_row([
            {"label": "总权益", "value": f"${equity:,.2f}",
             "sub": f"已实现盈亏 ${rpnl:+,.2f}", "color": "blue"},
            {"label": "未实现盈亏", "value": f"{upnl:+.2f}%",
             "sub": dir_label.get(direction, ""), "color": "green" if upnl >= 0 else "red"},
            {"label": f"持仓 ({BASE_CURRENCY})",
             "value": f"{pos_val:.6f}" if pos_val > 0 else "无持仓",
             "sub": (f"{account.get('leverage', 0)}x 杠杆" if is_futures
                     else f"余额 ${float(account.get('balance', 0)):,.2f}"),
             "color": "blue" if pos_val > 0 else "gray"},
            {"label": "当前信号", "value": f"{sig_emoji.get(sig, '⚪')} {sig.upper()}",
             "sub": f"现价 ${float(paper_state.get('price', 0)):,.2f}",
             "color": sig_color.get(sig, "gray")},
        ])

        # ── 次级指标折叠 ──
        with st.expander("📈 更多指标（价格 / 余额 / 资金费）", expanded=False):
            if is_futures:
                more_cols = st.columns(5)
                with more_cols[0]:
                    render_metric_card("price", float(paper_state.get("price", 0)))
                with more_cols[1]:
                    st.metric("钱包余额", f"${account.get('wallet_balance', 0):,.2f}")
                with more_cols[2]:
                    st.metric("可用余额", f"${account.get('available_balance', 0):,.2f}")
                with more_cols[3]:
                    render_metric_card("total_pnl", rpnl)
                with more_cols[4]:
                    _fr = paper_state.get("funding_rate")
                    _fr_txt = f"，当前费率 {_fr:+.4%}" if _fr is not None else ""
                    st.metric("资金费", f"${account.get('funding_fee_total', 0.0):+.2f}",
                              help=f"永续合约每 8h 按持仓名义价值 × 费率结算{_fr_txt}；正=净收入，负=净支出")
            else:
                more_cols = st.columns(3)
                with more_cols[0]:
                    render_metric_card("price", float(paper_state.get("price", 0)))
                with more_cols[1]:
                    render_metric_card("balance", float(account.get("balance", 0)))
                with more_cols[2]:
                    render_metric_card("total_pnl", rpnl)

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
                dir_label2 = "平多" if side == "close_long" else "平空"
                st.info(f"{icon} **{dir_label2}** ${price:,.2f} | PnL ${pnl:,.2f} | 钱包 ${account.get('wallet_balance', 0):,.2f}")
            elif side == "liquidation" and trade.get("pnl"):
                st.error(f"💥 **强平** {trade.get('direction', '').upper()} @ ${price:,.2f} | PnL ${trade['pnl']:,.2f}")

        # ── 5c. Charts ──
        chart_tab1, chart_tab2 = st.tabs(["📈 K 线 + 信号", "📊 权益曲线"])

        with chart_tab1:
            # TradingView lightweight-charts：K 线 + 成交量 + 成交标记 + 实时价线
            _tv_theme = st.session_state.get("theme_mode", "light")
            components.html(
                build_kline_tv_html(
                    df,
                    trades=account.get("trades", []),
                    ticker_last=ticker_data["last"] if ticker_data else None,
                    symbol=cfg.trading.symbol,
                    timeframe=timeframe,
                    theme=_tv_theme,
                    height=480,
                ),
                height=496,
            )

        with chart_tab2:
            if multi_states is not None:
                # 多 slot：权益曲线叠加对比（选中 slot 不影响此图）
                eq_map = {lb: (st_ or {}).get("account", {}).get("equity_history", [])
                          for lb, st_ in multi_states.items()}
                if any(eq_map.values()):
                    fig = multi_equity_chart(eq_map, title="模拟盘权益曲线（多策略叠加）",
                                             theme=st.session_state.get("theme_mode", "light"))
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("尚无权益历史数据")
            else:
                eq_hist = account.get("equity_history", [])
                if eq_hist:
                    fig = equity_curve_chart(eq_hist, title="模拟盘权益曲线", theme=st.session_state.get("theme_mode", "light"))
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("尚无权益历史数据")

        # ── 5d. 记录区：交易记录 / 持仓详情 / 账户信息 合并为 tabs ──
        rec_tab1, rec_tab2, rec_tab3 = st.tabs(["📋 交易记录", "📊 持仓详情", "ℹ️ 账户信息"])

        with rec_tab1:
            all_trades = account.get("trades", [])
            if all_trades:
                df_trades = pd.DataFrame(all_trades)
                df_trades = df_trades.iloc[::-1].reset_index(drop=True)
                display_cols = ["time", "side", "price", "size", "pnl", "fee"]
                if is_futures:
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

                # ── 交易统计（最近成交汇总，直接展示不再折叠）──
                st.markdown("##### 📊 交易统计")
                wins = [t for t in all_trades if t.get("pnl", 0) > 0]
                losses = [t for t in all_trades if t.get("pnl", 0) < 0]
                total_pnl = sum(t.get("pnl", 0) for t in all_trades)
                win_rate = len(wins) / len(all_trades) * 100 if all_trades else 0
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
                init_cap = float(account.get("initial_balance", 0))
                roi = total_pnl / init_cap * 100 if init_cap > 0 else 0
                roi_color = "#059669" if roi > 0 else "#dc2626"
                st.markdown(
                    f"**投资回报率 (ROI):** "
                    f"<span style='color:{roi_color}; font-weight:700; font-size:1.2rem;'>"
                    f"{roi:+.2f}%</span>"
                    f"&nbsp;&nbsp;(初始 ${init_cap:,.0f} → "
                    f"当前 ${equity:,.2f})",
                    unsafe_allow_html=True)
            else:
                st.info("暂无交易记录，策略触发信号后将在此显示")

        with rec_tab2:
            if is_futures:
                pos_cols = st.columns(5)
                with pos_cols[0]:
                    dir_emoji = {"long": "🟢", "short": "🔴", "flat": "⚪"}
                    st.metric(f"持仓 {dir_emoji.get(direction, '')}",
                              f"{pos_val:.6f}" if pos_val > 0 else "-")
                with pos_cols[1]:
                    st.metric("钱包余额", f"${account.get('wallet_balance', 0):,.2f}")
                with pos_cols[2]:
                    st.metric("可用余额", f"${account.get('available_balance', 0):,.2f}")
                with pos_cols[3]:
                    liq_p = float(account.get("liquidation_price", 0))
                    price_now = float(paper_state.get("price", 0))
                    if liq_p > 0 and price_now > 0:
                        dist = abs(price_now - liq_p) / price_now * 100
                        danger = "🔴" if dist < 5 else "🟡" if dist < 15 else "⚪"
                        st.metric(f"强平价 {danger}", f"${liq_p:,.2f}",
                                  delta=f"{dist:.1f}%" if price_now > liq_p else "⚠️ 已触发",
                                  delta_color="inverse" if price_now > liq_p else "off")
                    else:
                        st.metric("强平价", "-")
                with pos_cols[4]:
                    render_metric_card("total_pnl", rpnl)
            else:
                pos_cols = st.columns(4)
                with pos_cols[0]:
                    st.metric(f"持仓 ({BASE_CURRENCY})", f"{pos_val:.6f}")
                with pos_cols[1]:
                    render_metric_card("balance", float(account.get("balance", 0)))
                with pos_cols[2]:
                    render_metric_card("total_pnl", rpnl)
                with pos_cols[3]:
                    render_metric_card("equity", equity)

        with rec_tab3:
            total_bars = (rs or {}).get("bars_processed", 0)
            info_cols = st.columns(5)
            info_cols[0].metric("策略", _slot if multi_states is not None else selected_strategy)
            info_cols[1].metric("已处理 K 线", total_bars)
            info_cols[2].metric("交易次数", account.get("total_trades", len(account.get("trades", []))))
            if is_futures:
                info_cols[3].metric("钱包余额", f"${account.get('wallet_balance', 0):,.2f}")
                info_cols[4].metric("总权益", f"${equity:,.2f}")
            else:
                info_cols[3].metric("初始资金", f"${account.get('initial_balance', 0):,.0f}")
                info_cols[4].metric("当前权益", f"${equity:,.2f}")

            # 合约强平预警
            if is_futures:
                liq_price = float(account.get("liquidation_price", 0))
                curr_price = float(paper_state.get("price", 0))
                if liq_price > 0 and curr_price > 0:
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

    else:
        if running:
            empty_state("⏳", "正在初始化",
                        "模拟盘进程已启动，正在回放历史 K 线，数据马上出现")
        elif df is None:
            empty_state("💰", "模拟交易尚未启动",
                        "在上方控制面板设置资金和策略参数，点击「启动监控」即可连接 OKX 实时行情开始模拟")
        else:
            st.info("数据已加载，点击「启动监控」开始逐根处理 K 线信号")


_paper_monitor_fragment()
