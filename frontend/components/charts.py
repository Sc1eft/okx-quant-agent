"""Plotly chart components for the OKX Quant Agent frontend.
All charts use a unified light theme matching the design system.
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from typing import List, Dict, Optional


# ── Light Theme Chart Defaults ──
CHART_BG = "#ffffff"
PAPER_BG = "#f8fafc"
GRID_COLOR = "#e2e8f0"
FONT_COLOR = "#475569"
TITLE_COLOR = "#0f172a"
GREEN = "#059669"
RED = "#dc2626"
BLUE = "#2563eb"
AMBER = "#d97706"
PURPLE = "#7c3aed"


def _default_layout(title: str = "", theme: str = "light", **kwargs) -> dict:
    """Base layout configuration for all charts."""
    if theme == "dark":
        CHART_BG = "#1e293b"
        PAPER_BG = "#1e293b"
        GRID_COLOR = "#334155"
        FONT_COLOR = "#94a3b8"
        TITLE_COLOR = "#f1f5f9"
    else:
        CHART_BG = "#ffffff"
        PAPER_BG = "#f8fafc"
        GRID_COLOR = "#e2e8f0"
        FONT_COLOR = "#475569"
        TITLE_COLOR = "#0f172a"
    return dict(
        title=dict(text=title, font=dict(size=15, color=TITLE_COLOR), x=0, xanchor="left"),
        plot_bgcolor=CHART_BG,
        paper_bgcolor=PAPER_BG,
        font=dict(color=FONT_COLOR, family="-apple-system, BlinkMacSystemFont, sans-serif"),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#1e293b",
            font=dict(color="white", size=12),
            bordercolor="#334155",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11, color=FONT_COLOR),
            bordercolor=GRID_COLOR,
            borderwidth=0,
        ),
        margin=dict(l=40, r=20, t=50, b=40),
        **kwargs,
    )


def _update_axes(fig: go.Figure, theme: str = "light") -> None:
    """Apply consistent axis styling."""
    if theme == "dark":
        GC = "#334155"
        FC = "#94a3b8"
    else:
        GC = "#e2e8f0"
        FC = "#475569"
    fig.update_xaxes(
        gridcolor=GC,
        zeroline=False,
        showgrid=True,
        linecolor=GC,
        tickfont=dict(size=11, color=FC),
        title_font=dict(size=12, color=FC),
    )
    fig.update_yaxes(
        gridcolor=GC,
        zeroline=False,
        showgrid=True,
        linecolor=GC,
        tickfont=dict(size=11, color=FC),
        title_font=dict(size=12, color=FC),
    )


def equity_curve_chart(
    equity_curve: List[Dict[str, float]],
    benchmark_curve: Optional[List[Dict[str, float]]] = None,
    title: str = "权益曲线",
    theme: str = "light",
) -> go.Figure:
    """Plot equity curve with optional benchmark overlay."""
    fig = go.Figure()

    if equity_curve:
        df = pd.DataFrame(equity_curve)
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["equity"],
            mode="lines",
            name="策略权益",
            line=dict(color=GREEN, width=2.5),
            fill="tozeroy",
            fillcolor="rgba(5, 150, 105, 0.08)",
        ))

    if benchmark_curve:
        bdf = pd.DataFrame(benchmark_curve)
        fig.add_trace(go.Scatter(
            x=bdf["time"], y=bdf["equity"],
            mode="lines",
            name="基准 (Buy & Hold)",
            line=dict(color="#94a3b8", width=1.5, dash="dash"),
        ))

    fig.update_layout(**_default_layout(title=title, theme=theme))
    _update_axes(fig, theme=theme)
    return fig


def drawdown_chart(
    equity_curve: List[Dict[str, float]],
    title: str = "回撤曲线",
    theme: str = "light",
) -> go.Figure:
    """Plot drawdown from peak."""
    if not equity_curve:
        return go.Figure()

    df = pd.DataFrame(equity_curve)
    peak = df["equity"].cummax()
    drawdown = (df["equity"] - peak) / peak * 100

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["time"], y=drawdown,
        mode="lines",
        name="回撤 %",
        fill="tozeroy",
        line=dict(color=RED, width=1.5),
        fillcolor="rgba(220, 38, 38, 0.08)",
    ))

    fig.update_layout(**_default_layout(title=title, theme=theme))
    _update_axes(fig, theme=theme)
    fig.update_yaxes(
        zeroline=True,
        showgrid=True,
    )
    fig.update_xaxes()
    return fig


def signal_price_chart(
    price_data: List[Dict[str, float]],
    signals: List[Dict],
    title: str = "价格与信号",
    theme: str = "light",
) -> go.Figure:
    """Plot price line with BUY/SELL markers."""
    fig = go.Figure()

    if price_data:
        df = pd.DataFrame(price_data)
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["close"],
            mode="lines",
            name="价格",
            line=dict(color=BLUE, width=2),
        ))

    if signals:
        sdf = pd.DataFrame(signals)
        if not sdf.empty:
            buys = sdf[sdf["signal"] == "BUY"]
            sells = sdf[sdf["signal"].isin(["SELL", "EXIT"])]

            if not buys.empty:
                fig.add_trace(go.Scatter(
                    x=buys["time"], y=buys["price"],
                    mode="markers",
                    name="买入信号",
                    marker=dict(color=GREEN, size=11, symbol="triangle-up",
                                line=dict(color="white", width=1.5)),
                ))
            if not sells.empty:
                fig.add_trace(go.Scatter(
                    x=sells["time"], y=sells["price"],
                    mode="markers",
                    name="卖出信号",
                    marker=dict(color=RED, size=11, symbol="triangle-down",
                                line=dict(color="white", width=1.5)),
                ))

    fig.update_layout(**_default_layout(title=title, theme=theme))
    _update_axes(fig, theme=theme)
    return fig


def pnl_distribution_chart(
    trades: List[Dict],
    title: str = "盈亏分布",
    theme: str = "light",
) -> go.Figure:
    """Histogram of trade PnL percentages."""
    if not trades:
        return go.Figure()

    df = pd.DataFrame(trades)
    pnl = df["pnl_pct"]

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=pnl,
        nbinsx=30,
        name="交易盈亏",
        marker=dict(
            color=BLUE,
            line=dict(color="white", width=1),
        ),
    ))

    fig.update_layout(
        **_default_layout(
            title=title,
            theme=theme,
            bargap=0.05,
        )
    )
    _update_axes(fig, theme=theme)
    return fig


def sharpe_drop_chart(
    windows: List[Dict],
    title: str = "Train / Test Sharpe 对比",
    theme: str = "light",
) -> go.Figure:
    """Bar chart comparing train vs test Sharpe per window."""
    if not windows:
        return go.Figure()

    labels = [f"窗口 {i+1}" for i in range(len(windows))]
    train_sharpes = [w.get("train_sharpe", 0) or 0 for w in windows]
    test_sharpes = [w.get("test_sharpe", 0) or 0 for w in windows]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="训练 Sharpe",
        x=labels, y=train_sharpes,
        marker_color=BLUE,
        marker_line=dict(color="white", width=1),
    ))
    fig.add_trace(go.Bar(
        name="测试 Sharpe",
        x=labels, y=test_sharpes,
        marker_color=GREEN,
        marker_line=dict(color="white", width=1),
    ))

    fig.update_layout(
        **_default_layout(title=title, theme=theme, barmode="group"),
    )
    _update_axes(fig, theme=theme)
    return fig


def cumulative_pnl_chart(
    trades: List[Dict],
    title: str = "累计盈亏",
    theme: str = "light",
) -> go.Figure:
    """Cumulative PnL line chart."""
    if not trades:
        return go.Figure()

    df = pd.DataFrame(trades)
    df = df.sort_values("exit_time")
    df["cumulative_pnl"] = df["pnl"].cumsum()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["exit_time"], y=df["cumulative_pnl"],
        mode="lines",
        name="累计盈亏",
        fill="tozeroy",
        line=dict(color=BLUE, width=2),
        fillcolor="rgba(37, 99, 235, 0.08)",
    ))

    fig.update_layout(**_default_layout(title=title, theme=theme))
    _update_axes(fig, theme=theme)
    return fig
