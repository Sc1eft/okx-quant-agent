"""Dashboard page - system overview, account status, risk state."""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.utils.session_state import get_config
from frontend.utils.backtest_runner import run_all_strategies
from frontend.components.metrics_display import _render_metric_card
from agents.status_writer import read_agent_status, get_status_file_path


st.title("📊 系统总览")
st.markdown("OKX 虚拟币量化交易系统核心状态一览")


cfg = get_config()

# ============ Top KPI Row ============
st.subheader("系统状态")

# 读取运行中 Agent 写入的真实状态；文件不存在时降级为「Agent 未运行」
STATUS_FILE = Path(get_status_file_path())
agent_running = STATUS_FILE.exists()
status_data = read_agent_status() if agent_running else {}
a3 = status_data.get("agent3", {})
risk_status = a3.get("risk_status", {})
pm_status = status_data.get("position_monitor", {})
base_currency = cfg.trading.symbol.split("-")[0]

kpi_cols = st.columns(5)
with kpi_cols[0]:
    _render_metric_card("mode", cfg.mode)
with kpi_cols[1]:
    _render_metric_card("symbol", cfg.trading.symbol)
if agent_running:
    pos = a3.get("position") or {}
    with kpi_cols[2]:
        if pm_status.get("has_position") and pos:
            side = "🟢 多" if pos.get("side") == "long" else "🔴 空"
            pnl = pos.get("pnl", 0)
            _render_metric_card(
                "当前持仓",
                f"{side} {pos.get('size', 0):.4f} {base_currency} (${pnl:+,.2f})",
            )
        else:
            _render_metric_card("当前持仓", "无持仓")
    with kpi_cols[3]:
        _render_metric_card(
            "今日交易",
            f"{risk_status.get('daily_trade_count', 0)} / {risk_status.get('max_daily_trades', '?')}",
        )
    with kpi_cols[4]:
        risk_text = "🔴 日限暂停" if a3.get("paused_for_daily_limit") else "🟢 风控正常"
        _render_metric_card("风控状态", risk_text)
else:
    with kpi_cols[2]:
        _render_metric_card("当前持仓", "Agent 未运行")
    with kpi_cols[3]:
        _render_metric_card("今日交易", "Agent 未运行")
    with kpi_cols[4]:
        _render_metric_card("风控状态", "Agent 未运行")


# ============ Quick Actions ============
st.subheader("⚡ 快速操作")
action_cols = st.columns(3)
with action_cols[0]:
    if st.button("▶ 运行全部策略回测", use_container_width=True):
        with st.spinner("正在运行回测..."):
            results = run_all_strategies(cfg)
            if results:
                st.session_state.comparison_results = results
                st.success(f"回测完成! {len(results)} 个策略")
                # Show quick summary
                for name, result in results.items():
                    m = result.get("metrics", {})
                    st.markdown(f"- **{name}**: 收益 {m.get('total_return_pct', 0):+.2f}% | "
                               f"Sharpe {m.get('sharpe', 0):.2f} | "
                               f"回撤 {m.get('max_drawdown_pct', 0):.2f}%")
            else:
                st.warning("回测未能产生结果")

with action_cols[1]:
    if st.button("📄 查看配置", use_container_width=True):
        st.session_state.show_config = not st.session_state.get("show_config", False)

with action_cols[2]:
    # 按钮点击本身即触发 rerun，无需 on_click
    st.button("📊 刷新", use_container_width=True)


# ============ Config Display (toggle) ============
if st.session_state.get("show_config", False):
    st.subheader("⚙ 当前配置")
    cfg_dict = {
        "模式": cfg.mode,
        "交易对": f"{cfg.trading.symbol} ({cfg.trading.market})",
        "K线周期": cfg.trading.timeframes,
        "策略": cfg.strategy.enabled_strategies,
        "策略权重": cfg.strategy.strategy_weights,
        "滑点": f"{cfg.trading.slippage_pct}%",
        "Taker 费率": f"{cfg.trading.taker_fee}%",
        "Maker 费率": f"{cfg.trading.maker_fee}%",
        "最大仓位": f"{cfg.risk.max_position_pct}%",
        "单笔最大": f"{cfg.risk.max_single_order_pct}%",
        "日最大亏损": f"{cfg.risk.max_daily_loss_pct}%",
        "连续止损": cfg.risk.max_consecutive_losses,
        "恢复模式": cfg.risk.recovery_mode,
    }
    cfg_cols = st.columns(2)
    for i, (k, v) in enumerate(cfg_dict.items()):
        with cfg_cols[i % 2]:
            st.markdown(f"- **{k}**: {v}")


# ============ Recent Comparison Results ============
comparison_results = st.session_state.get("comparison_results", {})
if comparison_results:
    st.subheader("📊 最近回测对比")
    rows = []
    for name, result in comparison_results.items():
        m = result.get("metrics", {})
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
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
