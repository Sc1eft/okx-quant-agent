"""
Natural language strategy parser for Streamlit frontend.

Extracted from agent/strategy_interpreter.py — standalone, no architecture dependencies.
Replaces the old StrategyInterpreter.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("strategy_parser")

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def extract_timeframe(text: str) -> str:
    tf_map = {
        "秒级": "1s", "秒": "1s",
        "1分钟": "1m", "一分钟": "1m", "1分": "1m", "一分": "1m",
        "2分钟": "2m", "二分钟": "2m", "2分": "2m",
        "5分钟": "5m", "五分钟": "5m", "5分": "5m",
        "15分钟": "15m", "十五分钟": "15m", "15分": "15m",
        "30分钟": "30m", "三十分钟": "30m",
        "1小时": "1h", "一小时": "1h", "1h": "1h", "小时": "1h",
        "2小时": "2h", "二小时": "2h",
        "4小时": "4h", "四小时": "4h",
        "6小时": "6h", "六小时": "6h",
        "12小时": "12h", "十二小时": "12h",
        "日线": "1d", "天": "1d", "日": "1d", "1天": "1d", "一天": "1d",
        "2天": "2d", "二天": "2d",
        "周线": "1w", "周": "1w",
    }
    for key, val in sorted(tf_map.items(), key=lambda x: -len(x[0])):
        if key.lower() in text.lower():
            return val
    return "15m"


def extract_risk_params(text: str) -> dict:
    params: dict[str, Any] = {
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "position_size_pct": 10.0,
        "trailing_stop_activation_pct": None,
        "trailing_stop_distance_pct": None,
        "position_timeout_bars": None,
    }

    for pat in [
        r"止损[约为]?(\d+(?:\.\d+)?)\s*%?",
        r"(\d+(?:\.\d+)?)\s*%?\s*止[损亏]",
        r"亏损[到超达]?(\d+(?:\.\d+)?)\s*%?\s*(?:止损|平仓)",
    ]:
        m = re.search(pat, text)
        if m:
            params["stop_loss_pct"] = abs(float(m.group(1)))
            break

    for pat in [
        r"止盈[约为]?(\d+(?:\.\d+)?)\s*%?",
        r"(\d+(?:\.\d+)?)\s*%?\s*止盈",
        r"目标[盈利]?[约达]?(\d+(?:\.\d+)?)\s*%?",
    ]:
        m = re.search(pat, text)
        if m:
            params["take_profit_pct"] = abs(float(m.group(1)))
            break

    m = re.search(r"(\d+(?:\.\d+)?)\s*%[的的]?(?:资金|仓位|本金|账户)", text)
    if m:
        params["position_size_pct"] = float(m.group(1))
    else:
        kw = [
            (r"满仓|全仓|all.?in|梭哈|全部资金", 100.0),
            (r"重仓|大仓位|七成|八成|70%|80%", 75.0),
            (r"半仓|一半|五成|半|50%|5成", 50.0),
            (r"轻仓|小仓位|三成|少量|小部分|30%", 25.0),
            (r"迷你仓|微仓|试仓|一成|10%", 10.0),
        ]
        for pat, pct in kw:
            if re.search(pat, text, re.I):
                params["position_size_pct"] = pct
                break

    for pat in [
        r"(?:移动|追踪|浮动|动态)\s*止损[激活启动]?(?:距离|幅度|设为)?[约]?(\d+(?:\.\d+)?)\s*%?",
        r"(\d+(?:\.\d+)?)\s*%?\s*(?:移动|追踪|浮动|动态)\s*止损",
        r"(?:从最高|从高点|从峰值).{0,8}(?:回落|回撤|回调)(\d+(?:\.\d+)?)\s*%?\s*(?:止损|平仓|出场)",
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

    for pat in [
        r"(?:持仓|持有|持仓时间)[时超过]*(\d+)\s*根?\s*(?:K线|k线|candle|bar)",
        r"(\d+)\s*根?\s*(?:K线|k线|candle|bar)(?:后|以内|之后)(?:平仓|出场|止盈|止损)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            params["position_timeout_bars"] = int(m.group(1))
            break

    m = re.search(r"(\d+(?:\.\d+)?)\s*倍\s*(?:杠杆|leverage)", text, re.I)
    if m:
        params["leverage"] = float(m.group(1))

    m = re.search(r"(?:单笔|每笔|每次).{0,4}(?:最大|最多|亏损|损失).*?(\d+(?:\.\d+)?)\s*%", text, re.I)
    if m:
        params["max_loss_pct"] = float(m.group(1))

    for pat in [
        r"(?:同方向|同向).{0,6}(?:冷却|间隔|cooldown).*?(\d+)",
        r"(?:冷却|cooldown).*?(\d+)\s*(?:根|个|K线|candle|bar)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            params["cooldown_bars"] = int(m.group(1))
            break
    if "cooldown_bars" not in params and re.search(r"(?:2小时|两小时|2h|二小时)", text):
        params["cooldown_bars"] = 8

    m = re.search(r"(?:实体|波幅|波动).{0,4}(?:大于|超过|>)\s*\$?(\d+(?:\.\d+)?)", text, re.I)
    if m:
        params["volatility_body_threshold"] = float(m.group(1))
    m = re.search(
        r"(?:连续|两|2).{0,6}(?:根|个).{0,8}(?:和|合计|之和).{0,4}(?:大于|超过|>)\s*\$?(\d+(?:\.\d+)?)",
        text, re.I,
    )
    if m:
        params["volatility_sum_threshold"] = float(m.group(1))

    return params


# ──────────────────────────────────────────────
# Strategy builders
# ──────────────────────────────────────────────


def _int_groups(m: re.Match) -> list[int]:
    return [int(g) for g in m.groups() if g is not None and g.isdigit()]


def _build_ma_golden(m: re.Match) -> dict:
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
    p1, p2 = int(m.group(1)), int(m.group(2))
    return {
        "strategy_name": f"EMA{p1}金叉EMA{p2}买入",
        "entry_conditions": [{"indicator": f"ema_{p1}", "params": {"period": p1},
                              "comparison": "crosses_above",
                              "cross_with": f"ema_{p2}", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }


def _build_ema_death(m: re.Match) -> dict:
    p1, p2 = int(m.group(1)), int(m.group(2))
    return {
        "strategy_name": f"EMA{p1}死叉EMA{p2}卖出",
        "exit_conditions": [{"indicator": f"ema_{p1}", "params": {"period": p1},
                             "comparison": "crosses_below",
                             "cross_with": f"ema_{p2}", "value": None, "action": "sell"}],
        "entry_conditions": [],
    }


def _build_volatility_contrarian(m: re.Match = None) -> dict:
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
            "leverage": 5.0,
            "max_loss_pct": 3.0,
            "volatility_body_threshold": 15.0,
            "volatility_sum_threshold": 20.0,
            "cooldown_bars": 8,
        },
        "_notes": "15minK线实体>$15或连续2根之和>$20触发，反向开仓。风险预算仓位，多级移动止盈，同方向冷却。",
    }


# ──────────────────────────────────────────────
# Strategy patterns (priority-ordered)
# ──────────────────────────────────────────────

_STRATEGY_PATTERNS: list[tuple[re.Pattern, callable]] = [
    # Priority 0 — Volatility contrarian
    (re.compile(
        r"(?:波动率|波动性|volatility|波幅|震幅|实体|body).*?"
        r"(?:反向|逆向|逆势|contrarian)",
        re.I | re.DOTALL),
     _build_volatility_contrarian),
    (re.compile(
        r"(?:实体.*?(?:大于|超过|>)\s*\$?\d+(?:\.\d+)?|波动.*?(?:触发|入场))",
        re.I | re.DOTALL),
     _build_volatility_contrarian),

    # Priority 1 — Combined: RSI + MACD
    (re.compile(
        r"(?:rsi|RSI).*?(?:低于|小于)\s*(\d+).*?"
        r"(?:且|并且|同时|and).*?(?:macd|MACD).*?(?:金叉|上穿).*?(?:买入|做多)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI+MACD金叉买入",
        "entry_conditions": [
            {"indicator": "rsi_14", "params": {"period": 14},
             "comparison": "less_than", "value": int(m.group(1)), "action": "buy"},
            {"indicator": "macd", "params": {},
             "comparison": "crosses_above", "cross_with": "macd_signal", "value": None, "action": "buy"},
        ],
        "exit_conditions": [],
        "_condition_logic": "all",
    }),
    (re.compile(
        r"(?:rsi|RSI).*?(?:高于|大于)\s*(\d+).*?"
        r"(?:且|并且|同时|and).*?(?:macd|MACD).*?(?:死叉|下穿).*?(?:卖出|做空)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI+MACD死叉卖出",
        "exit_conditions": [
            {"indicator": "rsi_14", "params": {"period": 14},
             "comparison": "greater_than", "value": int(m.group(1)), "action": "sell"},
            {"indicator": "macd", "params": {},
             "comparison": "crosses_below", "cross_with": "macd_signal", "value": None, "action": "sell"},
        ],
        "entry_conditions": [],
        "_condition_logic": "all",
    }),

    # Priority 2 — MACD golden/death cross
    (re.compile(
        r"macd.*?(?:金叉|上穿).*?(?:买入|做多|开多).*?(?:死叉|下穿).*?(?:卖出|做空|开空|平多)",
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
    (re.compile(
        r"macd.*?(?:上穿|升穿|突破)\s*(?:零轴|0轴|0线)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD上穿零轴买入",
        "entry_conditions": [{"indicator": "macd", "params": {},
                              "comparison": "crosses_above", "value": 0, "action": "buy"}],
        "exit_conditions": [],
    }),
    (re.compile(
        r"macd.*?(?:下穿|跌破|跌穿)\s*(?:零轴|0轴|0线)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD下穿零轴卖出",
        "exit_conditions": [{"indicator": "macd", "params": {},
                             "comparison": "crosses_below", "value": 0, "action": "sell"}],
        "entry_conditions": [],
    }),

    # Priority 3 — MACD histogram
    (re.compile(
        r"macd.*?(?:柱|柱状图|histogram)\s*(?:转正|翻红|转为正|由负转正)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD柱转正买入",
        "entry_conditions": [{"indicator": "macd_histogram", "params": {},
                              "comparison": "crosses_above", "value": 0, "action": "buy"}],
        "exit_conditions": [],
    }),
    (re.compile(
        r"macd.*?(?:柱|柱状图|histogram)\s*(?:转负|翻绿|转为负|由正转负)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD柱转负卖出",
        "exit_conditions": [{"indicator": "macd_histogram", "params": {},
                             "comparison": "crosses_below", "value": 0, "action": "sell"}],
        "entry_conditions": [],
    }),

    # Priority 4 — MACD simple cross
    (re.compile(
        r"macd.*?(?:上穿|金叉|黄金交叉)\s*(?:慢线|dea|信号线)?",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD金叉买入",
        "entry_conditions": [{"indicator": "macd", "params": {},
                              "comparison": "crosses_above",
                              "cross_with": "macd_signal", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }),
    (re.compile(
        r"macd.*?(?:下穿|死叉|死亡交叉)\s*(?:慢线|dea|信号线)?",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "MACD死叉卖出",
        "exit_conditions": [{"indicator": "macd", "params": {},
                             "comparison": "crosses_below",
                             "cross_with": "macd_signal", "value": None, "action": "sell"}],
        "entry_conditions": [],
    }),

    # Priority 5 — EMA cross
    (re.compile(
        r"(?:EMA|ema|指数移动平均)\s*(\d+).*?(?:上穿|金叉).*?(?:EMA|ema)\s*(\d+)",
        re.I | re.DOTALL),
     _build_ema_golden),
    (re.compile(
        r"(?:EMA|ema|指数移动平均)\s*(\d+).*?(?:下穿|死叉).*?(?:EMA|ema)\s*(\d+)",
        re.I | re.DOTALL),
     _build_ema_death),

    # Priority 6 — MA trend (golden in + death out)
    (re.compile(
        r".*?(?:MA|ma|均线)\s*(\d+).*?(?:上穿|金叉).*?(?:MA|ma|均线)\s*(\d+)"
        r".*?(?:买入|做多|开多).*?"
        r"(?:下穿|死叉).*?(?:MA|ma|均线)?\s*(\d+)?.*?(?:卖出|做空|开空|平多)",
        re.I | re.DOTALL),
     _build_ma_trend),

    # Priority 7 — MA golden/death cross
    (re.compile(
        r"(?:MA|ma|均线)\s*(\d+).*?(?:上穿|金叉|升穿).*?(?:MA|ma|均线)\s*(\d+)",
        re.I | re.DOTALL),
     _build_ma_golden),
    (re.compile(
        r"(?:MA|ma|均线)\s*(\d+).*?(?:下穿|死叉|跌穿).*?(?:MA|ma|均线)\s*(\d+)",
        re.I | re.DOTALL),
     _build_ma_death),

    # Priority 8 — Price crosses MA
    (re.compile(
        r"(?:价格|价|收盘价)?\s*(?:上穿|突破|站上|升穿)\s*(?:MA|ma|均线)\s*(\d+)",
        re.I | re.DOTALL),
     _build_price_up_ma),
    (re.compile(
        r"(?:价格|价|收盘价)?\s*(?:下穿|跌破|跌穿|破位)\s*(?:MA|ma|均线)\s*(\d+)",
        re.I | re.DOTALL),
     _build_price_dn_ma),

    # Priority 9 — Bollinger
    (re.compile(
        r".*?(?:布林|bollinger)\s*(?:下轨|下沿|lower).*?(?:买入|做多|开多)"
        r".*?(?:布林)?\s*(?:上轨|上沿|upper).*?(?:卖出|做空|开空)",
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
    (re.compile(
        r"(?:触及|碰到|回踩|回落)\s*(?:布林|bollinger)\s*(?:下轨|下沿|lower)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "布林带下轨买入",
        "entry_conditions": [{"indicator": "close", "params": {},
                              "comparison": "less_than",
                              "cross_with": "bb_lower", "value": None, "action": "buy"}],
        "exit_conditions": [],
    }),
    (re.compile(
        r"(?:触及|碰到|突破|涨破)\s*(?:布林|bollinger)\s*(?:上轨|上沿|upper)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "布林带上轨卖出",
        "exit_conditions": [{"indicator": "close", "params": {},
                             "comparison": "greater_than",
                             "cross_with": "bb_upper", "value": None, "action": "sell"}],
        "entry_conditions": [],
    }),

    # Priority 10 — RSI mean reversion
    (re.compile(
        r"(?:rsi|RSI).*?(?:低于|小于)\s*(\d+).*?(?:买入|做多|开多).*?"
        r"(?:高于|大于)\s*(\d+).*?(?:卖出|做空|开空)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI均值回归",
        "entry_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                              "comparison": "less_than", "value": int(m.group(1)), "action": "buy"}],
        "exit_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                             "comparison": "greater_than", "value": int(m.group(2)), "action": "sell"}],
    }),
    (re.compile(
        r"(?:rsi|RSI).*?(?:低于|小于)\s*(\d+).*?(?:买入|做多|开多|long|低吸|抄底)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI超卖买入",
        "entry_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                              "comparison": "less_than", "value": int(m.group(1)), "action": "buy"}],
        "exit_conditions": [],
    }),
    (re.compile(
        r"(?:rsi|RSI).*?(?:高于|大于)\s*(\d+).*?(?:卖出|做空|开空|short|逃顶|高抛)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "RSI超买卖出",
        "exit_conditions": [{"indicator": "rsi_14", "params": {"period": 14},
                             "comparison": "greater_than", "value": int(m.group(1)), "action": "sell"}],
        "entry_conditions": [],
    }),

    # Priority 11 — Consecutive candles
    (re.compile(
        r"连续\s*(\d+)\s*(?:根|个|天).*?(?:阳线|上涨|收涨|红).*?(?:买入|做多|开多)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": f"连涨{int(m.group(1))}根买入",
        "entry_conditions": [{"indicator": "close", "params": {},
                              "comparison": "consecutive_gain",
                              "value": int(m.group(1)), "action": "buy"}],
        "exit_conditions": [],
    }),
    (re.compile(
        r"连续\s*(\d+)\s*(?:根|个|天).*?(?:阴线|下跌|收跌|绿).*?(?:卖出|做空|开空)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": f"连跌{int(m.group(1))}根卖出",
        "exit_conditions": [{"indicator": "close", "params": {},
                             "comparison": "consecutive_loss",
                             "value": int(m.group(1)), "action": "sell"}],
        "entry_conditions": [],
    }),

    # Priority 12 — Price change percentage
    (re.compile(
        r"(?:涨幅|涨|上涨|升)\s*(?:超过|大于|>)\s*(\d+(?:\.\d+)?)\s*%?"
        r".{0,8}(?:买入|做多|开多|追涨)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "涨幅突破买入",
        "entry_conditions": [{"indicator": "price_change_pct", "params": {"period": 1},
                              "comparison": "greater_than", "value": float(m.group(1)), "action": "buy"}],
        "exit_conditions": [],
    }),
    (re.compile(
        r"(?:跌幅|跌|下跌|降)\s*(?:超过|大于|>)\s*(\d+(?:\.\d+)?)\s*%?"
        r".{0,8}(?:卖出|做空|开空|止损)",
        re.I | re.DOTALL),
     lambda m: {
        "strategy_name": "跌幅突破卖出",
        "exit_conditions": [{"indicator": "price_change_pct", "params": {"period": 1},
                             "comparison": "less_than", "value": -float(m.group(1)), "action": "sell"}],
        "entry_conditions": [],
    }),
]


def _try_generic_extract(text: str) -> Optional[dict]:
    """Fallback: extract any recognizable conditions."""
    t = text.lower()
    entry_conds = []
    exit_conds = []
    has_buy = any(w in t for w in ["买入", "做多", "开多", "long", "低吸", "抄底"])
    has_sell = any(w in t for w in ["卖出", "做空", "开空", "short"])
    m_low = re.search(r"rsi\s*(?:低于|小于|在.*?以下)\s*(\d+)", t)
    m_high = re.search(r"rsi\s*(?:高于|大于|在.*?以上)\s*(\d+)", t)
    if m_low and m_high and has_buy and has_sell:
        entry_conds.append({"indicator": "rsi_14", "params": {"period": 14},
                            "comparison": "less_than", "value": int(m_low.group(1)), "action": "buy"})
        exit_conds.append({"indicator": "rsi_14", "params": {"period": 14},
                           "comparison": "greater_than", "value": int(m_high.group(1)), "action": "sell"})
    elif m_low and has_buy:
        entry_conds.append({"indicator": "rsi_14", "params": {"period": 14},
                            "comparison": "less_than", "value": int(m_low.group(1)), "action": "buy"})
    elif m_high and has_sell:
        exit_conds.append({"indicator": "rsi_14", "params": {"period": 14},
                           "comparison": "greater_than", "value": int(m_high.group(1)), "action": "sell"})
    if not entry_conds and not exit_conds:
        return None
    risk = extract_risk_params(text)
    return {
        "strategy_name": "通用条件策略",
        "description": text.strip(),
        "timeframe_hint": extract_timeframe(text),
        "entry_conditions": entry_conds,
        "exit_conditions": exit_conds,
        "risk_params": risk,
    }


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────


def parse_strategy_text(text: str) -> dict:
    """Parse a natural-language strategy description into structured rules.

    Returns a rules dict matching the BacktestEngine format,
    or {"parse_error": "..."} on failure.
    """
    if not text or not text.strip():
        return {"parse_error": "策略描述不能为空"}

    text_clean = re.sub(r"[，。！？；：、""''（）【】《》！？,!?;:\"'(){}]", " ", text.lower().strip())
    text_clean = re.sub(r"\s+", " ", text_clean)

    for pattern, builder in _STRATEGY_PATTERNS:
        m = pattern.search(text_clean)
        if m:
            try:
                result = builder(m)
                if result is None:
                    continue
            except Exception:
                continue
            risk = extract_risk_params(text)
            builder_risk = result.get("risk_params", {})
            merged = dict(builder_risk)
            for k, v in risk.items():
                if v is not None:
                    merged[k] = v
            result["risk_params"] = merged
            result["description"] = text.strip()
            result["timeframe_hint"] = extract_timeframe(text)
            logger.info(f"策略解析成功: {result.get('strategy_name', '未知策略')}")
            return result

    fallback = _try_generic_extract(text)
    if fallback:
        return fallback

    return {"parse_error": "无法解析该策略描述，请使用更明确的表述（如'当RSI低于30时买入，高于70时卖出'）"}
