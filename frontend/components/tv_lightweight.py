"""TradingView Lightweight Charts 封装的 K 线图组件。

与官方 TradingView 嵌入组件（tv.js，closed iframe）不同，
lightweight-charts 是 TradingView 开源的图表库，可以喂自己的数据：
OKX K 线 + 模拟盘成交标记 + 实时价格线，视觉风格与 TradingView 一致。

注意：lightweight-charts 时间轴一律按 UTC 显示，这里统一 +8h 偏移，
让刻度显示为北京时间（与页面其余部分一致）。
"""
from __future__ import annotations

import json

import pandas as pd

__all__ = ["build_kline_tv_html"]

# 成交标记映射：side → (shape, position, color, text)
_MARKER_MAP = {
    "buy": ("arrowUp", "belowBar", "#059669", "B"),
    "open_long": ("arrowUp", "belowBar", "#059669", "多"),
    "sell": ("arrowDown", "aboveBar", "#dc2626", "S"),
    "short": ("arrowDown", "aboveBar", "#dc2626", "空"),
    "open_short": ("arrowDown", "aboveBar", "#dc2626", "空"),
    "cover": ("circle", "aboveBar", "#f59e0b", "平"),
    "close_long": ("circle", "aboveBar", "#f59e0b", "平"),
    "close_short": ("circle", "belowBar", "#f59e0b", "平"),
    "liquidation": ("square", "aboveBar", "#dc2626", "爆"),
}

_TZ_SHIFT_S = 8 * 3600  # UTC → 北京时间显示偏移

_THEMES = {
    "dark": {
        "bg": "#1e293b", "text": "#94a3b8", "grid": "rgba(148,163,184,0.10)",
        "border": "#334155", "title": "#f1f5f9",
    },
    "light": {
        "bg": "#ffffff", "text": "#475569", "grid": "rgba(148,163,184,0.20)",
        "border": "#e2e8f0", "title": "#0f172a",
    },
}


def _to_tv_time(ts) -> int:
    """tz-aware 时间 → lightweight-charts time（UTC 秒 + 8h 显示偏移）"""
    return int(pd.Timestamp(ts).timestamp()) + _TZ_SHIFT_S


def build_kline_tv_html(
    df: pd.DataFrame,
    trades: list[dict] | None = None,
    ticker_last: float | None = None,
    symbol: str = "ETH-USDT",
    timeframe: str = "1h",
    theme: str = "dark",
    height: int = 480,
    visible_bars: int = 60,
) -> str:
    """生成 K 线 + 信号图表 HTML（给 streamlit.components.v1.html 用）。

    df: 含 open/high/low/close/volume 列、tz-aware DatetimeIndex 的 K 线数据（全部加载，
        用户可左右拖动查看历史；默认视图停在最近 visible_bars 根）
    trades: 账户成交记录（含 time/side/price），用于在图上打成交标记
    ticker_last: 实时最新价（画虚线价格线），None 则不画
    """
    t = _THEMES.get(theme, _THEMES["light"])

    candles = [
        {
            "time": _to_tv_time(ts),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
        }
        for ts, r in df.iterrows()
    ]
    volumes = [
        {
            "time": _to_tv_time(ts),
            "value": float(r.get("volume", 0) or 0),
            "color": "rgba(5,150,105,0.45)" if r["close"] >= r["open"] else "rgba(220,38,38,0.45)",
        }
        for ts, r in df.iterrows()
    ]

    markers = []
    for tr in trades or []:
        side = tr.get("side", "")
        if side not in _MARKER_MAP or not tr.get("price") or not tr.get("time"):
            continue
        shape, position, color, text = _MARKER_MAP[side]
        markers.append({
            "time": _to_tv_time(tr["time"]),
            "position": position, "shape": shape, "color": color, "text": text,
        })
    markers.sort(key=lambda m: m["time"])  # setMarkers 要求按时间升序

    tf_label = {"15m": "15 分钟", "1h": "1 小时", "4h": "4 小时", "1d": "1 天"}.get(timeframe, timeframe)
    # 有实时价（模拟盘）标"实时"，纯历史数据（回测）标"回测"
    mode_label = "实时" if ticker_last else "回测"
    price_line = (
        f'series.createPriceLine({{price: {float(ticker_last)}, color: "#2563eb", '
        f'lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, '
        f'axisLabelVisible: true, title: "实时"}});'
        if ticker_last else ""
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
  html, body {{ margin: 0; padding: 0; background: {t['bg']}; overflow: hidden; }}
  #header {{ font: 600 13px -apple-system, "Segoe UI", sans-serif; color: {t['title']};
             padding: 6px 8px 2px; }}
  #header span {{ color: {t['text']}; font-weight: 400; }}
  #chart {{ width: 100%; height: {height - 24}px; }}
</style>
</head>
<body>
<div id="header">{symbol} <span>· {tf_label} · {mode_label}</span></div>
<div id="chart"></div>
<script>
const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
  autoSize: true,
  layout: {{
    background: {{ type: 'solid', color: '{t["bg"]}' }},
    textColor: '{t["text"]}',
    fontFamily: '-apple-system, "Segoe UI", sans-serif',
  }},
  grid: {{
    vertLines: {{ color: '{t["grid"]}' }},
    horzLines: {{ color: '{t["grid"]}' }},
  }},
  crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
  rightPriceScale: {{ borderColor: '{t["border"]}' }},
  timeScale: {{ borderColor: '{t["border"]}', timeVisible: true, secondsVisible: false }},
  watermark: {{ visible: true, text: '{symbol}', color: 'rgba(148,163,184,0.08)',
               fontSize: 42, fontFamily: '-apple-system, sans-serif' }},
}});
const series = chart.addCandlestickSeries({{
  upColor: '#059669', downColor: '#dc2626',
  borderUpColor: '#059669', borderDownColor: '#dc2626',
  wickUpColor: '#059669', wickDownColor: '#dc2626',
  priceScaleId: 'right',
}});
series.priceScale().applyOptions({{ scaleMargins: {{ top: 0.06, bottom: 0.22 }} }});
series.setData({json.dumps(candles)});
const volSeries = chart.addHistogramSeries({{
  priceFormat: {{ type: 'volume' }}, priceScaleId: 'vol', lastValueVisible: false,
}});
chart.priceScale('vol').applyOptions({{ scaleMargins: {{ top: 0.82, bottom: 0 }} }});
volSeries.setData({json.dumps(volumes)});
series.setMarkers({json.dumps(markers)});
{price_line}
// 默认视图停在最近 visible_bars 根，更早的历史可向左拖动查看（含全部成交标记）
const n = {len(candles)}, vb = {int(visible_bars)};
if (n > vb) {{
  chart.timeScale().setVisibleLogicalRange({{ from: n - vb, to: n + 3 }});
}} else {{
  chart.timeScale().fitContent();
}}
</script>
</body>
</html>"""
