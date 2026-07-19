"""Dashboard — 指挥中心：上手引导 + 模块健康度 + 账户速览 + 快捷操作。"""

import subprocess
import sys
from pathlib import Path

import streamlit as st
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import data.heartbeat_db as _hb
import execution.paper_runner as _pr
from agents.status_writer import read_agent_status, get_status_file_path
from frontend.components.layout import (
    page_header, section_card, metric_row,
)
from frontend.components.metrics_display import _render_metric_card
from frontend.utils.backtest_runner import run_all_strategies
from frontend.utils.data_provider import fetch_ticker
from frontend.utils.session_state import get_config


cfg = get_config()

page_header(
    "指挥中心",
    "系统全局状态一览 — 行情、模拟盘、实盘 Agent 的健康度都在这里",
    badge=cfg.mode,
    badge_type="blue",
)


# ─────────────────────────────────────────────
# 模块健康度（一眼看懂系统各部分是否在干活）
# ─────────────────────────────────────────────

def _pid_alive(pid_file: Path) -> bool:
    """PID 文件 + tasklist 判断进程存活（与 paper_runner/heartbeat_db 同一模式）。"""
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


health_items = []

# 1) 行情连接（OKX 公开 ticker 探测）
try:
    ticker = fetch_ticker(cfg)
    change = float(ticker.get("change_24h", 0) or 0)
    health_items.append({
        "label": f"行情 {cfg.trading.symbol}",
        "value": f"${float(ticker.get('last', 0)):,.2f}",
        "sub": f"24h {change:+.2f}%",
        "color": "green" if change >= 0 else "red",
    })
except Exception:
    health_items.append({
        "label": "行情连接",
        "value": "连接失败",
        "sub": "检查网络后刷新",
        "color": "red",
    })

# 2) 心跳采集器
collector_on = _hb.is_collector_running()
health_items.append({
    "label": "心跳采集器",
    "value": "运行中" if collector_on else "未运行",
    "sub": "秒级行情采集",
    "color": "green" if collector_on else "gray",
})

# 3) 模拟盘 runner（含权益/信号）
runner_on = _pr.is_runner_running()
if runner_on:
    rs = _pr.read_state() or {}
    ps = rs.get("paper_state") or {}
    account = ps.get("account") or {}
    equity = float(account.get("equity", 0) or 0)
    sig = (ps.get("signal") or "hold").upper()
    health_items.append({
        "label": "模拟交易",
        "value": f"${equity:,.2f}" if equity else "运行中",
        "sub": f"当前信号 {sig}",
        "color": "blue",
    })
else:
    health_items.append({
        "label": "模拟交易",
        "value": "未运行",
        "sub": "到「模拟交易」页启动",
        "color": "gray",
    })

# 4) 实盘 Agent
agent_on = _pid_alive(PROJECT_ROOT / "data" / "agent.pid")
health_items.append({
    "label": "实盘 Agent",
    "value": "运行中" if agent_on else "未运行",
    "sub": "真实下单，谨慎启动",
    "color": "green" if agent_on else "gray",
})

metric_row(health_items)


# ─────────────────────────────────────────────
# 快速上手（新用户三步走）
# ─────────────────────────────────────────────
with section_card("快速上手", "🚀"):
    step_cols = st.columns(3)
    with step_cols[0]:
        st.markdown("**① 看行情**\n\n以太坊实时 K 线、AI 多空分析")
        st.page_link(
            "page_modules/9_🟢_EthereumLive.py",
            label="前往 以太坊行情 →",
        )
    with step_cols[1]:
        st.markdown("**② 跑模拟**\n\n零风险验证策略，跑通监控全流程")
        st.page_link(
            "page_modules/8_💰_PaperTrading.py",
            label="前往 模拟交易 →",
        )
    with step_cols[2]:
        st.markdown("**③ 上实盘**\n\n模拟稳定盈利后，再启动 AI Agent")
        st.page_link(
            "page_modules/11_🤖_AI_Trading.py",
            label="前往 AI 交易 →",
        )


# ─────────────────────────────────────────────
# 账户速览（实盘 Agent 状态，文件不存在时降级）
# ─────────────────────────────────────────────
STATUS_FILE = Path(get_status_file_path())
agent_reporting = STATUS_FILE.exists()
status_data = read_agent_status() if agent_reporting else {}
a3 = status_data.get("agent3", {})
risk_status = a3.get("risk_status", {})
pm_status = status_data.get("position_monitor", {})
base_currency = cfg.trading.symbol.split("-")[0]

with section_card("实盘账户速览", "🛡"):
    kpi_cols = st.columns(4)
    if agent_reporting:
        pos = a3.get("position") or {}
        with kpi_cols[0]:
            if pm_status.get("has_position") and pos:
                side = "🟢 多" if pos.get("side") == "long" else "🔴 空"
                pnl = pos.get("pnl", 0)
                _render_metric_card(
                    "当前持仓",
                    f"{side} {pos.get('size', 0):.4f} {base_currency} (${pnl:+,.2f})",
                )
            else:
                _render_metric_card("当前持仓", "无持仓")
        with kpi_cols[1]:
            _render_metric_card(
                "今日交易",
                f"{risk_status.get('daily_trade_count', 0)} / {risk_status.get('max_daily_trades', '?')}",
            )
        with kpi_cols[2]:
            risk_text = "🔴 日限暂停" if a3.get("paused_for_daily_limit") else "🟢 风控正常"
            _render_metric_card("风控状态", risk_text)
        with kpi_cols[3]:
            _render_metric_card("交易对", f"{cfg.trading.symbol} · {cfg.mode}")
    else:
        with kpi_cols[0]:
            _render_metric_card("当前持仓", "Agent 未上报")
        with kpi_cols[1]:
            _render_metric_card("今日交易", "—")
        with kpi_cols[2]:
            _render_metric_card("风控状态", "Agent 未上报")
        with kpi_cols[3]:
            _render_metric_card("交易对", f"{cfg.trading.symbol} · {cfg.mode}")


# ─────────────────────────────────────────────
# 快捷操作
# ─────────────────────────────────────────────
with section_card("快捷操作", "⚡"):
    action_cols = st.columns(3)
    with action_cols[0]:
        if st.button("▶ 运行全部策略回测", use_container_width=True):
            with st.spinner("正在运行回测..."):
                results = run_all_strategies(cfg)
                if results:
                    st.session_state.comparison_results = results
                    st.success(f"回测完成! {len(results)} 个策略")
                    for name, result in results.items():
                        m = result.get("metrics", {})
                        st.markdown(
                            f"- **{name}**: 收益 {m.get('total_return_pct', 0):+.2f}% | "
                            f"Sharpe {m.get('sharpe', 0):.2f} | "
                            f"回撤 {m.get('max_drawdown_pct', 0):.2f}%"
                        )
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
    with section_card("当前配置", "⚙"):
        cfg_dict = {
            "模式": cfg.mode,
            "交易对": f"{cfg.trading.symbol} ({cfg.trading.market})",
            "K线周期": cfg.trading.timeframes,
            "策略": cfg.strategy.enabled_strategies,
            "策略权重": cfg.strategy.strategy_weights,
            "滑点": f"{cfg.trading.slippage_pct}%",
            "Taker 费率": f"{cfg.trading.taker_fee}%",
            "Maker 费率": f"{cfg.trading.maker_fee}%",
            "最大仓位": f"{cfg.risk.max_position_pct:.0%}",
            "单笔最大": f"{cfg.risk.max_single_order_pct:.0%}",
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
    with section_card("最近回测对比", "📊"):
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
