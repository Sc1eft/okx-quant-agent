"""
Agent 审计模块 — 确保 Agent 不越界
"""

from __future__ import annotations


AUDIT_RULES = {
    "can_do": [
        "解释策略逻辑和信号",
        "总结回测报告",
        "检测过拟合迹象",
        "建议参数范围",
        "检查风控配置合理性",
        "生成交易日志摘要",
    ],
    "cannot_do": [
        "直接下单或修改订单",
        "绕过风控规则",
        "修改 API Key 或交易配置",
        "自动扩大仓位或加杠杆",
        "关闭风控保护",
        "自动部署到实盘",
    ],
    "required_approval": [
        "更改策略参数",
        "切换交易模式 (backtest→live)",
        "修改风控阈值",
        "启用新交易对",
    ],
}

RECOMMENDED_CHAIN = """
策略生成信号
  → 风控审核信号
    → 执行器下单
      → Agent 分析和审计（不下单）
        → 日志记录
"""


def audit_agent_action(action: str) -> tuple[bool, str]:
    """
    审计 Agent 操作是否在允许范围内
    """
    action_lower = action.lower()

    for forbidden in AUDIT_RULES["cannot_do"]:
        if any(kw in action_lower for kw in forbidden.split()):
            return False, f"❌ Agent 禁止操作: {forbidden}"

    for requires_approval in AUDIT_RULES["required_approval"]:
        if any(kw in action_lower for kw in requires_approval.split()):
            return False, f"⚠️  需要人工确认: {requires_approval}"

    return True, "✅ 操作在 Agent 允许范围内"
