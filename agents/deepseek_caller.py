"""
DeepSeek 交易决策调用器

将 Agent 1 的技术面信号 + Agent 2 的新闻/基本面数据
注入给 DeepSeek V4 Pro，获取交易决策。

复用根 config.py 中的 AgentConfig（api_key, model, base_url, temperature）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("deepseek_caller")

# ── 系统提示词 ──

_SYSTEM_PROMPT = """你是一位有15年经验的以太坊资深交易员，管理过亿美元的资金。
请基于以下多维数据，给出交易决策。

【当前仓位】
- 持仓方向: {position_direction}
- 持仓数量: {position_size} ETH
- 入场均价: {entry_price}
- 当前浮盈/浮亏: {pnl_pct}%

【风控状态】
- 今日交易次数: {daily_trade_count} / {max_daily_trades}
- 今日亏损: {daily_loss} USDT / {max_daily_loss} USDT
- 连续亏损次数: {consecutive_losses} / {max_consecutive_losses}
- 当前仓位乘数: {position_size_multiplier}x

【技术面摘要】
{agent1_summary}

【新闻与链上面】
{agent2_summary}

【历史交易统计】
- 本月交易次数: {monthly_trades}
- 本月胜率: {win_rate}%
- 本月盈亏: {monthly_pnl} USDT

请严格按以下 JSON 格式回复:
{{
    "action": "buy" | "sell" | "hold",
    "confidence": 0-100,
    "entry_price_min": "入场最低价",
    "entry_price_max": "入场最高价",
    "position_size_pct": "建议仓位占总资金百分比",
    "stop_loss": "止损价",
    "take_profit": "止盈价",
    "reason": "决策理由（中文，50字内）"
}}

注意：如果当前无仓位且 action 为 hold，则其他字段可为空字符串。
"""
# (不要注入用户输入到 f-string — 下面用 .format() 安全处理)


class DeepSeekTrader:
    """DeepSeek 交易决策器

    用法:
        trader = DeepSeekTrader(api_key, model, base_url)
        decision = trader.analyze(context_dict)
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com/v1",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: float = 30.0,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

        if not api_key:
            logger.warning("DeepSeek API Key 未设置")
        self._client = OpenAI(
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
            timeout=timeout,
        )

        # 统计
        self.total_calls = 0
        self.total_errors = 0

    def analyze(self, context: dict) -> dict:
        """调用 DeepSeek 分析，返回交易决策

        context 字段:
            position_direction: "long" / "short" / "none"
            position_size: float
            entry_price: float / ""
            pnl_pct: float / ""
            agent1_summary: str (技术面摘要)
            agent2_summary: str (新闻/基本面摘要)
            monthly_trades: int
            win_rate: float
            monthly_pnl: float
            current_price: float
        """
        self.total_calls += 1

        # 安全构建 prompt（不使用 f-string，防止注入）
        risk = context.get("risk_status", {})
        prompt_kwargs = {
            "position_direction": context.get("position_direction", "none"),
            "position_size": str(context.get("position_size", 0)),
            "entry_price": str(context.get("entry_price", "")),
            "pnl_pct": str(context.get("pnl_pct", "")),
            "daily_trade_count": str(risk.get("daily_trade_count", "0")),
            "max_daily_trades": str(risk.get("max_daily_trades", "10")),
            "daily_loss": str(risk.get("daily_loss_usdt", "0")),
            "max_daily_loss": str(risk.get("max_daily_loss_usdt", "100")),
            "consecutive_losses": str(risk.get("consecutive_losses", "0")),
            "max_consecutive_losses": str(risk.get("max_consecutive_losses", "3")),
            "position_size_multiplier": str(risk.get("position_size_multiplier", "1.0")),
            "agent1_summary": context.get("agent1_summary", "暂无数据"),
            "agent2_summary": context.get("agent2_summary", "暂无数据"),
            "monthly_trades": str(context.get("monthly_trades", 0)),
            "win_rate": str(context.get("win_rate", 0)),
            "monthly_pnl": str(context.get("monthly_pnl", 0)),
        }

        system_prompt = _SYSTEM_PROMPT.format(**prompt_kwargs)

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (
                        f"当前 ETH 价格: ${context.get('current_price', 0)}\n"
                        "请给出交易决策。"
                    )},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            content = resp.choices[0].message.content or ""
            return self._parse_response(content, context.get("current_price", 0))

        except Exception as e:
            self.total_errors += 1
            logger.error(f"DeepSeek API 调用失败: {e}")
            return self._fallback_decision(context.get("current_price", 0))

    # ── Agent 4 复盘分析 ──

    def analyze_review(self, prompt_text: str) -> dict:
        """用 DeepSeek 分析复盘数据（Agent 4 专用）

        Args:
            prompt_text: 完整的复盘 Prompt（已含所有上下文）

        Returns:
            解析后的 JSON dict，含 review_id, summary, market_regime, param_adjustments
            失败时返回 {"summary": "分析失败", "param_adjustments": []}
        """
        self.total_calls += 1
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个量化交易复盘分析 AI。分析交易数据，输出 JSON 格式的参数调整建议。"},
                    {"role": "user", "content": prompt_text},
                ],
                temperature=0.4,  # 复盘分析用略高温度以获取多样性洞察
                max_tokens=3000,
            )
            content = resp.choices[0].message.content or ""
            return self._parse_json_response(content)
        except Exception as e:
            self.total_errors += 1
            logger.error(f"DeepSeek 复盘分析失败: {e}")
            return {"summary": "分析失败", "param_adjustments": []}

    def _parse_json_response(self, content: str) -> dict:
        """从 DeepSeek 响应中提取 JSON（通用方法）"""
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        else:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                content = content[start:end + 1]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning(f"DeepSeek JSON 解析失败: {content[:200]}")
            return {"summary": "JSON 解析失败", "param_adjustments": []}

    def _parse_response(self, content: str, current_price: float) -> dict:
        """解析 DeepSeek 返回的 JSON"""

        # 提取 JSON（支持 ```json 围栏 或 裸 JSON）
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        else:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                content = content[start:end + 1]

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(f"DeepSeek 响应 JSON 解析失败: {content[:200]}")
            return self._fallback_decision(current_price)

        # 验证并填充默认值
        action = result.get("action", "hold")
        if action not in ("buy", "sell", "hold"):
            action = "hold"

        return {
            "action": action,
            "confidence": int(result.get("confidence", 0)),
            "entry_price_min": result.get("entry_price_min", ""),
            "entry_price_max": result.get("entry_price_max", ""),
            "position_size_pct": result.get("position_size_pct", ""),
            "stop_loss": result.get("stop_loss", ""),
            "take_profit": result.get("take_profit", ""),
            "reason": result.get("reason", ""),
            "_raw": content[:500],
        }

    def _fallback_decision(self, current_price: float) -> dict:
        """API 失败时的降级决策——不做任何交易"""
        logger.info("DeepSeek API 不可用，降级为 hold")
        return {
            "action": "hold",
            "confidence": 0,
            "entry_price_min": "",
            "entry_price_max": "",
            "position_size_pct": "",
            "stop_loss": "",
            "take_profit": "",
            "reason": "DeepSeek API 暂不可用，自动跳过",
            "_raw": "",
        }

    def get_stats(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "model": self.model,
        }
