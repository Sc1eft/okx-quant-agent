"""Backtest page - run backtests interactively and view results."""

import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
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
from frontend.components.tv_lightweight import build_kline_tv_html
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


def _render_heuristic_analysis(metrics: dict, strategy_name: str):
    """本地启发式回测分析（原 7_🤖_AgentAnalysis 页并入，不调用任何外部 API）"""
    warnings = []

    sharpe = metrics.get("sharpe", 0)
    if sharpe > 3:
        warnings.append("⚠️ **Sharpe > 3**: 可能存在过拟合，建议用 Walk-Forward 验证")
    elif sharpe > 2:
        warnings.append("✅ Sharpe > 2: 表现优秀")
    elif sharpe > 1:
        warnings.append("👍 Sharpe > 1: 表现良好")
    else:
        warnings.append("⚠️ Sharpe < 1: 策略风险调整后收益偏低")

    win_rate = metrics.get("win_rate", 0)
    if win_rate > 80:
        warnings.append("⚠️ **胜率 > 80%**: 对趋势跟踪策略来说偏高，需验证样本外表现")
    elif win_rate < 40:
        warnings.append("📉 胜率偏低，但若盈亏比较高则可以接受")

    total_trades = metrics.get("total_trades", 0)
    if total_trades < 20:
        warnings.append("⚠️ **交易次数 < 20**: 统计样本不足，结论置信度低")
    elif total_trades < 50:
        warnings.append("📊 交易次数适中，建议增加更多数据")

    max_dd = metrics.get("max_drawdown_pct", 0)
    if max_dd < 2:
        warnings.append("⚠️ **最大回撤 < 2%**: 异常偏低，请检查回测逻辑")
    elif max_dd > 20:
        warnings.append(f"🔴 **最大回撤 {max_dd:.1f}%**: 风险较高，建议缩小仓位")

    total_return = metrics.get("total_return_pct", 0)
    if total_return > 500:
        warnings.append("⚠️ **收益 > 500%**: 极度异常，请检查是否有计算错误")
    elif total_return > 100:
        warnings.append("🔥 收益较高，但需确认是否包含幸存者偏差")

    if not warnings:
        warnings.append("✅ 所有基础检查通过")

    for w in warnings:
        st.markdown(f"- {w}")
    st.caption(
        "改进方向: ① 滚动优化页做参数扫描与样本外验证 ② 仪表盘对比多策略 "
        "③ 模拟交易页检查风控配置。本分析由本地启发式规则生成，不调用 AI API。"
    )


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

    # ============ 智能分析（本地启发式） ============
    with st.expander("🤖 智能分析（本地启发式规则）", expanded=False):
        _render_heuristic_analysis(metrics, selected_strategy)

    st.divider()

    # ============ Charts ============
    tab1, tab2, tab3, tab4 = st.tabs(["权益曲线", "回撤曲线", "价格与信号", "盈亏分布"])

    with tab1:
        equity_curve = result.get("equity_curve", [])
        if equity_curve:
            fig = equity_curve_chart(equity_curve, title=f"{selected_strategy} - 权益曲线", theme=st.session_state.get("theme_mode", "light"))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无权益曲线数据")

    with tab2:
        if equity_curve:
            fig = drawdown_chart(equity_curve, theme=st.session_state.get("theme_mode", "light"))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无回撤数据")

    with tab3:
        price_data = result.get("price_data", [])
        if price_data and "open" in price_data[0]:
            # TradingView K 线 + 成交标记（入场 B / 出场 S），可拖动查看全部历史
            _bt_df = pd.DataFrame(price_data)
            _bt_df["time"] = pd.to_datetime(_bt_df["time"])
            _bt_df = _bt_df.set_index("time")
            _bt_markers = []
            for _t in result.get("trades", []):
                _bt_markers.append({"time": _t["entry_time"], "side": "buy", "price": _t["entry_price"]})
                _bt_markers.append({"time": _t["exit_time"], "side": "sell", "price": _t["exit_price"]})
            components.html(
                build_kline_tv_html(
                    _bt_df,
                    trades=_bt_markers,
                    symbol=result.get("symbol", cfg.trading.symbol),
                    timeframe=st.session_state.get("bt_data_tf", "1h"),
                    theme=st.session_state.get("theme_mode", "light"),
                    height=520,
                ),
                height=536,
            )
        elif result.get("signals"):
            # 旧格式结果（无 OHLC）回退 plotly 折线图
            fig = signal_price_chart(price_data, result["signals"], title=f"{selected_strategy} - 信号标记", theme=st.session_state.get("theme_mode", "light"))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无信号数据")

    with tab4:
        trades = result.get("trades", [])
        if trades:
            fig1 = pnl_distribution_chart(trades, theme=st.session_state.get("theme_mode", "light"))
            st.plotly_chart(fig1, use_container_width=True)
            fig2 = cumulative_pnl_chart(trades, theme=st.session_state.get("theme_mode", "light"))
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
