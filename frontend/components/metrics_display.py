"""KPI metric card components for the OKX Quant Agent frontend.

Uses the design system from style.css with accent-bordered premium cards.
"""

import streamlit as st
from typing import Dict, Any, Optional, Tuple


# ── Color palette (use CSS variable names for theme support) ──
GREEN = "var(--green)"
RED = "var(--red)"
BLUE = "var(--primary)"
AMBER = "var(--amber)"
PURPLE = "var(--purple)"
GRAY = "var(--text-muted)"
DARK = "var(--text-primary)"

# ── Friendly label mapping ──
_LABELS: Dict[str, str] = {
    "total_return_pct": "总收益率",
    "annual_return_pct": "年化收益率",
    "max_drawdown_pct": "最大回撤",
    "sharpe": "Sharpe 比率",
    "calmar": "Calmar 比率",
    "win_rate": "胜率",
    "profit_factor": "盈亏比",
    "total_trades": "总交易次数",
    "avg_hold_hours": "平均持仓时间",
    "benchmark_return_pct": "基准收益率",
    "final_equity": "最终权益",
    "avg_win": "平均盈利",
    "avg_loss": "平均亏损",
    "outperform_benchmark": "跑赢基准",
    "price": "价格",
    "equity": "总权益",
    "balance": "现金余额",
    "unrealized_pnl_pct": "未实现盈亏",
    "position_btc": "持仓 (BTC)",
    "signal": "信号",
    "total_pnl": "总盈亏",
    "avg_pnl_pct": "平均盈亏",
    "avg_win_pct": "平均盈利",
    "avg_loss_pct": "平均亏损",
    "mode": "运行模式",
    "symbol": "交易对",
}


def friendly_label(key: str) -> str:
    """Convert snake_case key to friendly Chinese label."""
    return _LABELS.get(key, key.replace("_", " ").title())


def _infer_card_type(label: str, value: Any) -> str:
    """Infer card accent color class from label and value."""
    if value is None:
        return "gray"

    pct_keys = {"total_return_pct", "annual_return_pct", "max_drawdown_pct",
                "win_rate", "benchmark_return_pct", "unrealized_pnl_pct",
                "avg_pnl_pct", "avg_win_pct", "avg_loss_pct"}
    money_keys = {"final_equity", "avg_win", "avg_loss", "total_pnl", "equity", "balance"}
    ratio_keys = {"sharpe", "calmar", "profit_factor"}
    pos_keys = {"position_btc"}

    if label in pct_keys:
        if isinstance(value, (int, float)):
            return "green" if value > 0 else ("red" if value < 0 else "gray")
        return "gray"
    if label in ratio_keys:
        if isinstance(value, (int, float)):
            return "green" if value > 1.5 else ("amber" if value > 0.5 else "red")
        return "gray"
    if label in money_keys:
        if isinstance(value, (int, float)):
            return "green" if value > 0 else ("red" if value < 0 else "gray")
        return "gray"
    if label in pos_keys:
        return "blue"
    if label == "signal":
        return "purple"
    if label == "total_trades":
        return "blue"
    return "gray"


def _format_value(label: str, value: Any) -> Tuple[str, str]:
    """Format metric value and return (display_string, css_color_class)."""
    if value is None:
        return "N/A", GRAY

    if isinstance(value, bool):
        return ("✅ 是" if value else "❌ 否"), (GREEN if value else RED)

    if isinstance(value, str):
        return value, DARK

    pct_keys = {"total_return_pct", "annual_return_pct", "max_drawdown_pct",
                "win_rate", "benchmark_return_pct", "unrealized_pnl_pct",
                "avg_pnl_pct", "avg_win_pct", "avg_loss_pct"}
    ratio_keys = {"sharpe", "calmar", "profit_factor"}
    money_keys = {"final_equity", "avg_win", "avg_loss", "total_pnl", "equity", "balance"}

    if label in pct_keys:
        color = GREEN if value > 0 else (RED if value < 0 else DARK)
        return f"{value:+.2f}%", color

    if label in ratio_keys:
        color = GREEN if value > 1.5 else (AMBER if value > 0.5 else RED)
        return f"{value:.2f}", color

    if label in money_keys:
        color = GREEN if value > 0 else (RED if value < 0 else DARK)
        return f"${value:,.2f}", color

    if label == "total_trades":
        return str(int(value)), BLUE

    if label == "avg_hold_hours":
        return f"{value:.1f}h", GRAY

    if label == "position_btc":
        return f"{value:.6f}", BLUE

    if isinstance(value, float):
        return f"{value:.2f}", DARK

    return str(value), DARK


def render_metric_card(
    label: str,
    value: Any,
    suffix: str = "",
    key: Optional[str] = None,
) -> None:
    """Render a single premium metric card with accent border.

    Uses st.markdown with inline styles for the card appearance.
    The card has a 4px accent border on the left, colored by metric type.
    """
    display_value, color = _format_value(label, value)
    friendly = friendly_label(label)
    card_type = _infer_card_type(label, value)

    accent_color = {
        "green": GREEN, "red": RED, "blue": BLUE,
        "amber": AMBER, "purple": PURPLE, "gray": GRAY,
    }.get(card_type, GRAY)

    html = f"""
    <div class="metric-card metric-card--{card_type}" style="
        border-left: 4px solid {accent_color};
        margin-bottom: 0.5rem;
    ">
        <div class="label">{friendly}</div>
        <div class="value" style="color: {color};">{display_value}</div>
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


def render_metric_grid(metrics: Dict[str, Any], cols: int = 4) -> None:
    """Render a grid of metric cards from a metrics dict.

    Each key becomes a label, each value becomes the displayed metric.
    """
    items = list(metrics.items())
    for i in range(0, len(items), cols):
        row = items[i : i + cols]
        cols_ui = st.columns(len(row))
        for col, (key, value) in zip(cols_ui, row):
            with col:
                render_metric_card(key, value)


# ── Backward compatibility aliases ──
_render_metric_card = render_metric_card
_friendly_label = friendly_label


def strategy_metric_row(results: Dict[str, Dict]) -> None:
    """Render a comparison row for multiple strategies' results."""
    if not results:
        return

    cols = st.columns(len(results))
    for col, (name, result) in zip(cols, results.items()):
        with col:
            metrics = result.get("metrics", {})
            st.markdown(f"**{name}**")
            tr = metrics.get("total_return_pct", 0)
            tr_color = GREEN if tr > 0 else RED
            dd = metrics.get("max_drawdown_pct", 0)
            st.markdown(
                f"总收益: <span style='color:{tr_color}'>{tr:+.2f}%</span>",
                unsafe_allow_html=True,
            )
            st.markdown(f"Sharpe: {metrics.get('sharpe', 0):.2f}")
            st.markdown(
                f"回撤: <span style='color:{RED}'>{dd:.2f}%</span>",
                unsafe_allow_html=True,
            )
            st.markdown(f"交易: {metrics.get('total_trades', 0)}")
            st.markdown("---")
