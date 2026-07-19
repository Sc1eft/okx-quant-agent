"""AI analysis utilities for ETH market data — DeepSeek multi-dimension analysis.

Extracted from 9_EthereumLive.py for reuse across multiple pages.
"""
from __future__ import annotations
import re
from typing import Optional

import pandas as pd

from frontend.utils.eth_news import _fmt_relative_time
from indicators import (
    calc_macd_summary as _calc_macd,
    calc_kdj_summary as _calc_kdj,
    calc_boll_summary as _calc_boll,
)

__all__ = [
    "_AI_SYSTEM_PROMPT",
    "_AI_CHAT_SYSTEM_PROMPT",
    "_call_ai_analysis",
    "_call_ai_chat",
    "_build_ai_analysis_prompt",
    "_sanitize_ai_text",
    "_ticker_summary",
    "_summarize_klines",
    "_calc_macd",
    "_calc_kdj",
    "_calc_boll",
    "_summarize_macd",
    "_summarize_kdj",
    "_summarize_boll",
]


# ════════════════════════════════════════════════════════════════
# AI SYSTEM PROMPTS
# ════════════════════════════════════════════════════════════════

_AI_SYSTEM_PROMPT = """你是专业的加密货币交易分析师，综合技术面 + 基本面给出决策建议。

技术面依据：K线形态、量价关系、多周期趋势、关联币种联动。
基本面依据：最新新闻事件、政策动向、行业动态、市场叙事。

## 动态权重策略（不同维度按以下权重加权）

1. **短期技术面（15分钟 K 线）** — 权重高
   - 短线走势直接决定入场时机，近期量价信号最敏感

2. **中期技术面（1小时 K 线）** — 权重中高
   - 确认趋势方向，过滤短期噪音

3. **长期技术面（日线）** — 权重中高
   - 日线 MACD/KDJ/布林带已完整计算，趋势转折信号参考价值高
   - 日线 SMA50/SMA200 位置指示大级别牛熊分界线
   - 日线趋势与短中期方向一致时增强信号可信度

4. **MACD 指标（多周期）** — 权重高
   - 关注 MACD 金叉/死叉信号、柱线方向（扩大/缩小）、零轴位置
   - 多周期共振（如 15m + 1h 同时金叉）增强信号可信度
   - 顶背离 / 底背离是强转折信号

5. **KDJ 随机指标（多周期）** — 权重中高
   - K 线穿越 D 线（金叉/死叉）提供短期买卖信号
   - J 值超买（>=100）或超卖（<=0）预示拐点风险
   - 多周期 KDJ 方向一致时趋势可靠

6. **布林带（多周期）** — 权重中高
   - 价格触及上轨/下轨预示反转或延续信号
   - 布林收口（squeeze）预示大幅波动即将到来
   - 价格沿上轨/下轨运行说明趋势强劲

7. **日线 SMA 均线** — 权重中
   - SMA50 与 SMA200 的排列关系（金叉/死叉）指示牛熊转换
   - 当前价格在 SMA20/SMA50/SMA200 上方还是下方

8. **关联币种（BTC/SOL/DOGE）** — 权重中
   - BTC 强相关时提高权重，脱离联动时降低

9. **新闻基本面** — 动态权重（按时效和冲击力调整）：
   - 🚨 **6 小时内 + 高冲击主题**（监管政策 / 安全事件 / ETF /
        协议升级等）→ **最高权重**，可能完全改变短期方向
   - **6~24 小时** → 高权重，尚未完全 priced in
   - **>24 小时** → 低权重，市场已充分消化
   - **常规新闻**（合作、生态发展、观点评论）→ 正常权重
   - 无新闻时，fundamental_analysis 返回空字符串

## 分析原则
- 所有 wind 权重已在上方数据中体现，请综合判断
- 高冲击新闻出现时，technical_analysis 权重相应降低
- 市场情绪应与价格行为相互印证，不一致时优先参考价格行为

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
- 所有文本字段使用中文，不要使用代码格式（反引号、代码块等）
- 只返回JSON，不要包含其他文字"""

_AI_CHAT_SYSTEM_PROMPT = """你是专业的加密货币交易分析师，正在回答用户基于市场分析的追问。

根据已有市场数据和分析结论回答用户问题：
- 基于数据说话，不臆测
- 简洁直接，有依据
- 无法从现有数据判断时如实说明
- 所有回复使用中文"""


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════


def _sanitize_ai_text(text: str) -> str:
    """Cleanse markdown code formatting from AI response text for safe display."""
    if not text:
        return ""
    # Remove markdown code fences (```...``` with optional language tag)
    text = re.sub(r'```(?:\w+)?\s*\n?', '', text)
    text = text.replace('```', '')
    # Replace inline code (`...`) with plain text
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text.strip()


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


# ════════════════════════════════════════════════════════════════
# MACD / KDJ / BOLL CALCULATION
# ════════════════════════════════════════════════════════════════


def _summarize_macd(macd: dict | None, label: str) -> str:
    """将 MACD 计算结果压缩为一行文字摘要。"""
    if macd is None:
        return f"{label} MACD: 数据不足"
    emoji = macd.get("crossover") or ("" if macd.get("histogram", 0) >= 0 else "")
    if macd["crossover"] == "bullish":
        emoji = "🐂"
    elif macd["crossover"] == "bearish":
        emoji = "🐻"
    elif macd["histogram"] >= 0:
        emoji = "📈"
    else:
        emoji = "📉"

    cross_str = ""
    if macd["crossover"]:
        cross_str = {"bullish": "金叉↑", "bearish": "死叉↓"}.get(macd["crossover"], "")
    dir_str = macd["hist_direction"] == "rising" and "柱线扩大" or "柱线缩小"

    return (
        f"{label} MACD: MACD {macd['macd']:+.2f} / SIG {macd['signal']:+.2f} / "
        f"HIST {macd['histogram']:+.2f} {emoji} "
        f"{cross_str} {dir_str}"
    )


def _summarize_kdj(kdj: dict | None, label: str) -> str:
    """将 KDJ 计算结果压缩为一行文字摘要。"""
    if kdj is None:
        return f"{label} KDJ: 数据不足"
    zone_emoji = {"overbought": "⚠️超买", "oversold": "🔻超卖", "normal": ""}.get(kdj["zone"], "")
    cross_str = ""
    if kdj["k_cross_d"] == "bullish":
        cross_str = "K↑D金叉"
    elif kdj["k_cross_d"] == "bearish":
        cross_str = "K↓D死叉"

    return (
        f"{label} KDJ: K {kdj['k']:.1f} / D {kdj['d']:.1f} / J {kdj['j']:.1f} "
        f"{cross_str} {zone_emoji}"
    )


def _summarize_boll(boll: dict | None, label: str) -> str:
    """将布林带计算结果压缩为一行文字摘要。"""
    if boll is None:
        return f"{label} 布林带: 数据不足"

    pos = boll.get("position_label", "inside")
    pos_pct = boll.get("position_pct", 50)
    squeeze = boll.get("squeeze", False)

    pos_str_map = {
        "touch_upper": "触及上轨 🔺",
        "touch_lower": "触及下轨 🔻",
        "inside": f"轨内 {pos_pct:.0f}% 位置",
    }
    pos_str = pos_str_map.get(pos, f"轨内 {pos_pct:.0f}% 位置")

    squeeze_str = " 🌀布林收口" if squeeze else ""

    return (
        f"{label} 布林带: 上轨 {boll['upper']:.2f} / 中轨 {boll['middle']:.2f} / "
        f"下轨 {boll['lower']:.2f} 带宽 {boll['bandwidth']:.2%} "
        f"价格{pos_str}{squeeze_str}"
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


# ════════════════════════════════════════════════════════════════
# PROMPT BUILDING
# ════════════════════════════════════════════════════════════════


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

    # ── 日线均线（大级别趋势参考） ──
    if klines_1d is not None and len(klines_1d) >= 20:
        close_d = klines_1d["close"].astype(float)
        sma20 = close_d.rolling(20).mean().iloc[-1]
        lines.append(f"日线 SMA20: {sma20:.2f}")
        if len(close_d) >= 50:
            sma50 = close_d.rolling(50).mean().iloc[-1]
            lines.append(f"日线 SMA50: {sma50:.2f}")
        if len(close_d) >= 200:
            sma200 = close_d.rolling(200).mean().iloc[-1]
            lines.append(f"日线 SMA200: {sma200:.2f}")
        lines.append("")

    # ── MACD 指标 ──
    lines.append("### MACD 指标")
    lines.append(_summarize_macd(_calc_macd(klines_15m), "短期(15分钟)"))
    lines.append(_summarize_macd(_calc_macd(klines_1h), "中期(1小时)"))
    lines.append(_summarize_macd(_calc_macd(klines_1d), "长期(日线)"))
    lines.append("")

    # ── KDJ 指标 ──
    lines.append("### KDJ 随机指标")
    lines.append(_summarize_kdj(_calc_kdj(klines_15m), "短期(15分钟)"))
    lines.append(_summarize_kdj(_calc_kdj(klines_1h), "中期(1小时)"))
    lines.append(_summarize_kdj(_calc_kdj(klines_1d), "长期(日线)"))
    lines.append("")

    # ── 布林带指标 ──
    lines.append("### 布林带")
    lines.append(_summarize_boll(_calc_boll(klines_15m), "短期(15分钟)"))
    lines.append(_summarize_boll(_calc_boll(klines_1h), "中期(1小时)"))
    lines.append(_summarize_boll(_calc_boll(klines_1d), "长期(日线)"))
    lines.append("")

    lines.append("### 关联币种行情")
    lines.append(_ticker_summary("BTC", btc_ticker))
    lines.append(_ticker_summary("SOL", sol_ticker))
    lines.append(_ticker_summary("DOGE", doge_ticker))
    lines.append("")

    # ── 新闻与政策基本面（含时效性标记）──
    if news:
        lines.append("### 近期新闻与政策（按时效排序）")
        for i, item in enumerate(news, 1):
            ts = item.get("timestamp", "")
            recency = _fmt_relative_time(ts) if ts else ""
            time_tag = f" [{recency}]" if recency else ""
            lines.append(f"{i}. [{item['source']}]{time_tag} {item['title']}")
        lines.append("")

    lines.append("---")
    lines.append(
        "请基于以上技术面数据 + 新闻基本面，给出 ETH 的综合多空分析。"
    )
    lines.append(
        "注意评估新闻事件对 ETH 价格的潜在多空影响。"
    )
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# API CALLS
# ════════════════════════════════════════════════════════════════


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
    import json

    prompt = _build_ai_analysis_prompt(
        ticker, klines_15m, klines_1h, klines_1d,
        btc_ticker, sol_ticker, doge_ticker,
        news=news,
    )
    try:
        from openai import OpenAI

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
        # 尝试提取 JSON（先找 ```json 围栏，再找首尾 { }）
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        else:
            # Fallback: 找第一个 { 和最后一个 }
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1 and end > start:
                content = content[start:end + 1]
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
            "summary": "AI 分析暂时不可用",
            "key_evidence": [f"API 调用失败: {e}"],
            "risk_warnings": ["AI 服务异常，请稍后重试"],
            "technical_analysis": "",
            "market_sentiment": "",
            "fundamental_analysis": "",
        }


def _call_ai_chat(
    question: str,
    context: dict | None,
    chat_history: list[dict],
    cfg,
) -> str:
    """调用 DeepSeek 回答用户对市场分析的追问"""
    from openai import OpenAI

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
                f"- [{n['source']}] {n['title']}" + (f" ({_fmt_relative_time(n.get('timestamp', ''))})" if n.get('timestamp') else "") for n in news
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
