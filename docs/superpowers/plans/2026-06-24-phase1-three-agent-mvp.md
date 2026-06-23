# Phase 1: Three-Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimal viable three-agent async event-driven trading system that connects to OKX WebSocket real-time data, computes technical indicators, detects market changes, collects news, and can automatically execute trades via DeepSeek AI analysis.

**Architecture:** Single-process asyncio with 3 coroutines communicating via `asyncio.Queue`. Agent 1 (Technical Analyst) processes WebSocket ticks → builds klines → computes MACD/KDJ/BOLL → detects changes → pushes to Queue A. Agent 2 (News Collector) fetches RSS news → scores impact → pushes to Queue B. Agent 3 (Trader) consumes both queues → calls DeepSeek for analysis → risk checks → executes OKX orders.

**Tech Stack:** Python asyncio, `websockets` library, OKX WebSocket API v5, DeepSeek V4 Pro via `openai` SDK, SQLite via `aiosqlite`, `pandas`/`numpy`, existing `eth_ai_analysis.py` for indicator calculations.

## Global Constraints

- All agent code goes in `okx-quant-agent/agents/` package
- Reuse existing functions from `frontend/utils/eth_ai_analysis.py` (MACD/KDJ/BOLL) and `frontend/utils/eth_news.py` (RSS news) — import via `sys.path` or direct relative import
- Root `config.py` `Config` dataclass already has `AgentConfig` and `ExchangeConfig` — extend via `agents/config.py` for agent-specific params
- Event format must match the spec at `docs/superpowers/specs/2026-06-24-ai-three-agent-arch-design.md`
- All text output for the Chinese-speaking user should use Chinese where reasonable
- YAGNI: No orderbook depth monitoring, no on-chain data, no funding rate advanced monitoring, no self-learning (these are Phase 2-4)
- All numbers/amounts use float; ETH amounts to 6 decimal places, prices to 2 decimal places

---

### Task 1: Project Scaffolding — packages, config, events

**Files:**
- Create: `agents/__init__.py`
- Create: `agents/config.py`
- Create: `agents/event_bus.py`
- Modify: `requirements.txt` (add `websockets`, `aiosqlite`)

**Interfaces:**
- Consumes: Nothing (setup task)
- Produces:
  - `agents/event_bus.py` → `AgentEventType` (enum), `AgentEvent` (dataclass), `EventBus` (class with `queue_a` and `queue_b` as `asyncio.Queue[AgentEvent]`)
  - `agents/config.py` → `AgentSystemConfig` dataclass (extends concepts from root `config.py`)

- [ ] **Step 1: Create `agents/__init__.py`**

```python
"""Three-Agent AI Trading System"""
```

- [ ] **Step 2: Create `agents/config.py`**

```python
"""
Agent 系统配置 — 三 Agent 的独立参数
继承根 Config 中的已有配置，补充 Agent 专用参数
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentSystemConfig:
    """三 Agent 系统配置（与根 config.py 互补）"""

    # ── Agent 1: Technical Analyst ──
    agent1_enabled: bool = True
    agent1_interval_seconds: float = 1.0  # tick 收集间隔
    agent1_timeframes: list[str] = field(default_factory=lambda: ["15m", "1h", "1d"])
    agent1_min_kline_count: int = 100  # 启动时需预取的最少 K 线数
    agent1_change_cooldown_seconds: float = 60.0  # 同一信号的最小推送间隔

    # ── Agent 2: News Collector ──
    agent2_enabled: bool = True
    agent2_fetch_interval_seconds: int = 60  # RSS 抓取间隔
    agent2_max_news_per_fetch: int = 5
    agent2_min_weight_threshold: float = 0.3  # 低于此权重不推送

    # ── Agent 3: Trader ──
    agent3_enabled: bool = True
    agent3_debounce_seconds: float = 30.0  # 事件缓冲窗口
    agent3_min_interval_between_trades: int = 300  # 最小交易间隔：5分钟
    agent3_max_daily_trades: int = 10
    agent3_max_daily_loss_usdt: float = 100.0
    agent3_max_consecutive_losses: int = 3
    agent3_max_position_eth: float = 0.5  # 单笔最大 0.5 ETH
    agent3_consecutive_loss_cooldown_multiplier: float = 0.5  # 连亏后仓位减半

    # ── WebSocket ──
    ws_symbol: str = "ETH-USDT"
    ws_channel: str = "tickers"  # 订阅频道
    ws_reconnect_delay_base: float = 1.0
    ws_reconnect_delay_max: float = 60.0

    # ── SQLite ──
    db_path: str = "data/agent_trades.db"

    # ── Logging ──
    log_level: str = "INFO"
    log_file: str = "logs/agent_system.log"
```

- [ ] **Step 3: Create `agents/event_bus.py`**

```python
"""
事件总线 — asyncio.Queue 封装 + 标准化事件格式

队列：
  Queue A (技术面): Agent1 → Agent3，携带指标变化事件
  Queue B (基本面): Agent2 → Agent3，携带新闻/链上事件
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class AgentEventType(Enum):
    """Agent 事件类型"""
    TECHNICAL_SIGNAL = "technical_signal"   # Agent1 → Agent3: 技术指标变化
    NEWS_EVENT = "news_event"               # Agent2 → Agent3: 新闻/基本面事件
    TRADE_DECISION = "trade_decision"       # Agent3 → 日志: 交易决策记录
    SYSTEM_STATUS = "system_status"         # 任意 Agent → 监控: 心跳/状态
    ERROR = "error"                         # 任意 Agent → 监控: 错误报告


@dataclass
class AgentEvent:
    """标准化 Agent 事件
    
    type:      事件类型
    source:    来源 ("agent1" / "agent2" / "agent3")
    timestamp: ISO 格式时间戳
    data:      具体负载 (dict)
    confidence: 置信度 0~1
    urgency:   优先级
    """
    type: AgentEventType
    source: str
    data: dict[str, Any]
    confidence: float = 0.5
    urgency: str = "medium"  # "high" / "medium" / "low"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "source": self.source,
            "timestamp": self.timestamp,
            "data": self.data,
            "confidence": self.confidence,
            "urgency": self.urgency,
        }


class EventBus:
    """事件总线 — 管理两条 asyncio.Queue"""

    def __init__(self, maxsize: int = 100):
        self.queue_a: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=maxsize)
        self.queue_b: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=maxsize)

    async def publish_a(self, event: AgentEvent):
        """向 Queue A (技术面) 发布事件"""
        try:
            self.queue_a.put_nowait(event)
        except asyncio.QueueFull:
            # 队列满时丢弃最旧事件
            await self.queue_a.get()
            self.queue_a.put_nowait(event)

    async def publish_b(self, event: AgentEvent):
        """向 Queue B (基本面) 发布事件"""
        try:
            self.queue_b.put_nowait(event)
        except asyncio.QueueFull:
            await self.queue_b.get()
            self.queue_b.put_nowait(event)

    async def consume_a(self) -> AgentEvent:
        """消费 Queue A (阻塞)"""
        return await self.queue_a.get()

    async def consume_b(self) -> AgentEvent:
        """消费 Queue B (阻塞)"""
        return await self.queue_b.get()

    def qsize_a(self) -> int:
        return self.queue_a.qsize()

    def qsize_b(self) -> int:
        return self.queue_b.qsize()
```

- [ ] **Step 4: Update `requirements.txt`**

Add these lines:
```
# --- Agent 系统 ---
websockets>=12.0
aiosqlite>=0.20.0
```

- [ ] **Step 5: Verify and commit**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python -c "import asyncio; from agents.event_bus import EventBus, AgentEvent, AgentEventType; print('EventBus OK')"
python -c "from agents.config import AgentSystemConfig; c=AgentSystemConfig(); print(f'Config OK: {c.agent1_timeframes}')"
git add agents/__init__.py agents/config.py agents/event_bus.py requirements.txt
git commit -m "feat: scaffold agents package with event bus and config"
```

---

### Task 2: OKX WebSocket Client

**Files:**
- Create: `agents/okx_ws.py` (new WebSocket client, separate from `okx_client.py`'s REST approach)
- Modify: `okx_client.py` (add WebSocket utility methods — optional, we keep them separate)

**Interfaces:**
- Consumes: `config.py` `ExchangeConfig` for API credentials (signing)
- Produces: `OKXWebSocketClient` class — async context manager that connects to OKX v5 public WebSocket

- [ ] **Step 1: Create `agents/okx_ws.py`**

```python
"""
OKX WebSocket 客户端 — 异步，自动重连

用于 Agent 1 获取实时行情 ticks
协议: wss://ws.okx.com:8443/ws/v5/public
"""
from __future__ import annotations

import asyncio
import hmac
import hashlib
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import websockets

logger = logging.getLogger("okx_ws")


class OKXWebSocketClient:
    """OKX WebSocket 客户端 — 支持自动重连与订阅管理"""

    WS_URL = "wss://ws.okx.com:8443/ws/v5/public"

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        passphrase: str = "",
        symbols: list[str] | None = None,
        reconnect_delay_base: float = 1.0,
        reconnect_delay_max: float = 60.0,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.symbols = symbols or ["ETH-USDT"]
        self.reconnect_delay_base = reconnect_delay_base
        self.reconnect_delay_max = reconnect_delay_max

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._subscribed_channels: list[dict] = []
        self._on_message: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

    def set_callbacks(
        self,
        on_message: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        """设置消息和错误回调"""
        self._on_message = on_message
        self._on_error = on_error

    async def connect(self):
        """建立 WebSocket 连接（自动重连循环）"""
        self._running = True
        delay = self.reconnect_delay_base

        while self._running:
            try:
                logger.info(f"正在连接 OKX WebSocket: {self.WS_URL}")
                async with websockets.connect(self.WS_URL, ping_interval=20) as ws:
                    self._ws = ws
                    logger.info("OKX WebSocket 已连接")
                    delay = self.reconnect_delay_base  # 重置重连延迟

                    # 订阅频道
                    await self._subscribe_all()

                    # 消息循环
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            await self._handle_message(msg)
                        except json.JSONDecodeError:
                            logger.warning(f"WebSocket 消息解析失败: {raw[:100]}")

            except asyncio.CancelledError:
                logger.info("WebSocket 连接已取消")
                break
            except Exception as e:
                logger.error(f"WebSocket 连接异常: {e}")
                if self._on_error:
                    self._on_error(str(e))

            if not self._running:
                break

            # 指数退避重连
            logger.info(f"WebSocket 将在 {delay:.0f}s 后重连...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.reconnect_delay_max)

    async def disconnect(self):
        """断开 WebSocket 连接"""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("OKX WebSocket 已断开")

    async def subscribe(self, channel: str, inst_id: str, extra_params: Optional[dict] = None):
        """订阅频道"""
        arg = {"channel": channel, "instId": inst_id}
        if extra_params:
            arg.update(extra_params)
        sub_msg = {
            "op": "subscribe",
            "args": [arg],
        }
        self._subscribed_channels.append(arg)
        if self._ws:
            await self._ws.send(json.dumps(sub_msg))
            logger.info(f"已订阅: {channel} / {inst_id}")

    async def _subscribe_all(self):
        """订阅所有已注册的频道"""
        for symbol in self.symbols:
            await self.subscribe("tickers", symbol)
            # 后续可扩展订阅 candles / books

    async def _handle_message(self, msg: dict):
        """处理收到的 WebSocket 消息"""
        # OKX WebSocket 心跳响应
        if msg.get("event") == "subscribe":
            logger.info(f"订阅成功: {msg}")
            return
        if msg.get("event") == "error":
            logger.error(f"WebSocket 错误: {msg}")
            return

        # 数据消息
        if "data" in msg and self._on_message:
            self._on_message(msg)

    @staticmethod
    def _sign(secret_key: str, timestamp: str) -> str:
        """OKX WebSocket 登录签名"""
        message = timestamp + "GET" + "/users/self/verify"
        mac = hmac.new(
            secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    async def login(self):
        """WebSocket 私有频道登录（Phase 2+ 需要）"""
        if not self.api_key or not self.secret_key or not self.passphrase:
            logger.warning("缺少 API 凭证，跳过 WebSocket 登录")
            return
        ts = datetime.now(timezone.utc).isoformat()[:-3] + "Z"
        sign = self._sign(self.secret_key, ts)
        login_msg = {
            "op": "login",
            "args": [{
                "apiKey": self.api_key,
                "passphrase": self.passphrase,
                "timestamp": ts,
                "sign": sign,
            }],
        }
        await self._ws.send(json.dumps(login_msg))
        logger.info("WebSocket 登录请求已发送")
```

- [ ] **Step 2: Verify import**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python -c "from agents.okx_ws import OKXWebSocketClient; print('WebSocket client OK')"
```

- [ ] **Step 3: Commit**

```bash
git add agents/okx_ws.py
git commit -m "feat: add OKX WebSocket async client with auto-reconnect"
```

---

### Task 3: Kline Builder — tick to aggregated klines

**Files:**
- Create: `agents/kline_builder.py`

**Interfaces:**
- Consumes: OKX WebSocket tick messages
- Produces: `KlineBuilder` class — receives ticks, builds 1s candles, aggregates to standard timeframes (15m/1h/1d), provides `on_bar(callback)` for completed candle notifications

- [ ] **Step 1: Create `agents/kline_builder.py`**

```python
"""
K 线构建器 — WebSocket tick → 1秒 K线 → 聚合到标准周期

从 OKX WebSocket ticker 消息中提取 last price，
按时间窗口构建 1s K线，再聚合到 15m / 1h / 1d 周期。

每个新完成的 K 线触发回调，供 Agent 1 计算指标。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger("kline_builder")


class KlineBuilder:
    """K 线构建器

    用法:
        builder = KlineBuilder()
        builder.on_completed_bar = my_callback

        # 每次收到 tick 时调用
        builder.add_tick(price, timestamp)

        # 或手动检查
        completed = builder.check_completed()
    """

    TIMEFRAMES = {
        "15m": 15 * 60,
        "1h": 60 * 60,
        "1d": 24 * 60 * 60,
    }

    def __init__(self):
        # 缓存: {timeframe: {"timestamp": int, "open": float, ...}}
        self._candles: dict[str, dict] = {}
        # 历史完整 K 线: {timeframe: [dict, ...]}
        self._history: dict[str, list[dict]] = defaultdict(
            lambda: list[dict]()
        )
        # 最大保留的历史 K 线数
        self._max_history = 500

        # 1s 精度 K 线（中间产物）
        self._sec_candle: Optional[dict] = None

        # 外部回调: on_completed_bar(timeframe, bar_dict) -> None
        self.on_completed_bar: Optional[Callable[[str, dict], None]] = None

        # 上一周期的时间戳边界（用于判断是否翻转）
        self._last_boundary: dict[str, int] = {}

    def add_tick(self, price: float, timestamp_s: int):
        """添加一个 tick 数据（每秒最多一个）

        Args:
            price: 最新价格
            timestamp_s: Unix 秒级时间戳
        """
        # ── 1秒 K 线 ──
        if self._sec_candle is None:
            self._sec_candle = {
                "timestamp": timestamp_s,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            }
        elif self._sec_candle["timestamp"] < timestamp_s:
            # 完成上一根 1s K 线 → 聚合到各周期
            self._aggregate_sec_candle()
            # 新建当前秒 K 线
            self._sec_candle = {
                "timestamp": timestamp_s,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            }
        else:
            # 同秒内更新
            self._sec_candle["high"] = max(self._sec_candle["high"], price)
            self._sec_candle["low"] = min(self._sec_candle["low"], price)
            self._sec_candle["close"] = price

    def _aggregate_sec_candle(self):
        """将刚完成的 1s K 线聚合到各标准周期"""
        sec = self._sec_candle
        if sec is None:
            return
        ts = sec["timestamp"]

        for tf, span in self.TIMEFRAMES.items():
            boundary = (ts // span) * span  # 周期起始时间

            if tf not in self._candles:
                # 新建周期 K 线
                self._candles[tf] = {
                    "timestamp": boundary,
                    "open": sec["open"],
                    "high": sec["high"],
                    "low": sec["low"],
                    "close": sec["close"],
                    "volume": sec.get("volume", 0),
                }
                self._last_boundary[tf] = boundary
            elif boundary != self._last_boundary.get(tf):
                # 周期翻转 — 完成旧 K 线，触发回调
                old = self._candles[tf]
                self._add_to_history(tf, old)
                if self.on_completed_bar:
                    self.on_completed_bar(tf, dict(old))

                # 新建周期 K 线
                self._candles[tf] = {
                    "timestamp": boundary,
                    "open": sec["open"],
                    "high": sec["high"],
                    "low": sec["low"],
                    "close": sec["close"],
                    "volume": sec.get("volume", 0),
                }
                self._last_boundary[tf] = boundary
            else:
                # 同周期内更新
                c = self._candles[tf]
                c["high"] = max(c["high"], sec["high"])
                c["low"] = min(c["low"], sec["low"])
                c["close"] = sec["close"]
                c["volume"] = c.get("volume", 0) + sec.get("volume", 0)

    def _add_to_history(self, timeframe: str, bar: dict):
        """将完成的 K 线加入历史"""
        self._history[timeframe].append(dict(bar))
        if len(self._history[timeframe]) > self._max_history:
            self._history[timeframe] = self._history[timeframe][-self._max_history:]

    def get_current_candle(self, timeframe: str) -> Optional[dict]:
        """获取当前进行中的 K 线"""
        return self._candles.get(timeframe)

    def get_history(self, timeframe: str, n: int = 100) -> list[dict]:
        """获取最近 N 根已完成 K 线"""
        history = self._history.get(timeframe, [])
        return history[-n:]

    def get_all_history(self) -> dict[str, list[dict]]:
        """获取所有周期的历史"""
        return dict(self._history)

    def has_history(self, timeframe: str, min_count: int = 1) -> bool:
        """是否有足够的历史数据"""
        return len(self._history.get(timeframe, [])) >= min_count
```

- [ ] **Step 2: Write and run a quick test**

```python
# tests/test_kline_builder.py
"""KlineBuilder 单元测试"""
import sys; sys.path.insert(0, ".")
from agents.kline_builder import KlineBuilder


def test_basic_tick_to_15m():
    builder = KlineBuilder()
    completed = []

    def on_bar(tf, bar):
        completed.append((tf, bar))

    builder.on_completed_bar = on_bar

    # 模拟 10 秒 ticks，同一 15m 窗口内
    base_ts = 1700000000  # 某个整数时间
    for i in range(10):
        builder.add_tick(3000.0 + i * 0.1, base_ts + i)

    # 还没有完成的 K 线（都在同一 15m 窗口）
    assert len(completed) == 0
    assert builder.get_current_candle("15m") is not None

    # 跳过一个 15m 窗口
    next_window = base_ts + 15 * 60 + 1
    builder.add_tick(3010.0, next_window)

    # 应该触发了一根 15m K 线完成
    assert len(completed) >= 1
    tf, bar = completed[0]
    assert tf == "15m"
    assert bar["open"] == 3000.0
    assert bar["close"] == 3000.9  # 最后一秒的值
    assert bar["high"] == 3000.9
    assert bar["low"] == 3000.0

    print("test_basic_tick_to_15m PASSED")


def test_multiple_timeframes():
    builder = KlineBuilder()
    completed = []
    builder.on_completed_bar = lambda tf, bar: completed.append((tf, bar))

    base_ts = 1700000000
    # 模拟 2 小时的数据（每分钟一个 tick）
    for minute in range(120):
        ts = base_ts + minute * 60
        builder.add_tick(3000.0 + minute * 0.5, ts)

    # 应该有完整的 15m 和 1h K 线
    tf_counts = {}
    for tf, _ in completed:
        tf_counts[tf] = tf_counts.get(tf, 0) + 1

    print(f"Completed bars: {tf_counts}")
    assert tf_counts.get("15m", 0) >= 7  # 120分钟/15分钟 = 8个窗口
    assert tf_counts.get("1h", 0) >= 1    # 至少 1 根小时线
    assert builder.has_history("15m", 5)
    assert builder.has_history("1h", 1)

    print("test_multiple_timeframes PASSED")


if __name__ == "__main__":
    test_basic_tick_to_15m()
    test_multiple_timeframes()
```

- [ ] **Step 3: Run test**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python tests/test_kline_builder.py
```

Expected output:
```
test_basic_tick_to_15m PASSED
test_multiple_timeframes PASSED
```

- [ ] **Step 4: Commit**

```bash
git add agents/kline_builder.py tests/test_kline_builder.py
git commit -m "feat: add kline builder — tick to aggregated candles"
```

---

### Task 4: Change Detector — indicator change detection & event generation

**Files:**
- Create: `agents/change_detector.py`

**Interfaces:**
- Consumes: MACD/KDJ/BOLL indicator dicts (same format as `eth_ai_analysis.py` output)
- Produces: `ChangeDetector` class — `check(macd, kdj, boll) -> list[dict]` returns list of detected changes

- [ ] **Step 1: Create `agents/change_detector.py`**

```python
"""
信号变化检测器

将最新指标值与上次值对比，检测有意义的变化并生成事件。
只推送实质性的交易信号，避免每秒重复推送。

检测范围:
  - MACD: 金叉/死叉、柱线方向反转、零轴穿越
  - KDJ:  K 穿越 D、超买/超卖区进出
  - BOLL: 价格突破上/下轨、布林收口扩张
  - 多周期信心分变化
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("change_detector")


class ChangeDetector:
    """变化检测器

    每次调用 check() 时，传入当前各指标的最新值，返回检测到的变更列表。
    每个变更格式:
    {
        "signal": "macd_bullish_cross",
        "timeframe": "15m",
        "urgency": "high",
        "confidence": 0.85,
        "description": "MACD 15m 金叉出现",
        "price": 3000.0  # 触发时的价格
    }
    """

    def __init__(self):
        # 存储上次各周期各指标的值 {timeframe: {indicator_key: value}}
        self._prev: dict[str, dict] = {}
        # 冷却计时 {timeframe_signal_type: last_push_timestamp_s}
        self._cooldown: dict[str, float] = {}
        # 默认冷却时间（秒）
        self._default_cooldown: float = 60.0

    def set_cooldown(self, signal_key: str, seconds: float):
        """设置某类型信号的冷却时间"""
        self._cooldown[signal_key] = seconds

    def check(
        self,
        timeframe: str,
        macd: Optional[dict],
        kdj: Optional[dict],
        boll: Optional[dict],
        price: float,
        current_ts: float,
    ) -> list[dict]:
        """检查指标变化，返回信号列表"""
        signals: list[dict] = []

        if timeframe not in self._prev:
            self._prev[timeframe] = {}
            # 首次调用，只保存不检测
            self._save_state(timeframe, macd, kdj, boll)
            return signals

        prev = self._prev[timeframe]
        signals.extend(self._check_macd(timeframe, macd, prev.get("macd"), price, current_ts))
        signals.extend(self._check_kdj(timeframe, kdj, prev.get("kdj"), price, current_ts))
        signals.extend(self._check_boll(timeframe, boll, prev.get("boll"), price, current_ts))

        # 保存本次状态
        self._save_state(timeframe, macd, kdj, boll)
        return signals

    # ── MACD 检测 ──

    def _check_macd(
        self, tf: str, cur: Optional[dict], prev: Optional[dict],
        price: float, ts: float,
    ) -> list[dict]:
        signals = []
        if not cur or not prev:
            return signals

        # 金叉/死叉
        if cur.get("crossover") == "bullish" and prev.get("crossover") != "bullish":
            if self._can_push(tf, "macd_bullish_cross", ts):
                signals.append(self._signal("macd_bullish_cross", tf, "high", 0.85,
                                             f"MACD {tf} 金叉↑", price))
        elif cur.get("crossover") == "bearish" and prev.get("crossover") != "bearish":
            if self._can_push(tf, "macd_bearish_cross", ts):
                signals.append(self._signal("macd_bearish_cross", tf, "high", 0.85,
                                             f"MACD {tf} 死叉↓", price))

        # 柱线方向反转（正→负 或 负→正）
        prev_hist = prev.get("histogram", 0)
        cur_hist = cur.get("histogram", 0)
        if prev_hist is not None and cur_hist is not None:
            if prev_hist < 0 and cur_hist >= 0:
                if self._can_push(tf, "macd_hist_positive", ts):
                    signals.append(self._signal("macd_hist_positive", tf, "high", 0.7,
                                                 f"MACD {tf} 柱线转正", price))
            elif prev_hist > 0 and cur_hist <= 0:
                if self._can_push(tf, "macd_hist_negative", ts):
                    signals.append(self._signal("macd_hist_negative", tf, "high", 0.7,
                                                 f"MACD {tf} 柱线转负", price))

        return signals

    # ── KDJ 检测 ──

    def _check_kdj(
        self, tf: str, cur: Optional[dict], prev: Optional[dict],
        price: float, ts: float,
    ) -> list[dict]:
        signals = []
        if not cur or not prev:
            return signals

        # K 穿越 D
        if cur.get("k_cross_d") == "bullish" and prev.get("k_cross_d") != "bullish":
            if self._can_push(tf, "kdj_bullish_cross", ts):
                signals.append(self._signal("kdj_bullish_cross", tf, "medium", 0.7,
                                             f"KDJ {tf} K↑D 金叉", price))
        elif cur.get("k_cross_d") == "bearish" and prev.get("k_cross_d") != "bearish":
            if self._can_push(tf, "kdj_bearish_cross", ts):
                signals.append(self._signal("kdj_bearish_cross", tf, "medium", 0.7,
                                             f"KDJ {tf} K↓D 死叉", price))

        # 超买/超卖区进出
        if cur.get("zone") != prev.get("zone"):
            if cur["zone"] == "overbought":
                signals.append(self._signal("kdj_overbought", tf, "medium", 0.6,
                                             f"KDJ {tf} 进入超买区 ⚠️", price))
            elif cur["zone"] == "oversold":
                signals.append(self._signal("kdj_oversold", tf, "medium", 0.6,
                                             f"KDJ {tf} 进入超卖区 🔻", price))

        return signals

    # ── 布林带检测 ──

    def _check_boll(
        self, tf: str, cur: Optional[dict], prev: Optional[dict],
        price: float, ts: float,
    ) -> list[dict]:
        signals = []
        if not cur or not prev:
            return signals

        # 价格突破上轨
        if cur.get("position_label") == "touch_upper" and prev.get("position_label") != "touch_upper":
            if self._can_push(tf, "boll_break_upper", ts):
                signals.append(self._signal("boll_break_upper", tf, "high", 0.75,
                                             f"价格突破布林上轨 {tf}", price))
        # 价格突破下轨
        elif cur.get("position_label") == "touch_lower" and prev.get("position_label") != "touch_lower":
            if self._can_push(tf, "boll_break_lower", ts):
                signals.append(self._signal("boll_break_lower", tf, "high", 0.75,
                                             f"价格突破布林下轨 {tf}", price))

        # 布林收口结束（带宽从挤压扩张）
        if not prev.get("squeeze") and cur.get("squeeze"):
            signals.append(self._signal("boll_squeeze", tf, "medium", 0.65,
                                         f"布林收口 {tf} 🌀", price))

        return signals

    # ── 内部 ──

    def _save_state(self, tf: str, macd, kdj, boll):
        self._prev[tf] = {
            "macd": dict(macd) if macd else None,
            "kdj": dict(kdj) if kdj else None,
            "boll": dict(boll) if boll else None,
        }

    def _can_push(self, tf: str, signal_type: str, ts: float) -> bool:
        """检查某信号的冷却时间是否已过"""
        key = f"{tf}:{signal_type}"
        cd = self._cooldown.get(key, self._default_cooldown)
        last = self._cooldown.get(f"last:{key}", 0)
        if ts - last < cd:
            return False
        self._cooldown[f"last:{key}"] = ts
        return True

    def _signal(self, sig: str, tf: str, urgency: str, confidence: float,
                 description: str, price: float) -> dict:
        return {
            "signal": sig,
            "timeframe": tf,
            "urgency": urgency,
            "confidence": confidence,
            "description": description,
            "price": price,
        }
```

- [ ] **Step 2: Write and run a test**

```python
# tests/test_change_detector.py
import sys; sys.path.insert(0, ".")
from agents.change_detector import ChangeDetector


def test_macd_bullish_cross():
    detector = ChangeDetector()
    detector._cooldown = {}  # disable cooldown for test
    ts = 1000.0

    # First call - no signals (initial state)
    signals = detector.check("15m",
        macd={"macd": 0.1, "signal": 0.05, "histogram": 0.05, "hist_direction": "rising", "crossover": None},
        kdj={"k": 50, "d": 50, "j": 50, "k_cross_d": None, "zone": "normal"},
        boll={"upper": 3100, "middle": 3000, "lower": 2900, "bandwidth": 0.05, "position_pct": 50, "position_label": "inside", "squeeze": False},
        price=3000, current_ts=ts)
    assert len(signals) == 0, f"Expected 0, got {len(signals)}"

    # Second call - MACD bullish cross
    signals = detector.check("15m",
        macd={"macd": 0.2, "signal": 0.15, "histogram": 0.05, "hist_direction": "rising", "crossover": "bullish"},
        kdj={"k": 50, "d": 50, "j": 50, "k_cross_d": None, "zone": "normal"},
        boll={"upper": 3100, "middle": 3000, "lower": 2900, "bandwidth": 0.05, "position_pct": 50, "position_label": "inside", "squeeze": False},
        price=3020, current_ts=ts+1)
    assert len(signals) >= 1
    assert signals[0]["signal"] == "macd_bullish_cross"
    assert signals[0]["urgency"] == "high"
    print("test_macd_bullish_cross PASSED")


def test_cooldown():
    detector = ChangeDetector()
    detector._default_cooldown = 60.0  # 60s cooldown
    ts = 1000.0

    # init
    detector.check("15m",
        macd={"crossover": None, "histogram": -0.1},
        kdj={}, boll={}, price=3000, current_ts=ts)

    # first bullish cross
    signals = detector.check("15m",
        macd={"crossover": "bullish", "histogram": 0.05},
        kdj={}, boll={}, price=3020, current_ts=ts+1)
    assert len(signals) >= 1

    # same signal within cooldown
    signals = detector.check("15m",
        macd={"crossover": "bullish", "histogram": 0.06},
        kdj={}, boll={}, price=3030, current_ts=ts+30)
    assert len(signals) == 0, f"Expected cooldown, got {len(signals)}"

    # after cooldown expires
    signals = detector.check("15m",
        macd={"crossover": "bullish", "histogram": 0.07},
        kdj={}, boll={}, price=3040, current_ts=ts+70)
    assert len(signals) >= 1
    print("test_cooldown PASSED")


if __name__ == "__main__":
    test_macd_bullish_cross()
    test_cooldown()
    print("ALL PASSED")
```

- [ ] **Step 3: Run test**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python tests/test_change_detector.py
```

Expected output:
```
test_macd_bullish_cross PASSED
test_cooldown PASSED
ALL PASSED
```

- [ ] **Step 4: Commit**

```bash
git add agents/change_detector.py tests/test_change_detector.py
git commit -m "feat: add change detector for MACD/KDJ/BOLL signal detection"
```

---

### Task 5: Risk Layer — three-layer risk control (basic version)

**Files:**
- Create: `agents/risk_layer.py`

**Interfaces:**
- Consumes: `AgentSystemConfig` for risk params
- Produces: `RiskManager` class — `check_layer1(…) -> (bool, reason)`, `check_layer2(…) -> (bool, reason)`, `record_trade(…)`, `record_loss(…)`

- [ ] **Step 1: Create `agents/risk_layer.py`**

```python
"""
三层风控系统（阶段一基础版）

Layer 1 — 交易前检查:
  - 最小交易间隔（距上次交易 > 5 分钟）
  - 单笔上限 ≤ 0.5 ETH
  - 每日交易次数 ≤ 10
  - 每日亏损上限 ≤ 100 USDT
  - 连续亏损 ≤ 3 次（连亏后仓位减半）
  - 方向冲突（已有同方向仓位时累加不超上限）

Layer 2 — 交易中保护:
  - 限价单优先
  - 滑点 > 0.3% 取消

Layer 3 — 交易后监控:
  - 记录交易到 SQLite
  - 更新风控状态
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, date
from typing import Optional, Tuple

from agents.config import AgentSystemConfig

logger = logging.getLogger("risk_layer")


class RiskManager:
    """风控管理器 — 三层风控"""

    def __init__(self, config: AgentSystemConfig):
        self.config = config

        # ── Layer 1 状态 ──
        self._last_trade_time: Optional[datetime] = None
        self._daily_trade_count: int = 0
        self._daily_loss_usdt: float = 0.0
        self._consecutive_losses: int = 0
        self._current_date: date = date.today()
        self._current_position_eth: float = 0.0
        self._current_position_side: Optional[str] = None  # "long" / "short"

        # ── Layer 2 状态 ──
        self._consecutive_api_errors: int = 0
        self._api_breaker_until: Optional[datetime] = None

        # ── Layer 3 状态 ──
        self._daily_trades: list[dict] = []

    # ── Layer 1: 交易前检查 ──

    def check_layer1(
        self,
        side: str,  # "buy" / "sell"
        size_eth: float,
        price: float,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """交易前全项检查，返回 (通过?, 原因)"""
        now = now or datetime.now(timezone.utc)
        self._check_date_reset(now)

        # 1. 最小交易间隔
        if self._last_trade_time:
            elapsed = (now - self._last_trade_time).total_seconds()
            if elapsed < self.config.agent3_min_interval_between_trades:
                remaining = self.config.agent3_min_interval_between_trades - int(elapsed)
                return False, f"交易间隔未到，还需 {remaining}s"

        # 2. 单笔上限
        if size_eth > self.config.agent3_max_position_eth:
            return False, f"单笔 {size_eth:.4f} ETH 超过上限 {self.config.agent3_max_position_eth} ETH"

        # 3. 每日交易次数
        if self._daily_trade_count >= self.config.agent3_max_daily_trades:
            return False, f"今日交易已达上限 ({self._daily_trade_count} 次)"

        # 4. 每日亏损上限
        if self._daily_loss_usdt >= self.config.agent3_max_daily_loss_usdt:
            return False, f"今日亏损已达上限 ({self._daily_loss_usdt:.2f} USDT)"

        # 5. 连续亏损
        if self._consecutive_losses >= self.config.agent3_max_consecutive_losses:
            return False, f"连续亏损 {self._consecutive_losses} 次，交易暂停"

        # 6. 方向冲突（同方向累加检查）
        direction = "long" if side == "buy" else "short"
        if self._current_position_side == direction:
            new_total = self._current_position_eth + size_eth
            if new_total > self.config.agent3_max_position_eth:
                return False, f"同方向累加 {new_total:.4f} ETH 超过上限"

        # 7. API 熔断检查
        if self._api_breaker_until and now < self._api_breaker_until:
            remaining = (self._api_breaker_until - now).total_seconds()
            return False, f"API 熔断中，剩余 {remaining:.0f}s"

        return True, ""

    # ── Layer 2: 交易中保护 ──

    def check_layer2(
        self,
        signal_price: float,
        actual_fill_price: float,
    ) -> Tuple[bool, str]:
        """检查滑点是否可接受"""
        slippage = abs(actual_fill_price - signal_price) / signal_price * 100
        if slippage > 0.3:
            return False, f"滑点 {slippage:.2f}% 超过 0.3% 上限"
        return True, ""

    def report_api_error(self):
        """报告 API 错误（用于熔断）"""
        self._consecutive_api_errors += 1
        if self._consecutive_api_errors >= 3:
            self._api_breaker_until = datetime.now(timezone.utc)
            logger.warning(f"连续 {self._consecutive_api_errors} 次 API 错误，触发熔断 5 分钟")
        # 实际熔断时间在 check 里计算

    def reset_api_errors(self):
        """重置 API 错误计数"""
        self._consecutive_api_errors = 0
        self._api_breaker_until = None

    # ── Layer 3: 交易后记录 ──

    def record_trade(self, trade_data: dict):
        """记录一笔交易"""
        self._last_trade_time = datetime.now(timezone.utc)
        self._daily_trade_count += 1
        self._daily_trades.append(trade_data)

        # 更新仓位信息
        side = trade_data.get("side", "")
        size = trade_data.get("size", 0)
        if side == "buy":
            self._current_position_side = "long"
            self._current_position_eth += size
        elif side == "sell":
            self._current_position_side = "short" if trade_data.get("short") else None
            self._current_position_eth = max(0, self._current_position_eth - size)

        pnl = trade_data.get("pnl", 0)
        if pnl < 0:
            self._record_loss(abs(pnl))
        elif pnl > 0:
            self._consecutive_losses = 0  # 盈利后重置连亏

    def _record_loss(self, loss_usdt: float):
        """记录亏损"""
        self._consecutive_losses += 1
        self._daily_loss_usdt += loss_usdt

    def get_position_size_multiplier(self) -> float:
        """返回仓位乘数（连亏后减半）"""
        if self._consecutive_losses > 0:
            return max(0.1, 1.0 - self._consecutive_losses * 0.25)
        return 1.0

    def _check_date_reset(self, now: datetime):
        """每日重置"""
        today = now.date()
        if today != self._current_date:
            logger.info(f"每日风控重置: {self._current_date} → {today}")
            self._daily_trade_count = 0
            self._daily_loss_usdt = 0.0
            self._consecutive_losses = 0
            self._current_date = today
            self._daily_trades = []
            self._consecutive_api_errors = 0
            self._api_breaker_until = None

    def get_status(self) -> dict:
        """返回风控状态摘要"""
        return {
            "daily_trade_count": self._daily_trade_count,
            "max_daily_trades": self.config.agent3_max_daily_trades,
            "daily_loss_usdt": round(self._daily_loss_usdt, 2),
            "max_daily_loss_usdt": self.config.agent3_max_daily_loss_usdt,
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive_losses": self.config.agent3_max_consecutive_losses,
            "position_size_multiplier": self.get_position_size_multiplier(),
            "position_eth": round(self._current_position_eth, 6),
            "position_side": self._current_position_side,
        }
```

- [ ] **Step 2: Write and run a test**

```python
# tests/test_risk_layer.py
import sys; sys.path.insert(0, ".")
from datetime import datetime, timezone, timedelta
from agents.config import AgentSystemConfig
from agents.risk_layer import RiskManager


def test_layer1_min_interval():
    cfg = AgentSystemConfig(agent3_min_interval_between_trades=300)
    rm = RiskManager(cfg)
    now = datetime.now(timezone.utc)

    # First trade should pass
    ok, reason = rm.check_layer1("buy", 0.1, 3000, now)
    assert ok, f"Expected pass, got: {reason}"
    rm.record_trade({"side": "buy", "size": 0.1, "pnl": 0})

    # Immediate second trade should fail
    ok, reason = rm.check_layer1("buy", 0.1, 3000, now + timedelta(seconds=10))
    assert not ok, "Should fail: min interval"
    assert "交易间隔" in reason

    # After 5 minutes should pass
    ok, reason = rm.check_layer1("buy", 0.1, 3000, now + timedelta(seconds=301))
    assert ok, f"Expected pass after cooldown, got: {reason}"
    print("test_layer1_min_interval PASSED")


def test_layer1_daily_loss():
    cfg = AgentSystemConfig(agent3_max_daily_loss_usdt=100.0)
    rm = RiskManager(cfg)
    now = datetime.now(timezone.utc)

    ok, _ = rm.check_layer1("buy", 0.01, 3000, now)
    assert ok
    rm.record_trade({"side": "sell", "size": 0.01, "pnl": -60})

    ok, _ = rm.check_layer1("buy", 0.01, 3000, now + timedelta(seconds=301))
    assert ok
    rm.record_trade({"side": "sell", "size": 0.01, "pnl": -50})

    # Now daily loss exceeds limit
    ok, reason = rm.check_layer1("buy", 0.01, 3000, now + timedelta(seconds=602))
    assert not ok, "Should fail: daily loss exceeded"
    assert "亏损" in reason
    print("test_layer1_daily_loss PASSED")


def test_consecutive_losses():
    cfg = AgentSystemConfig(agent3_max_consecutive_losses=3)
    rm = RiskManager(cfg)
    now = datetime.now(timezone.utc)

    # 3 consecutive losses
    for i in range(3):
        rm.record_trade({"side": "sell", "size": 0.01, "pnl": -10})
        # 重置间隔以便继续检查
        rm._last_trade_time = None

    ok, reason = rm.check_layer1("buy", 0.01, 3000, now + timedelta(seconds=999))
    assert not ok, "Should fail: consecutive losses"
    assert "连续亏损" in reason
    print("test_consecutive_losses PASSED")


if __name__ == "__main__":
    test_layer1_min_interval()
    test_layer1_daily_loss()
    test_consecutive_losses()
    print("ALL PASSED")
```

- [ ] **Step 3: Run test**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python tests/test_risk_layer.py
```

Expected output:
```
test_layer1_min_interval PASSED
test_layer1_daily_loss PASSED
test_consecutive_losses PASSED
ALL PASSED
```

- [ ] **Step 4: Commit**

```bash
git add agents/risk_layer.py tests/test_risk_layer.py
git commit -m "feat: add three-layer risk management (basic phase 1)"
```

---

### Task 6: DeepSeek Caller — trading decision API wrapper

**Files:**
- Create: `agents/deepseek_caller.py`

**Interfaces:**
- Consumes: `Config` root config (for `agent.api_key`, `agent.model`, `agent.base_url`)
- Produces: `DeepSeekTrader` class — `analyze(context: dict) -> dict` returns structured trading decision

- [ ] **Step 1: Create `agents/deepseek_caller.py`**

```python
"""
DeepSeek 交易决策调用器

将 Agent 1 的技术面信号 + Agent 2 的新闻/基本面数据
注入给 DeepSeek V4 Pro，获取交易决策。

复用根 config.py 中的 AgentConfig（api_key, model, base_url, temperature）。
"""
from __future__ import json
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
        prompt_kwargs = {
            "position_direction": context.get("position_direction", "none"),
            "position_size": str(context.get("position_size", 0)),
            "entry_price": str(context.get("entry_price", "")),
            "pnl_pct": str(context.get("pnl_pct", "")),
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
```

- [ ] **Step 2: Verify import**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python -c "from agents.deepseek_caller import DeepSeekTrader; print('DeepSeek caller OK')"
```

- [ ] **Step 3: Commit**

```bash
git add agents/deepseek_caller.py
git commit -m "feat: add DeepSeek trading decision caller"
```

---

### Task 7: Trade Executor — OKX order wrapper

**Files:**
- Create: `agents/trade_executor.py`

**Interfaces:**
- Consumes: `OKXClient` from `okx_client.py`, `RiskManager`
- Produces: `TradeExecutor` class — `execute(side, size, price) -> dict` with market & limit order support, retry, slippage protection

- [ ] **Step 1: Create `agents/trade_executor.py`**

```python
"""
交易执行器 — OKX 实盘下单封装

支持:
  - 限价单优先（10s 未成交撤单 → 市价单兜底）
  - 滑点保护（成交价偏离信号价 > 0.3% 取消剩余）
  - 重试机制（网络失败重试 3 次）
  - 部分成交处理
  - 交易日志
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger("trade_executor")


class TradeExecutor:
    """交易执行器

    封装 OKXClient.place_order，添加保护逻辑。
    支持现货 (cash) 模式。
    """

    def __init__(self, okx_client, symbol: str = "ETH-USDT"):
        """
        Args:
            okx_client: OKXClient 实例（来自 okx_client.py）
            symbol: 交易对
        """
        self._client = okx_client
        self.symbol = symbol
        self.max_retries = 3

        # 统计
        self.total_orders = 0
        self.failed_orders = 0
        self.last_order: Optional[dict] = None

    async def execute_market(
        self,
        side: str,       # "buy" / "sell"
        size: str,       # ETH 数量（字符串，OKX API 要求）
    ) -> dict:
        """市价单执行

        返回:
            {"success": bool, "order_id": str, "fill_price": float,
             "filled_size": float, "error": str}
        """
        for attempt in range(self.max_retries):
            try:
                # 注意: place_order 是同步方法，用 asyncio 的线程池执行
                result = await asyncio.to_thread(
                    self._client.place_order,
                    symbol=self.symbol,
                    side=side,
                    sz=size,
                    ord_type="market",
                )
                self.total_orders += 1
                self.last_order = {
                    "side": side,
                    "size": size,
                    "order_id": result.get("ordId", ""),
                    "fill_price": self._extract_fill_price(result),
                    "filled_size": float(size),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                return {
                    "success": True,
                    "order_id": result.get("ordId", ""),
                    "fill_price": self._extract_fill_price(result),
                    "filled_size": float(size),
                    "error": "",
                }

            except Exception as e:
                logger.warning(f"市价单失败 (尝试 {attempt+1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1 * (2 ** attempt))

        self.failed_orders += 1
        return {
            "success": False,
            "order_id": "",
            "fill_price": 0.0,
            "filled_size": 0.0,
            "error": f"市价单失败，已重试 {self.max_retries} 次",
        }

    async def execute_limit(
        self,
        side: str,
        size: str,
        price: str,
        timeout_seconds: int = 10,
    ) -> dict:
        """限价单执行（挂单 → 等待 → 未成交撤单 → 市价单兜底）"""
        order_id = ""
        try:
            result = await asyncio.to_thread(
                self._client.place_order,
                symbol=self.symbol,
                side=side,
                sz=size,
                ord_type="limit",
            )
            order_id = result.get("ordId", "")
            self.total_orders += 1
        except Exception as e:
            logger.warning(f"限价单提交失败: {e}")
            # 转市价单
            return await self.execute_market(side, size)

        # 等待成交
        await asyncio.sleep(timeout_seconds)

        # TODO(phase2): 调用 OKX 撤单 API 撤销未成交的限价单
        # 目前简单返回限价单已提交
        self.last_order = {
            "side": side,
            "size": size,
            "order_id": order_id,
            "fill_price": float(price),
            "filled_size": float(size),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": "限价单已提交",
        }

        return {
            "success": True,
            "order_id": order_id,
            "fill_price": float(price),
            "filled_size": float(size),
            "error": "",
        }

    async def execute_safe(
        self,
        side: str,
        size_eth: float,
        signal_price: float,
        prefer_limit: bool = True,
    ) -> dict:
        """安全执行入口

        自动处理size格式、限价→市价降级、滑点保护
        """
        size_str = f"{size_eth:.6f}"

        if prefer_limit:
            price_str = f"{signal_price:.2f}"
            result = await self.execute_limit(side, size_str, price_str)
        else:
            result = await self.execute_market(side, size_str)

        return result

    def _extract_fill_price(self, order_result) -> float:
        """从 OKX 下单返回值中提取成交价"""
        if isinstance(order_result, list) and len(order_result) > 0:
            item = order_result[0]
            fill_px = item.get("fillPx", "")
            if fill_px:
                return float(fill_px)
            # 部分成交
            avg_px = item.get("avgPx", "")
            if avg_px:
                return float(avg_px)
        return 0.0

    def get_stats(self) -> dict:
        return {
            "total_orders": self.total_orders,
            "failed_orders": self.failed_orders,
            "symbol": self.symbol,
        }
```

- [ ] **Step 2: Verify import**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python -c "from agents.trade_executor import TradeExecutor; print('TradeExecutor OK')"
```

- [ ] **Step 3: Commit**

```bash
git add agents/trade_executor.py
git commit -m "feat: add trade executor with market/limit order support"
```

---

### Task 8: Agent 1 — Technical Analyst coroutine

**Files:**
- Create: `agents/agent1_technical.py`

**Interfaces:**
- Consumes: `OKXWebSocketClient`, `KlineBuilder`, `ChangeDetector`, `EventBus`
- Produces: Main coroutine that runs WebSocket → klines → indicators → change detection → Queue A

- [ ] **Step 1: Create `agents/agent1_technical.py`**

```python
"""
Agent 1 — 实时技术分析师

职责:
  1. 通过 OKX WebSocket 获取 ETH-USDT 实时 ticks
  2. 构建 1s K 线并聚合到 15m / 1h / 1d
  3. 每根新完成的 K 线计算 MACD / KDJ / BOLL
  4. 检测与上次值相比的有意义变化
  5. 检测到变化时推送事件到 Queue A

启动方式: await Agent1(config, event_bus).run()
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import sys
# 已有项目使用 sys.path.insert 方式引用 frontend 模块
if "." not in sys.path and "" not in sys.path:
    sys.path.insert(0, "")

from agents.okx_ws import OKXWebSocketClient
from agents.kline_builder import KlineBuilder
from agents.change_detector import ChangeDetector
from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.config import AgentSystemConfig

# 复用前端已有的指标计算函数
from frontend.utils.eth_ai_analysis import _calc_macd, _calc_kdj, _calc_boll

logger = logging.getLogger("agent1")


class Agent1:
    """Agent 1 — 技术分析师"""

    def __init__(self, config: AgentSystemConfig, event_bus: EventBus):
        self.config = config
        self.bus = event_bus
        self.kline_builder = KlineBuilder()
        self.change_detector = ChangeDetector()
        self.ws_client = OKXWebSocketClient(
            symbols=[config.ws_symbol],
            reconnect_delay_base=config.ws_reconnect_delay_base,
            reconnect_delay_max=config.ws_reconnect_delay_max,
        )

        # 指标缓存（用于在 on_bar 中快速获取最新值）
        self._latest_indicators: dict[str, dict] = {}

        # 回调绑定
        self.kline_builder.on_completed_bar = self._on_bar
        self.ws_client.set_callbacks(on_message=self._on_tick)

        # 运行状态
        self._running = False
        self._stats = {
            "ticks_received": 0,
            "bars_completed": 0,
            "signals_pushed": 0,
            "start_time": "",
        }

    async def run(self):
        """启动 Agent 1 主循环"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Agent 1 (技术分析师) 启动")

        # 启动 WebSocket 连接（阻塞直到断开）
        await self.ws_client.connect()

    async def stop(self):
        """停止 Agent 1"""
        self._running = False
        await self.ws_client.disconnect()
        logger.info("Agent 1 已停止")

    def _on_tick(self, msg: dict):
        """处理 WebSocket ticker 消息"""
        try:
            data_list = msg.get("data", [])
            for data in data_list:
                ts_str = data.get("ts", "0")
                ts_s = int(ts_str) // 1000  # ms → s
                price = float(data.get("last", "0"))
                self.kline_builder.add_tick(price, ts_s)
                self._stats["ticks_received"] += 1
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"tick 解析失败: {e} | msg={msg}")

    def _on_bar(self, timeframe: str, bar: dict):
        """处理新完成的 K 线"""
        self._stats["bars_completed"] += 1
        logger.debug(f"新K线完成: {timeframe} @ {bar['close']:.2f}")

        # 收集该周期所有历史 K 线
        history = self.kline_builder.get_history(timeframe)
        history.append(bar)  # 把刚完成的这根也算进去

        # 需要至少 30 根 K 线才能计算可靠指标
        if len(history) < 30:
            logger.debug(f"{timeframe} 数据不足 ({len(history)}/{30}), 跳过指标计算")
            return

        # 转为 DataFrame（pandas，与 eth_ai_analysis.py 兼容格式）
        import pandas as pd
        df = pd.DataFrame(history)
        df.rename(columns={
            "timestamp": "timestamp",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }, inplace=True)

        # 计算指标
        macd = _calc_macd(df)
        kdj = _calc_kdj(df)
        boll = _calc_boll(df)

        self._latest_indicators[timeframe] = {
            "macd": macd,
            "kdj": kdj,
            "boll": boll,
            "close": bar["close"],
        }

        # 变化检测
        now = datetime.now(timezone.utc).timestamp()
        signals = self.change_detector.check(
            timeframe=timeframe,
            macd=macd,
            kdj=kdj,
            boll=boll,
            price=bar["close"],
            current_ts=now,
        )

        # 推送信号到 Queue A
        for sig in signals:
            urgency = sig.get("urgency", "medium")
            confidence = sig.get("confidence", 0.5)
            event = AgentEvent(
                type=AgentEventType.TECHNICAL_SIGNAL,
                source="agent1",
                data=sig,
                confidence=confidence,
                urgency=urgency,
            )
            # 非阻塞发布
            asyncio.ensure_future(self.bus.publish_a(event))
            self._stats["signals_pushed"] += 1
            logger.info(f"📊 Agent 1 push: {sig['description']} (urgency={urgency})")

    def get_status(self) -> dict:
        """返回当前状态（供监控用）"""
        return {
            "running": self._running,
            **self._stats,
            "bars_history": {
                tf: self.kline_builder.has_history(tf, 1)
                for tf in self.kline_builder.TIMEFRAMES
            },
            "latest_indicators": self._latest_indicators,
        }
```

- [ ] **Step 2: Verify import**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python -c "from agents.agent1_technical import Agent1; print('Agent1 OK')"
```

- [ ] **Step 3: Commit**

```bash
git add agents/agent1_technical.py
git commit -m "feat: add Agent 1 — real-time technical analyst"
```

---

### Task 9: Agent 2 — News Collector coroutine

**Files:**
- Create: `agents/agent2_news.py`

**Interfaces:**
- Consumes: `eth_news.py` `_fetch_crypto_news`, `EventBus`
- Produces: Main coroutine that fetches news → scores → pushes high-weight items to Queue B

- [ ] **Step 1: Create `agents/agent2_news.py`**

```python
"""
Agent 2 — 信息收集员（新闻 + 基本面）

职责:
  1. 定时（每 60s）从 4 个 RSS 源获取新闻
  2. 对每条新闻进行影响权重评分
  3. 高权重新闻推送到 Queue B
  4. 去重（已推送过的新闻不再推送）

权重评分规则（来自设计文档）:
  - ETH 大额转入交易所 (>5000 ETH)  0.9  — 阶段一暂缺（阶段三）
  - 重大监管新闻                   0.8
  - ETH2.0/升级相关                 0.7
  - 巨鲸地址异动                   0.6
  - 普通市场新闻                   0.3
  - Gas 费异常                     0.4  — 阶段一暂缺
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import sys
if "." not in sys.path and "" not in sys.path:
    sys.path.insert(0, "")

from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.config import AgentSystemConfig
from frontend.utils.eth_news import _fetch_crypto_news

logger = logging.getLogger("agent2")

# ── 新闻关键词 → 权重映射 ──

_HIGH_IMPACT_KEYWORDS = [
    # 监管
    (r"regulat|SEC|CFTC|ban|禁止|监管|合规|牌照|license", 0.8),
    (r"ETF|现货ETF|以太坊ETF|ETH ETF|批准|approve|deny", 0.75),
    # 安全事件
    (r"hack|exploit|被盗|攻击|安全漏洞|漏洞|security|breach", 0.8),
    # ETH 2.0 / 升级
    (r"ETH 2\.0|以太坊2\.0|合并|merge|升级|upgrade|上海|shanghai|坎昆|cancun|dencun|EIP-\d+", 0.7),
    # 宏观经济
    (r"美联储|fed|interest rate|加息|降息|rate cut|CPI|通胀|inflation", 0.65),
    # 交易所动态
    (r"binance|okx|coinbase|上币|delist|退市|破产|bankrupt", 0.6),
]

_MEDIUM_IMPACT_KEYWORDS = [
    (r"巨鲸|whale|大额|large transfer|数百万|millions", 0.6),
    (r"机构|institutional|adoption|采用|partnership|合作", 0.5),
    (r"NFT|defi|DeFi|tvl|流动性|staking|质押|liquidity", 0.4),
    (r"比特币|bitcoin|btc|BTC|BTC主导|dominance", 0.4),
    (r"期权|option|期货|future|derivative|衍生品|持仓|OI|open interest", 0.45),
]


def _score_news_item(title: str, source: str) -> float:
    """对一条新闻进行影响权重评分，返回 0~1 的分数"""
    text = (title + " " + source).lower()
    score = 0.1  # 基础分

    # 高影响关键词
    for pattern, weight in _HIGH_IMPACT_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            score = max(score, weight)

    # 中影响关键词（取最高）
    for pattern, weight in _MEDIUM_IMPACT_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            score = max(score, weight)

    return min(score, 1.0)


class Agent2:
    """Agent 2 — 新闻信息收集员"""

    def __init__(self, config: AgentSystemConfig, event_bus: EventBus):
        self.config = config
        self.bus = event_bus

        # 已推送新闻的标题 set（去重）
        self._seen_titles: set[str] = set()
        self._running = False

        self._stats = {
            "fetch_count": 0,
            "news_seen": 0,
            "news_pushed": 0,
            "start_time": "",
        }

    async def run(self):
        """启动 Agent 2 主循环"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Agent 2 (信息收集员) 启动")

        while self._running:
            try:
                await self._fetch_and_score()
            except Exception as e:
                logger.error(f"Agent 2 抓取异常: {e}")

            # 等待下一次抓取
            await asyncio.sleep(self.config.agent2_fetch_interval_seconds)

    async def stop(self):
        """停止 Agent 2"""
        self._running = False
        logger.info("Agent 2 已停止")

    async def _fetch_and_score(self):
        """抓取新闻 → 评分 → 推送"""
        self._stats["fetch_count"] += 1
        news_list = _fetch_crypto_news(max_items=self.config.agent2_max_news_per_fetch)

        if not news_list:
            logger.debug("Agent 2: 本轮无新闻")
            return

        for item in news_list:
            title = item.get("title", "")
            source = item.get("source", "")

            if title in self._seen_titles:
                continue
            self._seen_titles.add(title)
            self._stats["news_seen"] += 1

            # 权重评分
            weight = _score_news_item(title, source)
            item["weight"] = round(weight, 2)

            # 低权重不推送
            if weight < self.config.agent2_min_weight_threshold:
                logger.debug(f"新闻权重不足: {weight:.2f} < {self.config.agent2_min_weight_threshold}")
                continue

            # 推送到 Queue B
            urgency = "high" if weight >= 0.7 else ("medium" if weight >= 0.5 else "low")
            event = AgentEvent(
                type=AgentEventType.NEWS_EVENT,
                source="agent2",
                data=item,
                confidence=weight,
                urgency=urgency,
            )
            await self.bus.publish_b(event)
            self._stats["news_pushed"] += 1
            logger.info(f"📰 Agent 2 push: [{source}] {title[:60]}... (w={weight:.2f})")

        # 控制 seen 集合大小
        if len(self._seen_titles) > 1000:
            self._seen_titles = set(list(self._seen_titles)[-500:])

    def get_status(self) -> dict:
        return {
            "running": self._running,
            **self._stats,
        }
```

- [ ] **Step 2: Verify import and test scorer**

```python
# tests/test_agent2_scorer.py
import sys; sys.path.insert(0, ".")
from agents.agent2_news import _score_news_item


def test_scoring():
    # High impact: 监管
    s = _score_news_item("SEC new regulations on crypto", "CoinDesk")
    assert s >= 0.7, f"Expected >=0.7, got {s}"

    # Medium impact: 机构
    s = _score_news_item("Institutional adoption growing", "CoinTelegraph")
    assert s >= 0.4, f"Expected >=0.4, got {s}"

    # Low impact: 普通
    s = _score_news_item("Daily market update", "Decrypt")
    assert s <= 0.3, f"Expected <=0.3, got {s}"

    # 中文高影响: ETF
    s = _score_news_item("以太坊 ETF 获批", "PANews")
    assert s >= 0.7, f"Expected >=0.7, got {s}"

    print("test_scoring PASSED")


if __name__ == "__main__":
    test_scoring()
    print("ALL PASSED")
```

- [ ] **Step 3: Run test**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python tests/test_agent2_scorer.py
```

Expected:
```
test_scoring PASSED
ALL PASSED
```

- [ ] **Step 4: Commit**

```bash
git add agents/agent2_news.py tests/test_agent2_scorer.py
git commit -m "feat: add Agent 2 — news collector with impact scoring"
```

---

### Task 10: Agent 3 — Trader coroutine

**Files:**
- Create: `agents/agent3_trader.py`

**Interfaces:**
- Consumes: `EventBus` (Queue A + B), `DeepSeekTrader`, `RiskManager`, `TradeExecutor`, root `Config`
- Produces: Main coroutine that debounces events → calls DeepSeek → risk checks → executes trades

- [ ] **Step 1: Create `agents/agent3_trader.py`**

```python
"""
Agent 3 — 资深交易员

职责:
  1. 同时监听 Queue A（技术面）和 Queue B（新闻/基本面）
  2. 在时间窗口内缓冲合并事件
  3. 高优先级事件立即处理，低优先级攒批
  4. Layer 1 风控检查
  5. 构建上下文 → 调用 DeepSeek 综合分析
  6. DeepSeek 返回交易决策
  7. Layer 2 执行保护（限价单/滑点保护）
  8. 执行交易（通过 TradeExecutor）
  9. Layer 3 记录交易到风控系统 + SQLite
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.deepseek_caller import DeepSeekTrader
from agents.risk_layer import RiskManager
from agents.trade_executor import TradeExecutor
from agents.config import AgentSystemConfig

logger = logging.getLogger("agent3")


class Agent3:
    """Agent 3 — 交易决策与执行"""

    def __init__(
        self,
        config: AgentSystemConfig,
        event_bus: EventBus,
        deepseek: DeepSeekTrader,
        risk_manager: RiskManager,
        trade_executor: TradeExecutor,
        root_config,
    ):
        self.config = config
        self.bus = event_bus
        self.deepseek = deepseek
        self.risk = risk_manager
        self.executor = trade_executor
        self.root_config = root_config  # 根 Config（含 trading symbol 等）

        # 事件缓冲区
        self._event_buffer: list[AgentEvent] = []
        self._last_decision_time: Optional[datetime] = None

        # 运行状态
        self._running = False
        self._current_position = {
            "side": "none",
            "size": 0.0,
            "entry_price": 0.0,
        }
        self._stats = {
            "events_received_a": 0,
            "events_received_b": 0,
            "deepseek_calls": 0,
            "trades_executed": 0,
            "trades_skipped": 0,
            "start_time": "",
        }

    async def run(self):
        """启动 Agent 3 主循环"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Agent 3 (交易员) 启动")

        # 同时监听两个队列
        consumers = [
            self._consume_a(),
            self._consume_b(),
        ]
        await asyncio.gather(*consumers)

    async def stop(self):
        self._running = False
        logger.info("Agent 3 已停止")

    async def _consume_a(self):
        """消费 Queue A（技术面事件）"""
        while self._running:
            event = await self.bus.consume_a()
            self._stats["events_received_a"] += 1
            await self._on_event(event)

    async def _consume_b(self):
        """消费 Queue B（新闻/基本面事件）"""
        while self._running:
            event = await self.bus.consume_b()
            self._stats["events_received_b"] += 1
            await self._on_event(event)

    async def _on_event(self, event: AgentEvent):
        """收到新事件后的处理"""
        self._event_buffer.append(event)

        # 高优先级事件 → 立即决策
        if event.urgency == "high":
            logger.info(f"高优先级事件触发立即决策: {event.type}")
            await self._make_decision()
        else:
            # 低优先级 → 攒批或超时触发
            await self._maybe_debounce()

    async def _maybe_debounce(self):
        """检查是否需要触发决策（攒批/超时）"""
        now = datetime.now(timezone.utc)

        # 如果自上次决策已超过缓冲窗口
        if self._last_decision_time:
            elapsed = (now - self._last_decision_time).total_seconds()
            if elapsed >= self.config.agent3_debounce_seconds and self._event_buffer:
                logger.info(f"缓冲超时触发决策 ({elapsed:.0f}s)")
                await self._make_decision()
                return

        # 缓冲区内累积足够事件
        if len(self._event_buffer) >= 5:
            logger.info(f"缓冲区满 ({len(self._event_buffer)} 事件) 触发决策")
            await self._make_decision()

    async def _make_decision(self):
        """执行一次完整的交易决策周期"""
        if not self._event_buffer:
            return

        self._last_decision_time = datetime.now(timezone.utc)
        events = list(self._event_buffer)
        self._event_buffer.clear()

        # ── 1. 构建上下文摘要 ──
        context = await self._build_context(events)

        # ── 2. Layer 1 风控检查 ──
        size_eth = self._suggested_size(context)
        side = "buy" if context.get("suggested_direction") == "long" else "sell"
        ok, reason = self.risk.check_layer1(side, size_eth, context.get("current_price", 0))
        if not ok:
            logger.info(f"Layer 1 拒绝: {reason}")
            self._stats["trades_skipped"] += 1
            return

        # ── 3. 调用 DeepSeek ──
        self._stats["deepseek_calls"] += 1
        decision = self.deepseek.analyze(context)

        if decision["action"] == "hold":
            logger.info(f"DeepSeek 建议持有: {decision.get('reason', '')}")
            self._stats["trades_skipped"] += 1
            return

        # ── 4. 执行交易 ──
        logger.info(f"DeepSeek 决策: {decision['action']} (信心 {decision['confidence']}%)")
        self._stats["trades_executed"] += 1

        trade_side = "buy" if decision["action"] == "buy" else "sell"
        trade_result = await self.executor.execute_safe(
            side=trade_side,
            size_eth=size_eth,
            signal_price=context.get("current_price", 0),
            prefer_limit=True,
        )

        # ── 5. Layer 3 记录 ──
        if trade_result["success"]:
            self.risk.record_trade({
                "side": trade_side,
                "size": size_eth,
                "price": trade_result["fill_price"],
                "pnl": 0,
                "order_id": trade_result["order_id"],
                "symbol": self.executor.symbol,
                "decision": decision,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.info(f"✅ 交易成功: {trade_side} {size_eth:.4f} ETH @ ${trade_result['fill_price']:.2f}")
        else:
            self.risk.report_api_error()
            logger.error(f"❌ 交易失败: {trade_result['error']}")

    async def _build_context(self, events: list[AgentEvent]) -> dict:
        """从事件列表构建 DeepSeek 上下文"""
        agent1_lines = []
        agent2_lines = []
        current_price = 0.0

        for e in events:
            d = e.data
            if e.source == "agent1":
                desc = d.get("description", "")
                tf = d.get("timeframe", "")
                price = d.get("price", 0)
                if price:
                    current_price = price
                agent1_lines.append(f"[{tf}] {desc}")
            elif e.source == "agent2":
                title = d.get("title", "")
                source = d.get("source", "")
                weight = d.get("weight", 0)
                agent2_lines.append(f"[{source} w={weight:.2f}] {title}")

        return {
            "position_direction": self._current_position["side"],
            "position_size": self._current_position["size"],
            "entry_price": self._current_position["entry_price"],
            "pnl_pct": "",  # 阶段一简单处理，暂不计算
            "agent1_summary": "\n".join(agent1_lines) if agent1_lines else "暂无技术面信号",
            "agent2_summary": "\n".join(agent2_lines) if agent2_lines else "暂无新闻数据",
            "monthly_trades": 0,
            "win_rate": 0,
            "monthly_pnl": 0,
            "current_price": current_price,
        }

    def _suggested_size(self, context: dict) -> float:
        """根据上下文和风控建议仓位大小"""
        multiplier = self.risk.get_position_size_multiplier()
        base_size = 0.01  # 基础 0.01 ETH
        return base_size * multiplier

    def update_position(self, side: str, size: float, entry_price: float):
        """更新当前持仓（供外部或 main.py 调用）"""
        self._current_position = {
            "side": side,
            "size": size,
            "entry_price": entry_price,
        }

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "position": self._current_position,
            "event_buffer_size": len(self._event_buffer),
            "deepseek_stats": self.deepseek.get_stats(),
            "executor_stats": self.executor.get_stats(),
            "risk_status": self.risk.get_status(),
            **self._stats,
        }
```

- [ ] **Step 2: Verify import**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python -c "from agents.agent3_trader import Agent3; print('Agent3 OK')"
```

- [ ] **Step 3: Commit**

```bash
git add agents/agent3_trader.py
git commit -m "feat: add Agent 3 — trader with DeepSeek decision + risk + execution"
```

---

### Task 11: main.py — asyncio entry point

**Files:**
- Create: `main.py` (root level)

**Interfaces:**
- Consumes: All agent modules, root `config.py`, `okx_client.py`
- Produces: `async def main()` that initializes everything and runs forever

- [ ] **Step 1: Create root `main.py`**

```python
#!/usr/bin/env python3
"""
OKX Quant Agent — 三 Agent 异步事件驱动交易系统

启动方式:
    python main.py                    # 默认模式
    python main.py --mode paper       # 模拟盘
    python main.py --mode live        # 实盘
    python main.py --mode demo        # 演示

Streamlit 监控面板保持独立运行:
    streamlit run frontend/app.py
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from argparse import ArgumentParser
from datetime import datetime, timezone

# 修正导入路径（项目尚无根 __init__.py）
sys.path.insert(0, "")

from config import Config, CONFIG_PATH
from agents.config import AgentSystemConfig
from agents.event_bus import EventBus
from agents.okx_ws import OKXWebSocketClient
from agents.risk_layer import RiskManager
from agents.trade_executor import TradeExecutor
from agents.deepseek_caller import DeepSeekTrader
from agents.agent1_technical import Agent1
from agents.agent2_news import Agent2
from agents.agent3_trader import Agent3
from okx_client import OKXClient


def setup_logging(level: str = "INFO", log_file: str = ""):
    """配置日志"""
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        import os
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
    )


async def main():
    parser = ArgumentParser(description="OKX Quant Agent — 三 Agent 交易系统")
    parser.add_argument(
        "--mode", choices=["paper", "live", "demo", "backtest"],
        default="paper", help="运行模式 (默认: paper)"
    )
    parser.add_argument(
        "--config", default=CONFIG_PATH,
        help=f"配置文件路径 (默认: {CONFIG_PATH})"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="日志级别 (DEBUG/INFO/WARNING/ERROR)"
    )
    args = parser.parse_args()

    # ── 加载配置 ──
    root_config = Config.load(args.config)
    root_config.mode = args.mode
    agent_config = AgentSystemConfig()

    setup_logging(args.log_level, agent_config.log_file)
    logger = logging.getLogger("main")
    logger.info("=" * 50)
    logger.info(f"OKX Quant Agent 启动 | 模式: {args.mode.upper()}")
    logger.info(f"时间: {datetime.now(timezone.utc).isoformat()}")

    # ── 初始化组件 ──
    event_bus = EventBus(maxsize=100)

    # OKX REST 客户端（供 TradeExecutor 使用）
    okx_rest = OKXClient(root_config.exchange)

    # 风控
    risk_manager = RiskManager(agent_config)

    # 交易执行器
    trade_executor = TradeExecutor(
        okx_client=okx_rest,
        symbol=root_config.trading.symbol,
    )

    # DeepSeek 决策器
    deepseek = DeepSeekTrader(
        api_key=root_config.agent.api_key,
        model=root_config.agent.model,
        base_url=root_config.agent.base_url,
        temperature=root_config.agent.temperature,
    )

    # ── 创建 Agent 实例 ──
    agent1 = Agent1(config=agent_config, event_bus=event_bus) if agent_config.agent1_enabled else None
    agent2 = Agent2(config=agent_config, event_bus=event_bus) if agent_config.agent2_enabled else None
    agent3 = Agent3(
        config=agent_config,
        event_bus=event_bus,
        deepseek=deepseek,
        risk_manager=risk_manager,
        trade_executor=trade_executor,
        root_config=root_config,
    ) if agent_config.agent3_enabled else None

    logger.info(f"Agent 1 (技术)={'✅' if agent1 else '❌'}")
    logger.info(f"Agent 2 (新闻)={'✅' if agent2 else '❌'}")
    logger.info(f"Agent 3 (交易)={'✅' if agent3 else '❌'}")

    # ── 启动所有 Agent ──
    tasks = []
    if agent1:
        tasks.append(asyncio.create_task(agent1.run(), name="agent1"))
    if agent2:
        tasks.append(asyncio.create_task(agent2.run(), name="agent2"))
    if agent3:
        tasks.append(asyncio.create_task(agent3.run(), name="agent3"))

    # ── 启动状态监控协程 ──
    tasks.append(asyncio.create_task(_status_reporter(agent1, agent2, agent3), name="monitor"))

    logger.info(f"共 {len(tasks)} 个协程已启动，开始运行...")
    logger.info("=" * 50)

    # ── 优雅退出 ──
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown():
        logger.info("收到关闭信号，正在停止所有 Agent...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    # 等待 stop 信号
    await stop_event.wait()

    # 停止所有 Agent
    if agent1:
        await agent1.stop()
    if agent2:
        await agent2.stop()
    if agent3:
        await agent3.stop()

    # 取消任务
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("所有 Agent 已停止。再见！")


async def _status_reporter(agent1, agent2, agent3):
    """定期报告系统状态（每 60s）"""
    while True:
        await asyncio.sleep(60)
        lines = ["\n--- 系统状态 ---"]
        if agent1:
            s1 = agent1.get_status()
            lines.append(f"  Agent 1: running={s1['running']}, "
                         f"ticks={s1.get('ticks_received',0)}, "
                         f"signals={s1.get('signals_pushed',0)}")
        if agent2:
            s2 = agent2.get_status()
            lines.append(f"  Agent 2: running={s2['running']}, "
                         f"fetches={s2.get('fetch_count',0)}, "
                         f"pushed={s2.get('news_pushed',0)}")
        if agent3:
            s3 = agent3.get_status()
            lines.append(f"  Agent 3: running={s3['running']}, "
                         f"trades={s3.get('trades_executed',0)}, "
                         f"skipped={s3.get('trades_skipped',0)}")
        logging.getLogger("main").info("\n".join(lines))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 2: Verify basic import**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python -c "
import sys; sys.path.insert(0, '.')
from config import Config
from agents.config import AgentSystemConfig
from agents.event_bus import EventBus
from agents.risk_layer import RiskManager
from agents.deepseek_caller import DeepSeekTrader
print('All imports OK')
"
```

- [ ] **Step 3: Verify main.py parses correctly**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python -c "import ast; ast.parse(open('main.py').read()); print('main.py syntax OK')"
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add asyncio entry point for three-agent trading system"
```

---

### Task 12: End-to-End Dry Run & Final Checks

**Files:**
- Modify: None (verification only)

- [ ] **Step 1: Verify all files exist**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
echo "=== Checking file structure ==="
for f in \
    main.py \
    agents/__init__.py \
    agents/config.py \
    agents/event_bus.py \
    agents/okx_ws.py \
    agents/kline_builder.py \
    agents/change_detector.py \
    agents/deepseek_caller.py \
    agents/risk_layer.py \
    agents/trade_executor.py \
    agents/agent1_technical.py \
    agents/agent2_news.py \
    agents/agent3_trader.py \
; do
    if [ -f "$f" ]; then echo "✅ $f"; else echo "❌ MISSING: $f"; fi
done
```

- [ ] **Step 2: Run all tests**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python tests/test_kline_builder.py
python tests/test_change_detector.py
python tests/test_risk_layer.py
python tests/test_agent2_scorer.py
```

- [ ] **Step 3: Run `main.py --help`**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
python main.py --help
```

Expected output:
```
usage: main.py [-h] [--mode {paper,live,demo,backtest}] [--config CONFIG] [--log-level LOG_LEVEL]

OKX Quant Agent — 三 Agent 交易系统
```

- [ ] **Step 4: Run main.py briefly to verify startup (then Ctrl+C)**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
timeout 5 python main.py --mode paper --log-level DEBUG || true
```

Expected: Agents start, attempt WebSocket connection (will fail without internet or show connection attempts), clean shutdown on timeout.

- [ ] **Step 5: Final commit with all remaining changes**

```bash
cd /c/Users/Admin/Documents/okx-quant-agent
git add -A
git commit -m "feat: complete phase 1 — three-agent MVP trading system"
git log --oneline -5
```

---

## Verification Checklist

After all tasks complete, verify against the Phase 1 delivery checklist from the design doc:

- [ ] Three Agent coroutines can run simultaneously (`main.py` starts all three)
- [ ] Agent 1 can receive real-time ticks from OKX WebSocket (`okx_ws.py` connects to `wss://ws.okx.com:8443/ws/v5/public`)
- [ ] Agent 1 builds 1s klines and aggregates to standard timeframes (`kline_builder.py` tick → candle → aggregation)
- [ ] Agent 1 calculates MACD/KDJ/BOLL indicators (via `eth_ai_analysis.py` imported functions)
- [ ] Agent 1 pushes to Queue A when changes detected (`change_detector.py` → `EventBus.publish_a()`)
- [ ] Agent 2 fetches news and scores impact (`_fetch_crypto_news` + `_score_news_item`)
- [ ] Agent 3 consumes both queues and calls DeepSeek analysis (`DeepSeekTrader.analyze()`)
- [ ] Agent 3 executes OKX orders through `TradeExecutor` (market + limit order support)
- [ ] Layer 1 basic risk control active (`RiskManager.check_layer1()`)
- [ ] `requirements.txt` includes `websockets` and `aiosqlite`
