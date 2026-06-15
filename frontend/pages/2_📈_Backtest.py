"""Backtest page - run backtests interactively and view results."""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.utils.session_state import get_config, set_backtest_result, get_backtest_result
from frontend.utils import backtest_runner as _br
from frontend.utils.data_provider import fetch_okx_data
from frontend.components.charts import (
    equity_curve_chart, drawdown_chart, signal_price_chart,
    pnl_distribution_chart, cumulative_pnl_chart,
)
from frontend.components.metrics_display import render_metric_grid
from strategies.base import get_available_strategies


st.title("📈 回测")
st.markdown("运行历史回测并查看完整结果")


cfg = get_config()
strategies = get_available_strategies()

if not strategies:
    st.warning("无可用的策略")
    st.stop()

strategy_names = list(strategies.keys())

# ============ Sidebar Parameters ============
with st.sidebar:
    st.markdown("### 回测参数")

    selected_strategy = st.selectbox(
        "选择策略", strategy_names,
        index=strategy_names.index(st.session_state.get("last_backtest_strategy", strategy_names[0]))
        if st.session_state.get("last_backtest_strategy") in strategy_names else 0,
        key="bt_strategy",
    )

    order_type = st.selectbox("订单类型", ["market", "limit"], index=0, key="bt_order_type")

    slippage = st.slider(
        "滑点(%)", 0.0, 1.0,
        value=cfg.trading.slippage_pct,
        step=0.01, format="%.2f",
        key="bt_slippage",
    )

    # Override config for this run
    run_cfg = cfg
    run_cfg.trading.slippage_pct = slippage
    run_cfg.trading.default_order_type = order_type

    st.divider()
    st.markdown("### 数据源")
    data_source = st.radio("数据来源", ["真实 OKX 数据", "Mock 数据"], index=0, key="bt_data_source")
    if data_source == "真实 OKX 数据":
        data_limit = st.slider("K 线数量", 100, 500, 300, step=50, key="bt_data_limit")
        data_tf = st.selectbox("周期", ["1h", "15m", "4h", "1d"], index=0, key="bt_data_tf")

    st.divider()

    run_btn = st.button("▶ 运行回测", type="primary", use_container_width=True)
    compare_btn = st.button("📊 多策略对比", use_container_width=True)
    comparison_btn = st.button("🔄 市价/限价对比", use_container_width=True)


# ============ Prepare Data ============
def _get_data():
    """Get data based on selected data source."""
    data_source = st.session_state.get("bt_data_source", "真实 OKX 数据")
    if data_source == "真实 OKX 数据":
        limit = st.session_state.get("bt_data_limit", 300)
        tf = st.session_state.get("bt_data_tf", "1h")
        try:
            df = fetch_okx_data(cfg, limit=limit, timeframe=tf)
            if df is not None and not df.empty:
                return df
            st.warning("OKX 数据为空，回退使用回测引擎默认数据")
        except Exception as e:
            st.error(f"获取 OKX 数据失败: {e}")
            st.info("将使用回测引擎内置的数据")
            return None
    return None  # 让 runner 用 get_mock_data()


# ============ Run Backtest ============
if run_btn:
    data = _get_data()
    with st.spinner(f"正在运行 {selected_strategy} 回测..."):
        result = _br.run_backtest(selected_strategy, run_cfg, data=data)
        if result:
            set_backtest_result(selected_strategy, result)
            st.success(f"{selected_strategy} 回测完成!")
        else:
            st.error("回测失败")

# ============ Comparison ============
if compare_btn:
    data = _get_data()
    with st.spinner("正在运行全部策略回测对比..."):
        results = _br.run_all_strategies(run_cfg, data=data)
        if results:
            st.session_state.comparison_results = results
            st.success(f"对比回测完成! {len(results)} 个策略")

if comparison_btn:
    data = _get_data()
    with st.spinner(f"正在运行 {selected_strategy} 市价/限价对比..."):
        comp_result = _br.run_comparison(selected_strategy, run_cfg, data=data)
        if comp_result:
            st.session_state[f"comparison_{selected_strategy}"] = comp_result
            st.success("订单类型对比完成!")


# ============ Display Results ============
result = get_backtest_result(selected_strategy)

if result is None:
    st.info("👈 选择一个策略并点击「运行回测」开始")

    # Show comparison results if available
    comparison_results = st.session_state.get("comparison_results", {})
    if comparison_results:
        st.subheader("📊 多策略对比")
        rows = []
        for name, r in comparison_results.items():
            m = r.get("metrics", {})
            rows.append({
                "策略": name,
                "总收益%": f"{m.get('total_return_pct', 0):+.2f}%",
                "年化%": f"{m.get('annual_return_pct', 0):+.2f}%",
                "Sharpe": f"{m.get('sharpe', 0):.2f}",
                "回撤%": f"{m.get('max_drawdown_pct', 0):.2f}%",
                "胜率%": f"{m.get('win_rate', 0):.1f}%",
                "交易次数": m.get('total_trades', 0),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    metrics = result.get("metrics", {})

    # ============ Metrics Grid ============
    st.subheader("📊 核心指标")

    key_metrics = {
        "total_return_pct": metrics.get("total_return_pct"),
        "annual_return_pct": metrics.get("annual_return_pct"),
        "sharpe": metrics.get("sharpe"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "total_trades": metrics.get("total_trades"),
        "calmar": metrics.get("calmar"),
    }
    render_metric_grid(key_metrics, cols=4)

    # Additional metrics row
    extra_cols = st.columns(4)
    with extra_cols[0]:
        st.metric("最终权益", f"${metrics.get('final_equity', 0):,.2f}")
    with extra_cols[1]:
        bm = metrics.get("benchmark_return_pct", 0)
        st.metric("基准收益", f"{bm:+.2f}%")
    with extra_cols[2]:
        outperf = "✅ 是" if metrics.get("outperform_benchmark") else "❌ 否"
        st.metric("跑赢基准", outperf)
    with extra_cols[3]:
        st.metric("平均持仓", f"{metrics.get('avg_hold_hours', 0):.1f}h")

    st.divider()

    # ============ Charts ============
    tab1, tab2, tab3, tab4 = st.tabs(["权益曲线", "回撤曲线", "价格与信号", "盈亏分布"])

    with tab1:
        equity_curve = result.get("equity_curve", [])
        if equity_curve:
            fig = equity_curve_chart(equity_curve, title=f"{selected_strategy} - 权益曲线")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无权益曲线数据")

    with tab2:
        if equity_curve:
            fig = drawdown_chart(equity_curve)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无回撤数据")

    with tab3:
        signals = result.get("signals", [])
        if signals:
            # Build price data from equity curve times
            price_data = [{"time": p["time"], "close": p["equity"]} for p in equity_curve] if equity_curve else []
            fig = signal_price_chart(price_data, signals, title=f"{selected_strategy} - 信号标记")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无信号数据")

    with tab4:
        trades = result.get("trades", [])
        if trades:
            fig1 = pnl_distribution_chart(trades)
            st.plotly_chart(fig1, use_container_width=True)
            fig2 = cumulative_pnl_chart(trades)
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("暂无交易记录")

    st.divider()

    # ============ Trade Table ============
    st.subheader("📋 交易明细")
    trades = result.get("trades", [])
    if trades:
        df_trades = pd.DataFrame(trades)
        # Format columns
        display_cols = ["entry_time", "exit_time", "side", "entry_price",
                        "exit_price", "size", "pnl", "pnl_pct", "fee", "reason"]
        df_display = df_trades[[c for c in display_cols if c in df_trades.columns]]

        # Color PnL
        def color_pnl(val):
            if isinstance(val, (int, float)):
                return "color: #059669" if val > 0 else ("color: #dc2626" if val < 0 else "")
            return ""

        st.dataframe(
            df_display.style.map(color_pnl, subset=["pnl", "pnl_pct"]),
            use_container_width=True,
            hide_index=True,
        )

        # Download button
        csv = df_trades.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 导出 CSV",
            csv,
            f"{selected_strategy}_trades.csv",
            "text/csv",
        )
    else:
        st.info("本次回测无交易产生")

    # ============ Comparison Results ============
    comp_key = f"comparison_{selected_strategy}"
    if comp_key in st.session_state:
        st.divider()
        st.subheader("🔄 市价/限价对比")
        comp = st.session_state[comp_key]

        comp_cols = st.columns(3)
        with comp_cols[0]:
            st.metric("市场模式收益", f"{comp.get('market_return', 0):+.2f}%")
        with comp_cols[1]:
            st.metric("限价模式收益", f"{comp.get('limit_return', 0):+.2f}%")
        with comp_cols[2]:
            st.metric("推荐", comp.get("recommendation", ""))

    # Comparison results from multi-strategy run
    comparison_results = st.session_state.get("comparison_results", {})
    if comparison_results and len(comparison_results) > 1:
        st.divider()
        st.subheader("📊 多策略对比")
        rows = []
        for name, r in comparison_results.items():
            m = r.get("metrics", {})
            rows.append({
                "策略": name,
                "总收益%": f"{m.get('total_return_pct', 0):+.2f}%",
                "年化%": f"{m.get('annual_return_pct', 0):+.2f}%",
                "Sharpe": f"{m.get('sharpe', 0):.2f}",
                "回撤%": f"{m.get('max_drawdown_pct', 0):.2f}%",
                "胜率%": f"{m.get('win_rate', 0):.1f}%",
                "交易次数": m.get('total_trades', 0),
                "盈亏比": f"{m.get('profit_factor', 0):.2f}",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
