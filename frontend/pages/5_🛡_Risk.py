"""Risk page - risk configuration, status monitoring, and controls."""

import sys
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.utils.session_state import get_config, save_config
from risk.rules import RiskEngine, RiskState


st.title("🛡 风控面板")
st.markdown("风险控制参数查看、编辑和状态监控")


cfg = get_config()

# ============ Risk Status ============
st.subheader("📊 风控状态")

# Initialize risk engine in session state
if st.session_state.risk_engine is None:
    st.session_state.risk_engine = RiskEngine(cfg)
    st.session_state.risk_paused = False

risk_engine = st.session_state.risk_engine
state = risk_engine.state

status_cols = st.columns(4)
with status_cols[0]:
    paused = st.session_state.risk_paused
    status_text = "🔴 已暂停" if paused else "🟢 运行中"
    st.metric("当前状态", status_text)
with status_cols[1]:
    st.metric("连续亏损", f"{state.consecutive_losses}/{cfg.risk.max_consecutive_losses}")
with status_cols[2]:
    st.metric("当日亏损", f"{state.daily_loss_pct:.2f}% / {cfg.risk.max_daily_loss_pct}%")
with status_cols[3]:
    st.metric("当日交易", f"{state.daily_trades} / 20")

if state.is_paused:
    st.warning(f"⏸ 风控已暂停: {state.pause_reason}")

# Manual controls
ctrl_cols = st.columns(4)
with ctrl_cols[0]:
    if paused:
        if st.button("▶ 恢复交易", type="primary", use_container_width=True):
            st.session_state.risk_paused = False
            state.is_paused = False
            state.pause_reason = ""
            st.rerun()
    else:
        if st.button("⏸ 暂停交易", use_container_width=True):
            st.session_state.risk_paused = True
            state.is_paused = True
            state.pause_reason = "手动暂停"
            st.rerun()

with ctrl_cols[1]:
    if st.button("🔄 重置当日", use_container_width=True):
        state.reset_daily(10000)
        state.consecutive_losses = 0
        st.session_state.risk_paused = False
        st.success("当日风控状态已重置")

with ctrl_cols[2]:
    if st.button("📝 模拟亏损", use_container_width=True):
        risk_engine.record_trade_result(-2.5)
        if state.consecutive_losses >= cfg.risk.max_consecutive_losses:
            st.session_state.risk_paused = state.is_paused
        st.rerun()

with ctrl_cols[3]:
    if st.button("📝 模拟盈利", use_container_width=True):
        risk_engine.record_trade_result(1.5)
        st.rerun()

st.divider()

# ============ Risk Config Editor ============
st.subheader("⚙ 风控参数配置")

# Render risk config manually for better UX
risk = cfg.risk

risk_cols = st.columns(3)
with risk_cols[0]:
    st.markdown("**仓位限制**")
    new_max_pos = st.slider("最大仓位 (%)", 5.0, 100.0, risk.max_position_pct, key="risk_max_pos")
    new_max_order = st.slider("单笔最大 (%)", 1.0, 50.0, risk.max_single_order_pct, key="risk_max_order")

with risk_cols[1]:
    st.markdown("**亏损限制**")
    new_max_loss = st.slider("日最大亏损 (%)", 0.5, 20.0, risk.max_daily_loss_pct, step=0.5, key="risk_max_loss")
    new_max_cons = st.slider("连续止损次数", 1, 10, risk.max_consecutive_losses, key="risk_max_cons")

with risk_cols[2]:
    st.markdown("**冷却设置**")
    new_cooldown = st.slider("冷却 (根K线)", 0, 50, risk.cooldown_bars, key="risk_cooldown")
    new_expiry = st.slider("信号过期 (根K线)", 0, 10, risk.signal_expiry_bars, key="risk_expiry")

# Recovery mode
st.subheader("🔄 恢复模式")

rec_cols = st.columns(3)
with rec_cols[0]:
    new_recovery = st.selectbox(
        "恢复模式",
        ["manual", "auto_cool", "switch_strategy"],
        index=["manual", "auto_cool", "switch_strategy"].index(risk.recovery_mode),
        key="risk_recovery",
    )
with rec_cols[1]:
    new_rec_cooldown = st.number_input(
        "恢复冷却 (根K线)", 0, 100, risk.recovery_cooldown_bars, key="risk_rec_cooldown"
    )
with rec_cols[2]:
    new_max_restarts = st.number_input(
        "日最大重启次数", 1, 10, risk.max_daily_starts, key="risk_max_restarts"
    )

# Apply changes
if st.button("💾 应用风控设置", type="primary"):
    risk.max_position_pct = new_max_pos
    risk.max_single_order_pct = new_max_order
    risk.max_daily_loss_pct = new_max_loss
    risk.max_consecutive_losses = new_max_cons
    risk.cooldown_bars = new_cooldown
    risk.signal_expiry_bars = new_expiry
    risk.recovery_mode = new_recovery
    risk.recovery_cooldown_bars = new_rec_cooldown
    risk.max_daily_starts = new_max_restarts

    # Recreate risk engine with new config
    st.session_state.risk_engine = RiskEngine(cfg)
    save_config()
    st.success("风控参数已更新并保存!")

st.divider()

# ============ Recovery History ============
st.subheader("📋 暂停/恢复记录")

history = getattr(state, "strategy_switch_history", [])
if history:
    df = pd.DataFrame(history)
    st.dataframe(df, use_container_width=True)
else:
    st.info("暂无暂停记录")

# Audit info
st.divider()
st.subheader("🔒 审计规则速查")

audit_cols = st.columns(3)
with audit_cols[0]:
    st.markdown("**✅ Agent 可以做**")
    can_dos = [
        "解释策略逻辑",
        "总结回测报告",
        "检测过拟合",
        "建议参数范围",
        "检查风控配置",
    ]
    for item in can_dos:
        st.markdown(f"- ✅ {item}")

with audit_cols[1]:
    st.markdown("**❌ Agent 不可以做**")
    cannot_dos = [
        "直接下单或改单",
        "绕过风控",
        "修改 API Key",
        "自动扩大仓位",
        "关闭风控",
        "自动部署到实盘",
    ]
    for item in cannot_dos:
        st.markdown(f"- ❌ {item}")

with audit_cols[2]:
    st.markdown("**⚠️ 需人工审批**")
    approvals = [
        "修改策略参数",
        "切换交易模式",
        "修改风控阈值",
        "启用新交易对",
    ]
    for item in approvals:
        st.markdown(f"- ⚠️ {item}")
