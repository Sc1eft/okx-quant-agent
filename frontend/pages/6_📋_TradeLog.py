"""Trade Log page - view and analyze historical trades."""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.utils.session_state import get_backtest_result
from frontend.utils.backtest_runner import run_backtest
from frontend.components.charts import pnl_distribution_chart, cumulative_pnl_chart
from frontend.components.metrics_display import _render_metric_card
from strategies.base import get_available_strategies


st.title("📋 交易日志")
st.markdown("查看交易历史、分析盈亏分布")


cfg = None
strategies = get_available_strategies()
strategy_names = list(strategies.keys())

# ============ Load Data Section ============
st.subheader("📂 加载交易数据")

load_cols = st.columns([2, 1, 1])
with load_cols[0]:
    if strategy_names:
        selected = st.selectbox("选择策略", strategy_names, key="tl_strategy")
    else:
        st.warning("无可用的策略")
        st.stop()

with load_cols[1]:
    load_btn = st.button("📥 加载回测数据", use_container_width=True)

with load_cols[2]:
    load_all_btn = st.button("📥 全部加载", use_container_width=True)

# Load data on demand
if load_btn or load_all_btn:
    if load_all_btn:
        for name in strategy_names:
            if get_backtest_result(name) is None:
                from frontend.utils.session_state import get_config
                result = run_backtest(name, get_config())
                if result:
                    from frontend.utils.session_state import set_backtest_result
                    set_backtest_result(name, result)
    else:
        if get_backtest_result(selected) is None:
            from frontend.utils.session_state import get_config
            result = run_backtest(selected, get_config())
            if result:
                from frontend.utils.session_state import set_backtest_result
                set_backtest_result(selected, result)

    st.success("数据加载完成!")


# ============ All Trades from All Cached Results ============
all_trades = []
strategy_labels = []

for name in strategy_names:
    result = get_backtest_result(name)
    if result and result.get("trades"):
        trades = result["trades"]
        for t in trades:
            t["strategy"] = name
        all_trades.extend(trades)

if not all_trades:
    st.info("暂无交易数据。点击「加载回测数据」来加载。")
    st.stop()

df_all = pd.DataFrame(all_trades)

# ============ Filters ============
st.subheader("🔍 筛选")

filter_cols = st.columns(4)
with filter_cols[0]:
    # Strategy filter
    avail_strategies = df_all["strategy"].unique().tolist() if "strategy" in df_all.columns else []
    selected_strategies = st.multiselect(
        "策略", avail_strategies, default=avail_strategies,
        key="tl_filter_strat"
    )

with filter_cols[1]:
    # Side filter
    sides = df_all["side"].unique().tolist() if "side" in df_all.columns else []
    selected_sides = st.multiselect("方向", sides, default=sides, key="tl_filter_side")

with filter_cols[2]:
    # Min PnL
    if "pnl_pct" in df_all.columns:
        min_pnl = st.slider("最小盈亏 (%)", -20.0, 20.0, -20.0, 1.0, key="tl_filter_pnl",
                            format="%.1f")

with filter_cols[3]:
    # Reason filter
    reasons = df_all["reason"].unique().tolist() if "reason" in df_all.columns else []
    selected_reasons = st.multiselect("退出原因", reasons, default=reasons, key="tl_filter_reason")

# Apply filters
filtered = df_all.copy()
if selected_strategies and "strategy" in filtered.columns:
    filtered = filtered[filtered["strategy"].isin(selected_strategies)]
if selected_sides and "side" in filtered.columns:
    filtered = filtered[filtered["side"].isin(selected_sides)]
if selected_reasons and "reason" in filtered.columns:
    filtered = filtered[filtered["reason"].isin(selected_reasons)]

st.markdown(f"**共 {len(filtered)} 条交易记录** (全部 {len(df_all)} 条)")


# ============ Summary Stats ============
if not filtered.empty:
    st.subheader("📊 统计摘要")

    win_df = filtered[filtered["pnl"] > 0] if "pnl" in filtered.columns else pd.DataFrame()
    loss_df = filtered[filtered["pnl"] < 0] if "pnl" in filtered.columns else pd.DataFrame()

    sum_cols = st.columns(5)
    with sum_cols[0]:
        total_pnl = filtered["pnl"].sum() if "pnl" in filtered.columns else 0
        _render_metric_card("total_pnl", total_pnl)
    with sum_cols[1]:
        win_rate = len(win_df) / len(filtered) * 100 if len(filtered) > 0 else 0
        _render_metric_card("win_rate", win_rate)
    with sum_cols[2]:
        avg_pnl = filtered["pnl_pct"].mean() if "pnl_pct" in filtered.columns else 0
        _render_metric_card("avg_pnl_pct", avg_pnl)
    with sum_cols[3]:
        avg_win_pnl = win_df["pnl_pct"].mean() if not win_df.empty else 0
        _render_metric_card("avg_win_pct", avg_win_pnl)
    with sum_cols[4]:
        avg_loss_pnl = loss_df["pnl_pct"].mean() if not loss_df.empty else 0
        _render_metric_card("avg_loss_pct", avg_loss_pnl)


# ============ Charts ============
if not filtered.empty:
    st.subheader("📈 图表分析")

    chart_tabs = st.tabs(["盈亏分布", "累计盈亏", "交易详情"])

    with chart_tabs[0]:
        trades_list = filtered.to_dict("records")
        fig = pnl_distribution_chart(trades_list, theme=st.session_state.get("theme_mode", "light"))
        st.plotly_chart(fig, use_container_width=True)

    with chart_tabs[1]:
        fig = cumulative_pnl_chart(trades_list, theme=st.session_state.get("theme_mode", "light"))
        st.plotly_chart(fig, use_container_width=True)

    with chart_tabs[2]:
        # Sort by exit_time
        display_df = filtered.sort_values("exit_time") if "exit_time" in filtered.columns else filtered
        display_cols = ["strategy", "entry_time", "exit_time", "side", "entry_price",
                        "exit_price", "size", "pnl", "pnl_pct", "fee", "reason"]
        display_df = display_df[[c for c in display_cols if c in display_df.columns]]

        def color_pnl(val):
            if isinstance(val, (int, float)):
                return "color: #059669" if val > 0 else ("color: #dc2626" if val < 0 else "")
            return ""

        st.dataframe(
            display_df.style.map(color_pnl, subset=["pnl", "pnl_pct"]),
            use_container_width=True,
            hide_index=True,
        )

        # Export
        csv = filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 导出 CSV",
            csv,
            "all_trades.csv",
            "text/csv",
        )
