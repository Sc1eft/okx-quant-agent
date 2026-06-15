"""Ethereum Live Data — real-time ETH-USDT market data from OKX.

Always-on candlestick chart (like stock K-line) with expanded timeframe options
from second-level (via heartbeat WebSocket collector) through 15-day candles.

No "start monitoring" button needed — data loads automatically.
"""

from __future__ import annotations
import streamlit.components.v1 as _comps
from frontend.components.metrics_display import render_metric_card
from frontend.utils.data_provider import fetch_klines_with_agg, fetch_ticker
from frontend.utils.session_state import get_config

import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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

DEFAULT_TF_LABEL = "15分钟"

COLORS = {
    "purple": "#627eea",
    "purple_light": "#8b9cf7",
    "green": "#059669",
    "red": "#dc2626",
}

# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════


def _ss(key: str, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _fmt_change(c: float | None) -> str:
    if c is None:
        return ""
    return f"{c:+.2f}%"


def _friendly_tf(tf_key: str) -> str:
    """Internal key -> display label."""
    rev = {v: k for k, v in TIMEFRAMES.items()}
    return rev.get(tf_key, tf_key)


def _build_sparkline(ticks: list[dict], height: int = 100) -> go.Figure:
    """Mini price chart from tick data."""
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
        bgcolor="rgba(255,255,255,0.9)",
        bordercolor=color, borderwidth=1,
    )

    y_min, y_max = min(prices), max(prices)
    y_pad = max((y_max - y_min) * 0.3, y_min * 0.001)
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False, visible=False, showticklabels=False),
        yaxis=dict(
            range=[y_min - y_pad, y_max + y_pad],
            showgrid=True, gridcolor="rgba(148, 163, 184, 0.2)",
            zeroline=False, tickformat="$,.0f", side="right",
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1e293b", font=dict(color="white", size=11)),
    )
    return fig


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


def _build_candlestick_fig(
    df: pd.DataFrame,
    ticker_data: dict | None = None,
    tf_key: str = "1h",
    height: int = 520,
    is_seconds: bool = False,
) -> go.Figure:
    """Interactive candlestick chart with volume subplot & SMA overlays."""
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
            font=dict(size=15, color="#0f172a"),
            x=0, xanchor="left",
        ),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#f8fafc",
        font=dict(color="#475569", family="-apple-system, BlinkMacSystemFont, sans-serif"),
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
        gridcolor="#e2e8f0", zeroline=False,
        showgrid=True, linecolor="#e2e8f0",
        row=1, col=1,
    )
    fig.update_xaxes(
        gridcolor="#e2e8f0", zeroline=False,
        showgrid=True, linecolor="#e2e8f0",
        row=2, col=1,
    )
    fig.update_yaxes(
        gridcolor="#e2e8f0", zeroline=False,
        showgrid=True, linecolor="#e2e8f0",
        row=1, col=1,
    )
    fig.update_yaxes(
        gridcolor="#e2e8f0", zeroline=False,
        showgrid=False, linecolor="#e2e8f0",
        title_text=vol_label,
        row=2, col=1,
    )

    return fig

# ════════════════════════════════════════════════════════════════
# AI 多空分析 — 调用 DeepSeek 分析市场数据
# ════════════════════════════════════════════════════════════════


def _summarize_klines(df: pd.DataFrame | None, label: str) -> str:
    """将 K 线 DataFrame 压缩为一段文字摘要"""
    if df is None or df.empty:
        return f"{label}: 无数据"
    first_c = float(df["close"].iloc[0])
    last_c = float(df["close"].iloc[-1])
    high = float(df["high"].max())
    low = float(df["low"].min())
    vol_sum = float(df["volume"].sum())
    vol_avg = float(df["volume"].mean())
    change = (last_c - first_c) / first_c * 100 if first_c else 0
    return (
        f"{label}: {len(df)}根K线, 区间{low:.2f}~{high:.2f}, "
        f"最新收盘{last_c:.2f}, 涨跌{change:+.2f}%, "
        f"总成交量{vol_sum:.0f}, 均量{vol_avg:.0f}"
    )


def _ticker_summary(symbol: str, tk: dict | None) -> str:
    """将 ticker 数据压缩为一行摘要"""
    if not tk:
        return f"{symbol}: 无数据"
    price = tk.get("last", 0)
    chg = tk.get("change_24h", 0)
    vol = tk.get("volume_24h", 0)
    high = tk.get("high_24h", 0)
    low = tk.get("low_24h", 0)
    return (
        f"{symbol} ${price:,.2f} "
        f"24h涨跌{chg:+.2f}% "
        f"24h区间${low:,.2f}~${high:,.2f} "
        f"24h成交量{vol:,.0f}"
    )


_NEWS_FEEDS = [
    ("PANews", "https://www.panewslab.com/zh/rss.aspx"),
    ("Foresight News", "https://foresightnews.pro/rss/news"),
]

_READER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_crypto_news(max_items: int = 5) -> list[dict]:
    """从公开 RSS 源获取近期加密货币新闻及政策。

    尝试多个中文加密新闻 RSS 源，无需 API Key。
    全部失败时返回空列表——调用方自行降级。
    """
    import xml.etree.ElementTree as _ET
    import requests as _req

    pool: list[dict] = []
    seen: set[str] = set()

    for name, url in _NEWS_FEEDS:
        try:
            resp = _req.get(url, timeout=8, headers={"User-Agent": _READER_AGENT})
            if resp.status_code != 200:
                continue
            root = _ET.fromstring(resp.content)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                if title and title not in seen:
                    seen.add(title)
                    pool.append({"title": title, "source": name})
                if len(pool) >= max_items:
                    break
        except Exception:
            continue
        if len(pool) >= max_items:
            break

    return pool[:max_items]


def _build_ai_analysis_prompt(
    ticker: dict | None,
    klines_15m: pd.DataFrame | None,
    klines_1h: pd.DataFrame | None,
    klines_1d: pd.DataFrame | None,
    btc_ticker: dict | None,
    sol_ticker: dict | None,
    doge_ticker: dict | None,
    news: list[dict] | None = None,
) -> str:
    """将市场数据组装成给 AI 的提示词"""
    lines = ["## ETH-USDT 当前市场数据\n"]
    if ticker:
        lines.append("### 实时行情")
        lines.append(f"- 最新价: ${ticker.get('last', 0):,.2f}")
        lines.append(f"- 24h涨跌: {ticker.get('change_24h', 0):+.2f}%")
        lines.append(f"- 24h成交量: {ticker.get('volume_24h', 0):,.0f} ETH")
        lines.append(f"- 买一: ${ticker.get('bid', 0):,.2f} / 卖一: ${ticker.get('ask', 0):,.2f}")
        lines.append(f"- 24h最高: ${ticker.get('high_24h', 0):,.2f}")
        lines.append(f"- 24h最低: ${ticker.get('low_24h', 0):,.2f}")
        lines.append("")
    lines.append("### K 线数据")
    lines.append(_summarize_klines(klines_15m, "短期(15分钟)"))
    lines.append(_summarize_klines(klines_1h, "中期(1小时)"))
    lines.append(_summarize_klines(klines_1d, "长期(日线)"))
    lines.append("")
    lines.append("### 关联币种行情")
    lines.append(_ticker_summary("BTC", btc_ticker))
    lines.append(_ticker_summary("SOL", sol_ticker))
    lines.append(_ticker_summary("DOGE", doge_ticker))
    lines.append("")

    # ── 新闻与政策基本面 ──
    if news:
        lines.append("### 近期新闻与政策")
        for item in news:
            lines.append(f"- [{item['source']}] {item['title']}")
        lines.append("")

    lines.append("---")
    lines.append(
        "请基于以上技术面数据 + 新闻基本面，给出 ETH 的综合多空分析。"
        "注意评估新闻事件对 ETH 价格的潜在多空影响。"
    )
    return "\n".join(lines)


_AI_SYSTEM_PROMPT = """你是专业的加密货币交易分析师，综合技术面 + 基本面给出决策建议。

技术面依据：K线形态、量价关系、多周期趋势、关联币种联动。
基本面依据：最新新闻事件、政策动向、行业动态、市场叙事。

请以JSON格式返回，不要包含其他文字：

{
  "direction": "long" | "short" | "neutral",
  "confidence": 0-100,
  "summary": "一句话综合判断（中文）",
  "key_evidence": ["依据1（中文）", "依据2（中文）", "依据3（中文）", "依据4（中文）"],
  "risk_warnings": ["风险1（中文）", "风险2（中文）"],
  "technical_analysis": "技术面简要分析（中文，一两句话）",
  "market_sentiment": "市场情绪判断（中文，一两句话）",
  "fundamental_analysis": "根据新闻事件和政策做出的基本面判断（中文，一两句话），如果无新闻数据返回空字符串"
}

注意：
- direction 只能为 "long"、"short" 或 "neutral"
- confidence 为0-100的整数
- key_evidence 至少3条，至多5条，综合技术面和基本面各维度
- 所有文本字段使用中文
- 只返回JSON，不要包含其他文字"""


def _call_ai_analysis(
    ticker: dict | None,
    klines_15m: pd.DataFrame | None,
    klines_1h: pd.DataFrame | None,
    klines_1d: pd.DataFrame | None,
    btc_ticker: dict | None,
    sol_ticker: dict | None,
    doge_ticker: dict | None,
    cfg,
    news: list[dict] | None = None,
) -> dict:
    """调用 DeepSeek API 进行多空分析，失败时返回降级结果"""
    prompt = _build_ai_analysis_prompt(
        ticker, klines_15m, klines_1h, klines_1d,
        btc_ticker, sol_ticker, doge_ticker,
        news=news,
    )
    try:
        from openai import OpenAI
        import json, re
        client = OpenAI(
            api_key=cfg.agent.api_key or "sk-placeholder",
            base_url=cfg.agent.base_url,
        )
        resp = client.chat.completions.create(
            model=cfg.agent.model,
            messages=[
                {"role": "system", "content": _AI_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        content = resp.choices[0].message.content or ""
        # 尝试提取 JSON
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        result = json.loads(content)
        # 验证必要字段
        if "direction" not in result:
            result["direction"] = "neutral"
        if "confidence" not in result:
            result["confidence"] = 50
        for field in ("summary", "key_evidence", "risk_warnings", "technical_analysis", "market_sentiment", "fundamental_analysis"):
            result.setdefault(field, "" if field in ("summary", "technical_analysis", "market_sentiment", "fundamental_analysis") else [])
        return result
    except Exception as e:
        return {
            "direction": "neutral",
            "confidence": 0,
            "summary": f"AI 分析暂时不可用",
            "key_evidence": [f"API 调用失败: {e}"],
            "risk_warnings": ["AI 服务异常，请稍后重试"],
            "technical_analysis": "",
            "market_sentiment": "",
            "fundamental_analysis": "",
        }


# ── AI 追问对话 ──

_AI_CHAT_SYSTEM_PROMPT = """你是专业的加密货币交易分析师，正在回答用户基于市场分析的追问。

根据已有市场数据和分析结论回答用户问题：
- 基于数据说话，不臆测
- 简洁直接，有依据
- 无法从现有数据判断时如实说明
- 所有回复使用中文"""


def _call_ai_chat(
    question: str,
    context: dict | None,
    chat_history: list[dict],
    cfg,
) -> str:
    """调用 DeepSeek 回答用户对市场分析的追问"""
    from openai import OpenAI
    import json

    # ── 拼接上下文 ──
    ctx_parts = ["## 当前市场分析上下文\n"]
    if context:
        analysis = context.get("analysis_result") or {}
        if analysis:
            ctx_parts.append(
                f"### 此前分析结果\n"
                f"- 方向: {analysis.get('direction', '中性')}\n"
                f"- 信心指数: {analysis.get('confidence', 0)}%\n"
                f"- 综合判断: {analysis.get('summary', '')}\n"
                f"- 技术面: {analysis.get('technical_analysis', '')}\n"
                f"- 市场情绪: {analysis.get('market_sentiment', '')}\n"
                f"- 基本面: {analysis.get('fundamental_analysis', '')}\n"
            )
        market = context.get("market_summary", "")
        if market:
            ctx_parts.append(f"{market}\n")
        news = context.get("news", [])
        if news:
            ctx_parts.append("### 参考新闻\n" + "\n".join(
                f"- [{n['source']}] {n['title']}" for n in news
            ))

    context_text = "\n".join(ctx_parts)

    messages = [
        {"role": "system", "content": _AI_CHAT_SYSTEM_PROMPT},
        {"role": "system", "content": f"以下是当前分析上下文，请基于此回答：\n\n{context_text}"},
    ]
    # 带上最近 6 轮对话历史
    for msg in chat_history[-12:]:
        messages.append(msg)
    messages.append({"role": "user", "content": question})

    try:
        client = OpenAI(
            api_key=cfg.agent.api_key or "sk-placeholder",
            base_url=cfg.agent.base_url,
        )
        resp = client.chat.completions.create(
            model=cfg.agent.model,
            messages=messages,
            temperature=0.5,
            max_tokens=1000,
        )
        return resp.choices[0].message.content or "抱歉，我暂时无法回答这个问题。"
    except Exception as e:
        return f"❌ AI 响应失败: {e}"


# ════════════════════════════════════════════════════════════════
# PAGE
# ════════════════════════════════════════════════════════════════


st.markdown("""
    <div class="page-header">
        <h1>🟢 以太坊实时数据</h1>
        <p>ETH-USDT 实时行情 · K 线图表 · 秒级~15天 多周期 · 自动刷新</p>
    </div>
""", unsafe_allow_html=True)

st.markdown("""
<style>
/* ── 隐藏自动刷新的加载蒙版 ── */
div[data-testid="stStatusWidget"],
div[data-testid="stSpinner"] {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}
</style>
""", unsafe_allow_html=True)

# 注入 JS 持续移除加载蒙版（防 fragment 刷新时动态创建）
_comps.html("""
<script>
(function() {
    function hide() {
        parent.document.querySelectorAll(
            '[data-testid="stStatusWidget"],[data-testid="stSpinner"]'
        ).forEach(function(el) {
            el.style.setProperty('display', 'none', 'important');
        });
    }
    hide();
    setInterval(hide, 200);
})();
</script>
""", height=0)

cfg = get_config()

# ── Session state init ──
_ss("eth_ticker", None)
_ss("eth_data", None)
_ss("eth_timeframe", DEFAULT_TF_LABEL)
_ss("eth_data_count", 120)
_ss("eth_last_refresh", None)
_ss("eth_auto_refresh", True)

# ════════════════════════════════════════════════════════════════
# TOOLBAR
# ════════════════════════════════════════════════════════════════

st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-title">⚙ 控制面板</div>', unsafe_allow_html=True)

tf_labels = list(TIMEFRAMES.keys())
cur_label = st.session_state.eth_timeframe
default_idx = tf_labels.index(
    cur_label) if cur_label in tf_labels else tf_labels.index(DEFAULT_TF_LABEL)

ctrl_cols = st.columns([1.8, 1.5, 0.8, 1.0])

with ctrl_cols[0]:
    selected_tf = st.selectbox(
        "K 线周期",
        tf_labels,
        index=default_idx,
        key="eth_tf_sel")

with ctrl_cols[1]:
    dc = st.slider(
        "K 线数量",
        20,
        300,
        st.session_state.eth_data_count,
        step=10,
        key="eth_dc_slider")

with ctrl_cols[2]:
    st.caption("")
    if st.button("🔄", use_container_width=True):
        st.session_state.eth_data = None
        st.rerun()

with ctrl_cols[3]:
    auto = st.checkbox(
        "自动刷新",
        value=st.session_state.eth_auto_refresh,
        key="eth_auto_refresh")

st.markdown('</div>', unsafe_allow_html=True)

# ── Detect widget changes → reset data ──
tf_changed = selected_tf != st.session_state.eth_timeframe
count_changed = dc != st.session_state.eth_data_count
if tf_changed or count_changed:
    st.session_state.eth_timeframe = selected_tf
    st.session_state.eth_data_count = dc
    st.session_state.eth_data = None
    st.rerun()

# Resolve keys
tf_label = st.session_state.eth_timeframe
tf_key = TIMEFRAMES.get(tf_label, "1d")
data_count = st.session_state.eth_data_count
is_seconds_mode = tf_key == "1s"

# ════════════════════════════════════════════════════════════════
# TRADINGVIEW CHART (OKX mode only — outside fragment, no rerun)
# ════════════════════════════════════════════════════════════════

if not is_seconds_mode:
    import streamlit.components.v1 as components

    _TV_INTERVALS = {
        "1m": "1", "2m": "2", "15m": "15",
        "1h": "60", "6h": "360", "12h": "720",
        "1d": "1D", "2d": "2D", "15d": "1W",
    }
    tv_interval = _TV_INTERVALS.get(tf_key, "15")
    tv_theme = "light"

    st.markdown(
        '<div class="section-card" style="padding:0;overflow:hidden;border-radius:12px;">',
        unsafe_allow_html=True,
    )
    tv_html = f"""
    <div class="tradingview-widget-container" style="margin:0;line-height:1;">
        <div id="tv-eth-chart"></div>
        <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
        <script type="text/javascript">
        new TradingView.widget({{
            "width": "100%",
            "height": 540,
            "symbol": "OKX:ETHUSDT",
            "interval": "{tv_interval}",
            "timezone": "Asia/Shanghai",
            "theme": "{tv_theme}",
            "style": "1",
            "locale": "zh_CN",
            "toolbar_bg": "#f1f3f6",
            "enable_publishing": false,
            "allow_symbol_change": false,
            "hide_top_toolbar": false,
            "save_image": false,
            "container_id": "tv-eth-chart",
            "studies": [
                "MASimple@tv-basicstudies"
            ]
        }});
        </script>
    </div>
    """
    components.html(tv_html, height=570)
    st.markdown('</div>', unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# DATA FRAGMENT — 自动刷新隔离，不出蒙版
# ════════════════════════════════════════════════════════════════

refresh_interval_s = TIMEFRAME_REFRESH_S.get(tf_key, 5) if auto else None


@st.fragment(run_every=refresh_interval_s)
def _data_fragment():
    """数据获取 + 显示 + AI 喂数据 —— fragment 内刷新，不出蒙版"""
    import time as _time

    # 从 session_state 读取当前参数（让 fragment 的自动刷新拿到最新值）
    _tf_label = st.session_state.eth_timeframe
    _t_key = TIMEFRAMES.get(_tf_label, "1d")
    _d_count = st.session_state.eth_data_count
    _sec_mode = _t_key == "1s"

    # ── 获取数据 ──
    _cached_df = st.session_state.get("eth_data")

    # Ticker
    _ticker_data = st.session_state.get("eth_ticker")
    try:
        _ticker_data = fetch_ticker(cfg, symbol=ETH_SYMBOL)
        st.session_state.eth_ticker = _ticker_data
    except Exception as e:
        if _cached_df is None:
            st.warning(f"获取 ticker 失败: {e}")

    # K 线
    if _sec_mode:
        from data.heartbeat_db import HeartbeatDB, is_collector_running, start_collector
        if not is_collector_running():
            ok = start_collector()
            if ok:
                _time.sleep(1.5)
        _db = HeartbeatDB()
        try:
            _new_df = _db.get_second_candles(limit=_d_count)
            if _new_df is not None and not _new_df.empty:
                st.session_state.eth_data = _new_df
                _cached_df = _new_df
        except Exception as e:
            if _cached_df is None:
                st.error(f"读取心跳数据失败: {e}")
        finally:
            _db.close()
    else:
        try:
            _new_df = fetch_klines_with_agg(
                cfg, limit=_d_count,
                timeframe=_t_key, symbol=ETH_SYMBOL,
            )
            if _new_df is not None and not _new_df.empty:
                st.session_state.eth_data = _new_df
                _cached_df = _new_df
        except Exception as e:
            if _cached_df is None:
                st.error(f"获取数据失败: {e}")

    st.session_state.eth_last_refresh = datetime.now().strftime("%H:%M:%S")
    _df = _cached_df
    _last_refresh = st.session_state.eth_last_refresh

    # ── AI executor 喂数据 ──
    if st.session_state.get(
            "ai_running") and _df is not None and not _df.empty:
        _executor = st.session_state.get("ai_executor")
        if _executor is not None:
            _buf = _executor.bar_buffer
            if _buf is not None and not _buf.empty:
                _last_processed = _buf.index[-1]
                _new_bars = _df[_df.index > _last_processed]
            else:
                _new_bars = _df
            if not _new_bars.empty:
                for _, _bar in _new_bars.iterrows():
                    _executor.on_bar(_bar)
                st.session_state.ai_trade_state = _executor.get_state()

    # ── Ticker bar ──
    if _ticker_data:
        _tk = _ticker_data
        _change_24h = _tk.get("change_24h", 0) or 0
        _price_color = "green" if _change_24h >= 0 else "red"
        _lp = _tk.get("last", 0) or 0
        _bid = _tk.get("bid", 0) or 0
        _ask = _tk.get("ask", 0) or 0

        st.markdown(f"""
        <div class="ticker-bar">
        <div class="ticker-item">
        <span class="ticker-label">ETH-USDT</span>
        <span class="ticker-value {_price_color}">${_lp:,.2f} {_fmt_change(_change_24h)}</span>
        </div>
        <div class="ticker-item">
        <span class="ticker-label">买一 / 卖一</span>
        <span class="ticker-value">{f'${_bid:,.2f}' if _bid else 'N/A'} / {f'${_ask:,.2f}' if _ask else 'N/A'}</span>
        </div>
        <div class="ticker-item">
        <span class="ticker-label">24h 最高 / 最低</span>
        <span class="ticker-value">{f'${_tk.get("high_24h", 0) or 0:,.2f}' if _tk.get("high_24h") else "N/A"} / {f'${_tk.get("low_24h", 0) or 0:,.2f}' if _tk.get("low_24h") else "N/A"}</span>
        </div>
        <div class="ticker-item">
        <span class="ticker-label">24h 成交量</span>
        <span class="ticker-value">{f'{_tk.get("volume_24h", 0) or 0:,.0f} ETH'}</span>
        </div>
        <div style="margin-left:auto; display:flex; align-items:center; gap:0.5rem;">
        <span class="badge badge--green">✅ 实时数据</span>
        <span style="color:#94a3b8; font-size:0.8rem;">{_last_refresh}</span>
        </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("💡 加载后将显示实时行情")

        # ════════════════════════════════════════════════════════════
    # MAIN DISPLAY
    # ════════════════════════════════════════════════════════════

    if _sec_mode:
        # ── 心跳模式 ──
        from data.heartbeat_db import HeartbeatDB as _HB, is_collector_running as _hb_running, read_status as _hb_status

        _running = _hb_running()
        _status = _hb_status() if _running else None
        _tick_count = _status.get(
            "tick_count", 0) if _status else 0
        _uptime_str = _fmt_uptime(
            _status.get("started_at") if _status else None)

        _tps = "0"
        _sa = _status.get("started_at") if _status else None
        if _sa and _tick_count:
            try:
                _elapsed = max(
                    1,
                    (datetime.now(
                        timezone.utc) -
                        datetime.fromisoformat(_sa)).total_seconds())
                _tps = f"{_tick_count / _elapsed:.1f}"
            except Exception:
                pass

            _dot_bg = "#22c55e" if _running else "#94a3b8"
            _status_icon = "💓 运行中" if _running else "⏸ 已停止"
            _pulse_style = "animation:pulse 1.5s infinite;" if _running else ""

            st.markdown(f"""
                <style>
                @keyframes pulse {{ 0%{{box-shadow:0 0 0 0 rgba(34,197,94,0.7);}} 70%{{box-shadow:0 0 0 10px rgba(34,197,94,0);}} 100%{{box-shadow:0 0 0 0 rgba(34,197,94,0);}} }}
                </style>
                <div class="section-card" style="padding:0.55rem 1rem;">
                <div style="display:flex;align-items:center;gap:1.2rem;flex-wrap:wrap;font-size:0.82rem;">
                <span style="display:flex;align-items:center;gap:5px;">
                <span style="width:10px;height:10px;border-radius:50%;background:{_dot_bg};{_pulse_style}display:inline-block;"></span>
                <span style="font-weight:600;">{_status_icon}</span>
                </span>
                <span style="color:#64748b;">心跳 <strong style="color:#0f172a;">{_tick_count:,}</strong></span>
                <span style="color:#64748b;">速率 <strong style="color:#0f172a;">{_tps} /s</strong></span>
                <span style="color:#64748b;">运行时长 <strong style="color:#0f172a;">{_uptime_str}</strong></span>
                <span style="margin-left:auto;color:#64748b;">周期 <strong style="color:#0f172a;">1秒</strong></span>
                </div>
                </div>
                """, unsafe_allow_html=True)

            if not _running:
                st.warning("? 心跳采集器未运行。秒级数据需要 WebSocket 心跳采集器。")
                if auto:
                    _time.sleep(2)
                    st.rerun(scope="fragment")
                return

        # Big price
        if _df is not None and not _df.empty:
            _last_price_hb = float(_df["close"].iloc[-1])
            _prev_price_hb = st.session_state.get("eth_hb_prev_price", None)
            if _prev_price_hb is None:
                _prev_price_hb = _last_price_hb
                _price_dir = "up" if _last_price_hb > _prev_price_hb else "down" if _last_price_hb < _prev_price_hb else "flat"
                st.session_state.eth_hb_prev_price = _last_price_hb

                _prev_close_hb = float(
                    _df["close"].iloc[-2]) if len(_df) > 1 else _last_price_hb
                _chg_hb = (_last_price_hb - _prev_close_hb) / \
                    _prev_close_hb * 100 if _prev_close_hb else 0
                _price_color_hb = "#059669" if _price_dir == "up" else "#dc2626" if _price_dir == "down" else "#0f172a"
                _bg_chg = "#d1fae5" if _chg_hb >= 0 else "#fee2e2"
                _fg_chg = "#065f46" if _chg_hb >= 0 else "#991b1b"

                _bid_hb = _ticker_data.get("bid") if _ticker_data else None
                _ask_hb = _ticker_data.get("ask") if _ticker_data else None
                _spread_hb = (_ask_hb - _bid_hb) / _bid_hb * \
                    100 if _bid_hb and _ask_hb else 0

                st.markdown(f"""
                <div style="text-align:center;padding:1.5rem 1rem 1rem;background:white;border-radius:12px;border:1px solid #e2e8f0;margin-bottom:1rem;">
                <div style="font-size:3rem;font-weight:700;color:{_price_color_hb};font-variant-numeric:tabular-nums;line-height:1.1;">
                ${_last_price_hb:,.2f}
                </div>
                <div style="display:flex;justify-content:center;gap:1.5rem;margin-top:0.5rem;">
                <span style="font-size:0.95rem;color:#64748b;">
                24h <span style="display:inline-block;padding:1px 12px;border-radius:999px;background:{_bg_chg};color:{_fg_chg};font-weight:600;">{_chg_hb:+.2f}%</span>
                </span>
                <span style="font-size:0.95rem;color:#64748b;">
                💓 {_tps} ticks/s
                </span>
                </div>
                <div style="display:flex;justify-content:center;gap:1rem;margin-top:0.75rem;font-size:0.85rem;color:#475569;">
                <span style="background:#f1f5f9;padding:2px 10px;border-radius:6px;">买一 <strong>${_bid_hb:,.2f}</strong></span>
                <span style="background:#f1f5f9;padding:2px 10px;border-radius:6px;">卖一 <strong>${_ask_hb:,.2f}</strong></span>
                <span style="background:#f1f5f9;padding:2px 10px;border-radius:6px;">价差 <strong>{_spread_hb:.3f}%</strong></span>
                </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.info("⏳ 等待心跳数据聚合…")
                if auto:
                    _time.sleep(1)
                    st.rerun(scope="fragment")
                return

        # Sparkline
        _hb_db = _HB()
        try:
            _recent_ticks = _hb_db.get_recent_ticks(limit=120)
        finally:
            _hb_db.close()

            if _recent_ticks:
                _spark_fig = _build_sparkline(_recent_ticks, height=110)
                st.markdown(
                    '<div class="section-card" style="padding:0.5rem;">',
                    unsafe_allow_html=True)
                st.plotly_chart(
                    _spark_fig, use_container_width=True, config={
                        "displayModeBar": False})
                st.markdown('</div>', unsafe_allow_html=True)

        # KPI row
        _prices_hb = [t["price"] for t in _recent_ticks if t.get(
            "price")] if _recent_ticks else []
        if _prices_hb:
            _kpi_cols = st.columns(5)
            with _kpi_cols[0]:
                st.metric("时段最高",
                          f"${max(_prices_hb):,.2f}",
                          f"+{(max(_prices_hb) - _prices_hb[0]) / _prices_hb[0] * 100:+.2f}%")
                with _kpi_cols[1]:
                    st.metric("时段最低",
                              f"${min(_prices_hb):,.2f}",
                              f"-{(_prices_hb[0] - min(_prices_hb)) / _prices_hb[0] * 100:+.2f}%")
                    with _kpi_cols[2]:
                        st.metric(
                            "时段波幅", f"${
                                max(_prices_hb) - min(_prices_hb):,.2f}")
                        with _kpi_cols[3]:
                            st.metric("心跳总数", f"{_tick_count:,}")
                            with _kpi_cols[4]:
                                st.metric("采集速率", f"{_tps}/s")

                                # Ticks table
                                if _recent_ticks:
                                    with st.expander("📋 最近心跳记录", expanded=True):
                                        _rows = []
                                        for t in _recent_ticks[:50]:
                                            try:
                                                _ts_str = datetime.fromisoformat(
                                                    t["ts"]).strftime("%H:%M:%S.%f")[:10]
                                            except Exception:
                                                _ts_str = str(
                                                    t.get("ts", ""))[:10]
                                                _rows.append({
                                                    "时间": _ts_str,
                                                    "价格": f"${t['price']:,.2f}",
                                                    "买一": f"${t['bid']:,.2f}" if t.get("bid") else "-",
                                                    "卖一": f"${t['ask']:,.2f}" if t.get("ask") else "-",
                                                    "24h涨跌": f"{t.get('change_24h', 0):+.2f}%" if t.get("change_24h") is not None else "-",
                                                })
                                                st.dataframe(
                                                    pd.DataFrame(_rows), use_container_width=True, hide_index=True, column_config={
                                                        "时间": st.column_config.TextColumn(
                                                            "时间", width="small"), "价格": st.column_config.TextColumn(
                                                            "价格 💰", width="small"), "买一": st.column_config.TextColumn(
                                                            "买一", width="small"), "卖一": st.column_config.TextColumn(
                                                            "卖一", width="small"), "24h涨跌": st.column_config.TextColumn(
                                                            "24h涨跌", width="small"), })
                                                _total_ticks = _HB().count_ticks()
                                                st.caption(
                                                    f"显示最近 {min(50, len(_recent_ticks))} / 共 {_total_ticks:,} 条心跳记录")

                                                # Raw candle
                                                # data
                                                if _df is not None and not _df.empty:
                                                    with st.expander("📄 秒级 K 线数据"):
                                                        _dd = _df.copy()
                                                        _dd.index = _dd.index.strftime(
                                                            "%Y-%m-%d %H:%M:%S")
                                                        _dd = _dd.rename(
                                                            columns={
                                                                "open": "开盘",
                                                                "high": "最高",
                                                                "low": "最低",
                                                                "close": "收盘",
                                                                "volume": "笔数",
                                                            })
                                                        st.dataframe(
                                                            _dd.iloc[::-1], use_container_width=True)
                                                        _csv = _df.to_csv(
                                                            index=True).encode("utf-8")
                                                        st.download_button(
                                                            "📥 导出 CSV", _csv, "eth_heartbeat_candles.csv", "text/csv", use_container_width=True)

    else:
        # ── OKX 模式 ──
        if _df is not None and not _df.empty:
            st.markdown(
                f"""
            <div class="status-bar">
            <div class="status-item">
            <span class="status-dot"></span>
            <span style="font-weight:600; color:#059669;">自动刷新</span>
            </div>
            <div class="status-item">
            <span style="color:#64748b;">数据源</span>
            <span style="font-weight:700;">OKX · TradingView</span>
            </div>
            <div class="status-item">
            <span style="color:#64748b;">周期</span>
            <span style="font-weight:600;">{_friendly_tf(_t_key)}</span>
            </div>
            <div class="status-item">
            <span style="color:#64748b;">K 线数</span>
            <span style="font-weight:600;">{len(_df)}</span>
            </div>
            <div class="status-item" style="margin-left:auto;">
            <span style="color:#64748b;">刷新 {TIMEFRAME_REFRESH_S.get(_t_key, 10)}s</span>
            </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info(
                "⏳ 正在获取数据，请稍候…")
            if auto:
                _time.sleep(
                    1)
                st.rerun(
                    scope="fragment")
            return

        # KPI — 优先使用 ticker 实时数据，回退到 K 线收盘价
        _ticker_last = _ticker_data.get("last") if _ticker_data else None
        _last_price_val = _ticker_last if _ticker_last else float(_df["close"].iloc[-1])
        _prev_val = float(_df["close"].iloc[-2]
                          ) if len(_df) > 1 else _last_price_val
        _chg = (_last_price_val - _prev_val) / \
            _prev_val * 100 if _prev_val else 0

        _hv = _ticker_data.get(
            "high_24h", float(
                _df["high"].max())) if _ticker_data else float(
            _df["high"].max())
        _lv = _ticker_data.get(
            "low_24h", float(
                _df["low"].min())) if _ticker_data else float(
            _df["low"].min())
        _ch24 = _ticker_data.get("change_24h") if _ticker_data else _chg

        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="section-title">📊 数据统计</div>',
            unsafe_allow_html=True)
        _kpi_cols = st.columns(6)
        with _kpi_cols[0]:
            render_metric_card("eth_price", _last_price_val)
        with _kpi_cols[1]:
            render_metric_card("eth_high_24h", _hv)
        with _kpi_cols[2]:
            render_metric_card("eth_low_24h", _lv)
        with _kpi_cols[3]:
            render_metric_card("eth_volume_24h", float(_df["volume"].sum()))
        with _kpi_cols[4]:
            render_metric_card("eth_change_24h", _ch24)
        with _kpi_cols[5]:
            render_metric_card(
                "eth_range_24h", f"${float(_df['low'].min()):,.2f} ~ ${float(_df['high'].max()):,.2f}")
        st.markdown('</div>', unsafe_allow_html=True)

        # Market details
        st.markdown(
            '<div class="section-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="section-title">📋 市场详情</div>',
            unsafe_allow_html=True)

        _b = _ticker_data.get("bid") if _ticker_data else None
        _a = _ticker_data.get("ask") if _ticker_data else None

        _cols = st.columns(4)
        with _cols[0]:
            st.metric("最新价", f"${_last_price_val:,.2f}",
                      f"{_chg:+.2f}%" if abs(_chg) > 0.01 else None)
        with _cols[1]:
            st.metric("买一价", f"${_b:,.2f}" if _b else "N/A")
        with _cols[2]:
            st.metric("卖一价", f"${_a:,.2f}" if _a else "N/A")
        with _cols[3]:
            _spread = (_a - _b) / _b * 100 if (_a and _b and _b) else 0
            st.metric("价差", f"{_spread:.4f}%" if _spread > 0 else "N/A")

        _cp = ((_last_price_val - float(_df["close"].iloc[0])) / float(_df["close"].iloc[0]) * 100
               ) if len(_df) > 1 else 0
        _cols2 = st.columns(4)
        with _cols2[0]:
            st.metric("总成交量 (时段)", f"{float(_df['volume'].sum()):,.0f}")
        with _cols2[1]:
            st.metric("平均成交量", f"{float(_df['volume'].mean()):,.1f}")
        with _cols2[2]:
            st.metric("期间涨跌幅", f"{_cp:+.2f}%" if len(_df) > 1 else "N/A",
                      delta_color="normal" if _cp >= 0 else "inverse")
        with _cols2[3]:
            st.metric("K 线数量", len(_df))

        st.markdown('</div>', unsafe_allow_html=True)

        # Raw data
        with st.expander("📄 查看原始 K 线数据"):
            _dd = _df.copy()
            _dd.index = _dd.index.strftime("%Y-%m-%d %H:%M:%S")
            _dd = _dd.rename(columns={
                "open": "开盘",
                "high": "最高",
                "low": "最低",
                "close": "收盘",
                "volume": "成交量",
            })
            st.dataframe(_dd.iloc[::-1], use_container_width=True)
            _csv = _df.to_csv(index=True).encode("utf-8")
            st.download_button(
                "📥 导出 CSV", _csv, "eth_usdt_klines.csv",
                "text/csv", use_container_width=True)


_data_fragment()

# ════════════════════════════════════════════════════════════════
# AI 多空分析 — 基于多维度数据的智能分析
# ════════════════════════════════════════════════════════════════

_ss("ai_analysis_result", None)
_ss("ai_analysis_error", None)
_ss("ai_news", None)
_ss("ai_running", False)
_ss("ai_use_live_mode", False)
_ss("ai_initial_balance", 10000.0)
_ss("ai_chat_context", None)
_ss("ai_chat_messages", [])
_ss("ai_chat_loading", False)

with st.container():
    st.markdown("---")
    st.markdown("### 🤖 AI 多空分析")
    st.markdown(
        "基于K线数据、成交量、历史走势、关联币种表现及最新新闻政策综合判断，"
        "点击按钮后自动采集数据+新闻并调用AI给出多空建议。"
    )

    btn_cols = st.columns([3, 1.5, 1.5])
    with btn_cols[1]:
        analyze_btn = st.button("📊 开始分析", type="primary", use_container_width=True)
    with btn_cols[2]:
        if st.button("🗑 清除结果", use_container_width=True):
            st.session_state.ai_analysis_result = None
            st.session_state.ai_analysis_error = None
            st.session_state.ai_news = None
            st.session_state.ai_chat_context = None
            st.session_state.ai_chat_messages = []
            st.session_state.ai_chat_loading = False
            st.rerun()

    # ── 执行分析 ──
    if analyze_btn:
        st.session_state.ai_analysis_result = None
        st.session_state.ai_analysis_error = None
        st.session_state.ai_chat_loading = False
        try:
            with st.spinner("📡 正在收集市场数据和新闻…"):
                _tk = fetch_ticker(cfg, symbol="ETH-USDT")
                _k15 = fetch_klines_with_agg(cfg, limit=30, timeframe="15m", symbol="ETH-USDT")
                _k1h = fetch_klines_with_agg(cfg, limit=20, timeframe="1h", symbol="ETH-USDT")
                _k1d = fetch_klines_with_agg(cfg, limit=7, timeframe="1d", symbol="ETH-USDT")
                _btc = fetch_ticker(cfg, symbol="BTC-USDT")
                _sol = fetch_ticker(cfg, symbol="SOL-USDT")
                _doge = fetch_ticker(cfg, symbol="DOGE-USDT")
                _news = _fetch_crypto_news()
                st.session_state.ai_news = _news
                # 保存对话上下文（市场数据快照）
                _mk = (
                    f"### 实时行情\n{_ticker_summary('ETH', _tk)}\n\n"
                    f"{_summarize_klines(_k15, '短期(15分钟)')}\n"
                    f"{_summarize_klines(_k1h, '中期(1小时)')}\n"
                    f"{_summarize_klines(_k1d, '长期(日线)')}\n\n"
                    f"### 关联币种\n{_ticker_summary('BTC', _btc)}\n"
                    f"{_ticker_summary('SOL', _sol)}\n"
                    f"{_ticker_summary('DOGE', _doge)}"
                )
                st.session_state.ai_chat_context = {
                    "market_summary": _mk,
                    "news": _news,
                    "analysis_result": None,
                }
                st.session_state.ai_chat_messages = []  # 新分析，清空历史

            with st.spinner("🤖 AI 正在综合分析（技术面+基本面）…"):
                _result = _call_ai_analysis(
                    ticker=_tk,
                    klines_15m=_k15,
                    klines_1h=_k1h,
                    klines_1d=_k1d,
                    btc_ticker=_btc,
                    sol_ticker=_sol,
                    doge_ticker=_doge,
                    cfg=cfg,
                    news=_news,
                )
                st.session_state.ai_analysis_result = _result
                # 更新对话上下文中的分析结果
                if st.session_state.get("ai_chat_context"):
                    st.session_state.ai_chat_context["analysis_result"] = _result
        except Exception as e:
            st.session_state.ai_analysis_error = str(e)
        st.rerun()

    # ── 错误状态 ──
    _err = st.session_state.get("ai_analysis_error")
    if _err:
        st.error(f"❌ 分析失败: {_err}")

    # ── 结果显示 ──
    _raw_news = st.session_state.get("ai_news", [])
    _res = st.session_state.get("ai_analysis_result")
    if _res:
        _dir = _res.get("direction", "neutral")
        _conf = _res.get("confidence", 0)
        if _dir == "long":
            _dir_color = "#059669"
            _dir_icon = "📈"
            _dir_text = "看多"
        elif _dir == "short":
            _dir_color = "#dc2626"
            _dir_icon = "📉"
            _dir_text = "看空"
        else:
            _dir_color = "#64748b"
            _dir_icon = "⚖️"
            _dir_text = "中性"
        _conf_color = "#059669" if _conf >= 70 else "#f59e0b" if _conf >= 40 else "#94a3b8"
        _ev_html = "".join(
            f'<li style="margin-bottom:0.3rem;">{e}</li>' for e in _res.get("key_evidence", []))
        _risk_html = "".join(
            f'<li style="margin-bottom:0.3rem;">{r}</li>' for r in _res.get("risk_warnings", []))

        _fund_news_html = ""
        if _raw_news:
            _news_items = "".join(
                f'<li style="margin-bottom:0.25rem;color:#64748b;font-size:0.85rem;">'
                f'<span style="color:#0f172a;font-weight:500;">[{n["source"]}]</span> {n["title"]}</li>'
                for n in _raw_news
            )
            _fund_news_html = f"""
            <details style="margin-top:0.75rem;">
                <summary style="color:#64748b;font-size:0.85rem;cursor:pointer;">
                    📡 参考新闻（{len(_raw_news)}条）
                </summary>
                <ul style="margin:0.5rem 0 0 0;padding-left:1.2rem;">{_news_items}</ul>
            </details>"""

        st.markdown(f"""
        <div style="border:1px solid #e2e8f0;border-radius:12px;padding:1.25rem;background:white;margin-top:0.5rem;">
            <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;">
                <span style="font-size:1.8rem;">{_dir_icon}</span>
                <span style="font-size:1.5rem;font-weight:700;color:{_dir_color};">{_dir_text}</span>
                <div style="margin-left:auto;display:flex;align-items:center;gap:0.5rem;">
                    <span style="color:#64748b;font-size:0.85rem;">信心指数</span>
                    <span style="font-size:1.3rem;font-weight:700;color:{_conf_color};">{_conf}%</span>
                </div>
            </div>
            <p style="color:#475569;font-size:0.95rem;margin-bottom:1rem;">{_res.get("summary", "")}</p>
            <div style="margin-bottom:1rem;">
                <p style="font-weight:600;color:#0f172a;margin-bottom:0.4rem;">📌 关键依据</p>
                <ul style="margin:0;padding-left:1.2rem;color:#475569;font-size:0.9rem;">{_ev_html}</ul>
            </div>
            <div style="margin-bottom:1rem;">
                <p style="font-weight:600;color:#0f172a;margin-bottom:0.4rem;">⚠️ 风险提示</p>
                <ul style="margin:0;padding-left:1.2rem;color:#dc2626;font-size:0.9rem;">{_risk_html}</ul>
            </div>
            <div style="display:flex;gap:1rem;flex-wrap:wrap;">
                <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                    <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">🔬 技术面</p>
                    <p style="color:#475569;font-size:0.85rem;margin:0;">{_res.get("technical_analysis", "") or "—"}</p>
                </div>
                <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                    <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">🌊 市场情绪</p>
                    <p style="color:#475569;font-size:0.85rem;margin:0;">{_res.get("market_sentiment", "") or "—"}</p>
                </div>
                <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                    <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">📰 基本面</p>
                    <p style="color:#475569;font-size:0.85rem;margin:0;">{_res.get("fundamental_analysis", "") or "—"}</p>
                </div>
            </div>
            {_fund_news_html}
        </div>
        """, unsafe_allow_html=True)

    # ── AI 信号 → 交易执行 ──
    if not st.session_state.ai_running:
        if st.button("⚡ 按此信号交易", type="primary", use_container_width=True):
            from agent.signal_bridge import ai_signal_to_rules
            rules = ai_signal_to_rules(_res)
            _df = st.session_state.get("eth_data")
            if _df is None or _df.empty:
                st.error("❌ 暂无K线数据，请等待数据加载")
                st.stop()
            from execution.ai_executor import AIStrategyExecutor
            executor = AIStrategyExecutor(
                rules=rules, cfg=cfg,
                initial_balance=st.session_state.ai_initial_balance,
                mode="live" if st.session_state.ai_use_live_mode else "paper",
            )
            # 预热：跳过入场，只加载K线到缓冲区
            executor.ai_signal_skip_entry = True
            if _df is not None and not _df.empty:
                for _, bar in _df.iterrows():
                    executor.on_bar(bar)
            executor.ai_signal_skip_entry = False
            st.session_state.ai_strategy_rules = rules
            st.session_state.ai_executor = executor
            st.session_state.ai_running = True
            st.session_state.ai_trade_state = executor.get_state()
            st.rerun()
    else:
        st.info("⚠️ AI交易运行中，请先停止")

    # ── 💬 AI 追问对话 ──
    if _res:
        st.markdown("---")
        st.markdown("#### 💬 追问分析")

        chat_container = st.container()
        with chat_container:
            for msg in st.session_state.ai_chat_messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

        if st.session_state.ai_chat_loading:
            with st.chat_message("assistant"):
                st.markdown("🤔 思考中…")

        user_input = st.chat_input("对当前市场分析提问…（例如：为什么看空？ETH支撑位在哪？）",
                                   disabled=st.session_state.ai_chat_loading)
        if user_input and not st.session_state.ai_chat_loading:
            st.session_state.ai_chat_messages.append(
                {"role": "user", "content": user_input}
            )
            context = st.session_state.get("ai_chat_context")
            st.session_state.ai_chat_loading = True
            answer = _call_ai_chat(
                user_input, context,
                st.session_state.ai_chat_messages, cfg,
            )
            st.session_state.ai_chat_messages.append(
                {"role": "assistant", "content": answer}
            )
            st.session_state.ai_chat_loading = False
            st.rerun()


# ════════════════════════════════════════════════════════════════
# AI TRADING — natural-language strategy with auto-execution
# ════════════════════════════════════════════════════════════════

_ss("ai_strategy_text", "")
_ss("ai_strategy_rules", None)
_ss("ai_executor", None)
_ss("ai_trade_state", None)
_ss("ai_running", False)
_ss("ai_use_live_mode", False)
_ss("ai_initial_balance", 10000.0)
_ss("ai_interpret_error", None)

with st.expander("🤖 AI 交易", expanded=bool(st.session_state.ai_running)):
    st.markdown("输入交易策略的自然语言描述，AI 将解析为可执行规则并自动交易。")

    col_desc, col_ctrl = st.columns([3, 1])

    with col_desc:
        # 快捷模板
        tmpl_cols = st.columns([1, 1, 2])
        with tmpl_cols[0]:
            if st.button("📊 波动率反向", use_container_width=True,
                         disabled=st.session_state.ai_running):
                text = (
                    "15分钟K线实体大于15美元时反向开仓，"
                    "连续2根实体之和大于20美元也触发。"
                    "止损1.25%，多级移动止盈(1.25%保本/2.5%移动+1.25%/5%平半仓移动+2.5%)，"
                    "5倍杠杆，单笔最大亏损3%，同方向2小时冷却"
                )
                st.session_state.ai_strategy_text = text
                st.session_state.ai_text_input = text
                st.rerun()
        with tmpl_cols[1]:
            if st.button("📈 RSI均值回归", use_container_width=True,
                         disabled=st.session_state.ai_running):
                text = "RSI低于30买入，高于70卖出，止损2%，用10%资金"
                st.session_state.ai_strategy_text = text
                st.session_state.ai_text_input = text
                st.rerun()

        ai_text = st.text_area(
            "描述你的交易策略",
            value=st.session_state.ai_strategy_text,
            height=120,
            placeholder=(
                "例如：\n"
                "• RSI低于30买入，高于70卖出，止损2%，用10%资金\n"
                "• MA5金叉MA10买入，死叉卖出，止损3%\n"
                "• MACD金叉买入，移动止损2%，半仓\n"
                "• 价格突破60日均线买入，跌破60均线卖出，止盈5%\n"
                "• 连续3根阳线买入，连续2根阴线卖出\n"
                "• RSI低于30且MACD金叉买入\n"
                "• 布林带下轨买入，上轨卖出"
            ),
            key="ai_text_input",
        )

    with col_ctrl:
        st.markdown(
            "<div style='height:1.5rem'></div>",
            unsafe_allow_html=True)
        if st.button("🔮 解析策略", use_container_width=True, type="primary",
                     disabled=st.session_state.ai_running):
            if ai_text.strip():
                with st.spinner("🤖 AI 正在解析策略…"):
                    from agent.strategy_interpreter import StrategyInterpreter
                    interpreter = StrategyInterpreter(cfg)
                    rules = interpreter.interpret(ai_text)
                if "parse_error" in rules:
                    st.session_state.ai_interpret_error = rules["parse_error"]
                    st.session_state.ai_strategy_rules = None
                else:
                    st.session_state.ai_strategy_rules = rules
                    st.session_state.ai_strategy_text = ai_text
                    st.session_state.ai_interpret_error = None
                st.rerun()
            else:
                st.warning("请先输入策略描述")

        # Mode toggle
        st.session_state.ai_use_live_mode = st.checkbox(
            "实盘模式", value=st.session_state.ai_use_live_mode,
            disabled=st.session_state.ai_running,
            help="启用时使用 OKX API 直接下单（需 Trade 权限）",
        )

    # ── 解析结果显示 ──
    ai_rules = st.session_state.ai_strategy_rules
    ai_err = st.session_state.ai_interpret_error

    if ai_err:
        st.error(f"❌ {ai_err}")

    if ai_rules and ai_err is None:
        st.markdown("---")
        st.markdown(f"**📋 {ai_rules.get('strategy_name', 'AI策略')}**")
        if ai_rules.get("timeframe_hint"):
            st.caption(f"⏱ 建议周期: {ai_rules['timeframe_hint']}")

        # 入场条件
        entry = ai_rules.get("entry_conditions", [])
        exit_c = ai_rules.get("exit_conditions", [])
        risk = ai_rules.get("risk_params", {})

        cond_cols = st.columns(3)
        with cond_cols[0]:
            st.markdown("**📈 入场条件**")
            cond_logic = ai_rules.get("_condition_logic", "any")
            if entry:
                label = "（需全部满足）" if cond_logic == "all" else "（任一满足即入）"
                st.caption(label)
                for i, c in enumerate(entry):
                    period = c.get('params', {}).get('period', '')
                    comp = c.get('comparison', '')
                    comp_symbol = {
                        'less_than': '<', 'greater_than': '>',
                        'crosses_above': '上穿', 'crosses_below': '下穿',
                        'greater_or_equal': '≥', 'less_or_equal': '≤',
                        'consecutive_gain': '连涨', 'consecutive_loss': '连跌',
                        'touches': '≈',
                    }.get(comp, comp)
                    val = c.get('value', '') or ''
                    cross = c.get('cross_with', '')
                    if cross:
                        pnl = f" {comp_symbol} {cross.upper()}"
                    elif val:
                        pnl = f" {comp_symbol} {val}"
                    else:
                        pnl = f" {comp_symbol}"
                    st.markdown(
                        f"{i + 1}. {c.get('indicator', '').upper()}{period}{pnl}"
                        f" → {'买入' if c.get('action') == 'buy' else '卖出'}"
                    )
            else:
                st.markdown("*(无入场条件)*")
        with cond_cols[1]:
            st.markdown("**📉 出场条件**")
            if exit_c:
                for i, c in enumerate(exit_c):
                    period = c.get('params', {}).get('period', '')
                    comp = c.get('comparison', '')
                    comp_symbol = {
                        'less_than': '<', 'greater_than': '>',
                        'crosses_above': '上穿', 'crosses_below': '下穿',
                        'greater_or_equal': '≥', 'less_or_equal': '≤',
                        'consecutive_gain': '连涨', 'consecutive_loss': '连跌',
                    }.get(comp, comp)
                    val = c.get('value', '') or ''
                    cross = c.get('cross_with', '')
                    if cross:
                        pnl = f" {comp_symbol} {cross.upper()}"
                    elif val:
                        pnl = f" {comp_symbol} {val}"
                    else:
                        pnl = f" {comp_symbol}"
                    st.markdown(
                        f"{i + 1}. {c.get('indicator', '').upper()}{period}{pnl}"
                        f" → {'卖出' if c.get('action') == 'sell' else '买入'}"
                    )
            else:
                st.markdown("*(止盈/止损硬出场)*")
        with cond_cols[2]:
            st.markdown("**🛡 风控参数**")
            sl = risk.get("stop_loss_pct")
            tp = risk.get("take_profit_pct")
            ps = risk.get("position_size_pct", 10.0)
            ts_act = risk.get("trailing_stop_activation_pct")
            ts_dist = risk.get("trailing_stop_distance_pct")
            timeout = risk.get("position_timeout_bars")

            st.markdown(f"- 仓位: {ps:.0f}%")
            st.markdown(f"- 止损: {f'{sl:.1f}%' if sl else '无'}")
            st.markdown(f"- 止盈: {f'{tp:.1f}%' if tp else '无'}")
            if ts_dist:
                st.markdown(f"- 移动止损: 激活+{ts_act:.1f}% / 回落{ts_dist:.1f}%")
            if timeout:
                st.markdown(f"- 持仓超时: {timeout}根K线")

            # 波动率策略专属参数显示
            if ai_rules.get("_strategy_type") == "volatility_contrarian":
                st.markdown("---")
                st.markdown("**⚡ 波动率策略参数**")
                lev = risk.get("leverage", 5)
                max_loss = risk.get("max_loss_pct", 3)
                body_t = risk.get("volatility_body_threshold", 15)
                sum_t = risk.get("volatility_sum_threshold", 20)
                cool = risk.get("cooldown_bars", 8)
                st.markdown(
                    f"- 杠杆: {lev}x | 单笔最大亏损: {max_loss}%\n"
                    f"- 触发: 单根实体 > ${body_t} 或 连续2根之和 > ${sum_t}\n"
                    f"- 方向: 反向开仓（阴线→做多，阳线→做空）\n"
                    f"- 同向冷却: {cool}根K线 | 连亏: 仓位减半"
                )

            # 策略备注
            notes = ai_rules.get("_notes", "")
            if notes:
                st.caption(f"📝 {notes}")

        # 控制按钮
        st.markdown("---")
        btn_cols = st.columns([2, 1, 2])
        with btn_cols[0]:
            bal = st.number_input(
                "初始资金 (USDT)",
                min_value=100.0, max_value=10_000_000.0,
                value=st.session_state.ai_initial_balance,
                step=1000.0,
                disabled=st.session_state.ai_running,
                key="ai_balance_input",
            )

        with btn_cols[1]:
            st.markdown(
                "<div style='height:1.5rem'></div>",
                unsafe_allow_html=True)
            if not st.session_state.ai_running:
                if st.button("▶ 启动", use_container_width=True, type="primary"):
                    _df = st.session_state.get("eth_data")
                    if _df is None or _df.empty:
                        st.error("暂无K线数据，请等待数据加载")
                        st.stop()

                    from execution.ai_executor import AIStrategyExecutor
                    executor = AIStrategyExecutor(
                        rules=ai_rules,
                        cfg=cfg,
                        initial_balance=st.session_state.ai_initial_balance,
                        mode="live" if st.session_state.ai_use_live_mode else "paper",
                    )

                    # 预热：加载已有K线数据
                    if _df is not None and not _df.empty:
                        for _, bar in _df.iterrows():
                            executor.on_bar(bar)

                    st.session_state.ai_executor = executor
                    st.session_state.ai_running = True
                    st.session_state.ai_trade_state = executor.get_state()
                    st.rerun()
            else:
                if st.button("⏹ 停止", use_container_width=True, type="primary"):
                    st.session_state.ai_running = False
                    state = st.session_state.get("ai_trade_state")
                    if state:
                        st.success(
                            f"已停止 | 交易 {
                                state.get(
                                    'total_trades',
                                    0)} 笔 | " f"权益 ${
                                state.get(
                                    'account',
                                    {}).get(
                                    'equity',
                                    0):,.2f}")
                    st.rerun()

        # ── 运行状态 ──
        if st.session_state.ai_running and st.session_state.ai_trade_state:
            state = st.session_state.ai_trade_state
            acc = state.get("account", {})

            st.markdown("---")
            st.markdown(f"**🤖 运行中 — {state.get('strategy_name', 'AI策略')}**")

            # KPI
            kpi = st.columns(6)
            with kpi[0]:
                st.metric("状态", "🟢 运行中" if state.get("running") else "⏸ 已停止")
            with kpi[1]:
                sig = state.get("signal", "hold")
                sig_emoji = {
                    "buy": "🟢",
                    "sell": "🔴",
                    "short": "🔴",
                    "hold": "⚪",
                    "blocked": "🟡"}
                st.metric("最新信号", f"{sig_emoji.get(sig, '⚪')} {sig.upper()}")
            with kpi[2]:
                ip = acc.get("in_position", False)
                ps = state.get("position_side", "")
                pos_label = "✅ 持仓中"
                if ip and ps == "long":
                    pos_label = "🟢 多头"
                elif ip and ps == "short":
                    pos_label = "🔴 空头"
                elif not ip:
                    pos_label = "⬜ 空仓"
                st.metric("持仓", pos_label)
            with kpi[3]:
                ep = state.get("entry_price", 0)
                st.metric("入场价", f"${ep:,.2f}" if ep > 0 else "-")
            with kpi[4]:
                equity = acc.get("equity", 0)
                init_bal = acc.get("initial_balance", 0)
                pnl = equity - init_bal
                pnl_color = "green" if pnl >= 0 else "red"
                st.markdown(
                    f'<div style="padding:0.5rem 0;"><span style="color:#64748b;font-size:0.8rem;">总 P&L</span>'
                    f'<div style="font-size:1.3rem;font-weight:700;color:{pnl_color};">${pnl:+,.2f}</div></div>',
                    unsafe_allow_html=True,
                )
            with kpi[5]:
                st.metric("交易次数", state.get("total_trades", 0))

            # 信号原因
            if state.get("signal_reason"):
                st.info(
                    f"📡 最近信号: {
                        state['signal'].upper()} — {
                        state['signal_reason']}")

            # 多级止盈 + 冷却状态（波动率策略专属）
            if state.get("position_side") and state.get("in_position"):
                mtp = state.get("multi_tp_level", 0)
                dsp = state.get("dynamic_stop_price", 0)
                pcd = state.get("partial_close_done", False)
                mtp_labels = {
                    0: "未激活",
                    1: "保本",
                    2: "+1.25%",
                    3: "+2.5%/+2.5%-2.5%"}
                mtp_info = []
                if mtp > 0:
                    mtp_info.append(f"止盈级别: {mtp_labels.get(mtp, str(mtp))}")
                if dsp > 0:
                    mtp_info.append(f"动态止损: ${dsp:.2f}")
                if pcd:
                    mtp_info.append("✅ 已部分平仓")
                if mtp_info:
                    st.caption(" | ".join(mtp_info))

            # 冷却状态
            cr = acc.get("cooldown_remaining", 0)
            ptl = acc.get("prev_trade_loss", False)
            cool_info = []
            if cr > 0:
                cool_info.append(f"⏳ 冷却剩余: {cr}根K线")
            if ptl:
                cool_info.append("⚠️ 前笔亏损（仓位减半）")
            if cool_info:
                st.caption(" | ".join(cool_info))

            # 交易记录
            trades = acc.get("trades", [])
            if trades:
                with st.expander("📋 交易记录"):
                    import pandas as _pd
                    df_trades = _pd.DataFrame(
                        trades).iloc[::-1].reset_index(drop=True)
                    cols_show = ["time", "side", "price", "size", "pnl", "fee"]
                    cols_exist = [
                        c for c in cols_show if c in df_trades.columns]
                    df_trades_display = df_trades[cols_exist]
                    if "pnl" in df_trades_display.columns:
                        df_trades_display["pnl"] = df_trades_display["pnl"].apply(
                            lambda x: f"${x:+,.2f}" if isinstance(x, (int, float)) and x != 0 else "-"
                        )
                    st.dataframe(
                        df_trades_display,
                        use_container_width=True,
                        hide_index=True)

                    # 统计
                    wins = [t for t in trades if t.get("pnl", 0) > 0]
                    losses = [t for t in trades if t.get("pnl", 0) < 0]
                    win_rate = len(wins) / len(trades) * 100 if trades else 0
                    total_pnl = sum(t.get("pnl", 0) for t in trades)
                    stat_cols = st.columns(4)
                    stat_cols[0].metric("总盈亏", f"${total_pnl:+,.2f}")
                    stat_cols[1].metric("胜率", f"{win_rate:.1f}%")
                    stat_cols[2].metric("盈利次数", len(wins))
                    stat_cols[3].metric("亏损次数", len(losses))

        elif st.session_state.ai_running and not st.session_state.ai_trade_state:
            st.info("⏳ 等待数据…")
