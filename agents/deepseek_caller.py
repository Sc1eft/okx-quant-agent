"""
DeepSeek trades鍐崇瓥璋冪敤鍣?

灏?Agent 1 鐨勬妧鏈潰淇″彿 + Agent 2 鐨勬柊闂?鍩烘湰闈㈡暟鎹?
娉ㄥ叆缁?DeepSeek V4 Pro锛岃幏鍙栦氦鏄撳喅绛栥€?

澶嶇敤鏍?config.py 涓殑 AgentConfig锛坅pi_key, model, base_url, temperature锛夈€?
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("deepseek_caller")

# 鈹€鈹€ 绯荤粺鎻愮ず璇?鈹€鈹€

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
    "reason": "decision reason in Chinese, 50 chars max"
}}

Rules:
- buy/sell MUST include stop_loss, take_profit, position_size_pct (all required)
- hold: set position_size_pct=0, stop_loss/take_profit can be 0
- position_size_pct reflects conviction: high conviction 70-100, medium 30-70, low 5-30
- stop_loss and take_profit should consider volatility, don't set too tight
- If agent1_summary says "non-technical-trigger", focus on news sentiment and on-chain data
- If agent1_summary says "periodic-check", only trade when conditions are clearly favorable
- Use [Market State] regime guidance: trending → follow trend, ranging → mean-reversion, transition → reduce size
- Base stop_loss on [Multi-Timeframe Indicators] Bollinger bandwidth — wider bands need wider stops
- Consider [Recent Trades] patterns: identify and avoid repeating recent mistakes
- [Trading Advisory] provides expert-level trading guidance — treat it as recommended strategy
"""
# (涓嶈娉ㄥ叆鐢ㄦ埛杈撳叆鍒?f-string 鈥?涓嬮潰鐢?.format() 瀹夊叏澶勭悊)


class DeepSeekTrader:
    """DeepSeek trades鍐崇瓥鍣?

    鐢ㄦ硶:
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

        # 缁熻
        self.total_calls = 0
        self.total_errors = 0

    def analyze(self, context: dict) -> dict:
        """璋冪敤 DeepSeek 鍒嗘瀽锛岃繑鍥炰氦鏄撳喅绛?

        context 瀛楁:
            position_direction: "long" / "short" / "none"
            position_size: float
            entry_price: float / ""
            pnl_pct: float / ""
            agent1_summary: str (鎶€鏈潰鎽樿)
            agent2_summary: str (鏂伴椈/鍩烘湰闈㈡憳瑕?
            monthly_trades: int
            win_rate: float
            monthly_pnl: float
            current_price: float
        """
        self.total_calls += 1

        # 瀹夊叏鏋勫缓 prompt锛堜笉浣跨敤 f-string锛岄槻姝㈡敞鍏ワ級
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
            self.total_errors += 1
            logger.error(f"DeepSeek API 璋冪敤澶辫触: {e}")
            return self._fallback_decision(context.get("current_price", 0))

    # 鈹€鈹€ trades鎶ュ憡鍒嗘瀽 鈹€鈹€

    _TRADE_REPORT_SYSTEM_PROMPT = """浣犳槸涓€涓噺鍖栦氦鏄撳垎鏋?AI銆傚垎鏋愪互涓嬩氦鏄撴暟鎹紝璇嗗埆鐩堝埄鍜屼簭鎹熺殑妯″紡銆?

銆愬懆鏈熶俊鎭€?
- 鍛ㄦ湡绫诲瀷: {period_type}
- 鏃堕棿鑼冨洿: {period_start} ~ {period_end}

銆愮粺璁℃瑙堛€?
- 鎬讳氦鏄? {trades} trades
- 鐩堝埄: {wins} trades
- 浜忔崯: {losses} trades
- 鑳滅巼: {win_rate}%
- 鎬荤泩浜? {total_pnl} USDT
- 鏈€澶у洖鎾? {max_drawdown}%

銆愮泩鍒╀氦鏄撱€?
{win_details}

銆愪簭鎹熶氦鏄撱€?
{loss_details}

璇峰垎鏋愪互涓婃暟鎹紝杩斿洖涓ユ牸鐨?JSON 鏍煎紡锛堜笉瑕?markdown 鍥存爮锛夛細
{{
    "wins": {{
        "count": 鏁存暟,
        "total_profit": 娴偣鏁?
        "patterns": [
            {{
                "pattern": "鐩堝埄妯″紡鎻忚堪濡?MACD閲戝弶+KDJ瓒呭崠鍏辨尟鍋氬'",
                "wins_count": 鏁存暟,
                "avg_profit": 娴偣鏁?
                "takeaway": "杩欎釜妯″紡鍊煎緱缁х画/鍔犲己/娉ㄦ剰浠€涔?
            }}
        ]
    }},
    "losses": {{
        "count": 鏁存暟,
        "total_loss": 娴偣鏁?
        "patterns": [
            {{
                "pattern": "浜忔崯妯″紡鎻忚堪濡?甯冩灄甯︿笂杞ㄧ獊鐮磋拷澶?",
                "loss_count": 鏁存暟,
                "avg_loss": 娴偣鏁?
                "cause": "浜忔崯reason鍒嗘瀽",
                "suggestion": "鍏蜂綋鐨勮皟鏁村缓璁?
            }}
        ]
    }},
    "summary": "涓€鍙ヨ瘽鎬荤粨锛堜腑鏂囷紝50瀛楀唴锛?
}}

娉ㄦ剰锛氬鏋滃叏閮ㄧ泩鍒╁垯 losses.patterns 涓虹┖鍒楄〃锛?
濡傛灉鍏ㄩ儴浜忔崯鍒?wins.patterns 涓虹┖鍒楄〃銆?
"""

    def analyze_trade_report(self, context: dict) -> dict:
        """鍒嗘瀽涓€娈靛懆鏈熷唴鐨勪氦鏄撶泩浜忔ā寮忥紝璇嗗埆鐩堝埄瑙勫緥鍜屼簭鎹熷師鍥犮€?

        context 鍖呭惈:
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
        self.total_calls += 1
        stats = context.get("stats", {})

        # 鏍煎紡鍖栫泩鍒?浜忔崯trades璇︽儏
        def _format_trades(trades, label):
            if not trades:
                return f"no_{label}_trades"
            lines = []
            for i, t in enumerate(trades[:10], 1):  # 鏈€澶氫紶 10 trades
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
            "win_details": _format_trades(context.get("win_trades", []), "鐩堝埄"),
            "loss_details": _format_trades(context.get("loss_trades", []), "浜忔崯"),
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
            self.total_errors += 1
            logger.error(f"DeepSeek trades鎶ュ憡鍒嗘瀽澶辫触: {e}")
            return {
                "wins": {"count": 0, "total_profit": 0, "patterns": []},
                "losses": {"count": 0, "total_loss": 0, "patterns": []},
                "summary": "AI 鍒嗘瀽鏆備笉鍙敤",
            }

    # 鈹€鈹€ Agent 4 澶嶇洏鍒嗘瀽 鈹€鈹€

    def analyze_review(self, prompt_text: str) -> dict:
        """鐢?DeepSeek 鍒嗘瀽澶嶇洏鏁版嵁锛圓gent 4 涓撶敤锛?

        Args:
            prompt_text: 瀹屾暣鐨勫鐩?Prompt锛堝凡鍚墍鏈変笂涓嬫枃锛?

        Returns:
            瑙ｆ瀽鍚庣殑 JSON dict锛屽惈 review_id, summary, market_regime, param_adjustments
            澶辫触鏃惰繑鍥?{"summary": "鍒嗘瀽澶辫触", "param_adjustments": []}
        """
        self.total_calls += 1
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a quantitative trading review AI. Analyze trade data and output JSON format parameter adjustment suggestions."},
                    {"role": "user", "content": prompt_text},
                ],
                temperature=0.4,  # 澶嶇洏鍒嗘瀽鐢ㄧ暐楂樻俯搴︿互鑾峰彇澶氭牱鎬ф礊瀵?
                max_tokens=3000,
            )
            content = resp.choices[0].message.content or ""
            return self._parse_json_response(content)
        except Exception as e:
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

        # 鎻愬彇 JSON锛堟敮鎸?```json 鍥存爮 鎴?瑁?JSON锛?
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
            logger.warning(f"DeepSeek 鍝嶅簲 JSON 瑙ｆ瀽澶辫触: {content[:200]}")
            return self._fallback_decision(current_price)

        # Validate and fill defaults
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
            "reason": "DeepSeek API unavailable, auto-skip",
            "_raw": "",
        }

    def get_stats(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "model": self.model,
        }

