"""
Market State Classifier

根据多周期技术指标判断当前市场状态（趋势/波动/形态），
供 Agent 3 作为 DeepSeek 决策的上下文注入。

输入: Agent 1 的 _latest_indicators（按 timeframe 的 macd/kdj/boll 数据）
输出: {
    "trend": "uptrend" | "downtrend" | "sideways",
    "volatility": "high" | "medium" | "low",
    "regime": "trending" | "ranging" | "transition",
    "summary_line": str  # 一行摘要供 DeepSeek
}
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("market_state")

# 各周期做趋势判断时的权重（短->长递增）
_TF_TREND_WEIGHTS = {"15m": 0.15, "1h": 0.35, "1d": 0.50}
_VOLATILITY_BW_THRESHOLDS = {"low": 0.03, "high": 0.08}  # bandwidth 阈值


def classify_market(indicators: dict[str, dict]) -> dict[str, Any]:
    """多周期指标 → 市场状态分类

    Args:
        indicators: Agent 1 的 _latest_indicators 格式
            { timeframe: { "macd": {...}, "kdj": {...}, "boll": {...}, "close": float } }

    Returns:
        { trend, volatility, regime, summary_line }
    """
    # ── 数据充分的周期（15m / 1h / 1d 至少有一个） ──
    available = {tf: data for tf, data in indicators.items()
                 if tf in ("15m", "1h", "1d") and data and data.get("macd")}

    if not available:
        return {
            "trend": "sideways",
            "volatility": "medium",
            "regime": "ranging",
            "summary_line": "Market State: Data insufficient, defaulting to neutral",
        }

    # ── 1. 趋势方向判定 ──
    trend_scores = []
    for tf, data in available.items():
        macd = data["macd"]
        boll = data["boll"]
        close = data.get("close", 0)
        middle = boll.get("middle", 0) if boll else 0
        weight = _TF_TREND_WEIGHTS.get(tf, 0.2)

        # MACD 方向打分：+1 偏多，-1 偏空
        macd_score = 0
        if macd.get("histogram", 0) > 0:
            macd_score += 0.5
        if macd.get("crossover") == "bullish":
            macd_score += 0.5
        elif macd.get("crossover") == "bearish":
            macd_score -= 0.5
        if macd.get("histogram", 0) < 0:
            macd_score -= 0.5

        # 价格相对布林中轨位置：+1 偏多，-1 偏空
        price_score = 0
        if middle and close:
            pos_pct = boll.get("position", 50) if boll else 50
            if pos_pct > 60:
                price_score += 0.3
            elif pos_pct < 40:
                price_score -= 0.3
            # 突破上轨 = 强势，突破下轨 = 弱势
            if boll.get("position_label") == "touch_upper":
                price_score += 0.7
            elif boll.get("position_label") == "touch_lower":
                price_score -= 0.7

        tf_score = (macd_score + price_score) * weight
        trend_scores.append(tf_score)

    total_trend = sum(trend_scores)

    if total_trend > 0.15:
        trend = "uptrend"
    elif total_trend < -0.15:
        trend = "downtrend"
    else:
        trend = "sideways"

    # ── 2. 波动率判定（取 1h 带宽为基准，fallback 到 15m / 1d） ──
    volatility = "medium"
    for tf in ("1h", "15m", "1d"):
        data = available.get(tf)
        if data and data.get("boll"):
            bw = data["boll"].get("bandwidth", 0)
            if bw:
                if bw < _VOLATILITY_BW_THRESHOLDS["low"]:
                    volatility = "low"
                elif bw > _VOLATILITY_BW_THRESHOLDS["high"]:
                    volatility = "high"
                else:
                    volatility = "medium"
                break

    # ── 3. 市场形态判定 ──
    regime = "ranging"  # 默认震荡
    # 多周期 MACD 方向一致性
    directions = set()
    for data in available.values():
        h = data["macd"].get("histogram", 0)
        directions.add("positive" if h > 0 else "negative" if h < 0 else "flat")

    # 如果只有一个方向（全正或全负），且趋势分足够强 → 趋势
    if len(directions) == 1 and "flat" not in directions and abs(total_trend) > 0.25:
        regime = "trending"
    elif len(directions) <= 2 and "flat" not in directions:
        # 两个方向但趋势分不弱 → 过渡期
        regime = "transition"
    else:
        regime = "ranging"

    # squeeze 检测
    has_squeeze = any(
        data.get("boll", {}).get("squeeze")
        for data in available.values()
    )

    # ── 4. 摘要行 ──
    trend_labels = {"uptrend": "Uptrend ↑", "downtrend": "Downtrend ↓", "sideways": "Sideways →"}
    vol_labels = {"high": "High", "medium": "Medium", "low": "Low"}
    regime_labels = {
        "trending": "Trending — follow trend, avoid counter-trend entries",
        "ranging": "Ranging — mean-reversion, avoid chasing breakouts",
        "transition": "Transition — reduce position size, wait for confirmation",
    }

    squeeze_note = " | Bollinger Squeeze ⚡" if has_squeeze else ""
    summary = (
        f"Market: {trend_labels[trend]} | "
        f"Volatility: {vol_labels[volatility]} | "
        f"Regime: {regime_labels[regime]}"
        f"{squeeze_note}"
    )

    return {
        "trend": trend,
        "volatility": volatility,
        "regime": regime,
        "has_squeeze": has_squeeze,
        "summary_line": summary,
    }


def format_indicators_table(indicators: dict[str, dict]) -> str:
    """将多周期指标格式化为结构化表格文本（供 DeepSeek prompt 注入）

    Args:
        indicators: Agent 1 的 _latest_indicators

    Returns:
        多行字符串表格
    """
    tfs = ["15m", "1h", "1d"]
    available = {tf: indicators.get(tf) for tf in tfs if tf in indicators and indicators[tf]}

    if not available:
        return "[Indicators] Waiting for data..."

    # 每周期一行摘要
    rows = []
    rows.append("Indicator           | 15m          | 1h           | 1d")
    rows.append("-" * 60)

    # MACD Histogram
    vals = []
    for tf in tfs:
        d = available.get(tf)
        if d and d.get("macd"):
            h = d["macd"].get("histogram", 0)
            direction = d["macd"].get("hist_direction", "")
            arrow = "↑" if direction == "rising" else "↓" if direction == "falling" else "→"
            vals.append(f"{h:+.2f} {arrow}")
        else:
            vals.append("--")
    rows.append(f"MACD Histogram      | {vals[0]:<12} | {vals[1]:<12} | {vals[2]}")

    # MACD Cross
    vals = []
    for tf in tfs:
        d = available.get(tf)
        cross = d["macd"].get("crossover") if d and d.get("macd") else None
        vals.append(cross or "none")
    rows.append(f"MACD Cross          | {vals[0]:<12} | {vals[1]:<12} | {vals[2]}")

    # KDJ K/D
    vals = []
    for tf in tfs:
        d = available.get(tf)
        if d and d.get("kdj"):
            k_val = d["kdj"].get("k", 0)
            d_val = d["kdj"].get("d", 0)
            vals.append(f"{k_val:.1f}/{d_val:.1f}")
        else:
            vals.append("--")
    rows.append(f"KDJ K/D             | {vals[0]:<12} | {vals[1]:<12} | {vals[2]}")

    # J Value
    vals = []
    for tf in tfs:
        d = available.get(tf)
        if d and d.get("kdj"):
            j_val = d["kdj"].get("j", 50)
            tag = ""
            if j_val >= 100:
                tag = " (OB)"
            elif j_val <= 0:
                tag = " (OS)"
            elif j_val > 80:
                tag = " (high)"
            elif j_val < 20:
                tag = " (low)"
            vals.append(f"{j_val:.0f}{tag}")
        else:
            vals.append("--")
    rows.append(f"J Value             | {vals[0]:<12} | {vals[1]:<12} | {vals[2]}")

    # Bollinger Position
    vals = []
    for tf in tfs:
        d = available.get(tf)
        if d and d.get("boll"):
            pos = d["boll"].get("position", 50)
            label = d["boll"].get("position_label", "inside")
            if label == "touch_upper":
                tag = "(upper)"
            elif label == "touch_lower":
                tag = "(lower)"
            elif pos > 60:
                tag = "(high)"
            elif pos < 40:
                tag = "(low)"
            else:
                tag = "(mid)"
            vals.append(f"{pos:.0f}% {tag}")
        else:
            vals.append("--")
    rows.append(f"Bollinger Position  | {vals[0]:<12} | {vals[1]:<12} | {vals[2]}")

    # Bollinger Squeeze
    vals = []
    for tf in tfs:
        d = available.get(tf)
        sqz = d["boll"].get("squeeze") if d and d.get("boll") else None
        vals.append("yes ⚡" if sqz else "no")
    rows.append(f"Bollinger Squeeze   | {vals[0]:<12} | {vals[1]:<12} | {vals[2]}")

    return "\n".join(rows)
