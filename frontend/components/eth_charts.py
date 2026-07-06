"""ETH-specific chart components — candlestick chart and sparkline.

Extracted from 9_EthereumLive.py for reuse across Live and AI Trading pages.
"""
from __future__ import annotations
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

__all__ = [
    "COLORS",
    "TIMEFRAMES",
    "TIMEFRAME_REFRESH_S",
    "TV_INTERVAL_MAP",
    "_build_candlestick_fig",
    "_build_sparkline",
    "_build_tradingview_html",
    "_friendly_tf",
    "_fmt_uptime",
]


# ════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════

ETH_SYMBOL = "ETH-USDT"

# Display name -> internal key
TIMEFRAMES: dict[str, str] = {
    "秒": "1s",
    "1分钟": "1m",
    "2分钟": "2m",
    "15分钟": "15m",
    "1小时": "1h",
    "6小时": "6h",
    "12小时": "12h",
    "1天": "1d",
    "2天": "2d",
    "15天": "15d",
}

# Auto-refresh interval per internal key (seconds)
TIMEFRAME_REFRESH_S: dict[str, int] = {
    "1s": 1, "1m": 3, "2m": 5,
    "15m": 5, "1h": 10, "6h": 30,
    "12h": 60, "1d": 60, "2d": 120,
    "15d": 300,
}

COLORS = {
    "purple": "#627eea",
    "purple_light": "#8b9cf7",
    "green": "#059669",
    "red": "#dc2626",
}

# TradingView 周期代码映射（内部 key → TradingView interval）
TV_INTERVAL_MAP: dict[str, str] = {
    "1s": "1S", "1m": "1", "2m": "2", "3m": "3", "5m": "5",
    "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240",
    "6h": "360", "12h": "720", "1d": "D", "2d": "2D", "3d": "3D", "15d": "15D",
}


def _build_tradingview_html(
    symbol: str = "OKX:ETHUSDT",
    interval: str = "15",
    theme: str = "dark",
    height: int = 500,
) -> str:
    """生成 TradingView 专业图表嵌入 HTML（给 streamlit.components.v1.html 用）"""
    return f"""<!-- TradingView Widget BEGIN -->
<div class="tradingview-widget-container" style="position:relative;">
  <div id="tv_chart" style="height:{height}px;"></div>
  <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
  <script type="text/javascript">
  new TradingView.widget({{
    "container_id": "tv_chart",
    "width": "100%",
    "height": {height},
    "symbol": "{symbol}",
    "interval": "{interval}",
    "timezone": "Asia/Shanghai",
    "theme": "{theme}",
    "style": "1",
    "locale": "zh_CN",
    "toolbar_bg": "#f1f3f6",
    "enable_publishing": false,
    "hide_top_toolbar": false,
    "hide_legend": false,
    "save_image": false,
    "withdateranges": true,
    "studies": [
      "MACD@tv-basicstudies",
      "RSI@tv-basicstudies"
    ],
    "disabled_features": ["header_symbol_search"],
    "enabled_features": ["study_templates"]
  }});
  </script>
</div>
<!-- TradingView Widget END -->"""


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════


def _friendly_tf(tf_key: str) -> str:
    """Internal key -> display label."""
    rev = {v: k for k, v in TIMEFRAMES.items()}
    return rev.get(tf_key, tf_key)


def _fmt_uptime(started_at_str: str | None) -> str:
    if not started_at_str:
        return "-"
    try:
        start = datetime.fromisoformat(started_at_str)
        delta = datetime.now(timezone.utc) - start
        total_sec = int(delta.total_seconds())
        h, r = divmod(total_sec, 3600)
        m, s = divmod(r, 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        elif m > 0:
            return f"{m}m {s}s"
        return f"{s}s"
    except Exception:
        return "-"


# ════════════════════════════════════════════════════════════════
# CHART FUNCTIONS
# ════════════════════════════════════════════════════════════════


def _build_sparkline(ticks: list[dict], height: int = 100, theme: str = "light") -> go.Figure:
    """Mini price chart from tick data."""
    bg_transparent = "rgba(0,0,0,0)"
    grid_color = "rgba(148, 163, 184, 0.2)"

    if not ticks:
        fig = go.Figure()
        fig.update_layout(height=height)
        return fig

    df = pd.DataFrame(ticks)
    df = df.sort_values("ts_ms")
    prices = df["price"].values
    times = pd.to_datetime(df["ts"])

    color = COLORS["green"] if prices[-1] >= prices[0] else COLORS["red"]
    fill_color = "rgba(98, 126, 234, 0.15)"

    annot_bg = "rgba(30, 41, 59, 0.9)" if theme == "dark" else "rgba(255,255,255,0.9)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=times, y=prices,
        mode="lines",
        line=dict(color=color, width=2.5),
        fill="tozeroy",
        fillcolor=fill_color,
        name="ETH",
        hovertemplate="%{x|%H:%M:%S}<br>$%{y:,.2f}<extra></extra>",
    ))
    fig.add_annotation(
        x=times.iloc[-1], y=prices[-1],
        text=f"${prices[-1]:,.2f}",
        showarrow=True, arrowhead=0,
        ax=0, ay=-30,
        font=dict(size=11, color=color),
        bgcolor=annot_bg,
        bordercolor=color, borderwidth=1,
    )

    y_min, y_max = min(prices), max(prices)
    y_pad = max((y_max - y_min) * 0.3, y_min * 0.001)
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=bg_transparent,
        plot_bgcolor=bg_transparent,
        font=dict(color=grid_color),
        xaxis=dict(showgrid=False, visible=False, showticklabels=False),
        yaxis=dict(
            range=[y_min - y_pad, y_max + y_pad],
            showgrid=True, gridcolor=grid_color,
            zeroline=False, tickformat="$,.0f", side="right",
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1e293b", font=dict(color="white", size=11)),
    )
    return fig


def _build_candlestick_fig(
    df: pd.DataFrame,
    ticker_data: dict | None = None,
    tf_key: str = "1h",
    height: int = 400,
    is_seconds: bool = False,
    theme: str = "light",
) -> go.Figure:
    """Interactive candlestick chart with volume subplot & SMA overlays."""
    is_dark = theme == "dark"
    # Theme colors
    if is_dark:
        bg_plot = "#1e293b"
        bg_paper = "#1e293b"
        font_color = "#94a3b8"
        title_color = "#f1f5f9"
        grid_color = "#334155"
    else:
        bg_plot = "#ffffff"
        bg_paper = "#f8fafc"
        font_color = "#475569"
        title_color = "#0f172a"
        grid_color = "#e2e8f0"

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
    )

    # ── Candlestick ──
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        name="ETH-USDT",
        increasing_line_color=COLORS["green"],
        decreasing_line_color=COLORS["red"],
        increasing_fillcolor=COLORS["green"],
        decreasing_fillcolor=COLORS["red"],
        showlegend=False,
    ), row=1, col=1)

    # ── Volume bars ──
    vol_label = "笔数" if is_seconds else "成交量"
    colors = [COLORS["green"] if c >= o else COLORS["red"]
              for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"],
        name=vol_label,
        marker_color=colors, opacity=0.5,
        showlegend=False,
    ), row=2, col=1)

    # ── Real-time price line ──
    if ticker_data and ticker_data.get("last"):
        fig.add_hline(
            y=ticker_data["last"],
            line_dash="dash",
            line_color=COLORS["purple"],
            line_width=1.5,
            annotation_text=f"实时 ${ticker_data['last']:,.2f}",
            annotation_position="right",
            annotation=dict(font=dict(size=11, color=COLORS["purple"])),
            row=1, col=1,
        )

    # ── SMA overlays ──
    if len(df) >= 20:
        sma20 = df["close"].rolling(20).mean()
        fig.add_trace(go.Scatter(
            x=df.index, y=sma20,
            mode="lines", name="SMA 20",
            line=dict(color=COLORS["purple_light"], width=1.5, dash="dot"),
        ), row=1, col=1)
    if len(df) >= 50:
        sma50 = df["close"].rolling(50).mean()
        fig.add_trace(go.Scatter(
            x=df.index, y=sma50,
            mode="lines", name="SMA 50",
            line=dict(color="#f59e0b", width=1.5, dash="dot"),
        ), row=1, col=1)

    # ── Layout ──
    fig.update_layout(
        title=dict(
            text=f"🟢 ETH-USDT — {_friendly_tf(tf_key)} 图",
            font=dict(size=15, color=title_color),
            x=0, xanchor="left",
        ),
        plot_bgcolor=bg_plot,
        paper_bgcolor=bg_paper,
        font=dict(color=font_color, family="-apple-system, BlinkMacSystemFont, sans-serif"),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#1e293b", font=dict(color="white", size=12),
            bordercolor="#334155",
        ),
        xaxis_rangeslider_visible=False,
        height=height,
        margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(
            orientation="h", yanchor="bottom",
            y=1.02, xanchor="right", x=1,
            font=dict(size=11),
        ),
    )

    fig.update_xaxes(
        gridcolor=grid_color, zeroline=False,
        showgrid=True, linecolor=grid_color,
        row=1, col=1,
    )
    fig.update_xaxes(
        gridcolor=grid_color, zeroline=False,
        showgrid=True, linecolor=grid_color,
        row=2, col=1,
    )
    fig.update_yaxes(
        gridcolor=grid_color, zeroline=False,
        showgrid=True, linecolor=grid_color,
        row=1, col=1,
    )
    fig.update_yaxes(
        gridcolor=grid_color, zeroline=False,
        showgrid=False, linecolor=grid_color,
        title_text=vol_label,
        row=2, col=1,
    )

    return fig
