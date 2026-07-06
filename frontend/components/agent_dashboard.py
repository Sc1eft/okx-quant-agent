"""
Shared agent status card rendering for Streamlit dashboard pages.

Usage:
    from frontend.components.agent_dashboard import render_agent_cards

    render_agent_cards(status_data)
    render_agent_cards(status_data, show_recent=False)  # omit recent trades
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_FILE = PROJECT_ROOT / "data" / "agent_trades.db"

# Shared CSS for detail sections rendered inside Streamlit expanders (main page, not iframe)
_DETAIL_STYLE = """<style>
.detail-section-title {
    font-weight: 600;
    font-size: 0.82rem;
    color: #64748b;
    margin: 0.45rem 0 0.25rem;
    padding-bottom: 0.1rem;
    border-bottom: 1px solid #f1f5f9;
}
.detail-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.12rem 0;
    font-size: 0.8rem;
    line-height: 1.5;
}
.detail-row .label {
    color: #64748b;
}
.detail-row .value {
    color: #0f172a;
    font-family: monospace;
    font-size: 0.78rem;
    text-align: right;
}
.detail-sep {
    height: 1px;
    background: #f1f5f9;
    margin: 0.35rem 0;
}
/* ── Pipeline flow ── */
.pipeline-flow {
    display: flex;
    align-items: center;
    gap: 0.2rem;
    margin: 0.25rem 0;
}
.pipeline-step {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.3rem 0.4rem;
    border-radius: 8px;
    background: #f8fafc;
    min-width: 0;
}
.pipeline-step .step-dot { font-size: 0.85rem; }
.pipeline-step .step-content { flex: 1; min-width: 0; }
.pipeline-step .step-label {
    font-size: 0.62rem;
    color: #64748b;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.pipeline-step .step-value {
    font-size: 0.72rem;
    font-weight: 600;
    color: #0f172a;
    font-family: monospace;
}
.pipeline-arrow {
    color: #94a3b8;
    font-size: 0.9rem;
    flex-shrink: 0;
}
/* ── Per-timeframe progress bars ── */
.tf-progress { margin: 0.25rem 0; }
.tf-header {
    display: flex;
    justify-content: space-between;
    font-size: 0.68rem;
    margin-bottom: 0.15rem;
}
.tf-name { font-weight: 600; color: #0f172a; }
.tf-status { color: #64748b; font-family: monospace; font-size: 0.65rem; }
.tf-bar-bg {
    height: 5px;
    border-radius: 4px;
    overflow: hidden;
    background: #f1f5f9;
}
.tf-bar-fill {
    height: 100%;
    border-radius: 4px;
}
.tf-bar-ready { box-shadow: 0 0 6px rgba(34,197,94,0.4); }
</style>"""


def _uptime(start_str: str) -> str:
    """Format uptime from ISO start time string."""
    if not start_str:
        return "—"
    try:
        start = datetime.fromisoformat(start_str)
        delta = int((datetime.now(timezone.utc) - start).total_seconds())
        h, m = divmod(delta, 3600)
        m, s = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return "—"


def _activity_age(last_time: float) -> str:
    """Format seconds since last activity into a human age string."""
    if not last_time:
        return ""
    age = time.time() - last_time
    if age < 1:
        return "刚刚"
    if age < 60:
        return f"{age:.0f} 秒前"
    if age < 3600:
        return f"{age / 60:.0f} 分钟前"
    return f"{age / 3600:.1f} 小时前"


def _fmt(n) -> str:
    """Format a number for display (1k, 10k, etc.)."""
    if n is None:
        return "0"
    if isinstance(n, (int, float)):
        if n >= 100000:
            return f"{n / 1000:.0f}k"
        return f"{n:,}"
    return str(n)


def _build_agent1_detail_html(a1: dict) -> str:
    """Build expandable detail HTML for Agent 1 (Technical Analysis)."""
    parts = []

    ticks = a1.get("ticks_received", 0)
    bars = a1.get("bars_completed", 0)
    signals = a1.get("signals_pushed", 0)
    bar_counts = a1.get("bar_counts", {}) or {}
    indicators = a1.get("latest_indicators", {}) or {}
    signal_history = a1.get("signal_history", []) or []

    # ── 1. Pipeline flow ──
    parts.append('<div class="detail-section-title">🔄 数据流管道</div>')
    flow_ok = "🟢" if ticks > 0 else "🔴"
    bar_ok = "🟢" if bars > 0 else "🔴"
    sig_ok = "🟢" if signals > 0 else "⏳"
    bar_pct = min(bars / 90 * 100, 100) if bars > 0 else 0
    parts.append(f"""
<div class="pipeline-flow">
    <div class="pipeline-step active">
        <div class="step-dot">{flow_ok}</div>
        <div class="step-content">
            <div class="step-label">WebSocket 行情流</div>
            <div class="step-value">{ticks:,} ticks</div>
        </div>
    </div>
    <div class="pipeline-arrow">→</div>
    <div class="pipeline-step">
        <div class="step-dot">{bar_ok}</div>
        <div class="step-content">
            <div class="step-label">K线构建器</div>
            <div class="step-value">{bars} 根 K线</div>
        </div>
    </div>
    <div class="pipeline-arrow">→</div>
    <div class="pipeline-step">
        <div class="step-dot">{sig_ok}</div>
        <div class="step-content">
            <div class="step-label">信号检测引擎</div>
            <div class="step-value">{signals} 条信号</div>
        </div>
    </div>
</div>""")

    # ── 2. Per-timeframe bar progress ──
    parts.append('<div class="detail-sep"></div>')
    parts.append('<div class="detail-section-title">📐 各周期 K 线进度（需 30 根启动指标）</div>')

    timeline_labels = {"3m": "3 分钟", "5m": "5 分钟", "15m": "15 分钟", "1h": "1 小时", "1d": "日线"}
    for tf in ["3m", "5m", "15m", "1h", "1d"]:
        count = bar_counts.get(tf, 0)
        has_indicator = tf in indicators
        target = 30
        pct = min(count / target * 100, 100)
        label = timeline_labels.get(tf, tf)

        if has_indicator:
            close = indicators[tf].get("close", "—")
            close_str = f" @ ${close:,.2f}" if isinstance(close, (int, float)) else ""
            status_str = f"✅ 已就绪{close_str}"
        elif count >= target:
            status_str = "✅ 已就绪"
        else:
            remaining = target - count
            status_str = f"⏳ {count}/{target}（还需 {remaining} 根）"

        bar_color = "var(--green, #22c55e)" if has_indicator else "var(--accent, #3b82f6)" if count > 0 else "var(--border, #e2e8f0)"
        bg_color = "rgba(34,197,94,0.15)" if has_indicator else "rgba(59,130,246,0.12)" if count > 0 else "var(--border-light, #f1f5f9)"
        parts.append(f"""
<div class="tf-progress">
    <div class="tf-header">
        <span class="tf-name">{label}</span>
        <span class="tf-status">{status_str}</span>
    </div>
    <div class="tf-bar-bg" style="background:{bg_color};">
        <div class="tf-bar-fill {'tf-bar-ready' if has_indicator else ''}"
             style="width:{pct:.0f}%;background:{bar_color};"></div>
    </div>
</div>""")

    # ── 3. Detailed indicators per timeframe (only if available) ──
    if indicators:
        parts.append('<div class="detail-sep"></div>')
        for tf in ["3m", "5m", "15m", "1h", "1d"]:
            ind = indicators.get(tf, {})
            if not ind:
                continue
            macd = ind.get("macd", {}) or {}
            kdj_ = ind.get("kdj", {}) or {}
            boll = ind.get("boll", {}) or {}
            close = ind.get("close", "—")
            close_str = f"${close:,.2f}" if isinstance(close, (int, float)) else "—"

            # Direction summary
            dir_parts = []
            if macd:
                h = macd.get("histogram", 0)
                dir_parts.append("🟢 MACD+" if isinstance(h, (int, float)) and h > 0 else "🔴 MACD-" if isinstance(h, (int, float)) and h < 0 else "⚪ MACD")
            if kdj_:
                cross = kdj_.get("k_cross", "")
                if cross == "golden": dir_parts.append("KDJ↑")
                elif cross == "dead": dir_parts.append("KDJ↓")
                if kdj_.get("overbought"): dir_parts.append("⚠️超买")
                if kdj_.get("oversold"): dir_parts.append("🔥超卖")
            if boll:
                pos = boll.get("position", 0.5)
                if isinstance(pos, (int, float)):
                    if pos > 0.9: dir_parts.append("上轨")
                    elif pos < 0.1: dir_parts.append("下轨")
                    else: dir_parts.append("中轨")

            dir_text = " · ".join(dir_parts) if dir_parts else "—"

            parts.append(f'<div class="detail-section-title">{tf} @ {close_str}</div>')
            parts.append(f'<div class="detail-row"><span class="label">🎯 方向综合</span><span class="value">{dir_text}</span></div>')
            if macd:
                parts.append(
                    f'<div class="detail-row"><span class="label">MACD</span>'
                    f'<span class="value">DIF:{macd.get("macd","—")} DEA:{macd.get("signal","—")} Hist:{macd.get("histogram","—")}</span></div>'
                )
            if kdj_:
                parts.append(
                    f'<div class="detail-row"><span class="label">KDJ</span>'
                    f'<span class="value">K:{kdj_.get("k","—")} D:{kdj_.get("d","—")} J:{kdj_.get("j","—")}</span></div>'
                )
            if boll:
                parts.append(
                    f'<div class="detail-row"><span class="label">布林带</span>'
                    f'<span class="value">上:{boll.get("upper","—")} 中:{boll.get("mid","—")} 下:{boll.get("lower","—")}</span></div>'
                )

    # ── 4. Signal history ──
    if signal_history:
        parts.append('<div class="detail-sep"></div>')
        parts.append(f'<div class="detail-section-title">📡 最近信号推送（{len(signal_history)}条）</div>')
        for sig in reversed(signal_history[-10:]):
            ts = sig.get("ts", "")[5:19] if sig.get("ts") else ""
            desc = sig.get("description", "")
            urgency = sig.get("urgency", "")
            conf = sig.get("confidence", 0)
            tf = sig.get("timeframe", "")
            price = sig.get("price", 0)
            urg_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(urgency, "⚪")
            price_str = f" @ ${price:,.2f}" if isinstance(price, (int, float)) and price > 0 else ""
            parts.append(
                f'<div class="detail-row" style="font-size:0.68rem;">'
                f'<span class="label">{ts} {urg_icon} [{tf}]</span>'
                f'<span class="value">{desc}{price_str} (信心{conf:.0%})</span></div>'
            )

    if not parts:
        parts.append('<div class="detail-row" style="color:var(--text-muted);">⏳ 等待 Agent 1 生成首次技术分析…</div>')

    return "\n".join(parts)


def _build_agent2_detail_html(a2: dict) -> str:
    """Build expandable detail HTML for Agent 2 (News & Onchain)."""
    parts = []

    # News stats
    parts.append('<div class="detail-section-title">📰 新闻数据</div>')
    parts.append(
        f'<div class="detail-row"><span class="label">🔍 抓取轮次</span>'
        f'<span class="value">{_fmt(a2.get("fetch_count", 0))}</span></div>'
    )
    parts.append(
        f'<div class="detail-row"><span class="label">📖 已阅新闻</span>'
        f'<span class="value">{_fmt(a2.get("news_seen", 0))}</span></div>'
    )
    parts.append(
        f'<div class="detail-row"><span class="label">📰 推送信号</span>'
        f'<span class="value">{_fmt(a2.get("news_pushed", 0))}</span></div>'
    )

    # Onchain data detail
    onchain = a2.get("onchain", {}) or {}
    if onchain:
        parts.append('<div class="detail-sep"></div>')
        parts.append('<div class="detail-section-title">⛓️ 链上数据</div>')

        gas = onchain.get("last_gas_gwei", "—")
        gas_fetches = onchain.get("gas_fetches", 0)
        parts.append(
            f'<div class="detail-row"><span class="label">⛽ Gas</span>'
            f'<span class="value">{gas} Gwei (抓取{gas_fetches}次)</span></div>'
        )

        taker = onchain.get("last_taker_buy_ratio", 0)
        taker_fetches = onchain.get("taker_fetches", 0)
        taker_str = f"{taker:.1%}" if isinstance(taker, float) else str(taker)
        taker_sentiment = "🟢 偏多" if isinstance(taker, (int, float)) and taker > 0.6 else "🔴 偏空" if isinstance(taker, (int, float)) and taker < 0.4 else "⚪ 中性"
        parts.append(
            f'<div class="detail-row"><span class="label">{taker_sentiment} 吃单买比</span>'
            f'<span class="value">{taker_str} (抓取{taker_fetches}次)</span></div>'
        )

        funding = onchain.get("last_funding_rate", 0)
        fr_fetches = onchain.get("funding_fetches", 0)
        fr_str = f"{funding:+.5f}%" if isinstance(funding, (int, float)) else "—"
        parts.append(
            f'<div class="detail-row"><span class="label">💰 资金费率</span>'
            f'<span class="value">{fr_str} (抓取{fr_fetches}次)</span></div>'
        )

        whale = onchain.get("last_whale_count", "—")
        whale_fetches = onchain.get("whale_fetches", 0)
        parts.append(
            f'<div class="detail-row"><span class="label">🐋 巨鲸转账</span>'
            f'<span class="value">{whale} 笔 (抓取{whale_fetches}次)</span></div>'
        )

        events = onchain.get("events_pushed", 0)
        parts.append(
            f'<div class="detail-row"><span class="label">⛓️ 链上事件推送</span>'
            f'<span class="value">{events} 次</span></div>'
        )

    if not parts:
        parts.append('<div class="detail-row" style="color:var(--text-muted);">⏳ 等待 Agent 2 收集数据…</div>')

    return "\n".join(parts)


def _get_recent_decisions_html(limit: int = 5) -> str:
    """Read recent trades from SQLite and render decision details."""
    try:
        if not DB_FILE.exists():
            return ""
        conn = sqlite3.connect(str(DB_FILE))
        cur = conn.execute(
            "SELECT id, timestamp, side, size, price, pnl_close, trade_type, decision "
            "FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return ""

    if not rows:
        return ""

    parts = ['<div class="detail-sep"></div>']
    parts.append('<div class="detail-section-title">📋 最近交易决策</div>')

    for row in rows:
        trade_id, ts, side, size, price, pnl, trade_type, decision_json = row
        # Parse timestamp
        try:
            t = ts.split(".")[0] if ts else "—"
            if len(t) > 16:
                t = t[5:16]  # MM-DD HH:MM
        except Exception:
            t = "—"

        side_icon = "🟢" if side == "buy" else "🔴" if side == "sell" else "⚪"
        tt_label = "开" if trade_type == "open" else "平" if trade_type == "close" else "?"
        pnl_str = f"${pnl:+,.2f}" if isinstance(pnl, (int, float)) and pnl != 0 else "—"

        # Try to parse decision JSON
        decision_info = ""
        if decision_json and decision_json != "{}":
            try:
                import json
                d = json.loads(decision_json)
                action = d.get("action", "")
                conf = d.get("confidence", "")
                reason = d.get("reason", "")
                short_reason = (reason[:60] + "…") if len(reason) > 60 else reason
                action_map = {"buy": "买入", "sell": "卖出", "hold": "持有"}
                action_cn = action_map.get(action, action)
                decision_info = f" | {action_cn} (信心:{conf}%) {short_reason}"
            except Exception:
                pass

        parts.append(
            f'<div class="detail-row" style="font-size:0.68rem;">'
            f'<span class="label">{t} {side_icon} {tt_label}</span>'
            f'<span class="value">{side} {size:.4f} @ ${price:,.2f} {pnl_str}{decision_info}</span></div>'
        )

    return "\n".join(parts)


def _build_agent3_detail_html(a3: dict, recent_decisions_html: str = "") -> str:
    """Build expandable detail HTML for Agent 3 (Trading Decision)."""
    parts = []

    ds = a3.get("deepseek_stats", {}) or {}
    if ds:
        parts.append('<div class="detail-section-title">🧠 DeepSeek 分析统计</div>')
        parts.append(
            f'<div class="detail-row"><span class="label">总调用</span>'
            f'<span class="value">{_fmt(ds.get("total_calls", 0))}</span></div>'
        )
        parts.append(
            f'<div class="detail-row"><span class="label">总错误</span>'
            f'<span class="value">{_fmt(ds.get("total_errors", 0))}</span></div>'
        )
        avg_dur = ds.get("avg_duration_ms", 0)
        parts.append(
            f'<div class="detail-row"><span class="label">平均耗时</span>'
            f'<span class="value">{avg_dur:.0f}ms</span></div>'
        )
        model = ds.get("model", "—")
        parts.append(
            f'<div class="detail-row"><span class="label">模型</span>'
            f'<span class="value">{model}</span></div>'
        )

    # Risk status
    risk = a3.get("risk_status", {}) or {}
    if risk:
        parts.append('<div class="detail-sep"></div>')
        parts.append('<div class="detail-section-title">🛡️ 风控状态 (Layer 1)</div>')
        dt = risk.get("daily_trade_count", "—")
        md = risk.get("max_daily_trades", "—")
        parts.append(
            f'<div class="detail-row"><span class="label">📊 日交易</span>'
            f'<span class="value">{dt} / {md}</span></div>'
        )
        dl = risk.get("daily_loss_usdt", 0)
        mdl = risk.get("max_daily_loss_usdt", 0)
        parts.append(
            f'<div class="detail-row"><span class="label">💰 日亏损</span>'
            f'<span class="value">${dl:+.2f} / ${mdl:.2f}</span></div>'
        )
        cl = risk.get("consecutive_losses", 0)
        mcl = risk.get("max_consecutive_losses", 0)
        parts.append(
            f'<div class="detail-row"><span class="label">📉 连续亏损</span>'
            f'<span class="value">{cl} / {mcl}</span></div>'
        )
        mult = risk.get("position_size_multiplier", 1.0)
        parts.append(
            f'<div class="detail-row"><span class="label">📐 仓位乘数</span>'
            f'<span class="value">{mult:.2f}x</span></div>'
        )
        pos_eth = risk.get("position_eth", 0)
        pos_side = risk.get("position_side") or "none"
        parts.append(
            f'<div class="detail-row"><span class="label">📋 风控仓位</span>'
            f'<span class="value">{pos_side} {pos_eth:.4f} ETH</span></div>'
        )

    # Executor stats
    exec_stats = a3.get("executor_stats", {}) or {}
    if exec_stats:
        parts.append('<div class="detail-sep"></div>')
        parts.append('<div class="detail-section-title">💼 交易执行统计</div>')
        parts.append(
            f'<div class="detail-row"><span class="label">总订单</span>'
            f'<span class="value">{_fmt(exec_stats.get("total_orders", 0))}</span></div>'
        )
        parts.append(
            f'<div class="detail-row"><span class="label">失败订单</span>'
            f'<span class="value">{_fmt(exec_stats.get("failed_orders", 0))}</span></div>'
        )
        sym = exec_stats.get("symbol", "—")
        parts.append(
            f'<div class="detail-row"><span class="label">交易对</span>'
            f'<span class="value">{sym}</span></div>'
        )

    # Phase 4
    p4 = a3.get("phase4", {}) or {}
    if p4:
        parts.append('<div class="detail-sep"></div>')
        parts.append('<div class="detail-section-title">🧠 Phase 4 模块状态</div>')
        module_icons = {
            "confidence_scorer": "信心评分",
            "signal_aligner": "信号对齐",
            "review_generator": "复盘生成",
            "param_adapter": "参数自适应",
        }
        for key, label in module_icons.items():
            enabled = p4.get(key, False)
            icon = "✅" if enabled else "❌"
            parts.append(
                f'<div class="detail-row"><span class="label">{icon} {label}</span>'
                f'<span class="value">{"已启用" if enabled else "未启用"}</span></div>'
            )

        # Recent param changes
        param_changes = p4.get("param_changes", [])
        if param_changes:
            parts.append('<div class="detail-sep"></div>')
            parts.append('<div class="detail-section-title">📋 最近参数调整</div>')
            for entry in param_changes[-5:]:
                ts = entry.get("timestamp", "—").split(".")[0]  # trim microseconds
                reason = entry.get("reason", "")
                changes = entry.get("changes", {})
                change_str = " ".join(f"{k}:{v}" for k, v in changes.items())
                parts.append(
                    f'<div class="detail-row" style="font-size:0.7rem;">'
                    f'<span class="label">{ts}</span>'
                    f'<span class="value">{reason} {change_str}</span></div>'
                )

    # Event stats
    parts.append('<div class="detail-sep"></div>')
    parts.append('<div class="detail-section-title">📨 事件队列统计</div>')
    parts.append(
        f'<div class="detail-row"><span class="label">Queue A (技术面)</span>'
        f'<span class="value">{_fmt(a3.get("events_received_a", 0))} 事件</span></div>'
    )
    parts.append(
        f'<div class="detail-row"><span class="label">Queue B (新闻/链上)</span>'
        f'<span class="value">{_fmt(a3.get("events_received_b", 0))} 事件</span></div>'
    )
    parts.append(
        f'<div class="detail-row"><span class="label">📦 缓冲中</span>'
        f'<span class="value">{a3.get("event_buffer_size", 0)} 事件</span></div>'
    )

    if recent_decisions_html:
        parts.append(recent_decisions_html)

    return "\n".join(parts)


def _build_indicator_tags(indicators: dict) -> str:
    """Build HTML indicator tags from Agent 1's latest_indicators."""
    tag_parts = []
    for tf in ["3m", "5m", "15m", "1h", "1d"]:
        ind = indicators.get(tf, {})
        if not ind:
            continue
        parts = [
            '<span style="font-weight:600;font-size:0.65rem;'
            'color:var(--text-secondary,#64748b);min-width:1.5rem;">' + tf + "</span>"
        ]
        macd = ind.get("macd", {}) or {}
        kdj = ind.get("kdj", {}) or {}
        boll = ind.get("boll", {}) or {}

        if macd:
            h = macd.get("histogram", 0)
            if h is not None:
                cls = "bullish" if h > 0 else "bearish" if h < 0 else "neutral"
                arrow = "↑" if h > 0 else "↓" if h < 0 else "→"
                parts.append(f'<span class="tag {cls}">MACD{arrow}</span>')
        if kdj:
            if kdj.get("k_cross") == "golden":
                parts.append('<span class="tag bullish">KDJ↑</span>')
            elif kdj.get("k_cross") == "dead":
                parts.append('<span class="tag bearish">KDJ↓</span>')
            if kdj.get("overbought"):
                parts.append('<span class="tag bearish">超买</span>')
            if kdj.get("oversold"):
                parts.append('<span class="tag bullish">超卖</span>')
        if boll:
            if boll.get("squeeze"):
                parts.append('<span class="tag neutral">收口</span>')
            pos = boll.get("position", 0.5)
            if isinstance(pos, (int, float)):
                if pos > 0.9:
                    parts.append('<span class="tag bearish">上轨</span>')
                elif pos < 0.1:
                    parts.append('<span class="tag bullish">下轨</span>')
        if len(parts) > 1:
            margin = "margin-top:0.15rem;" if tag_parts else ""
            tag_parts.append(
                f'<div style="display:flex;gap:0.25rem;align-items:center;{margin}">'
                f'{" ".join(parts)}</div>'
            )
    return "\n".join(tag_parts) if tag_parts else (
        '<div style="font-size:0.7rem;color:var(--text-muted,#94a3b8);">等待指标数据…</div>'
    )


def _build_onchain_html(onchain: dict) -> str:
    """Build HTML for onchain data display."""
    if not onchain:
        return '<div style="font-size:0.7rem;color:var(--text-muted,#94a3b8);">等待链上数据…</div>'
    gas = onchain.get("last_gas_gwei", "—")
    taker = onchain.get("last_taker_buy_ratio", 0)
    funding = onchain.get("last_funding_rate", 0)
    whale = onchain.get("last_whale_count", "—")
    taker_str = f"{taker:.1%}" if isinstance(taker, float) else str(taker)
    fr_str = f"{funding:+.5f}%" if isinstance(funding, (int, float)) else "—"
    items = [
        f'<div class="metric-item"><span class="label">⛽ Gas</span>'
        f'<span class="value">{gas}</span></div>',
        f'<div class="metric-item"><span class="label">📊 吃单买</span>'
        f'<span class="value">{taker_str}</span></div>',
        f'<div class="metric-item"><span class="label">💰 资金费</span>'
        f'<span class="value">{fr_str}</span></div>',
        f'<div class="metric-item"><span class="label">🐋 巨鲸</span>'
        f'<span class="value">{whale}</span></div>',
    ]
    return "\n".join(items)


def _build_agent4_detail_html(a4: dict) -> str:
    """Build expandable detail HTML for Agent 4 (Review & Improve)."""
    parts = []

    parts.append('<div class="detail-section-title">📊 复盘统计</div>')
    parts.append(
        f'<div class="detail-row"><span class="label">复盘次数</span>'
        f'<span class="value">{_fmt(a4.get("total_reviews", 0))}</span></div>'
    )
    parts.append(
        f'<div class="detail-row"><span class="label">参数调整</span>'
        f'<span class="value">{_fmt(a4.get("total_adjustments", 0))}</span></div>'
    )
    parts.append(
        f'<div class="detail-row"><span class="label">调整错误</span>'
        f'<span class="value">{_fmt(a4.get("total_adjustment_errors", 0))}</span></div>'
    )
    parts.append(
        f'<div class="detail-row"><span class="label">交易计数</span>'
        f'<span class="value">{_fmt(a4.get("trade_count", 0))}</span></div>'
    )

    # 上次复盘信息
    last_summary = a4.get("last_review_summary", "")
    last_regime = a4.get("last_review_market_regime", "")
    last_time = a4.get("last_review_time", "")[:19] if a4.get("last_review_time") else "—"
    if last_summary:
        parts.append('<div class="detail-sep"></div>')
        parts.append('<div class="detail-section-title">🕐 上次复盘</div>')
        parts.append(
            f'<div class="detail-row"><span class="label">时间</span>'
            f'<span class="value">{last_time}</span></div>'
        )
        parts.append(
            f'<div class="detail-row"><span class="label">市场判断</span>'
            f'<span class="value">{last_regime}</span></div>'
        )
        parts.append(
            f'<div class="detail-row" style="font-size:0.68rem;">'
            f'<span class="label">摘要</span>'
            f'<span class="value" style="text-align:right;max-width:180px;">{last_summary[:80]}</span></div>'
        )

    # 复盘历史
    history = a4.get("review_history", [])
    if history:
        parts.append('<div class="detail-sep"></div>')
        parts.append(f'<div class="detail-section-title">📋 最近复盘记录 ({len(history)} 条)</div>')
        for r in reversed(history[-5:]):
            ts = r.get("timestamp", "")[5:19] if r.get("timestamp") else ""
            summary = r.get("summary", "")[:60]
            adj_count = r.get("adjustments_applied", 0)
            parts.append(
                f'<div class="detail-row" style="font-size:0.68rem;">'
                f'<span class="label">{ts}</span>'
                f'<span class="value">{summary} (调整{adj_count}参数)</span></div>'
            )

    if not parts:
        parts.append('<div class="detail-row" style="color:var(--text-muted);">⏳ 等待首次复盘触发…</div>')

    return "\n".join(parts)


def render_agent_cards(status_data: dict, *, show_recent: bool = True):
    """Render visual agent activity cards.

    Parameters
    ----------
    status_data : dict
        Parsed from agent_status.json — must have agent1/agent2/agent3 keys.
    show_recent : bool
        Whether to also show recent trades from SQLite below the cards.
    """
    a1 = status_data.get("agent1", {})
    a2 = status_data.get("agent2", {})
    a3 = status_data.get("agent3", {})

    # ── Indicator tags ──
    indicators = a1.get("latest_indicators", {}) or {}
    a1_tags = _build_indicator_tags(indicators)

    # ── Onchain ──
    onchain = a2.get("onchain", {}) or {}
    a2_onchain = _build_onchain_html(onchain)

    # ── Position ──
    position = a3.get("position", {}) or {}
    pos_side = position.get("side", "none")
    pos_size = position.get("size", 0)
    pos_price = position.get("entry_price", 0)
    if pos_side != "none" and pos_size > 0:
        arrow = "🟢" if pos_side == "long" else "🔴"
        pos_display = f"{arrow} {pos_side.upper()} {pos_size:.4f} @ ${pos_price:,.2f}"
    else:
        pos_display = "⬜ 无持仓"

    ds = a3.get("deepseek_stats", {}) or {}
    ds_calls = _fmt(ds.get("total_calls", 0))
    ds_avg = ds.get("avg_duration_ms", 0)

    # ── Recent decisions from SQLite ──
    agent3_decisions_html = _get_recent_decisions_html(5)

    # ── Status bar ──
    running_count = sum(1 for x in [a1, a2, a3] if x.get("running"))
    total_count = 3
    if running_count == total_count:
        status_dot_color = "#22c55e"
        status_label = "全部运行"
    elif running_count > 0:
        status_dot_color = "#f59e0b"
        status_label = f"部分运行 ({running_count}/{total_count})"
    else:
        status_dot_color = "#ef4444"
        status_label = "全部停止"

    # ── Activity summary line ──
    a1_act = (a1.get("current_activity") or "") if a1 else ""
    a2_act = (a2.get("current_activity") or "") if a2 else ""
    a3_act = (a3.get("current_activity") or "") if a3 else ""
    a4_status = status_data.get("agent4_reviewer", {})
    a4_act = (a4_status.get("current_activity") or "") if a4_status else ""

    # ── Render ──
    _ag_html = f"""<style>
    .agent-grid {{
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 0.75rem;
        margin: 0.5rem 0;
    }}
    .agent-card {{
        background: var(--bg-card, #ffffff);
        border: 1px solid var(--border, #e2e8f0);
        border-radius: 14px;
        padding: 0.75rem 0.85rem;
        transition: box-shadow 0.2s, border-color 0.2s;
    }}
    .agent-card:hover {{
        box-shadow: 0 4px 16px rgba(0,0,0,0.08);
    }}
    .agent-card.running {{
        border-color: var(--green-border, #bbf7d0);
    }}
    .agent-header {{
        display: flex;
        align-items: center;
        gap: 0.4rem;
        margin-bottom: 0.25rem;
    }}
    .status-dot {{
        width: 8px; height: 8px;
        border-radius: 50%;
        display: inline-block;
        flex-shrink: 0;
        transition: background 0.3s;
    }}
    .status-dot.running {{
        background: #22c55e;
        box-shadow: 0 0 8px rgba(34,197,94,0.6);
        animation: pulse-dot 2s infinite;
    }}
    .status-dot.stopped {{
        background: var(--text-muted, #94a3b8);
    }}
    @keyframes pulse-dot {{
        0%, 100% {{ opacity: 1; }}
        50% {{ opacity: 0.4; }}
    }}
    .agent-icon {{ font-size: 1rem; }}
    .agent-name {{ font-weight: 700; font-size: 0.82rem; color: var(--text-primary, #0f172a); }}
    .uptime {{ margin-left: auto; font-size: 0.65rem; color: var(--text-muted, #94a3b8); font-family: monospace; }}

    /* ── Hero activity line ── */
    .agent-activity {{
        background: var(--bg-card-hover, #f8fafc);
        border-radius: 10px;
        padding: 0.45rem 0.55rem;
        margin: 0.35rem 0 0.25rem;
        min-height: 2rem;
        display: flex;
        flex-direction: column;
        gap: 0.1rem;
        border-left: 3px solid var(--border, #e2e8f0);
        transition: border-color 0.3s;
    }}
    .agent-activity.active {{
        border-left-color: #22c55e;
    }}
    .agent-activity.idle {{
        border-left-color: #94a3b8;
    }}
    .agent-activity .act-text {{
        font-size: 0.8rem;
        font-weight: 600;
        color: #0f172a;
        line-height: 1.3;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .agent-activity .act-text.highlight {{
        color: #059669;
    }}
    .agent-activity .act-time {{
        font-size: 0.62rem;
        color: var(--text-muted, #94a3b8);
        font-family: monospace;
    }}

    .agent-metrics {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.1rem 0.5rem;
        margin-top: 0.3rem;
    }}
    .metric-item {{
        display: flex;
        justify-content: space-between;
        font-size: 0.75rem;
        padding: 0.08rem 0;
    }}
    .metric-item .label {{ color: var(--text-secondary, #64748b); }}
    .metric-item .value {{ font-weight: 600; color: #0f172a; font-family: monospace; font-size: 0.78rem; }}
    .tag-row {{ margin-top: 0.3rem; padding-top: 0.25rem; border-top: 1px solid var(--border-light, #f1f5f9); }}
    .tag {{
        font-size: 0.65rem;
        padding: 0.1rem 0.35rem;
        border-radius: 4px;
        background: var(--bg-input, #f1f5f9);
        color: var(--text-secondary, #475569);
        font-family: monospace;
        white-space: nowrap;
        line-height: 1.5;
    }}
    .tag.bullish {{ background: #dcfce7; color: #166534; }}
    .tag.bearish {{ background: #fee2e2; color: #991b1b; }}
    .tag.neutral {{ background: var(--bg-input, #f1f5f9); color: var(--text-secondary, #475569); }}
    .agent-footer {{
        font-size: 0.68rem;
        color: var(--text-muted, #94a3b8);
        margin-top: 0.35rem;
        padding-top: 0.3rem;
        border-top: 1px solid var(--border-light, #f1f5f9);
        display: flex;
        justify-content: space-between;
    }}
    /* ── Expandable detail sections — now in native st.expander ── */
    .detail-section-title {{
        font-weight: 600;
        font-size: 0.72rem;
        color: var(--text-secondary, #64748b);
        margin: 0.35rem 0 0.2rem;
        padding-bottom: 0.1rem;
        border-bottom: 1px solid var(--border-light, #f1f5f9);
    }}
    .detail-row {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.1rem 0;
        font-size: 0.7rem;
        line-height: 1.5;
    }}
    .detail-row .label {{
        color: var(--text-secondary, #64748b);
    }}
    .detail-row .value {{
        color: var(--text-primary, #0f172a);
        font-family: monospace;
        font-size: 0.7rem;
        text-align: right;
    }}
    .detail-sep {{
        height: 1px;
        background: var(--border-light, #f1f5f9);
        margin: 0.3rem 0;
    }}
    /* ── Pipeline flow ── */
    .pipeline-flow {{
        display: flex;
        align-items: center;
        gap: 0.2rem;
        margin: 0.25rem 0;
    }}
    .pipeline-step {{
        flex: 1;
        display: flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.3rem 0.4rem;
        border-radius: 8px;
        background: var(--bg-card-hover, #f8fafc);
        min-width: 0;
    }}
    .pipeline-step .step-dot {{ font-size: 0.85rem; }}
    .pipeline-step .step-content {{
        flex: 1;
        min-width: 0;
    }}
    .pipeline-step .step-label {{
        font-size: 0.62rem;
        color: var(--text-secondary, #64748b);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .pipeline-step .step-value {{
        font-size: 0.72rem;
        font-weight: 600;
        color: var(--text-primary, #0f172a);
        font-family: monospace;
    }}
    .pipeline-arrow {{
        color: var(--text-muted, #94a3b8);
        font-size: 0.9rem;
        flex-shrink: 0;
    }}
    /* ── Per-timeframe progress bars ── */
    .tf-progress {{
        margin: 0.25rem 0;
    }}
    .tf-header {{
        display: flex;
        justify-content: space-between;
        font-size: 0.68rem;
        margin-bottom: 0.15rem;
    }}
    .tf-name {{
        font-weight: 600;
        color: var(--text-primary, #0f172a);
    }}
    .tf-status {{
        color: var(--text-secondary, #64748b);
        font-family: monospace;
        font-size: 0.65rem;
    }}
    .tf-bar-bg {{
        height: 5px;
        border-radius: 4px;
        overflow: hidden;
    }}
    .tf-bar-fill {{
        height: 100%;
        border-radius: 4px;
    }}
    .tf-bar-ready {{
        box-shadow: 0 0 6px rgba(34,197,94,0.4);
    }}
    /* ── Dark mode overrides ── */
    body.dark-mode .agent-card {{ background: #1e293b; border-color: #334155; }}
    body.dark-mode .agent-card.running {{ border-color: #065f46; }}
    body.dark-mode .agent-name {{ color: #f1f5f9; }}
    body.dark-mode .agent-activity {{ background: rgba(255,255,255,0.06); }}
    body.dark-mode .agent-activity .act-text {{ color: #f1f5f9; }}
    body.dark-mode .agent-activity .act-text.highlight {{ color: #34d399; }}
    body.dark-mode .agent-activity .act-time {{ color: #64748b; }}
    body.dark-mode .metric-item .label {{ color: #94a3b8; }}
    body.dark-mode .metric-item .value {{ color: #e2e8f0; }}
    body.dark-mode .tag-row {{ border-top-color: #334155; }}
    body.dark-mode .tag.neutral {{ background: #334155; color: #cbd5e1; }}
    body.dark-mode .tag.bullish {{ background: #064e3b; color: #6ee7b7; }}
    body.dark-mode .tag.bearish {{ background: #7f1d1d; color: #fca5a5; }}
    body.dark-mode .agent-footer {{ color: #64748b; border-top-color: #334155; }}
    </style>
    <div class="agent-grid">
        <!-- ── Agent 1: 技术分析 ── -->
        <div class="agent-card{' running' if a1.get('running') else ''}">
            <div class="agent-header">
                <span class="status-dot {'running' if a1.get('running') else 'stopped'}"></span>
                <span class="agent-icon">📊</span>
                <span class="agent-name">Agent 1 · 技术分析</span>
                <span class="uptime">{'⏱ ' + _uptime(a1.get('start_time')) if a1.get('running') else ''}</span>
            </div>
            <div class="agent-activity {'active' if a1.get('running') and a1_act else 'idle'}">
                <div class="act-text">{a1_act or '⏸ 已停止'}</div>
                <div class="act-time">{'🔄 ' + _activity_age(a1.get('last_activity_time', 0)) if a1.get('running') else ''}</div>
            </div>
            <div class="agent-metrics">
                <div class="metric-item"><span class="label">📡 Ticks</span><span class="value">{_fmt(a1.get('ticks_received'))}</span></div>
                <div class="metric-item"><span class="label">📊 信号</span><span class="value">{_fmt(a1.get('signals_pushed'))}</span></div>
                <div class="metric-item"><span class="label">📐 K线</span><span class="value">{_fmt(a1.get('bars_completed'))}</span></div>
            </div>
            <div class="tag-row">{a1_tags}</div>
        </div>
        <!-- ── Agent 2: 信息收集 ── -->
        <div class="agent-card{' running' if a2.get('running') else ''}">
            <div class="agent-header">
                <span class="status-dot {'running' if a2.get('running') else 'stopped'}"></span>
                <span class="agent-icon">📰</span>
                <span class="agent-name">Agent 2 · 信息收集</span>
                <span class="uptime">{'⏱ ' + _uptime(a2.get('start_time')) if a2.get('running') else ''}</span>
            </div>
            <div class="agent-activity {'active' if a2.get('running') and a2_act else 'idle'}">
                <div class="act-text">{a2_act or '⏸ 已停止'}</div>
                <div class="act-time">{'🔄 ' + _activity_age(a2.get('last_activity_time', 0)) if a2.get('running') else ''}</div>
            </div>
            <div class="agent-metrics">
                <div class="metric-item"><span class="label">📰 新闻推送</span><span class="value">{_fmt(a2.get('news_pushed'))}</span></div>
                <div class="metric-item"><span class="label">⛓️ 链上事件</span><span class="value">{_fmt(a2.get('onchain_events_pushed'))}</span></div>
                <div class="metric-item"><span class="label">🔍 抓取</span><span class="value">{_fmt(a2.get('fetch_count'))}</span></div>
                <div class="metric-item"><span class="label">📖 已阅</span><span class="value">{_fmt(a2.get('news_seen'))}</span></div>
            </div>
            <div class="tag-row">{a2_onchain}</div>
        </div>
        <!-- ── Agent 3: 交易决策 ── -->
        <div class="agent-card{' running' if a3.get('running') else ''}">
            <div class="agent-header">
                <span class="status-dot {'running' if a3.get('running') else 'stopped'}"></span>
                <span class="agent-icon">🧠</span>
                <span class="agent-name">Agent 3 · 交易决策</span>
                <span class="uptime">{'⏱ ' + _uptime(a3.get('start_time')) if a3.get('running') else ''}</span>
            </div>
            <div class="agent-activity {'active' if a3.get('running') and a3_act else 'idle'}">
                <div class="act-text">{a3_act or '⏸ 已停止'}</div>
                <div class="act-time">{'🔄 ' + _activity_age(a3.get('last_activity_time', 0)) if a3.get('running') else ''}</div>
            </div>
            <div class="agent-metrics">
                <div class="metric-item"><span class="label">💰 成交</span><span class="value">{_fmt(a3.get('trades_executed'))}</span></div>
                <div class="metric-item"><span class="label">⏭ 跳过</span><span class="value">{_fmt(a3.get('trades_skipped'))}</span></div>
                <div class="metric-item"><span class="label">📨 事件</span><span class="value">{_fmt(a3.get('events_received_a'))}/{_fmt(a3.get('events_received_b'))}</span></div>
                <div class="metric-item"><span class="label">📦 缓冲</span><span class="value">{a3.get('event_buffer_size', '?')}</span></div>
            </div>
            <div class="tag-row">
                <div class="metric-item"><span class="label">🧠 DeepSeek</span><span class="value">{ds_calls}次{' / Ø ' + f'{ds_avg:.0f}ms' if ds_avg else ''}</span></div>
                <div class="metric-item"><span class="label">📋 持仓</span><span class="value" style="font-size:0.72rem;">{pos_display}</span></div>
            </div>
        </div>
        <!-- ── Agent 4: 复盘改进 ── -->
        <div class="agent-card{' running' if a4_status.get('running') else ''}">
            <div class="agent-header">
                <span class="status-dot {'running' if a4_status.get('running') else 'stopped'}"></span>
                <span class="agent-icon">🔄</span>
                <span class="agent-name">Agent 4 · 复盘改进</span>
                <span class="uptime">{'⏱ ' + _uptime(a4_status.get('start_time')) if a4_status.get('running') else ''}</span>
            </div>
            <div class="agent-activity {'active' if a4_status.get('running') and a4_act else 'idle'}">
                <div class="act-text">{a4_act or '⏸ 已停止'}</div>
                <div class="act-time">{'🔄 ' + _activity_age(a4_status.get('last_activity_time', 0)) if a4_status.get('running') else ''}</div>
            </div>
            <div class="agent-metrics">
                <div class="metric-item"><span class="label">🔄 复盘</span><span class="value">{_fmt(a4_status.get('total_reviews', 0))}</span></div>
                <div class="metric-item"><span class="label">⚙️ 调整</span><span class="value">{_fmt(a4_status.get('total_adjustments', 0))}</span></div>
                <div class="metric-item"><span class="label">📊 交易计数</span><span class="value">{_fmt(a4_status.get('trade_count', 0))}</span></div>
                <div class="metric-item"><span class="label">⏳ 复盘倒计时</span><span class="value">{a4_status.get('next_review_in', '—')} 笔</span></div>
            </div>
            <div class="tag-row">
                <div class="metric-item"><span class="label">⚠️ 错误</span><span class="value">{_fmt(a4_status.get('total_adjustment_errors', 0))}</span></div>
                <div class="metric-item"><span class="label">市场判断</span><span class="value" style="font-size:0.72rem;">{a4_status.get('last_review_market_regime', '—')[:12]}</span></div>
            </div>
        </div>
    </div>
    <script>
    try {{
        var pb = window.parent.document.body;
        if (pb.classList.contains('dark-mode')) document.body.classList.add('dark-mode');
    }} catch(e) {{}}
    </script>
    """
    import streamlit.components.v1 as _stc
    _stc.html(_ag_html, height=480)

    # ── Expandable detail sections (Streamlit native — maintains state across refreshes) ──
    with st.expander("📋 Agent 1 — 技术分析详情"):
        st.markdown(f"{_DETAIL_STYLE}{_build_agent1_detail_html(a1)}", unsafe_allow_html=True)
    with st.expander("📋 Agent 2 — 信息收集详情"):
        st.markdown(f"{_DETAIL_STYLE}{_build_agent2_detail_html(a2)}", unsafe_allow_html=True)
    with st.expander("📋 Agent 3 — 交易决策详情"):
        st.markdown(f"{_DETAIL_STYLE}{_build_agent3_detail_html(a3, agent3_decisions_html)}", unsafe_allow_html=True)
    with st.expander("📋 Agent 4 — 复盘改进详情"):
        st.markdown(f"{_DETAIL_STYLE}{_build_agent4_detail_html(a4_status)}", unsafe_allow_html=True)

    # ── Phase 4 metrics (compact row) ──
    p4_cols = st.columns(4)
    with p4_cols[0]:
        cs = a3.get("last_composite_score", "—")
        cc = a3.get("last_composite_confidence", "—")
        if isinstance(cs, (int, float)):
            cs_dir = "📈" if cs > 0.2 else "📉" if cs < -0.2 else "⚖️"
            st.metric(f"综合 {cs_dir}", f"{cs:+.2f}",
                      f"信心 {cc:.0%}" if isinstance(cc, (int, float)) else "—")
        else:
            st.metric("综合方向", str(cs))
    with p4_cols[1]:
        al = a3.get("last_alignment_score", "—")
        if isinstance(al, (int, float)):
            al_label = "🤝" if al >= 0.7 else "⚠️" if al < 0.4 else "⚪"
            st.metric(f"对齐 {al_label}", f"{al:.2f}")
        else:
            st.metric("信号对齐", str(al))
    with p4_cols[2]:
        pnl = a3.get("last_monthly_pnl", 0)
        if isinstance(pnl, (int, float)):
            st.metric("月度盈亏", f"${pnl:+,.2f}")
        else:
            st.metric("月度盈亏", str(pnl))
    with p4_cols[3]:
        wr = a3.get("last_win_rate", "—")
        md = a3.get("max_drawdown", "—")
        if isinstance(wr, (int, float)):
            st.metric("胜率", f"{wr:.1f}%",
                      f"回撤 {md:.2f}%" if isinstance(md, (int, float)) else None)
        else:
            st.metric("胜率", str(wr))

    # ── Signal alignment text ──
    sa_text = a3.get("signal_alignment", "")
    if sa_text and sa_text != "暂无对齐数据":
        al_score = a3.get("last_alignment_score", 0)
        if isinstance(al_score, (int, float)):
            border = "#059669" if al_score >= 0.7 else "#f59e0b"
            icon = "✅" if al_score >= 0.7 else "ℹ️"
        else:
            border = "#f59e0b"
            icon = "ℹ️"
        st.markdown(
            f'<div style="background:var(--bg-card-hover,#f8fafc);border-radius:8px;padding:0.5rem 0.75rem;'
            f'margin:0.25rem 0;border-left:4px solid {border};'
            f'font-size:0.82rem;color:var(--text-primary,#334155);">'
            f'{icon} 信号对齐: {sa_text}</div>',
            unsafe_allow_html=True,
        )

    # ── Adaptive params ──
    amt = a3.get("adjusted_max_trades")
    adb = a3.get("adjusted_debounce")
    ait = a3.get("adjusted_trade_interval")
    if any(v is not None for v in [amt, adb, ait]):
        st.caption(
            f"⚙️ 自适应参数 — 日交易上限: {amt} / "
            f"信号采集: {adb}s / 交易间隔: {ait}s"
        )

    # ── Recent trades from SQLite (optional) ──
    if show_recent:
        _show_recent_trades()


def _show_recent_trades(limit: int = 5):
    """Show recent trades as a compact table."""
    try:
        if not DB_FILE.exists():
            return
        conn = sqlite3.connect(str(DB_FILE))
        recent = pd.read_sql_query(
            "SELECT timestamp, side, size, price, pnl_close, trade_type "
            "FROM trades ORDER BY id DESC LIMIT ?",
            conn,
            params=(limit,),
        )
        conn.close()
        if recent.empty:
            return
        st.markdown("#### 📋 最近交易活动")
        recent["side"] = recent["side"].map(
            {"buy": "🟢 买入", "sell": "🔴 卖出"}
        ).fillna(recent["side"])
        recent["trade_type"] = recent["trade_type"].map(
            {"open": "开仓", "close": "平仓"}
        ).fillna(recent["trade_type"])
        recent["pnl_close"] = recent["pnl_close"].apply(
            lambda x: f"${x:+,.2f}" if isinstance(x, (int, float)) and x != 0 else "-"
        )
        recent.columns = ["时间", "方向", "数量", "价格", "盈亏", "类型"]
        st.dataframe(recent, use_container_width=True, hide_index=True)
    except Exception:
        pass
