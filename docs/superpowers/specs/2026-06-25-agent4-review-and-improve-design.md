# Agent 4: 复盘改进 Agent — 设计文档

> 日期: 2026-06-25
> 状态: 设计定稿，待实现
> 替换: Phase 4 `param_adapter.py`（规则式参数调整）

---

## 1. 动机

当前系统有 3 个 Agent 协作交易：

```
Agent 1 (技术面) → Queue A → Agent 3 (交易决策) → 执行交易
Agent 2 (新闻+链上) → Queue B → Agent 3
```

Phase 4 已有的 `review_generator.py`（报告生成）和 `param_adapter.py`（规则调参）过于简单：

- `review_generator` — 只输出胜率、PnL、最大回撤等统计数字，不做深层分析
- `param_adapter` — 3 条硬编码规则（胜率 >60% 加仓 / <40% 减仓 / 连续亏损加间隔），无法理解市场环境

加入 Agent 4 后形成**交易 → 复盘 → 改进 → 交易**的闭环：

```
Agent 3 → 交易 → Agent 4 (AI 复盘 → 调参) → 改进后的参数 → Agent 3 下次交易生效
                                                      ↘ Agent 1 灵敏度调整
```

---

## 2. 架构

### 2.1 整体数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                        同一进程 / 同一事件循环                         │
│                                                                     │
│  ┌──────────┐  Queue A   ┌──────────┐  notify_trade()  ┌──────────┐│
│  │ Agent 1  │ ──────────→│ Agent 3  │ ───────────────→ │ Agent 4  ││
│  │ 技术分析  │            │ 交易决策   │                  │ 复盘改进  ││
│  └──────────┘            └──────────┘                  └──────────┘│
│       ↑                       ↕ 读写                     ↓         │
│       │                  ┌────────────┐             直接改共享      │
│       │                  │  SQLite    │             config 对象     │
│       │                  │  trades 表  │                   ↓         │
│       │                  └────────────┘            AgentSystemConfig│
│       │                       ↕ 采集                                 │
│       └─── 读取 config ────┐  采集      ┌─── 读取 recent_news ──┐   │
│                            │  K-line   │                        │   │
│                            └── 行情  ←─┤  读取信号统计 ← Agent 1│   │
│                                         └── 读取链上数据 ← Agent 2│   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 设计原则

1. **Agent 4 不通过 EventBus 通信** — 复盘是批处理分析，不是实时事件。直接在进程内调用方法
2. **共享 `AgentSystemConfig` 引用** — Agent 4 直接修改 config 对象（锁保护），Agent 3/1 的下次循环自动感知
3. **安全检查优先** — 所有参数调整必须通过边界校验 + 防抖检查才能生效
4. **完全替代旧 `param_adapter.py`** — 不留两个调参引擎并行

### 2.3 通信方式

| 操作 | 机制 |
|------|------|
| Agent 3 → Agent 4 "新交易完成" | 直接调用 `agent4.notify_trade(trade_record)` |
| Agent 4 → 读取历史交易 | 直接查 SQLite `trades` 表 |
| Agent 4 → 调 DeepSeek | 在 `DeepSeekTrader` 上新加 `analyze_review(prompt_text) → dict` 方法，复用同一个 `OpenAI` client |
| Agent 4 → 改 Agent 3 参数 | `setattr(config, param, value)` + asyncio.Lock |
| Agent 4 → 改 Agent 1 参数 | 写入 config 新字段，Agent 1 每轮 `_on_bar` 读取 |
| Agent 4 → 采集行情 | 直接调 `KlineBuilder.get_history()` |
| Agent 4 → 采集 Agent 1 信号 | 调 `Agent1.get_recent_signal_stats()`（新增方法） |
| Agent 4 → 采集新闻 | 调 `Agent2.get_recent_news(n)`（新增方法） |
| Agent 4 → 采集链上 | 调 `Agent2.get_onchain_status()`（已有方法） |

---

## 3. 触发机制

### 3.1 触发条件

Agent 3 每完成一笔交易调用 `notify_trade()`，Agent 4 内部计数：

```python
self._trade_count += 1
if self._trade_count - self._last_review_count >= config.agent4_review_interval_trades:
    await self._run_review()
```

`agent4_review_interval_trades` 默认 **5**，每次复盘触发后会更新 `_last_review_count`。

### 3.2 防抖

- 同一参数两次调整最少间隔 `agent4_min_adjust_interval_seconds`（默认 300s / 5 分钟）
- 如果 DeepSeek 调用失败，不下沉、不重试、不阻塞交易流程
- 如果 DeepSeek 返回空调整列表，记录复盘但不改参数

---

## 4. DeepSeek 交互

### 4.1 复盘上下文模板

每次复盘构建的 Prompt 包含 6 个数据段：

```
【最近 5 笔交易】
# | 时间 | 方向 | 开仓价 | 平仓价 | PnL | 当时DeepSeek理由 | 市场状态
1 | 2026-06-25 10:00 | buy | 3200 | 3180 | -20 | MACD金叉 | 震荡
...

【大盘行情背景】（交易时段）
- ETH 价格区间: 3150 - 3250
- 涨跌幅: -1.2%
- 波动率 (ATR): 45 USDT
- 趋势判断: 震荡偏空

【Agent 1 信号统计】（交易时段内）
- 3m: 3 信号 (1买/2卖)

【同期新闻摘要】
- "ETH ETF 流入量创 3 日新低" (权重 0.6, 偏空)

【链上数据快照】（均值）
- Gas: 45 Gwei (低)
- 吃单比: 买 48% / 卖 52% (略偏空)
- 资金费率: -0.0005% (中性)

【当前运行参数】
- max_daily_trades: 10
- min_interval: 300s
- debounce: 30s
- max_position_eth: 0.5
- ...

【历史复盘记录】
- 上次复盘 (2026-06-24): 建议降低频率 → 采纳后胜率 30%→45%
```

### 4.2 DeepSeek 返回 JSON Schema

```json
{
  "review_id": "review_20260625_001",
  "summary": "最近5笔交易亏损3笔，主要问题：震荡市中频繁追涨...",
  "market_regime": "震荡偏空 | 趋势上涨 | 趋势下跌 | 高波动 | 低波动",
  "param_adjustments": [
    {
      "target": "agent3",
      "param": "agent3_max_daily_trades",
      "from": 10,
      "to": 6,
      "reason": "近期胜率低于40%，减少交易频率"
    }
  ],
  "strategy_insights": "当前市场处于震荡区间，MACD信号可靠性下降..."
}
```

### 4.3 可调参数及其安全边界

| 参数 | target | 安全范围 | 默认值 |
|------|--------|---------|--------|
| `agent3_max_daily_trades` | agent3 | [1, 30] | 10 |
| `agent3_debounce_seconds` | agent3 | [5, 300] | 30 |
| `agent3_min_interval_between_trades` | agent3 | [30, 3600] | 300 |
| `agent3_max_daily_loss_usdt` | agent3 | [10, 500] | 100 |
| `agent3_max_consecutive_losses` | agent3 | [1, 10] | 3 |
| `agent3_max_position_eth` | agent3 | [0.01, 2.0] | 0.5 |
| `agent3_position_size_multiplier` | agent3 | [0.1, 3.0] | 1.0 |
| `agent3_default_stop_loss_pct` | agent3 | [0.5, 10.0] | 2.0 |
| `agent3_default_take_profit_pct` | agent3 | [1.0, 20.0] | 4.0 |
| `agent1_change_cooldown` | agent1 | [10, 600] | 60 |

---

## 5. 文件改动清单

### 5.1 新建文件

| 文件 | 内容 | 估算行数 |
|------|------|---------|
| `agents/agent4_reviewer.py` | Agent4Reviewer 类：notify_trade → 数据采集 → DeepSeek → 校验 → 应用 | ~250 |

### 5.2 修改文件

| 文件 | 改动 | 估算 |
|------|------|------|
| `agents/config.py` | 新增 Agent 1 可调参数 + Agent 3 新参数 + Agent 4 配置 | +50 |
| `agents/agent1_technical.py` | 新增 `get_recent_signal_stats()`，`change_cooldown` 改为读 config | +30 |
| `agents/agent2_news.py` | 新增 `get_recent_news(n)` 方法 | +15 |
| `agents/agent3_trader.py` | 移除 `_param_adapter_loop()`，添加 `notify_trade()` → Agent 4 调用 | +40 / -20 |
| `agents/deepseek_caller.py` | 新增 `analyze_review(prompt_text) → dict` 方法，复用同一 OpenAI client | +25 |
| `main.py` | 创建 Agent4Reviewer，传给 Agent 3，启动 run() | +20 |
| `agents/status_writer.py` | 加入 agent4 状态块 | +15 |

### 5.3 停用

| 文件 | 处理 |
|------|------|
| `agents/param_adapter.py` | 保留文件、不再 import |

### 5.4 新增 Config 字段

加到 `AgentSystemConfig`：

```python
# ── Agent 1（新增可调参数，原写死在 change_detector.py）──
agent1_change_cooldown: float = 60.0

# ── Agent 3（新增）──
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

---

## 6. Agent4Reviewer 类设计

```python
class Agent4Reviewer:
    def __init__(self, config: AgentSystemConfig, deepseek: DeepSeekTrader,
                 db_path: str, kline_builder: KlineBuilder,
                 agent1: Agent1, agent2: Agent2):
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

    async def notify_trade(self, trade_record: dict) -> None
        """Agent 3 完成一笔交易后调用，触发计数检查"""

    async def run(self) -> None
        """主循环 — 当前为空（仅由 notify_trade 触发）"""

    async def _run_review(self) -> None
        """执行一次完整复盘"""

    def _load_recent_trades(self, n: int = 5) -> list[dict]
        """从 SQLite 加载最近 N 笔已完成交易"""

    def _collect_market_context(self) -> dict
        """采集 KlineBuilder 行情数据"""

    def _collect_signal_stats(self) -> dict
        """调 Agent1.get_recent_signal_stats()"""

    def _build_review_prompt(self, trades, market, signals, news, onchain, prev_reviews) -> str
        """构建完整的 DeepSeek 复盘 Prompt"""

    def _validate_adjustment(self, adj: dict) -> bool
        """边界校验 + 防抖"""

    def _apply_adjustment(self, adj: dict) -> None
        """写入共享 config（锁保护）"""

    def get_status(self) -> dict
        """返回复盘状态"""
```

---

## 7. Agent 3 集成点

`agent3_trader.py` 中的改动：

```python
# 1. 构造时接受 Agent4Reviewer 实例
def __init__(self, ..., agent4_reviewer=None):
    self._agent4 = agent4_reviewer

# 2. 移除旧的 _param_adapter_loop 和 _review_scheduler
# 3. 交易成功后调用 Agent 4
def _on_trade_executed(self, trade_record):
    self._stats["trades_executed"] += 1
    self.risk.record_trade(...)
    if self._agent4:
        asyncio.create_task(self._agent4.notify_trade(trade_record))

# 4. _build_context 中不再调用 param_adapter
```

---

## 8. 安全性

1. **边界校验** — 每个参数有硬编码的 `[min, max]` 范围，超出范围自动拒绝
2. **防抖** — 同一参数至少间隔 `agent4_min_adjust_interval_seconds` 才能再次修改
3. **DeepSeek 失败不影响交易** — 复盘是异步最佳努力，调用失败只记日志
4. **只收窄不放宽风险参数** — max_daily_loss_usdt、max_consecutive_losses 等风险参数 AI 只能降低不能提高（待定，可在校验层实现）
5. **调整记录日志** — 所有参数调整记入 `_review_history` 和 JSON status

---

## 9. 后续可扩展

- **手动触发复盘** — Dashboard 上添加「立即复盘」按钮
- **A/B 测试模式** — 保留多组参数，比较 Agent 4 调参前后的实际效果
- **更长的复盘周期** — 每 50 笔做一次深度复盘（对比当前设计每 5 笔的轻量复盘）
- **推送复盘报告** — 通过 Telegram/Discord 推送复盘总结到手机
