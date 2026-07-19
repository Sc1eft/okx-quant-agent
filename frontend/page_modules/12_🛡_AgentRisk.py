"""
Agent 风险监控面板（只读）
从 agent_status.json（运行中 Agent 写入）和 SQLite（交易记录）读取实时数据。
无文件时自动回退至演示数据。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.status_writer import read_agent_status, get_status_file_path
from agents.config import AgentSystemConfig

st.title("🛡 Agent 风控监控面板")
st.markdown("实时展示三 Agent 系统的风控状态、交易记录和持仓监控。**只读面板**，不参与决策。")

# ============ 自动检测数据源 ============

STATUS_FILE = Path(get_status_file_path())
DB_FILE = PROJECT_ROOT / "data" / "agent_trades.db"

has_live_status = STATUS_FILE.exists()
has_trade_db = DB_FILE.exists()

if has_live_status:
    st.info("🟢 **实时数据** — 读取自运行中的 Agent 系统")
    status_data = read_agent_status()
    agent3 = status_data.get("agent3", {})
    risk_status = agent3.get("risk_status", {})
    pm_status = status_data.get("position_monitor", {})
    rule_stats = agent3.get("rule_decider_stats", {})
    decision_engine = agent3.get("decision_engine", "rule")
    executor_stats = agent3.get("executor_stats", {})
    agent_mode = status_data.get("mode", "—")
else:
    st.info("🟡 **演示模式** — Agent 系统未运行，显示模拟数据")

# ── 四个标签页 ──
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 风控概览", "📋 交易日志", "📈 持仓监控", "⛓️ 链上数据", "🧠 自学习"])

with tab1:
    st.subheader("Layer 1 — 交易前风控")

    if has_live_status:
        l1 = {
            "每日交易": f"{risk_status.get('daily_trade_count', '?')} / {risk_status.get('max_daily_trades', '?')}",
            "每日亏损": f"${risk_status.get('daily_loss_usdt', '?')} / ${risk_status.get('max_daily_loss_usdt', '?')}",
            "连续亏损": f"{risk_status.get('consecutive_losses', '?')} / {risk_status.get('max_consecutive_losses', '?')}",
            "仓位乘数": f"{risk_status.get('position_size_multiplier', '?')}x",
            "当前仓位": f"{risk_status.get('position_side', 'none')} {risk_status.get('position_eth', 0)} ETH",
        }
    else:
        l1 = {
            "每日交易": "3 / 10",
            "每日亏损": "$25.50 / $100.00",
            "连续亏损": "1 / 3",
            "仓位乘数": "0.75x",
            "API 熔断": "未触发",
        }

    cols = st.columns(3)
    for i, (key, val) in enumerate(l1.items()):
        with cols[i % 3]:
            st.metric(key, val)

    st.divider()
    st.subheader("Layer 2 — 交易中保护")

    _cfg = AgentSystemConfig()
    l2_cols = st.columns(3)
    with l2_cols[0]:
        st.metric("限价单超时", f"{_cfg.limit_order_timeout_seconds}s",
                  help="Layer 2 — 限价单等待超时后自动撤单")
    with l2_cols[1]:
        st.metric("最大滑点", f"{_cfg.max_slippage_pct}%",
                  help="Layer 2 — 超过此滑点自动取消")
    with l2_cols[2]:
        st.metric("部分成交等待", f"{_cfg.partial_fill_timeout_seconds}s",
                  help="Layer 2 — 部分成交后等待剩余成交的超时")

    st.divider()
    st.subheader("Layer 3 — 交易后监控")

    l3_cols = st.columns(3)
    if has_live_status:
        stop_loss = pm_status.get("stop_loss_triggered", 0)
        take_profit = pm_status.get("take_profit_triggered", 0)
        trailing = pm_status.get("trailing_stop_triggered", 0)
    else:
        stop_loss = take_profit = trailing = 0

    with l3_cols[0]:
        st.metric("止损触发", f"{stop_loss} 次",
                  help=f"配置: 移动止损激活 {_cfg.trailing_stop_activation_pct}% / 距离 {_cfg.trailing_stop_distance_pct}%")
    with l3_cols[1]:
        st.metric("止盈触发", f"{take_profit} 次")
    with l3_cols[2]:
        st.metric("移动止损激活", f"{trailing} 次")

    # Agent 状态
    if has_live_status:
        st.divider()
        st.subheader("🤖 Agent 活动状态")
        st.markdown(f"**模式:** `{agent_mode}`")

        from frontend.components.agent_dashboard import render_agent_cards
        render_agent_cards(status_data, show_recent=False)


with tab2:
    st.subheader("最近交易记录")

    if has_trade_db:
        try:
            conn = sqlite3.connect(str(DB_FILE))
            df = pd.read_sql_query(
                "SELECT id, timestamp, side, size, price, pnl, pnl_close, "
                "trade_type, trade_group_id, order_id, symbol "
                "FROM trades ORDER BY id DESC LIMIT 50", conn
            )
            conn.close()
            if not df.empty:
                df["side"] = df["side"].map({"buy": "🟢 买入", "sell": "🔴 卖出"}).fillna(df["side"])
                df["trade_type"] = df["trade_type"].map(
                    {"open": "开仓", "close": "平仓"}
                ).fillna(df["trade_type"])
                df["pnl_close"] = df["pnl_close"].apply(
                    lambda x: f"${x:+,.2f}" if isinstance(x, (int, float)) and x != 0 else "-"
                )
                st.dataframe(df, use_container_width=True, hide_index=True)
                st.caption(f"共 {len(df)} 条记录（最近 50 条）")
            else:
                st.info("交易记录表已存在，但暂无数据")
        except Exception as e:
            st.info(f"读取交易记录失败: {e}")
    else:
        st.warning("以下为演示数据，非真实交易记录")
        demo_trades = pd.DataFrame([
            {"时间": "2026-06-24 10:30:00", "方向": "🟢 买入", "数量": "0.01 ETH",
             "价格": "$3,450.00", "状态": "成交", "订单ID": "12345"},
            {"时间": "2026-06-24 11:15:00", "方向": "🔴 卖出", "数量": "0.01 ETH",
             "价格": "$3,480.50", "状态": "部分成交(50%)", "订单ID": "12346"},
        ])
        st.dataframe(demo_trades, use_container_width=True, hide_index=True)

    # 交易统计
    st.subheader("📊 交易统计")

    if has_live_status:
        exec_stats = executor_stats or {}
        total = agent3.get("trades_executed", 0)
        failed = exec_stats.get("failed_orders", 0)
        rule_calls = rule_stats.get("total_calls", 0)
    else:
        total, failed, rule_calls = 2, 0, 5

    stat_cols = st.columns(3)
    with stat_cols[0]:
        st.metric("成交次数", str(total))
    with stat_cols[1]:
        st.metric("失败订单", str(failed))
    with stat_cols[2]:
        st.metric("规则决策次数", str(rule_calls))


with tab3:
    st.subheader("当前持仓")

    if has_live_status and pm_status:
        pos_side = pm_status.get("position_side", "none")
        has_pos = pm_status.get("has_position", False)

        if has_pos:
            entry = pm_status.get("entry_price", 0)
            sl = pm_status.get("stop_loss", 0)
            tp = pm_status.get("take_profit", 0)
            size = pm_status.get("position_size", 0)
            trailing_active = pm_status.get("trailing_stop_active", False)

            pos_data = {
                "方向": "🟢 多头 / Long" if pos_side == "long" else "🔴 空头 / Short",
                "数量": f"{size:.4f} ETH",
                "入场价": f"${entry:,.2f}",
                "止损位": f"${sl:,.2f}",
                "止盈位": f"${tp:,.2f}",
                "移动止损": "✅ 已激活" if trailing_active else "⬜ 未激活",
            }
        else:
            pos_data = {"当前持仓": "无", "说明": "等待 Agent 3 开仓信号"}

        cols = st.columns(4)
        for i, (key, val) in enumerate(pos_data.items()):
            with cols[i % 4]:
                st.metric(key, val)
    else:
        st.warning("以下为演示数据，非真实持仓")
        demo_pos = {
            "方向": "🟢 多头 / Long",
            "数量": "0.01 ETH",
            "入场价": "$3,450.00",
            "当前价": "$3,500.00",
            "浮盈": "+$0.50 (+1.45%)",
            "止损位": "$3,380.00 (-2.0%)",
            "止盈位": "$3,620.00 (+4.9%)",
            "移动止损": "⬜ 未激活",
        }
        cols = st.columns(4)
        for i, (key, val) in enumerate(demo_pos.items()):
            with cols[i % 4]:
                st.metric(key, val)

    st.divider()
    st.subheader("止盈止损状态")

    sl_tp_cols = st.columns(3)
    with sl_tp_cols[0]:
        st.markdown("**SL ——— TP 位置**")
        if has_live_status and pm_status and pm_status.get("has_position"):
            entry = pm_status.get("entry_price", 0)
            sl = pm_status.get("stop_loss", 0)
            tp = pm_status.get("take_profit", 0)
            if entry and sl != tp:
                progress_val = min(max((entry - sl) / (tp - sl) if sl < tp else (entry - tp) / (sl - tp), 0), 1)
                st.progress(progress_val, text=f"{progress_val:.0%} 向 TP")
            else:
                st.progress(0.5, text="50%")
        else:
            st.progress(0.45, text="45% 向 TP")
    with sl_tp_cols[1]:
        if has_live_status and pm_status and pm_status.get("has_position"):
            entry = pm_status.get("entry_price", 0)
            sl = pm_status.get("stop_loss", 0)
            if entry and sl:
                dist_entry = abs(entry - sl) / entry * 100 if entry else 0
                st.metric("距离止损", f"${abs(entry - sl):,.2f} ({dist_entry:.1f}%)")
            else:
                st.metric("距离止损", "—")
        else:
            st.metric("距离止损", "$120.00 (3.5%)")
    with sl_tp_cols[2]:
        if has_live_status and pm_status and pm_status.get("has_position"):
            entry = pm_status.get("entry_price", 0)
            tp = pm_status.get("take_profit", 0)
            if entry and tp:
                dist_entry = abs(tp - entry) / entry * 100 if entry else 0
                st.metric("距离止盈", f"${abs(tp - entry):,.2f} ({dist_entry:.1f}%)")
            else:
                st.metric("距离止盈", "—")
        else:
            st.metric("距离止盈", "$120.00 (3.5%)")

with tab4:
    st.subheader("⛓️ 链上数据监控（Phase 3）")

    if has_live_status:
        onchain = status_data.get("agent2", {}).get("onchain", {})
        if onchain:
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("⛽ Gas 费", f"{onchain.get('last_gas_gwei', '—')} Gwei",
                          help=f"轮次: {onchain.get('gas_fetches', 0)}")
            with col2:
                buy_ratio = onchain.get('last_taker_buy_ratio', 0)
                if buy_ratio:
                    sentiment = "🟢 偏多" if buy_ratio > 0.6 else ("🔴 偏空" if buy_ratio < 0.4 else "⚪ 中性")
                else:
                    sentiment = "—"
                st.metric("📊 吃单买比", f"{buy_ratio:.1%}" if buy_ratio else "—",
                          help=f"{sentiment} | 轮次: {onchain.get('taker_fetches', 0)}")
            with col3:
                fr = onchain.get('last_funding_rate', 0)
                st.metric("💰 资金费率", f"{fr:+.4f}%" if fr else "—",
                          help=f"轮次: {onchain.get('funding_fetches', 0)}")
            with col4:
                st.metric("🐋 巨鲸转账", str(onchain.get('last_whale_count', '—')),
                          help=f"轮次: {onchain.get('whale_fetches', 0)}")

            st.divider()
            st.subheader("链上推送统计")
            stat_c = st.columns(3)
            with stat_c[0]:
                st.metric("总推送事件", str(onchain.get("events_pushed", 0)))
            with stat_c[1]:
                st.metric("Gas 抓取", str(onchain.get("gas_fetches", 0)))
            with stat_c[2]:
                st.metric("吃单比抓取", str(onchain.get("taker_fetches", 0)))
        else:
            st.info("链上数据收集器未运行或尚无数据")
    else:
        demo_onchain = {
            "⛽ Gas 费": "35 Gwei (低)",
            "📊 吃单买比": "58.3% (偏多)",
            "💰 资金费率": "+0.0021% (中性)",
            "🐋 巨鲸": "最近 24h: 3 笔",
        }
        cols = st.columns(4)
        for i, (k, v) in enumerate(demo_onchain.items()):
            with cols[i % 4]:
                st.metric(k, v)

        st.divider()
        st.info("💡 链上数据功能需要以下 API Key:\n"
                "- **Etherscan API Key**: Gas 费监控\n"
                "- **Whale Alert API Key**: 巨鲸转账监控\n"
                "- **Taker Volume / Funding Rate**: OKX 公开 API（无需 Key）")


with tab5:
    st.subheader("🧠 Phase 4 — 自学习指标")

    if has_live_status and agent3:
        p4 = agent3.get("phase4", {})
        phase4_cols = st.columns(4)

        with phase4_cols[0]:
            cs = agent3.get("last_composite_score", "—")
            cc = agent3.get("last_composite_confidence", "—")
            if isinstance(cs, (int, float)):
                cs_dir = "📈 偏多" if cs > 0.2 else "📉 偏空" if cs < -0.2 else "⚖️ 中性"
                st.metric(f"综合方向 ({cs_dir})", f"{cs:+.2f}",
                          f"信心 {cc:.0%}" if isinstance(cc, (int, float)) else "—")
            else:
                st.metric("综合方向", str(cs))

        with phase4_cols[1]:
            al = agent3.get("last_alignment_score", "—")
            if isinstance(al, (int, float)):
                al_label = "🤝 共识" if al >= 0.7 else ("⚠️ 分歧" if al < 0.4 else "⚪ 中性")
                st.metric(f"信号对齐 ({al_label})", f"{al:.2f}")
            else:
                st.metric("信号对齐", str(al))

        with phase4_cols[2]:
            pnl = agent3.get("last_monthly_pnl", 0)
            if isinstance(pnl, (int, float)):
                st.metric("月度盈亏", f"${pnl:+,.2f}",
                          delta_color="normal" if pnl >= 0 else "inverse")
            else:
                st.metric("月度盈亏", str(pnl))

        with phase4_cols[3]:
            wr_val = agent3.get("last_win_rate", "—")
            md_val = agent3.get("max_drawdown", "—")
            if isinstance(wr_val, (int, float)):
                st.metric("月度胜率", f"{wr_val:.1f}%",
                          f"最大回撤 {md_val:.2f}%" if isinstance(md_val, (int, float)) else None)
            else:
                st.metric("月度胜率", str(wr_val))

        # 信号对齐摘要
        sa_text = agent3.get("signal_alignment", "")
        if sa_text and sa_text != "暂无对齐数据":
            al_score = agent3.get("last_alignment_score", 0)
            border_color = "#059669" if isinstance(al_score, (int, float)) and al_score >= 0.7 else "#f59e0b"
            st.markdown(
                f'<div style="background:#f8fafc;border-radius:8px;padding:0.75rem;'
                f'margin-top:0.75rem;border-left:4px solid {border_color};'
                f'font-size:0.9rem;color:#334155;">'
                f'📋 信号对齐摘要: {sa_text}</div>',
                unsafe_allow_html=True,
            )

        # 自适应参数
        st.divider()
        st.subheader("⚙️ 自适应参数")
        amt = agent3.get("adjusted_max_trades", "—")
        adb = agent3.get("adjusted_debounce", "—")
        ait = agent3.get("adjusted_trade_interval", "—")

        acols = st.columns(3)
        with acols[0]:
            st.metric("最大日交易", amt, help=f"Config 默认: {_cfg.agent3_max_daily_trades}")
        with acols[1]:
            st.metric("信号采集间隔", f"{adb}s", help=f"Config 默认: {_cfg.agent3_debounce_seconds}s")
        with acols[2]:
            st.metric("最小交易间隔", f"{ait}s", help=f"Config 默认: {_cfg.agent3_min_interval_between_trades}s")

        # 参数调整历史
        param_changes = p4.get("param_changes", [])
        if param_changes:
            st.subheader("📋 参数调整记录")
            for entry in param_changes[-10:]:
                ts = entry.get("timestamp", "—")
                changes = entry.get("changes", {})
                reason = entry.get("reason", "")
                st.markdown(f"- **{ts}** — {reason}")
                if changes:
                    st.caption("  " + "  ".join(f"{k}: {v}" for k, v in changes.items()))
        else:
            st.info("暂无参数调整记录（需至少 10 笔交易后才会触发自适应调整）")

        # 决策引擎统计
        st.divider()
        st.subheader("🧭 决策引擎统计")
        rs = rule_stats or {}
        dcols = st.columns(2)
        with dcols[0]:
            engine_label = "规则决策 (RuleDecider)" if decision_engine == "rule" else str(decision_engine)
            st.metric("决策引擎", engine_label)
        with dcols[1]:
            st.metric("决策次数", rs.get("total_calls", 0))

    else:
        demo_p4 = {
            "综合方向": "⚖️ 中性 (0.00)",
            "信号对齐": "⚪ 0.50 (待数据)",
            "月度盈亏": "$0.00",
            "月度胜率": "0.0%",
            "自适应参数": "日交易 10 / 采集 30s / 间隔 300s",
            "决策引擎": "规则决策 / 0 次",
        }
        cols = st.columns(3)
        for i, (k, v) in enumerate(demo_p4.items()):
            with cols[i % 3]:
                st.metric(k, v)
        st.divider()
        st.info("💡 点击「启动 Agent」运行三 Agent 系统后，此页面将实时显示自学习指标。")


st.divider()
st.caption(f"🕐 面板刷新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据延迟 ≤ 5s | 自动检测数据源")
