"""Walk-Forward Analysis page - WF, parameter sweep, OOS test."""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.utils.session_state import get_config
from frontend.utils.backtest_runner import (
    run_walk_forward, run_param_sweep, run_oos_test,
)
from frontend.utils.data_provider import fetch_okx_data
from frontend.components.charts import sharpe_drop_chart
from strategies.base import get_available_strategies


st.title("🔬 高级分析")
st.markdown("Walk-Forward 验证、参数扫描和样本外测试")


cfg = get_config()
strategies = get_available_strategies()

if not strategies:
    st.warning("无可用的策略")
    st.stop()

strategy_names = list(strategies.keys())

st.session_state.setdefault("wf_results", {})
st.session_state.setdefault("param_sweep_results", {})
st.session_state.setdefault("oos_results", {})

sel_cols = st.columns([2, 2, 1, 1])
with sel_cols[0]:
    selected_strategy = st.selectbox(
        "选择策略",
        strategy_names,
        key="wf_strategy",
    )
with sel_cols[1]:
    data_source = st.radio(
        "数据来源", ["真实 OKX 数据", "Mock 数据"],
        index=0, horizontal=True, key="wf_data_source",
    )
with sel_cols[2]:
    data_limit = st.slider("K 线数量", 500, 2000, 1000, step=100, key="wf_data_limit")
with sel_cols[3]:
    data_tf = st.selectbox("周期", ["1h", "15m", "4h", "1d"], index=0, key="wf_data_tf")


def _get_data():
    """按数据源选择取数；真实数据失败时回退 Mock（runner 内置）。"""
    if st.session_state.get("wf_data_source", "真实 OKX 数据") != "真实 OKX 数据":
        return None
    try:
        df = fetch_okx_data(
            cfg,
            limit=st.session_state.get("wf_data_limit", 1000),
            timeframe=st.session_state.get("wf_data_tf", "1h"),
        )
        if df is not None and not df.empty:
            return df
        st.warning("OKX 数据为空，回退使用 Mock 数据")
    except Exception as e:
        st.error(f"获取 OKX 数据失败: {e}，回退使用 Mock 数据")
    return None

# ============ Tabs for three analysis types ============
tab1, tab2, tab3 = st.tabs(["Walk-Forward", "参数扫描", "样本外测试 (OOS)"])


# ==================== TAB 1: Walk-Forward ====================
with tab1:
    st.markdown("""
    **Walk-Forward 验证** 将数据分为多个时间窗口，每个窗口用前 70% 训练、后 30% 测试。
    对比训练集和测试集的 Sharpe 比率，判断策略是否存在过拟合。
    """)

    wf_cols = st.columns([2, 1, 1])
    with wf_cols[0]:
        n_windows = st.slider("窗口数量", 2, 8, 4, key="wf_windows")
    with wf_cols[1]:
        run_wf_btn = st.button("▶ 运行 WF", type="primary", use_container_width=True)
    with wf_cols[2]:
        if st.button("清空结果", use_container_width=True):
            st.session_state.wf_results = {}
            st.rerun()

    if run_wf_btn:
        with st.spinner(f"正在运行 {selected_strategy} Walk-Forward 分析..."):
            result = run_walk_forward(selected_strategy, cfg, n_windows=n_windows, data=_get_data())
            if result:
                st.session_state.wf_results[selected_strategy] = result
                st.success("Walk-Forward 分析完成!")
            else:
                st.error("分析失败")

    # Display results
    wf_result = st.session_state.wf_results.get(selected_strategy)
    if wf_result:
        # Verdict
        verdict = wf_result.get("verdict", "")
        verdict_color = {"PASS": "#059669", "WARNING": "#d97706", "FAIL": "#dc2626"}
        vc = verdict_color.get(verdict, "#64748b")

        st.markdown(f"### 判定结果: <span style='color:{vc};font-size:1.2rem;font-weight:600'>{verdict}</span>",
                     unsafe_allow_html=True)
        st.markdown(wf_result.get("details", ""))

        # Summary metrics
        sum_cols = st.columns(4)
        with sum_cols[0]:
            avg_tr = wf_result.get("avg_train_return", 0) or 0
            st.metric("平均训练收益", f"{avg_tr:+.2f}%")
        with sum_cols[1]:
            avg_te = wf_result.get("avg_test_return", 0) or 0
            st.metric("平均测试收益", f"{avg_te:+.2f}%")
        with sum_cols[2]:
            avg_ts = wf_result.get("avg_train_sharpe", 0) or 0
            st.metric("平均训练 Sharpe", f"{avg_ts:.2f}")
        with sum_cols[3]:
            avg_tes = wf_result.get("avg_test_sharpe", 0) or 0
            st.metric("平均测试 Sharpe", f"{avg_tes:.2f}")

        extra_cols = st.columns(3)
        with extra_cols[0]:
            drop = wf_result.get("sharpe_drop_pct", 0) or 0
            st.metric("Sharpe 下降", f"{drop:.1f}%",
                      delta_color="inverse")
        with extra_cols[1]:
            ratio = wf_result.get("stable_window_ratio", 0) or 0
            st.metric("正向窗口占比", f"{ratio:.0%}")
        with extra_cols[2]:
            rc = wf_result.get("return_consistency", 0) or 0
            st.metric("收益稳定性 (CV)", f"{rc:.2f}")

        # Sharpe drop chart
        windows = wf_result.get("windows", [])
        if windows:
            st.divider()
            st.subheader("各窗口 Train/Test Sharpe 对比")
            fig = sharpe_drop_chart(windows, theme=st.session_state.get("theme_mode", "light"))
            st.plotly_chart(fig, use_container_width=True)

            # Window details table
            st.subheader("窗口详情")
            rows = []
            for i, w in enumerate(windows):
                rows.append({
                    "窗口": f"#{i+1}",
                    "训练期": f"{str(w.get('train_start', ''))[:10]} - {str(w.get('train_end', ''))[:10]}",
                    "测试期": f"{str(w.get('test_start', ''))[:10]} - {str(w.get('test_end', ''))[:10]}",
                    "训练收益%": f"{(w.get('train_return', 0) or 0):+.2f}",
                    "测试收益%": f"{(w.get('test_return', 0) or 0):+.2f}",
                    "训练 Sharpe": f"{(w.get('train_sharpe', 0) or 0):.2f}",
                    "测试 Sharpe": f"{(w.get('test_sharpe', 0) or 0):.2f}",
                    "训练回撤%": f"{(w.get('train_max_dd', 0) or 0):.2f}",
                    "测试回撤%": f"{(w.get('test_max_dd', 0) or 0):.2f}",
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("点击「运行 WF」开始 Walk-Forward 分析")


# ==================== TAB 2: Parameter Sweep ====================
with tab2:
    st.markdown("""
    **Monte Carlo 参数扫描** 对策略参数进行随机采样，评估参数敏感度。
    变异系数 (CV) 越低，参数越稳定，策略越可靠。
    """)

    ps_cols = st.columns([3, 1, 1])
    with ps_cols[0]:
        n_iter = st.slider("迭代次数", 50, 500, 200, step=50, key="ps_iter")
    with ps_cols[1]:
        run_ps_btn = st.button("▶ 运行扫描", type="primary", use_container_width=True)
    with ps_cols[2]:
        if st.button("清空结果", key="clear_ps", use_container_width=True):
            st.session_state.param_sweep_results = {}
            st.rerun()

    if run_ps_btn:
        with st.spinner(f"正在运行 {selected_strategy} 参数扫描 ({n_iter} 次迭代)..."):
            result = run_param_sweep(selected_strategy, cfg, n_iter, data=_get_data())
            if result:
                st.session_state.param_sweep_results[selected_strategy] = result
                st.success("参数扫描完成!")
            else:
                st.error("参数扫描失败")

    ps_result = st.session_state.param_sweep_results.get(selected_strategy)
    if ps_result:
        verdict = ps_result.get("verdict", "")
        verdict_color = {"PASS": "#059669", "WARNING": "#d97706", "FAIL": "#dc2626"}
        vc = verdict_color.get(verdict, "#64748b")

        st.markdown(f"### 判定结果: <span style='color:{vc}'>{verdict}</span>",
                     unsafe_allow_html=True)
        st.markdown(ps_result.get("details", ""))

        # Metrics
        mcols = st.columns(4)
        with mcols[0]:
            st.metric("最佳收益", f"{(ps_result.get('best_return', 0) or 0):+.2f}%")
        with mcols[1]:
            st.metric("最差收益", f"{(ps_result.get('worst_return', 0) or 0):+.2f}%")
        with mcols[2]:
            st.metric("中位数收益", f"{(ps_result.get('median_return', 0) or 0):+.2f}%")
        with mcols[3]:
            cv = ps_result.get("param_stability", 0) or 0
            st.metric("变异系数 (CV)", f"{cv:.2f}",
                      delta_color="inverse")

        # Top params
        n_valid = ps_result.get("n_valid", 0)
        n_skipped = ps_result.get("n_skipped_low_trades", 0)
        if n_skipped:
            st.caption(f"有效参数组合 {n_valid} 组（{n_skipped} 组因交易次数不足被排除）")
        top_params = ps_result.get("top_10pct_params", [])
        if top_params:
            st.subheader("🏆 Top 10% 参数组合（含样本外复验）")
            rows = []
            for i, p in enumerate(top_params):
                row = {"排名": i + 1}
                row.update(p.get("params", {}))
                row.update({
                    "IS 收益%": f"{(p.get('return', 0) or 0):+.2f}",
                    "Sharpe": f"{(p.get('sharpe', 0) or 0):.2f}",
                    "回撤%": f"{(p.get('max_dd', 0) or 0):.2f}",
                    "交易": p.get("trades", 0),
                })
                if "oos_return" in p:
                    row["OOS 收益%"] = f"{(p.get('oos_return', 0) or 0):+.2f}"
                    row["保留率%"] = p.get("oos_retention", "-")
                rows.append(row)
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("点击「运行扫描」开始参数扫描")


# ==================== TAB 3: OOS Test ====================
with tab3:
    st.markdown("""
    **样本外测试 (Out-of-Sample Test)** 保留最后 30% 数据作为"未见过的数据"进行最终验证。
    如果样本外表现与样本内差距过大，说明策略可能过拟合。
    """)

    oos_cols = st.columns([1, 1])
    with oos_cols[0]:
        run_oos_btn = st.button("▶ 运行 OOS 测试", type="primary", use_container_width=True)
    with oos_cols[1]:
        if st.button("清空结果", key="clear_oos", use_container_width=True):
            st.session_state.oos_results = {}
            st.rerun()

    if run_oos_btn:
        with st.spinner(f"正在运行 {selected_strategy} 样本外测试..."):
            result = run_oos_test(selected_strategy, cfg, data=_get_data())
            if result:
                st.session_state.oos_results[selected_strategy] = result
                st.success("OOS 测试完成!")
            else:
                st.error("OOS 测试失败")

    oos_result = st.session_state.oos_results.get(selected_strategy)
    if oos_result:
        verdict = oos_result.get("verdict", "")
        verdict_color = {"PASS": "#059669", "WARNING": "#d97706", "FAIL": "#dc2626"}
        vc = verdict_color.get(verdict, "#64748b")

        st.markdown(f"### 判定结果: <span style='color:{vc};font-weight:600'>{verdict}</span>",
                     unsafe_allow_html=True)

        oos_cols = st.columns(4)
        with oos_cols[0]:
            st.metric("样本内收益",
                      f"{(oos_result.get('in_sample_return', 0) or 0):+.2f}%")
        with oos_cols[1]:
            st.metric("样本外收益",
                      f"{(oos_result.get('out_of_sample_return', 0) or 0):+.2f}%")
        with oos_cols[2]:
            st.metric("样本外 Sharpe",
                      f"{(oos_result.get('out_of_sample_sharpe', 0) or 0):.2f}")
        with oos_cols[3]:
            retention = oos_result.get("retention_ratio", 0) or 0
            st.metric("收益保留率", f"{retention:.0f}%")

        # Simple bar chart
        theme = st.session_state.get("theme_mode", "light")
        if theme == "dark":
            chart_bg, paper_bg = "#1e293b", "#1e293b"
            grid_color, font_color, title_color = "#334155", "#94a3b8", "#f1f5f9"
        else:
            chart_bg, paper_bg = "#ffffff", "#f8fafc"
            grid_color, font_color, title_color = "#e2e8f0", "#475569", "#0f172a"
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=["样本内", "样本外"],
            y=[oos_result.get("in_sample_return", 0) or 0,
               oos_result.get("out_of_sample_return", 0) or 0],
            marker_color=["#2563eb", "#059669"],
            marker_line=dict(color="white", width=1),
        ))
        fig.update_layout(
            title=dict(text="样本内 vs 样本外收益对比", font=dict(size=14, color=title_color), x=0, xanchor="left"),
            yaxis_title="收益率 (%)",
            plot_bgcolor=chart_bg,
            paper_bgcolor=paper_bg,
            font=dict(color=font_color, family="-apple-system, BlinkMacSystemFont, sans-serif"),
        )
        fig.update_xaxes(gridcolor=grid_color, zeroline=False)
        fig.update_yaxes(gridcolor=grid_color, zeroline=True, zerolinecolor=grid_color)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("点击「运行 OOS 测试」开始样本外验证")
