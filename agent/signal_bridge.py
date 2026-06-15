"""
SignalBridge — 将 AI 多空分析结果转换为交易执行器规则

从 DeepSeek 分析 JSON → AIStrategyExecutor 可消费的结构化规则

Usage:
    from agent.signal_bridge import ai_signal_to_rules
    rules = ai_signal_to_rules(ai_analysis_result)
    executor = AIStrategyExecutor(rules=rules, ...)
"""

from typing import Any


def ai_signal_to_rules(signal: dict, initial_balance: float = 10000.0) -> dict:
    """将 AI 分析结果转换为 executor rules

    Args:
        signal: DeepSeek 返回的分析结果
                （必须含 direction, confidence；可选 summary, key_evidence 等）
        initial_balance: 初始资金（仅用于风控参数参考）

    Returns:
        符合 AIStrategyExecutor.rules 格式的 dict
    """
    direction = signal.get("direction", "neutral")
    confidence = signal.get("confidence", 0)

    dir_label = (
        "看多"
        if direction == "long"
        else "看空"
        if direction == "short"
        else "中性"
    )

    return {
        "strategy_name": f"AI信号-{dir_label}",
        "_strategy_type": "ai_signal",
        "timeframe_hint": "15m",
        "entry_conditions": [],
        "exit_conditions": [],
        "risk_params": {
            "stop_loss_pct": 1.5,
            "take_profit_pct": 3.0,
            "max_loss_pct": 3.0,
            "leverage": 1.0,
            "position_timeout_bars": 96,  # 24h / 15min
            "trailing_stop_activation_pct": 2.0,
            "trailing_stop_distance_pct": 1.25,
        },
        "ai_signal": {
            "original_direction": direction,
            "confidence": confidence,
            "summary": signal.get("summary", ""),
            "key_evidence": signal.get("key_evidence", []),
            "risk_warnings": signal.get("risk_warnings", []),
            "technical_analysis": signal.get("technical_analysis", ""),
            "market_sentiment": signal.get("market_sentiment", ""),
            "fundamental_analysis": signal.get("fundamental_analysis", ""),
            "analyzed_at": "",
        },
        "_notes": (
            f"AI信号: 信心指数{confidence}%, "
            f"依据: {len(signal.get('key_evidence', []))}条"
        ),
    }
