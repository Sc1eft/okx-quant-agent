"""
Market State Classifier

根据多周期技术指标判断当前市场状态（趋势/波动/形态），
供 Agent 3 作为 DeepSeek 决策的上下文注入。

输入: Agent 1 的 _latest_indicators（按 timeframe 的 macd/kdj/boll 数据）
输出: {
    "trend": "uptrend" | "downtrend" | "sideways",
    "volatility": "high" | "medium" | "low",
    "regime": "trending" | "ranging" | "transition",
    "conviction": float (0~1),  # 判定可靠度
    "summary_line": str  # 一行摘要供 DeepSeek
}
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("market_state")

# 所有可用周期及其趋势权重（短→长递增，3m/5m 仅辅助非主力）
_TF_TREND_WEIGHTS = {
    "3m": 0.05, "5m": 0.08, "15m": 0.17, "1h": 0.35, "1d": 0.35,
}
# 波动率带宽阈值
_VOLATILITY_BW_THRESHOLDS = {"low": 0.03, "medium": 0.06, "high": 0.10}


def classify_market(indicators: dict[str, dict]) -> dict[str, Any]:
    """多周期指标 → 市场状态分类

    Args:
        indicators: Agent 1 的 _latest_indicators 格式
            { timeframe: { "macd": {...}, "kdj": {...}, "boll": {...}, "close": float } }

    Returns:
        { trend, volatility, regime, conviction, summary_line }
    """
    # ── 可用周期：全部 5 个周期，含 3m/5m ──
    all_tfs = ["3m", "5m", "15m", "1h", "1d"]
    available = {
        tf: indicators[tf] for tf in all_tfs
        if tf in indicators and indicators[tf] and indicators[tf].get("macd")
    }
    main_tfs = [tf for tf in ["15m", "1h", "1d"] if tf in available]

    if not available or not main_tfs:
        return {
            "trend": "sideways",
            "volatility": "medium",
            "regime": "ranging",
            "conviction": 0.0,
            "summary_line": "Market State: insufficient data, default to neutral",
        }

    # ── 1. 趋势方向判定（加权） ──
    trend_scores = []
    conviction_factors = []
    for tf, data in available.items():
        macd = data["macd"]
        boll = data["boll"]
        close = data.get("close", 0)
        middle = boll.get("middle", 0) if boll else 0
        weight = _TF_TREND_WEIGHTS.get(tf, 0.1)

        # MACD 方向打分
        macd_score = 0.0
        if macd.get("histogram", 0) > 0:
            macd_score += 0.5
        if macd.get("crossover") == "bullish":
            macd_score += 0.5
        elif macd.get("crossover") == "bearish":
            macd_score -= 0.5
        if macd.get("histogram", 0) < 0:
            macd_score -= 0.5

        # 价格相对布林中轨位置
        price_score = 0.0
        if middle and close and boll:
            pos_pct = boll.get("position_pct", 50)
            if pos_pct > 60:
                price_score += 0.3
            elif pos_pct < 40:
                price_score -= 0.3
            if boll.get("position_label") == "touch_upper":
                price_score += 0.7
            elif boll.get("position_label") == "touch_lower":
                price_score -= 0.7

        tf_score = (macd_score + price_score) * weight
        trend_scores.append((tf_score, weight))

        # 长周期权重越高 → 贡献更多到 conviction
        conviction_factors.append((abs(macd_score + price_score), weight))

    total_trend = sum(ts for ts, _ in trend_scores)
    total_weight = sum(w for _, w in trend_scores)

    if total_trend > 0.15:
        trend = "uptrend"
    elif total_trend < -0.15:
        trend = "downtrend"
    else:
        trend = "sideways"

    # ── 2. 波动率判定（优先 1h，逐级 fallback） ──
    volatility = "medium"
    for tf in ("1h", "15m", "5m", "1d"):
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
                # 在 bandwidth > 0.10 时进一步细分
                if bw > 0.15:
                    volatility = "very_high"
                break

    # ── 3. 市场形态判定 ──
    # 多周期 MACD 直方图方向一致性
    directions = set()
    for tf in main_tfs:
        data = available.get(tf)
        if data:
            h = data["macd"].get("histogram", 0)
            directions.add("positive" if h > 0 else "negative" if h < 0 else "flat")

    # squeeze 统计——至少 2 个周期 squeeze 才算
    squeeze_count = sum(
        1 for tf in all_tfs
        if tf in available and available[tf].get("boll", {}).get("squeeze")
    )
    has_multi_squeeze = squeeze_count >= 2

    # 带宽扩张趋势（从 3m/5m 的带宽来判断近期是扩张还是收缩）
    bw_trend = "stable"
    short_tfs = ["3m", "5m"]
    bw_changes = []
    for tf in short_tfs:
        data = available.get(tf)
        if data and data.get("boll"):
            cur_bw = data["boll"].get("bandwidth", 0)
            prev_bw = data["boll"].get("_prev_bandwidth", cur_bw)
            if prev_bw > 0:
                bw_changes.append((cur_bw - prev_bw) / prev_bw)
    if bw_changes:
        avg_change = sum(bw_changes) / len(bw_changes)
        bw_trend = "expanding" if avg_change > 0.05 else ("contracting" if avg_change < -0.05 else "stable")

    # 判断 regime
    regime = "ranging"
    # 趋势条件：主力周期方向一致（全正或全负）+ 趋势分够强
    one_direction = len(directions) == 1 and "flat" not in directions
    if one_direction and abs(total_trend) > 0.25:
        regime = "trending"
    elif has_multi_squeeze:
        # 多周期 squeeze = 即将变盘
        regime = "transition"
    elif len(directions) <= 2 and "flat" not in directions and abs(total_trend) > 0.15:
        # 方向有分歧但趋势分不太弱
        if bw_trend == "expanding" and volatility in ("high", "very_high"):
            regime = "trending"  # 带宽扩张 + 高波动 = 趋势正在形成
        else:
            regime = "transition"
    elif abs(total_trend) < 0.10:
        regime = "ranging"

    # ── 4. 置信度（conviction）──
    # 长周期对趋势的影响力 / 总权重，越高 = 判定越可信
    main_weight = sum(_TF_TREND_WEIGHTS.get(tf, 0) for tf in main_tfs if tf in available)
    all_weight = sum(_TF_TREND_WEIGHTS.get(tf, 0) for tf in available)
    trend_strength = min(1.0, abs(total_trend) * 2)  # 0~1

    # direction uniformity：方向越一致 conviction 越高
    if len(directions) == 0:
        uniformity = 0.0
    elif len(directions) == 1 and "flat" not in directions:
        uniformity = 0.9
    elif len(directions) == 1:
        uniformity = 0.5  # 全是 flat
    elif len(directions) == 2 and "flat" in directions:
        uniformity = 0.4  # 有 flat 但有方向
    else:
        uniformity = 0.2  # 方向冲突

    conviction = round(
        trend_strength * 0.4
        + uniformity * 0.3
        + (main_weight / max(all_weight, 0.01)) * 0.3,
        3,
    )
    conviction = max(0.0, min(1.0, conviction))

    # ── 5. 摘要行 ──
    trend_labels = {"uptrend": "Uptrend ↑", "downtrend": "Downtrend ↓", "sideways": "Sideways →"}
    vol_labels = {
        "low": "Low", "medium": "Medium", "high": "High", "very_high": "Very High",
    }
    regime_labels = {
        "trending": "Trending — follow trend, avoid counter-trend entries",
        "ranging": "Ranging — mean-reversion, avoid chasing breakouts",
        "transition": "Transition — reduce position size, wait for confirmation",
    }

    squeeze_note = f" | {squeeze_count}x Squeeze ⚡" if has_multi_squeeze else ""
    conv_note = f" [conviction={conviction:.2f}]"
    summary = (
        f"Market: {trend_labels[trend]} | "
        f"Volatility: {vol_labels[volatility]} | "
        f"Regime: {regime_labels[regime]}"
        f"{squeeze_note}{conv_note}"
    )

    return {
        "trend": trend,
        "volatility": volatility,
        "regime": regime,
        "conviction": conviction,
        "has_squeeze": has_multi_squeeze,
        "squeeze_count": squeeze_count,
        "bw_trend": bw_trend,
        "summary_line": summary,
    }


def format_indicators_table(indicators: dict[str, dict]) -> str:
    """将多周期指标格式化为结构化表格文本（供 DeepSeek prompt 注入）

    相比之前版本：
    - 新增带宽对比行
    - 新增挤压/扩张状态行
    - 新增价格位置标签简化版
    - 去掉冗余的 J 值行（已被 K/D 覆盖）
    - 突出最重要的三列（15m/1h/1d）并在左侧加 3m/5m 小字参考
    """
    tfs = ["3m", "5m", "15m", "1h", "1d"]
    available = {tf: indicators.get(tf) for tf in tfs if tf in indicators and indicators[tf]}

    if not available:
        return "[Indicators] Waiting for data..."

    # 主周期：15m/1h/1d，副周期：3m/5m
    main_cols = ["15m", "1h", "1d"]
    side_cols = ["3m", "5m"]

    def _val(tf, key, subkey=None, fmt="{:.2f}", default="--"):
        d = available.get(tf)
        if not d:
            return default
        if subkey:
            v = d.get(key, {})
            val = v.get(subkey) if isinstance(v, dict) else None
        else:
            val = d.get(key)
        if val is None:
            return default
        try:
            return fmt.format(val)
        except (ValueError, TypeError):
            return str(val)

    def _label(tf, key, subkey, mapping, default="--"):
        d = available.get(tf)
        if not d:
            return default
        if subkey:
            v = d.get(key, {})
            val = v.get(subkey) if isinstance(v, dict) else None
        else:
            val = d.get(key)
        return mapping.get(str(val) if not isinstance(val, str) else val, str(val) if val else default)

    rows = []
    hdr = f"{'Indicator':<20} | {'3m':<10} {'5m':<10} | {'15m':<12} {'1h':<12} {'1d':<12}"
    rows.append(hdr)
    rows.append("-" * len(hdr))

    # ── MACD Histogram ──
    def _macd_hist(tf):
        d = available.get(tf)
        if d and d.get("macd"):
            h = d["macd"].get("histogram", 0)
            direction = d["macd"].get("hist_direction", "")
            arrow = "↑" if direction == "rising" else "↓" if direction == "falling" else "→"
            return f"{h:+.2f} {arrow}"
        return "--"

    def _macd_cross(tf):
        d = available.get(tf)
        if d and d.get("macd"):
            cross = d["macd"].get("crossover")
            if cross == "bullish":
                return "金叉↑"
            elif cross == "bearish":
                return "死叉↓"
            return "-"
        return "--"

    vals = [_macd_hist(tf) for tf in tfs]
    rows.append(f"MACD Histogram      | {vals[0]:<10} {vals[1]:<10} | {vals[2]:<12} {vals[3]:<12} {vals[4]:<12}")

    vals = [_macd_cross(tf) for tf in tfs]
    rows.append(f"MACD Cross          | {vals[0]:<10} {vals[1]:<10} | {vals[2]:<12} {vals[3]:<12} {vals[4]:<12}")

    # ── KDJ K/D ──
    def _kdj_kd(tf):
        d = available.get(tf)
        if d and d.get("kdj"):
            k = d["kdj"].get("k", 0)
            d_val = d["kdj"].get("d", 0)
            zone = d["kdj"].get("zone", "")
            tag = "🔥" if zone == "oversold" else "⚠️" if zone == "overbought" else ""
            return f"{k:.0f}/{d_val:.0f} {tag}"
        return "--"

    vals = [_kdj_kd(tf) for tf in tfs]
    rows.append(f"KDJ K/D             | {vals[0]:<10} {vals[1]:<10} | {vals[2]:<12} {vals[3]:<12} {vals[4]:<12}")

    # ── J Value ──
    def _j_val(tf):
        d = available.get(tf)
        if d and d.get("kdj"):
            j = d["kdj"].get("j", 50)
            if j >= 100:
                tag = " OB!"
            elif j <= 0:
                tag = " OS!"
            elif j > 80:
                tag = " high"
            elif j < 20:
                tag = " low"
            else:
                tag = ""
            return f"{j:.0f}{tag}"
        return "--"

    vals = [_j_val(tf) for tf in tfs]
    rows.append(f"J Value             | {vals[0]:<10} {vals[1]:<10} | {vals[2]:<12} {vals[3]:<12} {vals[4]:<12}")

    # ── Bollinger Position ──
    def _boll_pos(tf):
        d = available.get(tf)
        if d and d.get("boll"):
            pos = d["boll"].get("position_pct", 50)
            label = d["boll"].get("position_label", "inside")
            if label == "touch_upper":
                return f"{pos:.0f}% (上轨)"
            elif label == "touch_lower":
                return f"{pos:.0f}% (下轨)"
            elif pos > 65:
                return f"{pos:.0f}% (高位)"
            elif pos < 35:
                return f"{pos:.0f}% (低位)"
            return f"{pos:.0f}%"
        return "--"

    vals = [_boll_pos(tf) for tf in tfs]
    rows.append(f"Boll Position       | {vals[0]:<10} {vals[1]:<10} | {vals[2]:<12} {vals[3]:<12} {vals[4]:<12}")

    # ── Bollinger Bandwidth（新增！）──
    def _bw(tf):
        d = available.get(tf)
        if d and d.get("boll"):
            bw = d["boll"].get("bandwidth", 0)
            sqz = d["boll"].get("squeeze", False)
            return f"{bw:.2%}{' 🌀' if sqz else ''}"
        return "--"

    vals = [_bw(tf) for tf in tfs]
    rows.append(f"Boll Bandwidth      | {vals[0]:<10} {vals[1]:<10} | {vals[2]:<12} {vals[3]:<12} {vals[4]:<12}")

    return "\n".join(rows)
