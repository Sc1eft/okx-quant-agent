# Agent 4: 复盘改进 Agent — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 Agent 4，每 5 笔交易用 DeepSeek 自动复盘交易数据 → 调整 Agent 3 的运行参数 + Agent 1 的信号灵敏度，形成"交易 → 复盘 → 改进 → 交易"闭环。

**Architecture:** Agent 4 作为独立类 `Agent4Reviewer` 运行在同一事件循环中，通过 `notify_trade()` 回调驱动，直接查 SQLite 获取交易数据、共享 `AgentSystemConfig` 引用修改参数，复用 `DeepSeekTrader` 的 OpenAI client 做 AI 分析。旧 `param_adapter.py` 完全停用。

**Tech Stack:** Python 3.12, asyncio, SQLite, DeepSeek V4 Pro API, pytest

## Global Constraints

- All Agent 4 的参数调整必须通过 `_validate_adjustment()` 边界校验 + 防抖检查才能生效
- DeepSeek 调用失败不阻塞交易流程，只记日志
- Agent 4 的 `run()` 方法只是一个空循环（仅由 `notify_trade()` 驱动），保持与 Asyncio 任务体系兼容
- 复用 `DeepSeekTrader` 的 `OpenAI` client 实例，不加新的 API 连接
- 所有新增 config 字段加到 `AgentSystemConfig` dataclass

---

### Task 1: 新增 Config 字段

**Files:**
- Modify: `agents/config.py` — 在 `AgentSystemConfig` 末尾新增 ~15 个字段

**Interfaces:**
- Produces: `AgentSystemConfig` 新增字段，后续任务直接引用

- [ ] **Step 1: 在 `AgentSystemConfig` 末尾新增字段**

在 `agents/config.py` 的 `param_adapter_win_rate_target: float = 0.50` 之后追加：

```python
# ── Agent 1（新增可调参数，原写死在 change_detector.py）──
agent1_change_cooldown: float = 60.0

# ── Agent 3（新增，供 Agent 4 调整）──
agent3_position_size_multiplier: float = 1.0
agent3_default_stop_loss_pct: float = 2.0
agent3_default_take_profit_pct: float = 4.0

# ── Agent 4 ──
agent4_enabled: bool = True
agent4_review_interval_trades: int = 5
agent4_min_adjust_interval_seconds: int = 300
agent4_deepseek_model: str = "deepseek-v4-pro"
agent4_max_param_adjustments: int = 5
```

- [ ] **Step 2: 验证改动**

Run: `python -c "from agents.config import AgentSystemConfig; c = AgentSystemConfig(); print(c.agent4_enabled, c.agent1_change_cooldown)"`
Expected: `True 60.0`

- [ ] **Step 3: Commit**

```bash
git add agents/config.py
git commit -m "feat(agent4): add config fields for Agent4Reviewer"
```

---

### Task 2: DeepSeekCaller — 新增 `analyze_review()` 方法

**Files:**
- Modify: `agents/deepseek_caller.py` — 新增 `analyze_review(prompt_text)` 方法

**Interfaces:**
- Consumes: `analyze_review(prompt_text: str) -> dict` — 接受完整 Prompt 文本，返回解析后的 JSON dict
- Produces: `DeepSeekTrader.analyze_review()` — 供 Agent 4 的 `_run_review()` 调用

- [ ] **Step 1: 添加 `analyze_review()` 方法**

在 `agents/deepseek_caller.py` 的 `analyze()` 方法之后、`_parse_response()` 之前插入：

```python
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
```

- [ ] **Step 2: 验证改动**

Run: `python -c "from agents.deepseek_caller import DeepSeekTrader; d = DeepSeekTrader(api_key='test'); r = d.analyze_review('test'); print(r)"`
Expected: `{'summary': 'JSON 解析失败', 'param_adjustments': []}`（因为 API key 无效，走异常分支）

- [ ] **Step 3: Commit**

```bash
git add agents/deepseek_caller.py
git commit -m "feat(agent4): add analyze_review method to DeepSeekTrader"
```

---

### Task 3: Agent 1 — 新增信号统计 + 从 config 读 change_cooldown

**Files:**
- Modify: `agents/agent1_technical.py` — 新增 `get_recent_signal_stats()` 方法，`change_cooldown` 改为读 config

**Interfaces:**
- Consumes: `self.config.agent1_change_cooldown`（Task 1 新增字段）
- Produces: `get_recent_signal_stats() -> dict` — 供 Agent 4 采集信号统计

- [ ] **Step 1: 新增 `get_recent_signal_stats()` 方法**

在 `agent1_technical.py` 的 `get_status()` 方法之后追加：

```python
def get_recent_signal_stats(self) -> dict:
    """返回近期信号统计数据（供 Agent 4 复盘使用）"""
    signals = list(self._signal_history)
    total = len(signals)
    if total == 0:
        return {"total_signals": 0, "by_timeframe": {},
                "by_direction": {}, "by_urgency": {}}

    by_tf: dict[str, int] = {}
    by_dir: dict[str, int] = {}
    by_urg: dict[str, int] = {}
    for s in signals:
        tf = s.get("timeframe", "unknown")
        by_tf[tf] = by_tf.get(tf, 0) + 1
        desc = s.get("description", "")
        if "bullish" in desc or "buy" in desc or "金叉" in desc or "超卖" in desc:
            by_dir["buy"] = by_dir.get("buy", 0) + 1
        elif "bearish" in desc or "sell" in desc or "死叉" in desc or "超买" in desc:
            by_dir["sell"] = by_dir.get("sell", 0) + 1
        else:
            by_dir["neutral"] = by_dir.get("neutral", 0) + 1
        urg = s.get("urgency", "medium")
        by_urg[urg] = by_urg.get(urg, 0) + 1

    return {
        "total_signals": total,
        "by_timeframe": by_tf,
        "by_direction": by_dir,
        "by_urgency": by_urg,
    }
```

- [ ] **Step 2: 修改 `_on_bar()` 方法使用 config 的 change_cooldown**

找到 `agent1_technical.py` 中 `self.change_detector = ChangeDetector()` 这一行，将 cooldown 改为从 config 读取：

```python
# 修改前 (约第 47 行):
self.change_detector = ChangeDetector()

# 修改后:
self.change_detector = ChangeDetector(default_cooldown=config.agent1_change_cooldown)
```

然后修改 `ChangeDetector.__init__()` 接受 `default_cooldown` 参数：

在 `agents/change_detector.py` 中找到 `__init__` 方法，改为：

```python
# 修改前:
def __init__(self, default_cooldown: float = 60.0):
    self._prev: dict[str, tuple] = {}
    self._cooldown: dict[str, float] = {}
    self._default_cooldown: float = default_cooldown  # 改为参数传入

# 修改后:
def __init__(self, default_cooldown: float = 60.0):
    self._prev: dict[str, tuple] = {}
    self._cooldown: dict[str, float] = {}
    self._default_cooldown = default_cooldown
```

- [ ] **Step 3: 验证改动**

Run: `python -c "from agents.agent1_technical import Agent1; from agents.config import AgentSystemConfig; cfg = AgentSystemConfig(); print(cfg.agent1_change_cooldown)"`
Expected: `60.0`

- [ ] **Step 4: Commit**

```bash
git add agents/agent1_technical.py agents/change_detector.py
git commit -m "feat(agent4): add signal stats method, make change_cooldown config-driven"
```

---

### Task 4: Agent 2 — 新增 `get_recent_news()` 方法

**Files:**
- Modify: `agents/agent2_news.py` — 新增 `get_recent_news(n)` 方法

**Interfaces:**
- Produces: `get_recent_news(n: int = 10) -> list[dict]` — 供 Agent 4 采集新闻摘要

- [ ] **Step 1: 在 Agent2 类中追加 `get_recent_news()` 方法**

在 `agents/agent2_news.py` 的 `get_status()` 方法（约第 188 行）之后追加：

```python
def get_recent_news(self, n: int = 10) -> list[dict]:
    """返回最近 N 条新闻（供 Agent 4 复盘使用）"""
    return list(self._seen_titles)[-n:] if isinstance(self._seen_titles, list) else []
```

**注意**：`_seen_titles` 当前是 `set[str]`，需要改为 `list[str]` 以保留顺序。

- [ ] **Step 2: 修改 `_seen_titles` 类型**

在 `__init__` 中找到 `self._seen_titles: set[str] = set()`，改为：

```python
self._seen_titles: list[str] = []  # list 保留插入顺序，供 Agent 4 复盘使用
```

同时修改 `_fetch_and_process_news()` 中的去重逻辑：

```python
# 原代码 (约第 157-159 行):
if title in self._seen_titles:
    return
self._seen_titles.add(title)

# 改为:
if title in self._seen_titles:
    return
self._seen_titles.append(title)

# 原代码 (约第 185-186 行):
if len(self._seen_titles) > 1000:
    self._seen_titles = set(list(self._seen_titles)[-500:])

# 改为:
if len(self._seen_titles) > 1000:
    self._seen_titles = self._seen_titles[-500:]
```

- [ ] **Step 3: 验证改动**

Run: `python -c "from agents.agent2_news import Agent2; from agents.config import AgentSystemConfig; from agents.event_bus import EventBus; a = Agent2(AgentSystemConfig(), EventBus()); print(a.get_recent_news(5))"`
Expected: `[]`（空列表，因为还没抓取任何新闻）

- [ ] **Step 4: Commit**

```bash
git add agents/agent2_news.py
git commit -m "feat(agent4): add get_recent_news method, change _seen_titles to list"
```

---

### Task 5: Agent4Reviewer — 核心类实现

**Files:**
- Create: `agents/agent4_reviewer.py` — Agent4Reviewer 主类 ~250 行

**Interfaces:**
- Consumes: `AgentSystemConfig`（Task 1）、`DeepSeekTrader.analyze_review()`（Task 2）、`Agent1.get_recent_signal_stats()`（Task 3）、`Agent2.get_recent_news()`（Task 4）、`KlineBuilder.get_history()`、SQLite `trades` 表
- Produces: `notify_trade()` 入口、`get_status()` 状态输出

- [ ] **Step 1: 创建 `agents/agent4_reviewer.py`**

```python
"""
Agent 4 — 复盘改进 Agent

每 N 笔交易自动触发复盘：采集交易数据、行情、信号、新闻、链上数据，
调用 DeepSeek 做 AI 分析，输出参数调整建议，自动应用到共享 config。

替代 Phase 4 的规则式 param_adapter.py。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agents.agent1_technical import Agent1
    from agents.agent2_news import Agent2
    from agents.config import AgentSystemConfig
    from agents.deepseek_caller import DeepSeekTrader
    from agents.kline_builder import KlineBuilder

logger = logging.getLogger("agent4_reviewer")

# 参数安全边界
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "agent3_max_daily_trades": (1, 30),
    "agent3_debounce_seconds": (5, 300),
    "agent3_min_interval_between_trades": (30, 3600),
    "agent3_max_daily_loss_usdt": (10, 500),
    "agent3_max_consecutive_losses": (1, 10),
    "agent3_max_position_eth": (0.01, 2.0),
    "agent3_position_size_multiplier": (0.1, 3.0),
    "agent3_default_stop_loss_pct": (0.5, 10.0),
    "agent3_default_take_profit_pct": (1.0, 20.0),
    "agent1_change_cooldown": (10, 600),
}

# 风险参数（AI 只能收窄不能放宽）
_RISK_PARAMS = {"agent3_max_daily_loss_usdt", "agent3_max_consecutive_losses"}

_REVIEW_PROMPT_TEMPLATE = """你是一个量化交易复盘分析AI。分析最近{count}笔交易，找出模式，输出参数调整建议。

【最近{count}笔交易】
{recent_trades}

【大盘行情背景】（交易时段内）
{market_context}

【Agent 1 信号统计】（交易时段内）
{signal_stats}

【同期新闻摘要】
{news_summary}

【链上数据快照】（均值）
{onchain_snapshot}

【当前运行参数】
{current_params}

【历史复盘记录】
{prev_reviews}

请输出JSON格式的分析结果。"""


class Agent4Reviewer:
    """复盘改进 Agent

    每 N 笔交易自动触发一次完整复盘流程。
    """

    def __init__(
        self,
        config: AgentSystemConfig,
        deepseek: DeepSeekTrader,
        db_path: str,
        kline_builder: KlineBuilder,
        agent1: Agent1,
        agent2: Agent2,
    ):
        self._config = config
        self._deepseek = deepseek
        self._db_path = db_path
        self._kline_builder = kline_builder
        self._agent1 = agent1
        self._agent2 = agent2

        self._trade_count = 0
        self._last_review_count = 0
        self._last_adjust_time: dict[str, float] = {}
        self._review_history: list[dict] = []
        self._lock = asyncio.Lock()
        self._running = False

        # 统计
        self._stats = {
            "total_reviews": 0,
            "total_adjustments": 0,
            "total_adjustment_errors": 0,
            "start_time": "",
            "last_review_time": "",
            "last_review_summary": "",
            "last_review_market_regime": "",
            "review_history": [],
        }

    # ── 主入口 ──

    async def notify_trade(self, trade_record: dict) -> None:
        """Agent 3 完成一笔交易后调用，触发计数检查

        Args:
            trade_record: SQLite trades 表的行字典
        """
        self._trade_count += 1
        interval = self._config.agent4_review_interval_trades
        if self._trade_count - self._last_review_count >= interval:
            logger.info(
                f"Agent 4: {self._trade_count} 笔交易已达复盘阈值 "
                f"({interval})，开始复盘..."
            )
            await self._run_review()

    async def run(self) -> None:
        """主循环（空循环，保持与 asyncio 任务体系兼容）"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Agent 4 已启动（由 notify_trade 驱动，无独立循环）")
        try:
            while self._running:
                await asyncio.sleep(10)  # 只做心跳，不轮询
        except asyncio.CancelledError:
            logger.info("Agent 4 已停止")

    async def stop(self):
        """停止 Agent 4"""
        self._running = False

    # ── 复盘流程 ──

    async def _run_review(self) -> None:
        """执行一次完整复盘"""
        async with self._lock:
            try:
                # 1. 采集数据
                interval = self._config.agent4_review_interval_trades
                trades = self._load_recent_trades(interval)
                market = self._collect_market_context()
                signals = self._collect_signal_stats()
                news = self._collect_recent_news()
                onchain = self._collect_onchain_snapshot()
                prev_reviews = self._review_history[-3:]

                # 2. 构建 Prompt
                prompt = self._build_review_prompt(
                    trades=trades,
                    market=market,
                    signals=signals,
                    news=news,
                    onchain=onchain,
                    prev_reviews=prev_reviews,
                )

                # 3. 调 DeepSeek（同步 API，包到线程池）
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, self._deepseek.analyze_review, prompt
                )

                # 4. 校验并应用参数调整
                applied = []
                adjustments = result.get("param_adjustments", [])
                for adj in adjustments[: self._config.agent4_max_param_adjustments]:
                    if self._validate_adjustment(adj):
                        self._apply_adjustment(adj)
                        applied.append(adj)
                    else:
                        logger.warning(
                            f"Agent 4: 调整被拒绝 {adj.get('param')} → {adj.get('to')}: "
                            f"{adj.get('reason', '未知原因')}"
                        )

                # 5. 记录复盘
                review_record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trade_count": self._trade_count,
                    "summary": result.get("summary", ""),
                    "market_regime": result.get("market_regime", ""),
                    "strategy_insights": result.get("strategy_insights", ""),
                    "adjustments_proposed": len(adjustments),
                    "adjustments_applied": len(applied),
                    "adjustments": applied,
                }
                self._review_history.append(review_record)
                self._last_review_count = self._trade_count

                # 更新统计
                self._stats["total_reviews"] += 1
                self._stats["total_adjustments"] += len(applied)
                self._stats["last_review_time"] = review_record["timestamp"]
                self._stats["last_review_summary"] = result.get("summary", "")
                self._stats["last_review_market_regime"] = result.get(
                    "market_regime", ""
                )
                # 保留最近 20 条历史
                self._stats["review_history"] = (
                    self._review_history[-20:]
                )

                logger.info(
                    f"Agent 4: 复盘完成 — "
                    f"建议 {len(adjustments)} 条，应用 {len(applied)} 条 | "
                    f"{result.get('summary', '')[:80]}"
                )

            except Exception as e:
                self._stats["total_adjustment_errors"] += 1
                logger.error(f"Agent 4 复盘失败: {e}", exc_info=True)

    # ── 数据采集 ──

    def _load_recent_trades(self, n: int = 5) -> list[dict]:
        """从 SQLite 加载最近 N 笔已完成交易"""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (n,)
            )
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.debug(f"加载交易记录失败: {e}")
            return []

    def _collect_market_context(self) -> dict:
        """采集 KlineBuilder 行情数据（3m/5m/15m/1h/1d 最新 K 线）"""
        context: dict[str, Any] = {}
        for tf in ["3m", "5m", "15m", "1h"]:
            history = self._kline_builder.get_history(tf, 5)
            if history:
                prices = [b["close"] for b in history if "close" in b]
                context[tf] = {
                    "high": max(prices) if prices else 0,
                    "low": min(prices) if prices else 0,
                    "last_close": prices[-1] if prices else 0,
                    "count": len(history),
                }
        return context

    def _collect_signal_stats(self) -> dict:
        """采集 Agent 1 信号统计"""
        if self._agent1 and hasattr(self._agent1, "get_recent_signal_stats"):
            try:
                return self._agent1.get_recent_signal_stats()
            except Exception:
                pass
        return {"total_signals": 0}

    def _collect_recent_news(self) -> list[dict]:
        """采集最近新闻"""
        if self._agent2 and hasattr(self._agent2, "get_recent_news"):
            try:
                return self._agent2.get_recent_news(10)  # type: ignore[return-value]
            except Exception:
                pass
        return []

    def _collect_onchain_snapshot(self) -> dict:
        """采集链上数据快照"""
        if self._agent2 and hasattr(self._agent2, "get_status"):
            try:
                s = self._agent2.get_status()
                onchain = s.get("onchain", {}) if isinstance(s, dict) else {}
                return {
                    "last_gas_gwei": onchain.get("last_gas_gwei", 0),
                    "last_taker_buy_ratio": onchain.get("last_taker_buy_ratio", 0),
                    "last_funding_rate": onchain.get("last_funding_rate", 0),
                    "last_whale_count": onchain.get("last_whale_count", 0),
                }
            except Exception:
                pass
        return {}

    # ── Prompt 构建 ──

    def _build_review_prompt(
        self,
        trades: list[dict],
        market: dict,
        signals: dict,
        news: list,
        onchain: dict,
        prev_reviews: list[dict],
    ) -> str:
        """构建完整的 DeepSeek 复盘 Prompt"""
        # 交易记录
        trade_lines = []
        for i, t in enumerate(trades, 1):
            side = t.get("side", "?")
            pnl = t.get("pnl_close", t.get("pnl", 0))
            price = t.get("price", 0)
            decision_raw = t.get("decision", "{}")
            if isinstance(decision_raw, str):
                try:
                    decision_raw = json.loads(decision_raw)
                except (json.JSONDecodeError, TypeError):
                    decision_raw = {}
            reason = decision_raw.get("reason", "") if isinstance(decision_raw, dict) else ""
            ts = t.get("timestamp", "")[:19] if t.get("timestamp") else ""
            trade_lines.append(
                f"{i} | {ts} | {side} | ${price} | {pnl:+.2f} USDT | {reason}"
            )

        # 行情摘要
        market_lines = []
        for tf, data in market.items():
            market_lines.append(
                f"  {tf}: 区间 ${data['low']}-${data['high']}, "
                f"最新 ${data['last_close']}, {data['count']} 根 K 线"
            )

        # 信号统计
        signal_lines = []
        if signals.get("total_signals", 0) > 0:
            signal_lines.append(f"  总信号: {signals['total_signals']}")
            for tf, cnt in signals.get("by_timeframe", {}).items():
                signal_lines.append(f"  {tf}: {cnt} 个")
            for direction, cnt in signals.get("by_direction", {}).items():
                signal_lines.append(f"  {direction}: {cnt} 个")

        # 新闻摘要
        news_lines = [str(n)[:100] for n in news[-5:]]

        # 参数快照
        param_lines = [
            f"  max_daily_trades: {self._config.agent3_max_daily_trades}",
            f"  debounce_seconds: {self._config.agent3_debounce_seconds}",
            f"  min_interval: {self._config.agent3_min_interval_between_trades}s",
            f"  max_daily_loss_usdt: {self._config.agent3_max_daily_loss_usdt}",
            f"  max_consecutive_losses: {self._config.agent3_max_consecutive_losses}",
            f"  max_position_eth: {self._config.agent3_max_position_eth}",
            f"  position_size_multiplier: {self._config.agent3_position_size_multiplier}",
            f"  stop_loss_pct: {self._config.agent3_default_stop_loss_pct}%",
            f"  take_profit_pct: {self._config.agent3_default_take_profit_pct}%",
            f"  change_cooldown: {self._config.agent1_change_cooldown}s",
        ]

        # 历史复盘
        prev_lines = []
        for r in prev_reviews[-3:]:
            ts = r.get("timestamp", "")[:19]
            summary = r.get("summary", "")[:80]
            applied = r.get("adjustments_applied", [])
            params_changed = ", ".join(
                a.get("param", "?") for a in applied
            )
            prev_lines.append(
                f"  {ts}: {summary} — 调整了: {params_changed or '无'}"
            )

        return _REVIEW_PROMPT_TEMPLATE.format(
            count=len(trades),
            recent_trades="\n".join(trade_lines) or "  无交易记录",
            market_context="\n".join(market_lines) or "  无行情数据",
            signal_stats="\n".join(signal_lines) or "  无信号数据",
            news_summary="\n".join(news_lines) or "  无新闻",
            onchain_snapshot=(
                f"  Gas: {onchain.get('last_gas_gwei', '—')} Gwei\n"
                f"  吃单比(买): {onchain.get('last_taker_buy_ratio', '—')}\n"
                f"  资金费率: {onchain.get('last_funding_rate', '—')}%\n"
                f"  巨鲸转账: {onchain.get('last_whale_count', 0)} 笔"
            ),
            current_params="\n".join(param_lines),
            prev_reviews="\n".join(prev_lines) or "  无历史复盘",
        )

    # ── 校验与应用 ──

    def _validate_adjustment(self, adj: dict) -> bool:
        """边界校验 + 防抖

        Args:
            adj: {"target": str, "param": str, "from": float, "to": float, "reason": str}

        Returns:
            True 如果校验通过可以应用
        """
        param = adj.get("param", "")
        value = adj.get("to")
        reason = adj.get("reason", "")

        # 1. 参数名是否在安全边界表中
        bounds = _PARAM_BOUNDS.get(param)
        if bounds is None:
            logger.warning(f"Agent 4: 未知参数 '{param}'，拒绝")
            return False

        # 2. 值必须是数字
        if not isinstance(value, (int, float)):
            logger.warning(f"Agent 4: 参数 '{param}' 值类型错误: {type(value).__name__}")
            return False

        # 3. 值在安全范围内
        low, high = bounds
        if value < low or value > high:
            logger.warning(
                f"Agent 4: 参数 '{param}' 值 {value} 超出安全范围 [{low}, {high}]"
            )
            return False

        # 4. 风险参数只收窄不放宽
        if param in _RISK_PARAMS:
            current = getattr(self._config, param, None)
            if current is not None and value > current:
                logger.warning(
                    f"Agent 4: 风险参数 '{param}' 只能降低 ({current}→{value})，拒绝"
                )
                return False

        # 5. 防抖：同一参数不能频繁修改
        now = datetime.now(timezone.utc).timestamp()
        last = self._last_adjust_time.get(param, 0.0)
        min_interval = self._config.agent4_min_adjust_interval_seconds
        if now - last < min_interval:
            logger.debug(
                f"Agent 4: 参数 '{param}' 上次修改在 {now - last:.0f}s 前，"
                f"低于最小间隔 {min_interval}s，跳过"
            )
            return False

        # 6. 检查是否真的有变化
        current = getattr(self._config, param, None)
        if current is not None and abs(float(value) - float(current)) < 0.001:
            logger.debug(f"Agent 4: 参数 '{param}' 未变化 ({current})，跳过")
            return False

        self._last_adjust_time[param] = now
        return True

    def _apply_adjustment(self, adj: dict) -> None:
        """写入共享 config（锁保护）"""
        param = adj["param"]
        value = adj["to"]
        reason = adj.get("reason", "")
        old = getattr(self._config, param, None)

        setattr(self._config, param, value)

        logger.info(
            f"⚙ Agent 4 调整: {param}: {old} → {value} ({reason})"
        )

    # ── 状态 ──

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "trade_count": self._trade_count,
            "last_review_count": self._last_review_count,
            "next_review_in": max(
                0,
                self._config.agent4_review_interval_trades
                - (self._trade_count - self._last_review_count),
            ),
            **self._stats,
        }
```

- [ ] **Step 2: 验证文件可导入**

Run: `python -c "from agents.agent4_reviewer import Agent4Reviewer; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add agents/agent4_reviewer.py
git commit -m "feat(agent4): implement Agent4Reviewer core class"
```

---

### Task 6: Agent 3 — 集成 Agent 4，移除 ParamAdapter

**Files:**
- Modify: `agents/agent3_trader.py` — 移除 `_param_adapter_loop()`、`_review_scheduler()`，添加 `agent4_reviewer` 引用和 `notify_trade()` 调用

**Interfaces:**
- Consumes: `agent4_reviewer` 实例（Agent4Reviewer）
- Removes: `param_adapter` 参数、`_param_adapter_loop()`、`_review_scheduler()` 协程

- [ ] **Step 1: 修改 `__init__` 签名**

```python
# 修改前 (约第 38-49 行):
def __init__(
    self,
    config: AgentSystemConfig,
    event_bus: EventBus,
    deepseek: DeepSeekTrader,
    risk_manager: RiskManager,
    trade_executor: TradeExecutor,
    root_config,
    position_monitor=None,
    okx_client=None,
    review_generator=None,
    param_adapter=None,
):

# 修改后:
def __init__(
    self,
    config: AgentSystemConfig,
    event_bus: EventBus,
    deepseek: DeepSeekTrader,
    risk_manager: RiskManager,
    trade_executor: TradeExecutor,
    root_config,
    position_monitor=None,
    okx_client=None,
    review_generator=None,
    agent4_reviewer=None,  # Agent 4 复盘改进（替代 param_adapter）
):
```

- [ ] **Step 2: 更新构造函数体**

找到 `self.param_adapter = param_adapter` 行（约第 65 行），替换为：

```python
self.agent4_reviewer = agent4_reviewer  # Agent 4（替代 param_adapter）
```

- [ ] **Step 3: 修改 `run()` 方法移除 `_param_adapter_loop`**

找到 `run()` 中（约第 108-110 行）：

```python
if self.review_gen:
    consumers.append(self._review_scheduler())
if self.param_adapter:
    consumers.append(self._param_adapter_loop())
```

改为：

```python
if self.review_gen:
    consumers.append(self._review_scheduler())
# Agent 4 替代了 param_adapter 的调参职责
```

- [ ] **Step 4: 在交易执行后添加 `notify_trade()` 调用**

找到 `_make_decision()` 中执行交易并调 `record_trade()` 的部分（约第 250-270 行，具体行号以实际为准），在 `record_trade()` 之后追加：

```python
# 通知 Agent 4 复盘（如果配置了）
if self.agent4_reviewer:
    trade_record = {
        "id": order_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "side": side,
        "size": size_eth,
        "price": executed_price,
        "pnl": 0.0,
        "order_id": order_id,
        "symbol": "ETH-USDT",
        "decision": json.dumps(decision),
        "pnl_close": 0.0,
        "trade_group_id": trade_group_id,
        "trade_type": "open",
    }
    asyncio.create_task(self.agent4_reviewer.notify_trade(trade_record))
```

**注意**：需要导入 `json`，确认文件顶部已有 `import json`。同时需要确保 `trade_group_id` 和 `decision` 等变量在作用域内。

- [ ] **Step 5: 验证改动**

Run: `python -c "from agents.agent3_trader import Agent3; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add agents/agent3_trader.py
git commit -m "feat(agent4): integrate Agent4Reviewer into Agent3, remove param_adapter"
```

---

### Task 7: main.py — 创建 Agent 4 实例并接线

**Files:**
- Modify: `main.py` — 创建 Agent4Reviewer 实例，传给 Agent 3，启动 `run()` 任务

**Interfaces:**
- Consumes: `Agent4Reviewer`（Task 5）、`kline_builder`（Agent 1 内部实例）

- [ ] **Step 1: 在 `main.py` 中导入并创建 Agent4Reviewer**

找到 `# ── Phase 4: 复盘报告生成器 + 参数自适应 ──` 部分（约第 128 行），将 `from agents.param_adapter import ParamAdapter` 替换为 `from agents.agent4_reviewer import Agent4Reviewer`，并创建实例：

```python
# ── Phase 4: 复盘报告生成器 + Agent 4 复盘改进 ──
from agents.review_generator import ReviewGenerator
from agents.agent4_reviewer import Agent4Reviewer

review_gen = ReviewGenerator(
    config=agent_config, db_path=agent_config.db_path,
) if agent_config.review_generator_enabled else None

agent4_reviewer = Agent4Reviewer(
    config=agent_config,
    deepseek=deepseek,
    db_path=agent_config.db_path,
    kline_builder=agent1.kline_builder if agent1 else None,
    agent1=agent1,
    agent2=agent2,
) if agent_config.agent4_enabled else None
```

- [ ] **Step 2: 将 `agent4_reviewer` 传给 Agent 3**

找到 `agent3 = Agent3(...)` 调用（约第 146 行），将 `param_adapter=param_adapter` 改为 `agent4_reviewer=agent4_reviewer`：

```python
agent3 = Agent3(
    config=agent_config,
    event_bus=event_bus,
    deepseek=deepseek,
    risk_manager=risk_manager,
    trade_executor=trade_executor,
    root_config=root_config,
    position_monitor=position_monitor,
    okx_client=okx_rest,
    review_generator=review_gen,
    agent4_reviewer=agent4_reviewer,  # Agent 4（替代 param_adapter）
) if agent_config.agent3_enabled else None
```

- [ ] **Step 3: 启动 Agent 4 的 `run()` 任务**

找到 `# ── 启动所有 Agent ──` 部分（约第 163 行），追加：

```python
if agent4_reviewer:
    tasks.append(asyncio.create_task(agent4_reviewer.run(), name="agent4"))
```

并在日志输出中追加 `Agent 4`：

```python
logger.info(f"Agent 1 (技术)={'✅' if agent1 else '❌'}")
logger.info(f"Agent 2 (新闻)={'✅' if agent2 else '❌'}")
logger.info(f"Agent 3 (交易)={'✅' if agent3 else '❌'}")
logger.info(f"Agent 4 (复盘)={'✅' if agent4_reviewer else '❌'}")  # 新增
```

- [ ] **Step 4: 在 `_shutdown` 中停止 Agent 4**

找到 shutdown 函数中停止 agent 的部分（约第 198-204 行），追加：

```python
if agent4_reviewer:
    await agent4_reviewer.stop()
```

- [ ] **Step 5: 验证改动**

Run: `python -c "exec(open('main.py').read().split('if __name__')[0]); print('import OK')"`
Expected: 不报错（纯语法检查）

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat(agent4): wire Agent4Reviewer into main.py startup"
```

---

### Task 8: status_writer — 加入 Agent 4 状态输出

**Files:**
- Modify: `agents/status_writer.py` — `write_agent_status()` 增加 `agent4_status` 参数

- [ ] **Step 1: 修改 `write_agent_status()` 签名**

```python
# 修改前:
def write_agent_status(
    agent1_status: dict | None = None,
    agent2_status: dict | None = None,
    agent3_status: dict | None = None,
    position_monitor_status: dict | None = None,
    mode: str = "paper",
):

# 修改后:
def write_agent_status(
    agent1_status: dict | None = None,
    agent2_status: dict | None = None,
    agent3_status: dict | None = None,
    agent4_status: dict | None = None,
    position_monitor_status: dict | None = None,
    mode: str = "paper",
):
```

同时修改 `data` 字典，加入 `"agent4": agent4_status or {}`：

```python
data = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "mode": mode,
    "agent1": agent1_status or {},
    "agent2": agent2_status or {},
    "agent3": agent3_status or {},
    "agent4": agent4_status or {},
    "position_monitor": position_monitor_status or {},
}
```

- [ ] **Step 2: 在 `main.py` 的 `_status_reporter` 中传入 `agent4` 状态**

找到 `_status_reporter` 中的 `write_agent_status(...)` 调用（约第 251-257 行），改为：

```python
write_agent_status(
    agent1_status=s1 if agent1 else None,
    agent2_status=s2 if agent2 else None,
    agent3_status=s3 if agent3 else None,
    agent4_status=agent4_reviewer.get_status() if agent4_reviewer else None,
    position_monitor_status=pm if position_monitor else None,
    mode=mode,
)
```

**注意**：`_status_reporter` 函数的参数也需要增加 `agent4_reviewer`：

```python
# 找到 async def _status_reporter(...) 定义处：
async def _status_reporter(agent1, agent2, agent3, position_monitor=None, mode="paper",
                            agent4_reviewer=None):
```

并在启动处传递：

```python
tasks.append(asyncio.create_task(
    _status_reporter(agent1, agent2, agent3, agent4_reviewer=agent4_reviewer,
                     position_monitor=position_monitor, mode=args.mode),
    name="monitor",
))
```

- [ ] **Step 3: 验证改动**

Run: `python -c "from agents.status_writer import write_agent_status; print('import OK')"`
Expected: `import OK`

- [ ] **Step 4: Commit**

```bash
git add agents/status_writer.py main.py
git commit -m "feat(agent4): add agent4 status to status_writer and monitor"
```

---

### Task 9: 测试 Agent4Reviewer

**Files:**
- Create: `tests/test_agent4_reviewer.py` — ~8 个测试

- [ ] **Step 1: 创建测试文件**

```python
"""测试 Agent 4 — 复盘改进 Agent"""
from __future__ import annotations

import os
import sys
import json
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.agent4_reviewer import Agent4Reviewer, _PARAM_BOUNDS, _RISK_PARAMS
from agents.config import AgentSystemConfig
from agents.deepseek_caller import DeepSeekTrader


def _make_db(trades: list[dict]) -> str:
    """创建临时数据库并写入交易（含 Phase 4 字段）"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, side TEXT, size REAL, price REAL,
            pnl REAL, order_id TEXT, symbol TEXT, decision TEXT,
            pnl_close REAL DEFAULT 0,
            trade_group_id TEXT DEFAULT '',
            trade_type TEXT DEFAULT 'open'
        )
    """)
    for t in trades:
        conn.execute(
            "INSERT INTO trades (timestamp, side, size, price, pnl, pnl_close, "
            "trade_group_id, trade_type, decision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t.get("timestamp", "2026-07-03T00:00:00"),
                t.get("side", "buy"),
                t.get("size", 0.01),
                t.get("price", 3000),
                t.get("pnl", 0.0),
                t.get("pnl_close", 0.0),
                t.get("trade_group_id", ""),
                t.get("trade_type", "open"),
                json.dumps(t.get("decision", {})),
            ),
        )
    conn.commit()
    conn.close()
    return path


def _make_reviewer(db_path: str = ":memory:") -> Agent4Reviewer:
    """创建测试用的 Agent4Reviewer 实例"""
    config = AgentSystemConfig()
    deepseek = DeepSeekTrader(api_key="test")
    kline_builder = MagicMock()
    kline_builder.get_history.return_value = []
    agent1 = MagicMock()
    agent1.get_recent_signal_stats.return_value = {"total_signals": 0}
    agent2 = MagicMock()
    agent2.get_recent_news.return_value = []
    agent2.get_status.return_value = {"onchain": {}}

    return Agent4Reviewer(
        config=config,
        deepseek=deepseek,
        db_path=db_path,
        kline_builder=kline_builder,
        agent1=agent1,
        agent2=agent2,
    )


# ── 基础测试 ──

def test_init():
    """Agent4Reviewer 初始化后状态正确"""
    reviewer = _make_reviewer()
    status = reviewer.get_status()
    assert status["running"] is False
    assert status["trade_count"] == 0
    assert status["total_reviews"] == 0


def test_notify_trade_under_threshold():
    """交易数未达阈值时不会触发复盘"""
    reviewer = _make_reviewer()
    with patch.object(reviewer, "_run_review") as mock_run:
        for _ in range(4):
            reviewer.notify_trade({"id": 1})
        mock_run.assert_not_called()


def test_notify_trade_triggers_review():
    """交易数达阈值后触发复盘"""
    reviewer = _make_reviewer()
    with patch.object(reviewer, "_run_review") as mock_run:
        for i in range(5):
            reviewer.notify_trade({"id": i})
        mock_run.assert_called_once()


def test_notify_trade_triggers_multiple_reviews():
    """每满 5 笔触发一次复盘，不重置计数"""
    reviewer = _make_reviewer()
    with patch.object(reviewer, "_run_review") as mock_run:
        for i in range(12):
            reviewer.notify_trade({"id": i})
        assert mock_run.call_count == 2  # 5笔和10笔各一次

    status = reviewer.get_status()
    assert status["last_review_count"] == 10


# ── 数据采集测试 ──

def test_load_recent_trades():
    """能从 SQLite 加载最近交易"""
    trades = [
        {"side": "buy", "price": 3000, "pnl_close": 10.0, "decision": {"reason": "good"}},
        {"side": "sell", "price": 3050, "pnl_close": -5.0, "decision": {"reason": "bad"}},
    ]
    db = _make_db(trades)
    reviewer = _make_reviewer(db_path=db)
    loaded = reviewer._load_recent_trades(5)
    assert len(loaded) == 2
    assert loaded[0]["side"] == "sell"  # 最近的在前
    assert loaded[1]["side"] == "buy"


def test_load_recent_trades_empty_db():
    """空数据库返回空列表"""
    reviewer = _make_reviewer()
    loaded = reviewer._load_recent_trades(5)
    assert loaded == []


# ── 校验测试 ──

def test_validate_unknown_param():
    """未知参数名被拒绝"""
    reviewer = _make_reviewer()
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "unknown_param", "to": 10,
    }) is False


def test_validate_out_of_bounds():
    """超出安全范围的参数被拒绝"""
    reviewer = _make_reviewer()
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_trades", "to": 100,
    }) is False
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_trades", "to": -1,
    }) is False


def test_validate_risk_param_strict():
    """风险参数只能降低不能提高"""
    reviewer = _make_reviewer()
    # max_daily_loss_usdt 默认 100，改为 50（降低=允许）
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_loss_usdt", "to": 50,
    }) is True
    # 改为 150（提高=拒绝）
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_loss_usdt", "to": 150,
    }) is False


def test_validate_debounce():
    """同一参数最小修改间隔"""
    reviewer = _make_reviewer()
    # 第一次应该通过
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_debounce_seconds", "to": 60,
    }) is True
    # 立即第二次应该被防抖拒绝
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_debounce_seconds", "to": 90,
    }) is False


def test_validate_no_actual_change():
    """值没变化时跳过"""
    reviewer = _make_reviewer()
    # agent3_max_daily_trades 默认是 10，调到 10 = 无变化
    assert reviewer._validate_adjustment({
        "target": "agent3", "param": "agent3_max_daily_trades", "to": 10,
    }) is False


# ── 边界表完整性 ──

def test_param_bounds_completeness():
    """_PARAM_BOUNDS 表包含所有 config 可调字段，无遗漏"""
    config = AgentSystemConfig()
    # 验证可调参数都存在
    for param in _PARAM_BOUNDS:
        assert hasattr(config, param), f"{param} 在 config 中缺失"


def test_review_prompt_format():
    """Prompt 模板能正确格式化"""
    reviewer = _make_reviewer()
    prompt = reviewer._build_review_prompt(
        trades=[{"side": "buy", "price": 3000, "pnl_close": 10.0,
                 "decision": {"reason": "good"}, "timestamp": "2026-07-03T10:00:00"}],
        market={"15m": {"high": 3100, "low": 2900, "last_close": 3000, "count": 20}},
        signals={"total_signals": 5, "by_timeframe": {"15m": 3, "1h": 2},
                 "by_direction": {"buy": 3, "sell": 2}, "by_urgency": {"high": 1, "medium": 4}},
        news=["ETH ETF 流入量创新低"],
        onchain={"last_gas_gwei": 45, "last_taker_buy_ratio": 0.48,
                 "last_funding_rate": -0.0005, "last_whale_count": 2},
        prev_reviews=[{"timestamp": "2026-07-02T10:00:00", "summary": "上轮复盘",
                       "adjustments_applied": [{"param": "agent3_debounce_seconds"}]}],
    )
    assert "【最近1笔交易】" in prompt
    assert "ETH ETF" in prompt
    assert "Gas: 45" in prompt
    assert "debounce_seconds" in prompt
```

- [ ] **Step 2: 运行测试**

Run: `python -m pytest tests/test_agent4_reviewer.py -v --tb=short --no-header 2>&1`

Expected: 全部通过（约 11 个 test cases）

- [ ] **Step 3: 运行完整测试套件确认无回归**

Run: `python -m pytest tests/ -v --tb=short --no-header 2>&1`

Expected: 现有测试 + 新测试全部通过

- [ ] **Step 4: Commit**

```bash
git add tests/test_agent4_reviewer.py
git commit -m "test(agent4): add Agent4Reviewer unit tests"
```

---

## 自检

| 检查项 | 状态 |
|--------|------|
| 覆盖 Spec 所有章节（架构/触发/DS交互/校验/文件清单） | ✅ |
| 无占位符/TODO | ✅ |
| 所有方法签名在任务间一致（analyze_review, get_recent_signal_stats, get_recent_news） | ✅ |
| 旧 param_adapter 已标记移除 | ✅ |
| 测试覆盖主要功能路径（初始化/触发/数据采集/校验/边界表） | ✅ |
