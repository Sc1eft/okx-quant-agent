"""
自然语言 → 结构化交易规则

用户用中文描述策略（如"当RSI低于30时买入，高于70时卖出"），
此模块调用 DeepSeek API 将其解析为可执行的 JSON 规则。
API 不可用时降级为本地关键词匹配。

支持 30+ 种策略模式和丰富的中文表述。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import Config

logger = logging.getLogger("agent.interpreter")

# ════════════════════════════════════════════════════════════════
# 辅助函数 — 时间周期
# ════════════════════════════════════════════════════════════════


def _extract_timeframe(text: str) -> str:
    """从描述中提取建议的 K 线周期"""
    tf_map = {
        "秒级": "1s", "秒": "1s",
        "1分钟": "1m", "一分钟": "1m", "1分": "1m", "一分": "1m",
        "2分钟": "2m", "二分钟": "2m", "2分": "2m",
        "5分钟": "5m", "五分钟": "5m", "5分": "5m",
        "15分钟": "15m", "十五分钟": "15m", "15分": "15m",
        "30分钟": "30m", "三十分钟": "30m",
        "1小时": "1h", "一小时": "1h", "1h": "1h", "小时": "1h",
        "2小时": "2h", "二小时": "2h",
        "4小时": "4h", "四小时": "4h", "4h": "4h",
        "6小时": "6h", "六小时": "6h",
        "12小时": "12h", "十二小时": "12h",
        "日线": "1d", "天": "1d", "日": "1d", "1天": "1d", "一天": "1d",
        "2天": "2d", "二天": "2d",
        "周线": "1w", "周": "1w",
    }
    # 按 key 长度降序排序，防止短 key（如 "5分钟"）误匹配长 key（如 "15分钟"）
    for key, val in sorted(tf_map.items(), key=lambda x: -len(x[0])):
        if key.lower() in text.lower():
            return val
    return "15m"


# ════════════════════════════════════════════════════════════════
# 辅助函数 — 风控参数提取
# ════════════════════════════════════════════════════════════════


def _extract_risk_params(text: str) -> dict:
    """从文本中提取止损/止盈/仓位/移动止损等参数"""
    params: Dict[str, Any] = {
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "position_size_pct": 10.0,
        "trailing_stop_activation_pct": None,
        "trailing_stop_distance_pct": None,
        "position_timeout_bars": None,
    }

    # ── 止损 ──
    for pat in [
        r"止损[约为]?(\d+(?:\.\d+)?)\s*%?",
        r"(\d+(?:\.\d+)?)\s*%?\s*止[损亏]",
        r"亏损[到超达]?(\d+(?:\.\d+)?)\s*%?\s*(?:止损|平仓)",
        r"亏[了]?(\d+(?:\.\d+)?)\s*%?\s*(?:就|则|便)?(?:止损|平|出)",
        r"(?:最大|最多|最大允许).{0,4}(?:亏损|回撤|损失)[约]?(\d+(?:\.\d+)?)\s*%?",
        r"(?:回撤|drawdown)[超超过]?(\d+(?:\.\d+)?)\s*%?\s*(?:止损|平仓|出场)?",
    ]:
        m = re.search(pat, text)
        if m:
            params["stop_loss_pct"] = abs(float(m.group(1)))
            break

    # ── 止盈 ──
    for pat in [
        r"止盈[约为]?(\d+(?:\.\d+)?)\s*%?",
        r"(\d+(?:\.\d+)?)\s*%?\s*止盈",
        r"目标[盈利]?[约达]?(\d+(?:\.\d+)?)\s*%?",
        r"盈利[到超达]?(\d+(?:\.\d+)?)\s*%?\s*(?:止盈|平仓|出)",
        r"赚[了]?(\d+(?:\.\d+)?)\s*%?\s*(?:就|则|便)?(?:止盈|平|出)",
        r"(?:止盈|获利|目标).{0,4}(?:设在?|为)?(\d+(?:\.\d+)?)\s*%?",
    ]:
        m = re.search(pat, text)
        if m:
            params["take_profit_pct"] = abs(float(m.group(1)))
            break

    # ── 仓位比例 ──
    m = re.search(r"(\d+(?:\.\d+)?)\s*%[的的]?(?:资金|仓位|本金|账户)", text)
    if m:
        params["position_size_pct"] = float(m.group(1))
    else:
        position_keywords = [
            (r"满仓|全仓|all.?in|梭哈|全部资金|all in", 100.0),
            (r"重仓|大仓位|七成|八成|70%|80%", 75.0),
            (r"半仓|一半|五成|半|50%|5成", 50.0),
            (r"轻仓|小仓位|三成|少量|小部分|30%", 25.0),
            (r"迷你仓|微仓|试仓|一成|10%", 10.0),
        ]
        for pat, pct in position_keywords:
            if re.search(pat, text, re.I):
                params["position_size_pct"] = pct
                break

    # ── 移动止损 / 追踪止损 ──
    for pat in [
        r"(?:移动|追踪|浮动|动态|trailing)\s*止损[激活启动]?(?:距离|幅度|设为)?[约]?(\d+(?:\.\d+)?)\s*%?",
        r"(\d+(?:\.\d+)?)\s*%?\s*(?:移动|追踪|浮动|动态|trailing)\s*止损",
        r"(?:从最高|从高点|从峰值).{0,8}(?:回落|回撤|回调)(\d+(?:\.\d+)?)\s*%?\s*(?:止损|平仓|出场)",
        r"(?:回落|回撤|回调)(\d+(?:\.\d+)?)\s*%?\s*(?:止损|平仓|出场)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            dist = float(m.group(1))
            params["trailing_stop_distance_pct"] = dist
            params["trailing_stop_activation_pct"] = max(dist * 2.0, 0.5)
            break

    m = re.search(
        r"盈利[超超过]?(\d+(?:\.\d+)?)\s*%[后以]?.{0,6}"
        r"(?:移动|追踪|浮动|动态)\s*止损[距离幅度]?[约]?(\d+(?:\.\d+)?)\s*%?",
        text, re.I,
    )
    if m:
        params["trailing_stop_activation_pct"] = float(m.group(1))
        params["trailing_stop_distance_pct"] = float(m.group(2))

    # ── 持仓超时 ──
    for pat in [
        r"(?:持仓|持有|持仓时间)[时超过]*(\d+)\s*根?\s*(?:K线|k线|candle|bar|蜡烛)",
        r"(\d+)\s*根?\s*(?:K线|k线|candle|bar|蜡烛)(?:后|以内|之后)(?:平仓|出场|止盈|止损)",
        r"(?:超时|超期|timeout)[超过]*(\d+)\s*根?\s*(?:K线|k线|candle|bar)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            params["position_timeout_bars"] = int(m.group(1))
            break

    # ── 杠杆 ──
    m = re.search(r"(\d+(?:\.\d+)?)\s*倍\s*(?:杠杆|leverage)", text, re.I)
    if m:
        params["leverage"] = float(m.group(1))

    # ── 单笔最大亏损 ──
    m = re.search(r"(?:单笔|每笔|每次).{0,4}(?:最大|最多|亏损|损失).*?(\d+(?:\.\d+)?)\s*%", text, re.I)
    if m:
        params["max_loss_pct"] = float(m.group(1))

    # ── 冷却（同方向间隔） ──
    for pat in [
        r"(?:同方向|同向).{0,6}(?:冷却|间隔|cooldown).*?(\d+)",
        r"(?:冷却|cooldown).*?(\d+)\s*(?:根|个|K线|candle|bar)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            params["cooldown_bars"] = int(m.group(1))
            break
    if "cooldown_bars" not in params:
        if re.search(r"(?:2小时|两小时|2h|二小时|2H)", text):
            params["cooldown_bars"] = 8

    # ── 波动率阈值 ──
    m = re.search(r"(?:实体|波幅|波动).{0,4}(?:大于|超过|>)\s*\$?(\d+(?:\.\d+)?)", text, re.I)
    if m:
        params["volatility_body_threshold"] = float(m.group(1))
    m = re.search(r"(?:连续|两|2).{0,6}(?:根|个).{0,8}(?:和|合计|之和).{0,4}(?:大于|超过|>)\s*\$?(\d+(?:\.\d+)?)", text, re.I)
    if m:
        params["volatility_sum_threshold"] = float(m.group(1))

    return params


# ════════════════════════════════════════════════════════════════
# 辅助函数 — 策略构建
# ════════════════════════════════════════════════════════════════


def _int_groups(m: re.Match) -> list[int]:
    """从匹配组中提取所有非空的整数值"""
    return [int(g) for g in m.groups() if g is not None and g.isdigit()]


def _build_ma_golden(m: re.Match) -> dict:
    """MA金叉买入"""
    gs = _int_groups(m)
    p1 = gs[0] if len(gs) >= 1 else 5
    p2 = gs[1] if len(gs) >= 2 else 20
    return {
        "strategy_name": f"MA{p1}金叉MA{p2}买入",
        "entry_conditions": [{"indicator": f"sma_{p1}", "params": {"period": p1},
                              "comparison": "crosses_above",
                              "cross_with": f"sma_{p2}", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }


def _build_ma_death(m: re.Match) -> dict:
    """MA死叉卖出"""
    gs = _int_groups(m)
    p1 = gs[0] if len(gs) >= 1 else 5
    p2 = gs[1] if len(gs) >= 2 else 20
    return {
        "strategy_name": f"MA{p1}死叉MA{p2}卖出",
        "exit_conditions": [{"indicator": f"sma_{p1}", "params": {"period": p1},
                             "comparison": "crosses_below",
                             "cross_with": f"sma_{p2}", "value": None, "action": "sell"}],
        "entry_conditions": [],
    }


def _build_ma_trend(m: re.Match) -> dict:
    """双均线趋势跟踪"""
    gs = _int_groups(m)
    if len(gs) >= 3:
        ps, pl1, pl2 = gs[0], gs[1], gs[2]
    elif len(gs) >= 2:
        ps, pl1 = gs[0], gs[1]
        pl2 = pl1
    else:
        ps, pl1, pl2 = 5, 10, 10
    return {
        "strategy_name": f"MA{ps}/{pl1}趋势跟踪",
        "entry_conditions": [{"indicator": f"sma_{ps}", "params": {"period": ps},
                              "comparison": "crosses_above",
                              "cross_with": f"sma_{pl1}", "value": None, "action": "buy"}],
        "exit_conditions": [{"indicator": f"sma_{ps}", "params": {"period": ps},
                             "comparison": "crosses_below",
                             "cross_with": f"sma_{pl2}", "value": None, "action": "sell"}],
    }


def _build_price_up_ma(m: re.Match) -> dict:
    """价格上穿均线买入"""
    gs = _int_groups(m)
    p = gs[0] if gs else 20
    return {
        "strategy_name": f"突破MA{p}买入",
        "entry_conditions": [{"indicator": "close", "params": {},
                              "comparison": "crosses_above",
                              "cross_with": f"sma_{p}", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }


def _build_price_dn_ma(m: re.Match) -> dict:
    """价格下穿均线卖出"""
    gs = _int_groups(m)
    p = gs[0] if gs else 20
    return {
        "strategy_name": f"跌破MA{p}卖出",
        "exit_conditions": [{"indicator": "close", "params": {},
                             "comparison": "crosses_below",
                             "cross_with": f"sma_{p}", "value": None, "action": "sell"}],
        "entry_conditions": [],
    }


def _build_ema_golden(m: re.Match) -> dict:
    """EMA金叉买入"""
    p1, p2 = int(m.group(1)), int(m.group(2))
    return {
        "strategy_name": f"EMA{p1}金叉EMA{p2}买入",
        "entry_conditions": [{"indicator": f"ema_{p1}", "params": {"period": p1},
                              "comparison": "crosses_above",
                              "cross_with": f"ema_{p2}", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }


def _build_ema_death(m: re.Match) -> dict:
    """EMA死叉卖出"""
    p1, p2 = int(m.group(1)), int(m.group(2))
    return {
        "strategy_name": f"EMA{p1}死叉EMA{p2}卖出",
        "exit_conditions": [{"indicator": f"ema_{p1}", "params": {"period": p1},
                             "comparison": "crosses_below",
                             "cross_with": f"ema_{p2}", "value": None, "action": "sell"}],
        "entry_conditions": [],
    }


def _build_ma_rsi_filter(m: re.Match) -> Optional[dict]:
    """MA金叉 + RSI>XX 组合"""
    gs = _int_groups(m)
    if len(gs) < 3:
        return None
    p1, p2, rv = gs[0], gs[1], gs[2]
    return {
        "strategy_name": f"MA{p1}金叉+RSI>{rv}买入",
        "entry_conditions": [
            {"indicator": f"sma_{p1}", "params": {"period": p1},
             "comparison": "crosses_above",
             "cross_with": f"sma_{p2}", "value": None, "action": "buy"},
            {"indicator": "rsi_14", "params": {"period": 14},
             "comparison": "greater_than", "value": rv, "action": "buy"},
        ],
        "exit_conditions": [],
        "_condition_logic": "all",
    }


def _build_volatility_contrarian(m: re.Match) -> dict:
    """波动率触发反向策略"""
    return {
        "strategy_name": "波动率触发反向策略",
        "_strategy_type": "volatility_contrarian",
        "entry_conditions": [],
        "exit_conditions": [],
        "_condition_logic": "any",
        "risk_params": {
            "stop_loss_pct": 1.25,
            "take_profit_pct": None,
            "position_size_pct": None,
            "trailing_stop_activation_pct": None,
            "trailing_stop_distance_pct": None,
            "position_timeout_bars": 16,
            # 波动率策略专用参数
            "leverage": 5.0,
            "max_loss_pct": 3.0,
            "volatility_body_threshold": 15.0,
            "volatility_sum_threshold": 20.0,
            "cooldown_bars": 8,
        },
        "_notes": "15minK线实体>$15或连续2根之和>$20触发，反向开仓。风险预算仓位，多级移动止盈，同方向冷却。",
    }


# ════════════════════════════════════════════════════════════════
# 策略模式列表（按优先级排序）
# ════════════════════════════════════════════════════════════════
# 规则：具体 → 一般。组合条件最先，单条件最后。
# ════════════════════════════════════════════════════════════════

_STRATEGY_PATTERNS: List[Tuple[re.Pattern, callable]] = [

    # ════════════════════════════════════════
    # PRIORITY 0 — 波动率触发反向策略（最高优先级）
    # ════════════════════════════════════════

    (re.compile(
        r"(?:"
        r"波动率|波动性|volatility|波幅|震幅|振幅|实体大小|实体波动"
        r")"
        r".*?"
        r"(?:反向|逆向|逆势|contrarian|contra)"
        r".*?"
        r"(?:策略|交易|开仓|下单|开多|开空)",
        re.I | re.DOTALL),
     _build_volatility_contrarian),

    (re.compile(
        r"(?:"
        r"实体.*?(?:大于|超过|>|≥)\s*\$?(\d+(?:\.\d+)?)"
        r"|15分钟.*?(?:实体|波幅)"
        r"|连续.*?(?:2|二|两).*?(?:根|个).*?(?:和|合计|之和).*?(?:大于|超过|>)\s*\$?(\d+(?:\.\d+)?)"
        r"|波动.*?(?:触发|入场|开仓)"
        r")",
        re.I | re.DOTALL),
     _build_volatility_contrarian),

    (re.compile(
        r"(?:"
        r"实体|波动|body|volatility"
        r").*?"
        r"(?:反向|逆向|逆势)"
        r".*?(?:开|建|入场|触发)",
        re.I | re.DOTALL),
     _build_volatility_contrarian),

    # ════════════════════════════════════════
    # PRIORITY 1 — 组合条件（含"且"）
    # ════════════════════════════════════════

    # RSI低于XX + MACD金叉 → 买入
    (re.compile(
        r"(?:rsi|RSI|相对强弱).*?"
        r"(?:低于|小于|＜)\s*(\d+).*?"
        r"(?:且|并且|同时|而且|and|AND|、|以及|加上|并)"
        r".*?(?:macd|MACD).*?(?:金叉|上穿|上穿零轴|上穿0轴)"
        r".*?(?:买入|做多|开多)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI+MACD金叉买入",
        "entry_conditions": [
            {"indicator": "rsi_14", "params": {"period": 14},
             "comparison": "less_than", "value": int(m.group(1)), "action": "buy"},
            {"indicator": "macd", "params": {},
             "comparison": "crosses_above",
             "cross_with": "macd_signal", "value": None, "action": "buy"},
        ],
        "exit_conditions": [],
        "_condition_logic": "all",
    }),

    # RSI高于XX + MACD死叉 → 卖出
    (re.compile(
        r"(?:rsi|RSI|相对强弱).*?"
        r"(?:高于|大于|＞)\s*(\d+).*?"
        r"(?:且|并且|同时|而且|and|AND|、|以及|加上|并)"
        r".*?(?:macd|MACD).*?(?:死叉|下穿|下穿零轴|下穿0轴)"
        r".*?(?:卖出|做空|开空)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI+MACD死叉卖出",
        "exit_conditions": [
            {"indicator": "rsi_14", "params": {"period": 14},
             "comparison": "greater_than", "value": int(m.group(1)), "action": "sell"},
            {"indicator": "macd", "params": {},
             "comparison": "crosses_below",
             "cross_with": "macd_signal", "value": None, "action": "sell"},
        ],
        "entry_conditions": [],
        "_condition_logic": "all",
    }),

    # MA金叉 + RSI>XX → 买入
    (re.compile(
        r".*?(?:MA|ma|SMA|sma|均线)\s*(\d+).*?(?:上穿|金叉)\s*(?:MA|ma|SMA|sma|均线)\s*(\d+)"
        r".*?(?:且|并且|同时|而且|and|AND|、|以及|加上|并)"
        r".*?(?:rsi|RSI).*?(?:大于|高于|＞)\s*(\d+)"
        r".*?(?:买入|做多|开多)",
        re.I | re.DOTALL),
     _build_ma_rsi_filter),

    # ════════════════════════════════════════
    # PRIORITY 2 — MACD 完整策略
    # ════════════════════════════════════════

    (re.compile(
        r"macd.*?(?:金叉|上穿).*?(?:买入|做多|开多).*?"
        r"(?:macd\s*)?(?:死叉|下穿).*?(?:卖出|做空|开空|平多)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD金死叉策略",
        "entry_conditions": [{"indicator": "macd", "params": {},
                              "comparison": "crosses_above",
                              "cross_with": "macd_signal", "value": None, "action": "buy"}],
        "exit_conditions": [{"indicator": "macd", "params": {},
                             "comparison": "crosses_below",
                             "cross_with": "macd_signal", "value": None, "action": "sell"}],
    }),

    # ════════════════════════════════════════
    # PRIORITY 3 — MACD 零轴
    # ════════════════════════════════════════

    (re.compile(
        r"macd\s*(?:的|之|中)?\s*(?:dif|DIFF|diff|快线|白线)?\s*"
        r"(?:上穿|升穿|突破|站上|回到|转正|翻红|上破)"
        r"\s*(?:零轴|0轴|0线|零线|零位)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD上穿零轴买入",
        "entry_conditions": [{"indicator": "macd", "params": {},
                              "comparison": "crosses_above", "value": 0, "action": "buy"}],
        "exit_conditions": [],
    }),

    (re.compile(
        r"macd\s*(?:的|之|中)?\s*(?:dif|DIFF|diff|快线|白线)?\s*"
        r"(?:下穿|跌破|跌穿|回到|转负|翻绿|下破)"
        r"\s*(?:零轴|0轴|0线|零线|零位)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD下穿零轴卖出",
        "exit_conditions": [{"indicator": "macd", "params": {},
                             "comparison": "crosses_below", "value": 0, "action": "sell"}],
        "entry_conditions": [],
    }),

    # ════════════════════════════════════════
    # PRIORITY 4 — MACD 柱状图
    # ════════════════════════════════════════

    (re.compile(
        r"macd\s*(?:柱|柱状图|直方图|bars?|histogram|红绿柱|能量柱)\s*"
        r"(?:转正|翻红|转为正|变红|变为正|由负转正|大于0|>0|变成红色)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD柱转正买入",
        "entry_conditions": [{"indicator": "macd_histogram", "params": {},
                              "comparison": "crosses_above", "value": 0, "action": "buy"}],
        "exit_conditions": [],
    }),

    (re.compile(
        r"macd\s*(?:柱|柱状图|直方图|bars?|histogram|红绿柱|能量柱)\s*"
        r"(?:转负|翻绿|转为负|变绿|变为负|由正转负|小于0|<0|变成绿色)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD柱转负卖出",
        "exit_conditions": [{"indicator": "macd_histogram", "params": {},
                             "comparison": "crosses_below", "value": 0, "action": "sell"}],
        "entry_conditions": [],
    }),

    # ════════════════════════════════════════
    # PRIORITY 5 — MACD 简单金叉/死叉
    # ════════════════════════════════════════

    (re.compile(
        r"macd\s*(?:的|之|中)?\s*(?:快线|dif|DIF|DIFF|diff|白线)?\s*"
        r"(?:上穿|金叉|升穿|向上突破|向上穿越|黄金交叉)"
        r"\s*(?:慢线|dea|DEA|信号线|signal|黄线)?",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD金叉买入",
        "entry_conditions": [{"indicator": "macd", "params": {},
                              "comparison": "crosses_above",
                              "cross_with": "macd_signal", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }),

    (re.compile(
        r"macd\s*(?:的|之|中)?\s*(?:快线|dif|DIF|DIFF|diff|白线)?\s*"
        r"(?:下穿|死叉|跌穿|向下跌破|死亡交叉)"
        r"\s*(?:慢线|dea|DEA|信号线|signal|黄线)?",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD死叉卖出",
        "exit_conditions": [{"indicator": "macd", "params": {},
                             "comparison": "crosses_below",
                             "cross_with": "macd_signal", "value": None, "action": "sell"}],
        "entry_conditions": [],
    }),

    # ════════════════════════════════════════
    # PRIORITY 6 — EMA（先于 MA，防误抓）
    # ════════════════════════════════════════

    (re.compile(
        r"(?:EMA|ema|指数移动平均|指数平均)\s*(\d+).*?"
        r"(?:上穿|金叉|升穿).*?"
        r"(?:EMA|ema|指数移动平均|指数平均)\s*(\d+)",
        re.I | re.DOTALL),
     _build_ema_golden),

    (re.compile(
        r"(?:EMA|ema|指数移动平均|指数平均)\s*(\d+).*?"
        r"(?:下穿|死叉|跌穿).*?"
        r"(?:EMA|ema|指数移动平均|指数平均)\s*(\d+)",
        re.I | re.DOTALL),
     _build_ema_death),

    # ════════════════════════════════════════
    # PRIORITY 7 — MA 趋势跟踪（金叉入+死叉出）
    # ════════════════════════════════════════

    (re.compile(
        r".*?"
        r"(?:(?:MA|ma|SMA|sma|均线|日线|移动平均|平均线|线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|日线|移动平均|平均线|线))"
        r".*?"
        r"(?:上穿|金叉|升穿|向上突破).*?"
        r"(?:(?:MA|ma|SMA|sma|均线|日线|移动平均|平均线|线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|日线|移动平均|平均线|线))"
        r".*?"
        r"(?:买入|做多|开多).*?"
        r"(?:下穿|死叉|跌穿|向下跌破)"
        r"(?:"
        r"(?:(?:MA|ma|SMA|sma|均线|日线|移动平均|平均线|线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|日线|移动平均|平均线|线))"
        r")?"
        r".*?"
        r"(?:卖出|做空|开空|平多)",
        re.I | re.DOTALL),
     _build_ma_trend),

    # ════════════════════════════════════════
    # PRIORITY 8 — MA 金叉 / 死叉
    # ════════════════════════════════════════

    (re.compile(
        r"(?:"
        r"(?:MA|ma|SMA|sma|均线|日均线|日线|移动平均|移动均线|平均线|线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|日均线|移动平均|平均线|线)"
        r"|(?:短期|短周期|短线|快线)\s*MA"
        r").*?"
        r"(?:上穿|金叉|升穿|向上突破|上传|上破|穿过|上交叉|黄金交叉|cross)"
        r".*?"
        r"(?:"
        r"(?:MA|ma|SMA|sma|均线|日均线|日线|移动平均|移动均线|平均线|线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|日均线|移动平均|平均线|线)"
        r"|(?:长期|长周期|长线|慢线)\s*MA"
        r")",
        re.I | re.DOTALL),
     _build_ma_golden),

    (re.compile(
        r"(?:"
        r"(?:MA|ma|SMA|sma|均线|日均线|日线|移动平均|移动均线|平均线|线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|日均线|移动平均|平均线|线)"
        r"|(?:短期|短周期|短线|快线)\s*MA"
        r").*?"
        r"(?:下穿|死叉|跌穿|向下跌破|下破|下交叉|死亡交叉)"
        r".*?"
        r"(?:"
        r"(?:MA|ma|SMA|sma|均线|日均线|日线|移动平均|移动均线|平均线|线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|日均线|移动平均|平均线|线)"
        r"|(?:长期|长周期|长线|慢线)\s*MA"
        r")",
        re.I | re.DOTALL),
     _build_ma_death),

    # ════════════════════════════════════════
    # PRIORITY 9 — 价格突破/跌破 均线
    # ════════════════════════════════════════

    (re.compile(
        r"(?:价格|价|收盘价|行情|price|当前价|股价)?\s*"
        r"(?:上穿|突破|站上|站稳|升穿|穿过|超过|涨破|收于.*?之上|向上突破)"
        r"\s*(?:"
        r"(?:MA|ma|均线|SMA|sma|EMA|ema|移动平均|平均线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|移动平均|平均线)"
        r")",
        re.I | re.DOTALL),
     _build_price_up_ma),

    (re.compile(
        r"(?:价格|价|收盘价|行情|price|当前价|股价)?\s*"
        r"(?:下穿|跌破|跌穿|下破|收于.*?之下|向下跌破|破位|失守)"
        r"\s*(?:"
        r"(?:MA|ma|均线|SMA|sma|EMA|ema|移动平均|平均线)\s*(\d+)"
        r"|(\d+)\s*(?:日|根|期)?\s*(?:MA|ma|均线|移动平均|平均线)"
        r")",
        re.I | re.DOTALL),
     _build_price_dn_ma),

    # ════════════════════════════════════════
    # PRIORITY 10 — 突破前高 / 跌破前低
    # ════════════════════════════════════════

    (re.compile(
        r"(?:价格|价|突破|涨破)\s*(?:近期|前期|前|阶段|历史|N?)\s*(?:高点|高位|高|最高|阻力|压力)"
        r".{0,8}(?:买入|做多|开多|long|追涨)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "突破前高买入",
        "entry_conditions": [{"indicator": "close", "params": {},
                              "comparison": "greater_than", "value": None, "action": "buy"}],
        "exit_conditions": [],
        "_notes": "突破前高需要在on_bar中获取近期最高价",
    }),

    (re.compile(
        r"(?:价格|价|跌破)\s*(?:近期|前期|前|阶段|历史|N?)\s*(?:低点|低位|低|最低|支撑)"
        r".{0,8}(?:卖出|做空|开空|short|追空)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "跌破前低卖出",
        "exit_conditions": [{"indicator": "close", "params": {},
                             "comparison": "less_than", "value": None, "action": "sell"}],
        "entry_conditions": [],
        "_notes": "跌破前低需要在on_bar中获取最近N根K线的最低价",
    }),

    # ════════════════════════════════════════
    # PRIORITY 11 — RSI 均值回归（双条件）
    # ════════════════════════════════════════

    (re.compile(
        r"(?:rsi|RSI|相对强弱).*?"
        r"(?:低于|小于|＜|跌破|下破|跌到|跌至|回落至|回落到|向下|下方|以下|触碰|触及|回踩)\s*(\d+).*?"
        r"(?:买入|做多|开多|买多|建多|long|低吸|抄底).*?"
        r"(?:rsi\s*)?"
        r"(?:高于|大于|＞|升破|上破|涨到|涨至|反弹至|反弹到|向上|上方|以上|触碰|触及|突破)\s*(\d+).*?"
        r"(?:卖出|做空|开空|卖空|short|平多|平仓|逃顶|高抛)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI均值回归",
        "entry_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                              "comparison": "less_than", "value": int(m.group(1)), "action": "buy"}],
        "exit_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                             "comparison": "greater_than", "value": int(m.group(2)), "action": "sell"}],
    }),

    # 反序：RSI高于XX卖，低于XX买
    (re.compile(
        r"(?:rsi|RSI|相对强弱).*?"
        r"(?:高于|大于|＞|升破|上破|涨到|涨至|向上|上方|以上|超过)\s*(\d+).*?"
        r"(?:卖出|做空|开空|卖空|short|卖)"
        r".*?"
        r"(?:rsi\s*)?"
        r"(?:低于|小于|＜|跌破|下破|跌到|跌至|向下|下方|以下)\s*(\d+).*?"
        r"(?:买入|做多|开多|买多|long|低吸|抄底|买)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI反向均值回归",
        "entry_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                              "comparison": "less_than", "value": int(m.group(2)), "action": "buy"}],
        "exit_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                             "comparison": "greater_than", "value": int(m.group(1)), "action": "sell"}],
    }),

    # ════════════════════════════════════════
    # PRIORITY 12 — RSI 超卖买入
    # ════════════════════════════════════════

    (re.compile(
        r"(?:rsi|RSI|相对强弱)\s*"
        r"(?:低于|小于|＜|跌破|下破|跌到|跌至|在.*?以下|下方|以下|向下突破|触碰|触及|回踩|超过|突破)\s*(\d+).*?"
        r"(?:买入|做多|开多|买多|建多|long|低吸|抄底|建仓|入场)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI超卖买入",
        "entry_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                              "comparison": "less_than", "value": int(m.group(1)), "action": "buy"}],
        "exit_conditions": [],
    }),

    (re.compile(r"(?:rsi|RSI)\s*(?:进入|在|处于)?\s*(?:超卖|超卖区域|超卖区|超卖状态)", re.I),
     lambda m: {
        "strategy_name": "RSI超卖买入",
        "entry_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                              "comparison": "less_than", "value": 30, "action": "buy"}],
        "exit_conditions": [],
    }),

    # ════════════════════════════════════════
    # PRIORITY 13 — RSI 超买卖出
    # ════════════════════════════════════════

    (re.compile(
        r"(?:rsi|RSI|相对强弱)\s*"
        r"(?:高于|大于|＞|升破|上破|涨到|涨至|在.*?以上|上方|以上|向上突破|触碰|触及|超过|突破)\s*(\d+).*?"
        r"(?:卖出|做空|开空|卖空|short|平多|平仓|逃顶|高抛|出场)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI超买卖出",
        "exit_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                             "comparison": "greater_than", "value": int(m.group(1)), "action": "sell"}],
        "entry_conditions": [],
    }),

    (re.compile(r"(?:rsi|RSI)\s*(?:进入|在|处于)?\s*(?:超买|超买区域|超买区|超买状态)", re.I),
     lambda m: {
        "strategy_name": "RSI超买卖出",
        "exit_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                             "comparison": "greater_than", "value": 70, "action": "sell"}],
        "entry_conditions": [],
    }),

    # ════════════════════════════════════════
    # PRIORITY 14 — RSI 穿越
    # ════════════════════════════════════════

    (re.compile(
        r"(?:rsi|RSI)\s*(?:从下方|从下向上|自下而上|向上|从下)\s*(?:上穿|升穿|突破|穿|金叉)\s*(\d+)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": f"RSI上穿{int(m.group(1))}买入",
        "entry_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                              "comparison": "crosses_above", "value": int(m.group(1)), "action": "buy"}],
        "exit_conditions": [],
    }),

    (re.compile(
        r"(?:rsi|RSI)\s*(?:从上方|从上向下|自上而下|向下|从上)\s*(?:下穿|跌穿|死叉|跌破)\s*(\d+)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": f"RSI下穿{int(m.group(1))}卖出",
        "exit_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                             "comparison": "crosses_below", "value": int(m.group(1)), "action": "sell"}],
        "entry_conditions": [],
    }),

    # ════════════════════════════════════════
    # PRIORITY 15 — 布林带完整策略（先于单条件）
    # ════════════════════════════════════════

    (re.compile(
        r".*?(?:布林|布林带|bollinger|布林线|通道)\s*(?:下轨|下沿|lower)"
        r".*?(?:买入|做多|开多|long|入场|买)"
        r".*?"
        r"(?:布林|布林带|bollinger|布林线|通道)?\s*(?:上轨|上沿|upper)"
        r".*?(?:卖出|做空|开空|平多|平仓|出场|卖|short)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "布林带区间策略",
        "entry_conditions": [{"indicator": "close", "params": {},
                              "comparison": "less_than",
                              "cross_with": "bb_lower", "value": None, "action": "buy"}],
        "exit_conditions": [{"indicator": "close", "params": {},
                             "comparison": "greater_than",
                             "cross_with": "bb_upper", "value": None, "action": "sell"}],
    }),

    # ════════════════════════════════════════
    # PRIORITY 16 — 布林带单条件
    # ════════════════════════════════════════

    (re.compile(
        r"(?:价格|价|收盘价|行情)?\s*"
        r"(?:触及|碰到|触碰|接触|回踩|回落至|回落到|跌至|跌到|下探|接近|靠近|接近了|在)\s*"
        r"(?:布林|布林带|布林线|布林通道|bollinger|布林轨|通道)\s*"
        r"(?:下轨|下沿|下边界|下线|lower|下轨道|下带)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "布林带下轨买入",
        "entry_conditions": [{"indicator": "close", "params": {},
                              "comparison": "less_than",
                              "cross_with": "bb_lower", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }),

    (re.compile(
        r"(?:价格|价|收盘价|行情)?\s*"
        r"(?:触及|碰到|触碰|接触|突破|涨破|上探|涨至|涨到|靠近|接近|在)\s*"
        r"(?:布林|布林带|布林线|布林通道|bollinger|布林轨|通道)\s*"
        r"(?:上轨|上沿|上边界|上线|upper|上轨道|上带)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "布林带上轨卖出",
        "exit_conditions": [{"indicator": "close", "params": {},
                             "comparison": "greater_than",
                             "cross_with": "bb_upper", "value": None, "action": "sell"}],
        "entry_conditions": [],
    }),

    (re.compile(
        r"(?:价格|价|收盘价)?\s*"
        r"(?:回踩|回落|回调|接近|靠近|回到|回落至)\s*"
        r"(?:布林|布林带|布林线|布林通道|bollinger|布林轨)\s*"
        r"(?:中轨|中线|middle|中枢|中轨道)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "布林带中轨支撑买入",
        "entry_conditions": [{"indicator": "close", "params": {},
                              "comparison": "greater_than",
                              "cross_with": "bb_middle", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }),

    # ════════════════════════════════════════
    # PRIORITY 17 — 涨跌幅
    # ════════════════════════════════════════

    (re.compile(
        r"(?:涨幅|涨|上涨|升|反弹|拉升|拉涨)\s*"
        r"(?:超过|大于|高于|达|超|>|≥|突破|不小[于]?)\s*"
        r"(\d+(?:\.\d+)?)\s*%?"
        r".{0,8}(?:买入|做多|开多|追涨|入场|建仓)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "涨幅突破买入",
        "entry_conditions": [{"indicator": "price_change_pct", "params": {"period": 1},
                              "comparison": "greater_than", "value": float(m.group(1)), "action": "buy"}],
        "exit_conditions": [],
    }),

    (re.compile(
        r"(?:跌幅|跌|下跌|降|回调|回落)\s*"
        r"(?:超过|大于|低于|超|>|≥|不小[于]?)\s*"
        r"(\d+(?:\.\d+)?)\s*%?"
        r".{0,8}(?:卖出|做空|开空|止损|出场|平仓)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "跌幅突破卖出",
        "exit_conditions": [{"indicator": "price_change_pct", "params": {"period": 1},
                             "comparison": "less_than", "value": -float(m.group(1)), "action": "sell"}],
        "entry_conditions": [],
    }),

    # ════════════════════════════════════════
    # PRIORITY 18 — 成交量
    # ════════════════════════════════════════

    (re.compile(
        r"(?:成交量|量能|volume|vol|量)\s*"
        r"(?:放大|放量|增加|增|暴增|激增|扩大|超过|大于|>|是.*?[的]?)"
        r".*?(\d+(?:\.\d+)?)\s*(?:倍|倍以[上下]|倍左右)"
        r".{0,8}(?:买入|做多|开多|入场)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": f"放量{float(m.group(1))}倍买入",
        "entry_conditions": [{"indicator": "volume", "params": {},
                              "comparison": "greater_than", "value": None, "action": "buy"}],
        "exit_conditions": [],
        "_notes": f"成交量超过平均的{float(m.group(1))}倍",
    }),

    (re.compile(
        r"(?:成交量|量能|volume|vol|量)\s*"
        r"(?:缩小|缩量|减少|萎缩|缩|减量)"
        r".{0,12}(?:买入|做多|建仓|入场)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "缩量买入",
        "entry_conditions": [{"indicator": "volume", "params": {},
                              "comparison": "less_than", "value": None, "action": "buy"}],
        "exit_conditions": [],
        "_notes": "成交量低于均量",
    }),

    # ════════════════════════════════════════
    # PRIORITY 19 — 连续阳线/阴线
    # ════════════════════════════════════════

    (re.compile(
        r"(?:"
        r"连续\s*(\d+)\s*(?:根|个|天|日|次|笔|条)?\s*(?:阳线|上涨|收涨|收阳|阳柱|红柱|收红|红K|涨)"
        r"|连涨\s*(\d+)\s*(?:根|个|天|日|次|笔|条)?"
        r")"
        r".{0,12}(?:买入|做多|开多|入场|建仓|做多单)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": f"连涨{int(m[1] or m[2])}根买入",
        "entry_conditions": [{"indicator": "close", "params": {},
                              "comparison": "consecutive_gain",
                              "value": int(m[1] or m[2]), "action": "buy"}],
        "exit_conditions": [],
    }),

    (re.compile(
        r"(?:"
        r"连续\s*(\d+)\s*(?:根|个|天|日|次|笔|条)?\s*(?:阴线|下跌|收跌|收阴|阴柱|绿柱|收绿|绿K|跌)"
        r"|连跌\s*(\d+)\s*(?:根|个|天|日|次|笔|条)?"
        r")"
        r".{0,12}(?:卖出|做空|开空|出场|平仓)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": f"连跌{int(m.group(1))}根卖出",
        "exit_conditions": [{"indicator": "close", "params": {},
                             "comparison": "consecutive_loss",
                             "value": int(m.group(1)), "action": "sell"}],
        "entry_conditions": [],
    }),
]


def _parse_locally(text: str) -> Optional[dict]:
    """关键词匹配降级解析 — 遍历 _STRATEGY_PATTERNS 返回第一个匹配"""
    text_clean = re.sub(r"[，。！？；：、""''（）【】《》！？,!?;:\"'(){}]", " ", text.lower().strip())
    text_clean = re.sub(r"\s+", " ", text_clean)

    for pattern, builder in _STRATEGY_PATTERNS:
        m = pattern.search(text_clean)
        if m:
            try:
                result = builder(m)
                if result is None:
                    continue
            except Exception as e:
                logger.debug(f"策略模式执行失败: {e}")
                continue

            risk = _extract_risk_params(text)
            # 合并 risk_params：提取的值优先，提取不到的保留 builder 的默认值
            builder_risk = result.get("risk_params", {})
            merged = dict(builder_risk)
            for k, v in risk.items():
                if v is not None:
                    merged[k] = v
            result["risk_params"] = merged
            result["description"] = text.strip()
            result["timeframe_hint"] = _extract_timeframe(text)
            logger.info(f"本地解析成功: {result.get('strategy_name', '未知策略')}")
            return result

    logger.info("没有匹配到已知策略模式，尝试通用提取")
    return _try_generic_extract(text)


def _try_generic_extract(text: str) -> Optional[dict]:
    """兜底：从文字中提取任何可识别的交易条件"""
    text_lower = text.lower()
    entry_conds = []
    exit_conds = []

    has_buy = any(w in text_lower for w in ["买入", "做多", "开多", "买多", "long", "低吸", "抄底"])
    has_sell = any(w in text_lower for w in ["卖出", "做空", "开空", "卖空", "short"])

    m_low = re.search(r"rsi\s*(?:低于|小于|＜|在.*?以下)\s*(\d+)", text_lower)
    m_high = re.search(r"rsi\s*(?:高于|大于|＞|在.*?以上)\s*(\d+)", text_lower)

    if m_low and m_high and has_buy and has_sell:
        entry_conds.append({
            "indicator": "rsi_14", "params": {"period": 14},
            "comparison": "less_than", "value": int(m_low.group(1)), "action": "buy",
        })
        exit_conds.append({
            "indicator": "rsi_14", "params": {"period": 14},
            "comparison": "greater_than", "value": int(m_high.group(1)), "action": "sell",
        })
    elif m_low and has_buy:
        entry_conds.append({
            "indicator": "rsi_14", "params": {"period": 14},
            "comparison": "less_than", "value": int(m_low.group(1)), "action": "buy",
        })
    elif m_high and has_sell:
        exit_conds.append({
            "indicator": "rsi_14", "params": {"period": 14},
            "comparison": "greater_than", "value": int(m_high.group(1)), "action": "sell",
        })

    if not entry_conds and not exit_conds:
        return None

    risk = _extract_risk_params(text)
    return {
        "strategy_name": "通用条件策略",
        "description": text.strip(),
        "timeframe_hint": _extract_timeframe(text),
        "entry_conditions": entry_conds,
        "exit_conditions": exit_conds,
        "risk_params": risk,
    }


# ════════════════════════════════════════════════════════════════
# DeepSeek API 调用
# ════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你是一个量化交易策略解析器。将用户的自然语言策略描述转换为结构化 JSON。

规则格式:
```json
{
  "strategy_name": "策略名称（中文，简洁有力）",
  "description": "用户原始描述",
  "timeframe_hint": "建议的K线周期 (1m/5m/15m/1h/4h/1d)",
  "entry_conditions": [
    {"indicator": "指标名", "params": {"period": 数字}, "comparison": "比较方式", "value": 数字或null, "action": "buy"}
  ],
  "exit_conditions": [
    {"indicator": "指标名", "params": {"period": 数字}, "comparison": "比较方式", "value": 数字或null, "action": "sell"}
  ],
  "risk_params": {
    "stop_loss_pct": 数字或null,
    "take_profit_pct": 数字或null,
    "position_size_pct": 数字,
    "trailing_stop_activation_pct": 数字或null,
    "trailing_stop_distance_pct": 数字或null,
    "position_timeout_bars": 数字或null
  }
}
```

支持的 indicator:
- "rsi_14": RSI(14), params: {"period": 14}
- "rsi_6": RSI(6), params: {"period": 6}
- "rsi_20": RSI(20), params: {"period": 20}
- "sma_N": 简单移动平均, 如"sma_5","sma_10","sma_20","sma_50","sma_200"
- "ema_N": 指数移动平均, 如"ema_12","ema_26","ema_20"
- "macd": MACD 快线 (DIF)
- "macd_signal": MACD 信号线 (DEA)
- "macd_histogram": MACD 柱状图
- "bb_upper": 布林带上轨
- "bb_middle": 布林带中轨
- "bb_lower": 布林带下轨
- "close": 收盘价
- "volume": 成交量
- "price_change_pct": 1根K线价格变动百分比

comparison: "greater_than", "less_than", "crosses_above", "crosses_below", "consecutive_gain", "consecutive_loss"

重要规则:
1. 多条条件用"且"关系时，_condition_logic 设为 "all"
2. 两个指标交叉时，用 "cross_with" 指定另一个指标
3. indicator 使用带周期的全名（如 rsi_14 而非 rsi）
4. 不确定的字段设 null 或空列表
5. 描述过于模糊无法解析时: entry_conditions 空 + "parse_error"

请严格只输出 JSON，不要加解释。"""


class StrategyInterpreter:
    """自然语言 → 交易规则 JSON"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = None

    def _init_client(self):
        if self._client is not None:
            return
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.cfg.agent.api_key or "sk-placeholder",
                base_url=self.cfg.agent.base_url,
            )
        except ImportError:
            logger.warning("openai 库未安装，使用本地解析")
            self._client = None

    def interpret(self, text: str) -> dict:
        if not text or not text.strip():
            return {"parse_error": "策略描述不能为空"}

        local_result = _parse_locally(text)
        if local_result:
            logger.info(f"本地解析成功: {local_result['strategy_name']}")

        if self.cfg.agent.enabled:
            api_result = self._call_api(text)
            if api_result and "parse_error" not in api_result:
                logger.info(f"AI 解析成功: {api_result.get('strategy_name', '')}")
                return api_result

        if local_result:
            return local_result

        return {"parse_error": "无法解析该策略描述，请使用更明确的表述（如'当RSI低于30时买入，高于70时卖出'）"}

    def _call_api(self, text: str) -> Optional[dict]:
        self._init_client()
        if self._client is None:
            return None

        try:
            resp = self._client.chat.completions.create(
                model=self.cfg.agent.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=self.cfg.agent.max_tokens,
            )
            content = resp.choices[0].message.content or ""
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            result = json.loads(content)

            if "risk_params" not in result:
                result["risk_params"] = {}
            risk_defaults = {
                "stop_loss_pct": None, "take_profit_pct": None,
                "position_size_pct": 10.0,
                "trailing_stop_activation_pct": None,
                "trailing_stop_distance_pct": None,
                "position_timeout_bars": None,
            }
            for k, v in risk_defaults.items():
                if k not in result["risk_params"] or result["risk_params"][k] is None:
                    result["risk_params"][k] = v

            result.setdefault("entry_conditions", [])
            result.setdefault("exit_conditions", [])
            result.setdefault("_condition_logic", "any")
            result.setdefault("_notes", "")

            return result

        except Exception as e:
            logger.warning(f"AI 解析失败: {e}，降级到本地解析")
            return None
