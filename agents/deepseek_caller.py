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
import threading
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("deepseek_caller")

# ── 系统提示词 ──

_SYSTEM_PROMPT = """You are a senior ETH futures trader with 15 years of experience.
Make trading decisions based on multi-dimensional data.

[Position]
- Direction: {position_direction}
- Size: {position_size} ETH
- Entry: {entry_price}
- PnL: {pnl_pct}%
- Market mode: {market_mode}
- Leverage: {leverage}x

[Risk Status]
- Today trades: {daily_trade_count} / {max_daily_trades}
- Today loss: {daily_loss} USDT / {max_daily_loss} USDT
- Consecutive losses: {consecutive_losses} / {max_consecutive_losses}
- Position multiplier: {position_size_multiplier}x
- Max position: {max_position_eth} ETH (~${max_position_value_usdt} USD)

[Multi-Timeframe Indicators]
{agent1_indicators_table}

[Market State]
{market_state_summary}

[Signal Events Summary]
{agent1_summary}

[Trading Advisory]
{agent4_advisory}

[Recent Trades]
{recent_trades_summary}

[News & On-chain]
{agent2_summary}

[Trade History]
- Monthly trades: {monthly_trades}
- Win rate: {win_rate}%
- Monthly PnL: {monthly_pnl} USDT

Reply in strict JSON format:
{{
    "action": "buy" | "sell" | "hold",
    "confidence": 0-100,
    "entry_price_min": "lowest entry price",
    "entry_price_max": "highest entry price",
    "position_size_pct": "position size 0-100, 0=min 100=max, reflects conviction",
    "stop_loss": "stop loss price",
    "take_profit": "take profit price",
    "add_to_position": "optional true/false. Only valid when same direction as current position. true=add to existing position, false=replace (close then reopen). Default: follow conviction-based heuristic",
    "reason": "decision reason in Chinese, 50 chars max"
}}

Rules:
- buy/sell MUST include stop_loss, take_profit, position_size_pct (all required)
- hold: set position_size_pct=0, stop_loss/take_profit can be 0
- position_size_pct reflects conviction: high conviction 70-100, medium 30-70, low 5-30
- stop_loss and take_profit should consider volatility, don't set too tight
- When you already hold a position in the same direction as your new action, set add_to_position=true to average into the existing position. Set add_to_position=false to close and reopen.
- If agent1_summary says "non-technical-trigger", focus on news sentiment and on-chain data
- If agent1_summary says "periodic-check", only trade when conditions are clearly favorable
- Use [Market State] regime guidance: trending → follow trend, ranging → mean-reversion, transition → reduce size
- [Market State] conviction: <0.3 means the classification is unreliable — be conservative; >0.7 means strong alignment — trust the regime
- Base stop_loss on [Multi-Timeframe Indicators] Bollinger bandwidth — wider bands need wider stops
- Bollinger Bandwidth "squeeze 🌀" means the market is coiling (breakout coming soon); expanding bandwidth with trend confirms the trend is real
- Squeezing market + low bandwidth on 1h/1d = reduce position size, trade mean-reversion only
- Expanding bandwidth + clear MACD direction = trend is establishing, follow it with conviction
- Consider [Recent Trades] patterns: identify and avoid repeating recent mistakes
- [Trading Advisory] provides expert-level trading guidance — treat it as recommended strategy
- **Quality over quantity**: if conviction is low (<50) and alignment is unclear, hold. Not every bar needs a trade.
"""

# (不要注入用户输入到 f-string ── 下面用 .format() 安全处理)


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
            logger.warning("DeepSeek API Key was not configured")
        self._client = OpenAI(
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
            timeout=timeout,
        )

        # 统计（thread-safe，asyncio.to_thread 多线程访问）
        self._lock = threading.Lock()
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
        with self._lock:
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
            "agent1_indicators_table": context.get("agent1_indicators_table", "指标数据未就绪"),
            "market_state_summary": context.get("market_state_summary", "市场状态未就绪"),
            "agent4_advisory": context.get("agent4_advisory", "暂无交易建议"),
            "recent_trades_summary": context.get("recent_trades_summary", "暂无近期交易数据"),
            "agent2_summary": context.get("agent2_summary", "暂无数据"),
            "monthly_trades": str(context.get("monthly_trades", 0)),
            "win_rate": str(context.get("win_rate", 0)),
            "monthly_pnl": str(context.get("monthly_pnl", 0)),
            "market_mode": context.get("market_mode", "futures"),
            "leverage": str(context.get("leverage", 10)),
            "max_position_eth": str(context.get("max_position_eth", 0.5)),
        }
        # Calculate max opening value
        price_ct = context.get("current_price", 0)
        max_eth = context.get("max_position_eth", 0.5)
        prompt_kwargs["max_position_value_usdt"] = str(round(float(max_eth) * float(price_ct), 0))

        system_prompt = _SYSTEM_PROMPT.format(**prompt_kwargs)

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (
                        f"Current ETH price: ${context.get('current_price', 0)}\n"
                        "Please provide your trading decision."
                    )},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            content = resp.choices[0].message.content or ""
            return self._parse_response(content, context.get("current_price", 0))

        except Exception as e:
            with self._lock:
                self.total_errors += 1
            logger.error(f"DeepSeek API 调用失败: {e}")
            return self._fallback_decision(context.get("current_price", 0))

    # ── 交易报告分析 ──

    _TRADE_REPORT_SYSTEM_PROMPT = """你是一个量化交易分析 AI。分析以下交易数据，识别盈利和亏损的模式。

【周期信息】
- 周期类型: {period_type}
- 时间范围: {period_start} ~ {period_end}

【统计概览】
- 总交易: {trades} trades
- 盈利: {wins} trades
- 亏损: {losses} trades
- 胜率: {win_rate}%
- 总盈亏: {total_pnl} USDT
- 最大回撤: {max_drawdown}%

【盈利交易】
{win_details}

【亏损交易】
{loss_details}

请分析以上数据，返回严格的 JSON 格式（不要 markdown 围栏）：
{{
    "wins": {{
        "count": 整数,
        "total_profit": 浮点数,
        "patterns": [
            {{
                "pattern": "盈利模式描述如'MACD金叉+KDJ超卖共振做多'",
                "wins_count": 整数,
                "avg_profit": 浮点数,
                "takeaway": "这个模式值得继续/加强/注意什么"
            }}
        ]
    }},
    "losses": {{
        "count": 整数,
        "total_loss": 浮点数,
        "patterns": [
            {{
                "pattern": "亏损模式描述如'布林带上轨突破追多'",
                "loss_count": 整数,
                "avg_loss": 浮点数,
                "cause": "亏损原因分析",
                "suggestion": "具体的调整建议"
            }}
        ]
    }},
    "summary": "一句话总结（中文，50字内）"
}}

注意：如果全部盈利则 losses.patterns 为空列表，
如果全部亏损则 wins.patterns 为空列表。
"""

    def analyze_trade_report(self, context: dict) -> dict:
        """分析一段周期内的交易盈亏模式，识别盈利规律和亏损原因。

        context 包含:
            period_type: "daily" | "weekly" | "monthly"
            period_start: str (ISO datetime)
            period_end: str (ISO datetime)
            stats: { trades, wins, losses, win_rate, total_pnl, max_drawdown_pct }
            win_trades: [{ pnl, side, reason, entry_price, exit_price }]
            loss_trades: [{ pnl, side, reason, entry_price, exit_price }]

        Returns:
            { wins: { count, total_profit, patterns: [...] },
              losses: { count, total_loss, patterns: [...] },
              summary: "..." }
        """
        with self._lock:
            self.total_calls += 1
        stats = context.get("stats", {})

        # 格式化盈利/亏损 trades 详情
        def _format_trades(trades, label):
            if not trades:
                return f"no_{label}_trades"
            lines = []
            for i, t in enumerate(trades[:10], 1):  # 最多传 10 trades
                reason = t.get("reason", "")[:60]
                lines.append(
                    f"  {i}. side:{t.get('side','')} pnl:{t.get('pnl',0):+.2f} "
                    f"entry:{t.get('entry_price','')} exit:{t.get('exit_price','')} "
                    f"reason:{reason}"
                )
            if len(trades) > 10:
                lines.append(f"  ... {len(trades)-10} more trades")
            return "\n".join(lines)

        prompt_kwargs = {
            "period_type": context.get("period_type", ""),
            "period_start": context.get("period_start", ""),
            "period_end": context.get("period_end", ""),
            "trades": str(stats.get("trades", 0)),
            "wins": str(stats.get("wins", 0)),
            "losses": str(stats.get("losses", 0)),
            "win_rate": str(stats.get("win_rate", 0)),
            "total_pnl": str(stats.get("total_pnl", 0)),
            "max_drawdown": str(stats.get("max_drawdown_pct", 0)),
            "win_details": _format_trades(context.get("win_trades", []), "盈利"),
            "loss_details": _format_trades(context.get("loss_trades", []), "亏损"),
        }

        system_prompt = self._TRADE_REPORT_SYSTEM_PROMPT.format(**prompt_kwargs)

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请分析以上交易数据。"},
                ],
                temperature=0.4,
                max_tokens=2000,
            )
            content = resp.choices[0].message.content or ""
            return self._parse_json_response(content)
        except Exception as e:
            with self._lock:
                self.total_errors += 1
            logger.error(f"DeepSeek 交易报告分析失败: {e}")
            return {
                "wins": {"count": 0, "total_profit": 0, "patterns": []},
                "losses": {"count": 0, "total_loss": 0, "patterns": []},
                "summary": "AI 分析暂不可用",
            }

    # ── Agent 4 复盘分析 ──

    def analyze_review(self, prompt_text: str) -> dict:
        """用 DeepSeek 分析复盘数据（Agent 4 专用）

        Args:
            prompt_text: 完整的复盘 Prompt（已含所有上下文）

        Returns:
            解析后的 JSON dict，含 review_id, summary, market_regime, param_adjustments
            失败时返回 {"summary": "分析失败", "param_adjustments": []}
        """
        with self._lock:
            self.total_calls += 1
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a quantitative trading review AI. Analyze trade data and output JSON format parameter adjustment suggestions."},
                    {"role": "user", "content": prompt_text},
                ],
                temperature=0.4,  # 复盘分析用略高温度以获取多样性洞察
                max_tokens=3000,
            )
            content = resp.choices[0].message.content or ""
            return self._parse_json_response(content)
        except Exception as e:
            with self._lock:
                self.total_errors += 1
            logger.error(f"DeepSeek review analysis failed: {e}")
            return {"summary": "Analysis failed", "param_adjustments": []}

    def _repair_json(self, content: str) -> Optional[dict]:
        """尝试修复被截断/不完整的 JSON

        DeepSeek 可能返回未闭合的 JSON（截断），此方法尝试：
        1. 关闭未闭合的字符串和花括号/方括号
        2. 移除尾部的多余逗号
        """
        # 确保从 { 开始
        start = content.find("{")
        if start == -1:
            return None
        content = content[start:]

        # 逐字符追踪嵌套状态
        result = []
        brace_depth = 0
        bracket_depth = 0
        in_string = False
        escaped = False

        for ch in content:
            if escaped:
                escaped = False
                result.append(ch)
                continue
            if ch == '\\' and in_string:
                escaped = True
                result.append(ch)
                continue
            if ch == '"' and not escaped:
                in_string = not in_string
                result.append(ch)
                continue
            if in_string:
                result.append(ch)
                continue
            if ch == '{':
                brace_depth += 1
                result.append(ch)
            elif ch == '}':
                brace_depth = max(0, brace_depth - 1)
                result.append(ch)
            elif ch == '[':
                bracket_depth += 1
                result.append(ch)
            elif ch == ']':
                bracket_depth = max(0, bracket_depth - 1)
                result.append(ch)
            else:
                result.append(ch)

        # 关闭未闭合的字符串
        if in_string:
            result.append('"')

        output = ''.join(result)
        # 移除闭合括号前的尾随逗号: {...,} → {...}
        output = re.sub(r',\s*([}\]])', r'\1', output)
        # 闭合未关闭的括号
        output += '}' * brace_depth + ']' * bracket_depth

        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return None

    def _parse_json_response(self, content: str) -> dict:
        """Extract JSON from DeepSeek response (general method)"""
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        else:
            start = content.find("{")
            if start != -1:
                end = content.rfind("}")
                if end != -1 and end > start:
                    content = content[start:end + 1]
                else:
                    # 没有找到 } → 整个 { 之后的内容可能是被截断的 JSON
                    content = content[start:]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试修复截断 JSON
        repaired = self._repair_json(content)
        if repaired is not None:
            logger.info(f"DeepSeek JSON 修复成功: {str(repaired)[:100]}...")
            return repaired

        logger.warning(f"DeepSeek JSON parse failed (and repair failed): {content[:200]}")
        return {"summary": "JSON parse failed", "param_adjustments": []}

    def _parse_response(self, content: str, current_price: float) -> dict:
        """Parse DeepSeek response JSON"""

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

        # Validate and fill defaults
        action = result.get("action", "hold")
        if action not in ("buy", "sell", "hold"):
            action = "hold"

        add_to_position_raw = result.get("add_to_position")
        add_to_position = None
        if isinstance(add_to_position_raw, bool):
            add_to_position = add_to_position_raw
        elif isinstance(add_to_position_raw, str):
            add_to_position = add_to_position_raw.lower() == "true"

        return {
            "action": action,
            "confidence": int(result.get("confidence", 0)),
            "entry_price_min": result.get("entry_price_min", ""),
            "entry_price_max": result.get("entry_price_max", ""),
            "position_size_pct": result.get("position_size_pct", ""),
            "stop_loss": result.get("stop_loss", ""),
            "take_profit": result.get("take_profit", ""),
            "add_to_position": add_to_position,
            "reason": result.get("reason", ""),
            "_raw": content[:500],
        }

    def _fallback_decision(self, current_price: float) -> dict:
        """Fallback when API fails - return hold"""
        logger.info("DeepSeek API unavailable, falling back to hold")
        return {
            "action": "hold",
            "confidence": 0,
            "entry_price_min": "",
            "entry_price_max": "",
            "position_size_pct": "",
            "stop_loss": "",
            "take_profit": "",
            "add_to_position": None,
            "reason": "DeepSeek API unavailable, auto-skip",
            "_raw": "",
        }

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_calls": self.total_calls,
                "total_errors": self.total_errors,
                "model": self.model,
            }
