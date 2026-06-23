# Three-Agent AI Trading Architecture

> **Project:** OKX Quant Agent — ETH-USDT 全自动 AI 交易系统
> **Date:** 2026-06-24
> **Status:** Design Approved — Pending Phase 1 Implementation

---

## I. Vision

将现有的单线程 Streamlit 分析页面升级为**三 Agent 异步事件驱动架构**，实现：

- 毫秒级实时市场数据响应
- 多维度信号融合（技术面 + 新闻面 + 链上数据）
- 全自动实盘交易（Agent 3 直接调用 OKX `place_order`）
- 持续自我优化的学习系统

---

## II. System Architecture (Final)

```
┌─────────────────────────────────────────────────────────────────┐
│                       单进程 asyncio 事件循环                       │
│                                                                   │
│  ┌──────────────────┐          ┌──────────────────────────────┐  │
│  │   Agent 1        │  Queue A │         Agent 3              │  │
│  │   技术分析师      │◀─────────│         资深交易员            │  │
│  │                  │─────────▶│                              │  │
│  │  • OKX WebSocket │  事件通知 │  • 消费双队列                │  │
│  │  • 实时 K 线构建  │          │  • DeepSeek 综合分析          │  │
│  │  • MACD/KDJ/BOLL │          │  • 三层风控检查              │  │
│  │  • 订单簿深度    │  Queue B │  • OKX place_order           │  │
│  │  • 资金费率      │◀─────────│  • 交易日志                   │  │
│  └──────────────────┘  事件通知 │                              │  │
│                                 │  ┌────────────────────────┐  │  │
│  ┌──────────────────┐          │  │  状态持久化 → SQLite    │  │  │
│  │   Agent 2        │─────────▶│  │  (交易记录/盈亏/信號)    │  │  │
│  │   信息收集员      │  Queue B │  └────────────────────────┘  │  │
│  │                  │  数据    └──────────────────────────────┘  │
│  │  • RSS 新闻源    │                                           │
│  │  • 链上大额转账  │                                           │
│  │  • 影响权重评分  │                                           │
│  │  • Gas 费监控    │                                           │
│  └──────────────────┘                                           │
│                                                                   │
│  ┌──────────────────────────────────────────────┐                │
│  │  Streamlit 页面（只读监控面板）                  │                │
│  │  通过 SQLite 读取 Agent 状态，不参与决策          │                │
│  └──────────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────────┘
```

### 通信协议

| 队列 | 数据 | 推送方 | 消费方 | 触发条件 |
|------|------|--------|--------|----------|
| **Queue A** | 技术信号事件 | Agent 1 | Agent 3 | 检测到趋势变化/突破信号 |
| **Queue B** | 新闻/链上事件 | Agent 2 | Agent 3 | 高影响力信息出现时 |

**事件消息格式:**
```python
{
    "type": "technical_signal",  # 或 "news_event", "onchain_alert"
    "source": "Agent 1",
    "timestamp": "2026-06-24T10:30:00+08:00",
    "data": { ... },  # 具体负载
    "confidence": 0.75,  # 信度 0-1
    "urgency": "high"  # high / medium / low
}
```

---

## III. Agent 1 — 实时技术分析师

### 职责

通过 OKX WebSocket 获取实时行情，持续计算技术指标，检测到**重大变化**时向 Agent 3 推送事件。

### 数据流

```
OKX WebSocket (wss://ws.okx.com:8443/ws/v5/public)
    │
    ├── 实时 ticks (ETH-USDT)
    │       │
    │       ├──→ 1秒级 K线构建 (1s candle builder)
    │       │       │
    │       │       ├──→ 周期聚合: 15m / 1h / 1d K线
    │       │       │       │
    │       │       │       ├──→ MACD(12,26,9)  → 金叉/死叉/柱方向
    │       │       │       ├──→ KDJ(9,3,3)     → K/D/J 值/交叉
    │       │       │       └──→ BOLL(20,2)     → 带宽/挤压/位置
    │       │       │
    │       │       └──→ 变化检测 (与前值对比)
    │       │               ├──→ MACD 柱方向反转 → push
    │       │               ├──→ KDJ 金叉/死叉   → push
    │       │               ├──→ 价格突破布林带   → push
    │       │               └──→ 多周期对齐评分变化 → push
    │       │
    │       └──→ 订单簿深度 (get_order_book 每10s)
    │               ├──→ 买卖盘口厚度
    │               ├──→ 大单堆积位 (墙)
    │               └──→ 吃单比 (taker buy/sell)
    │
    └──→ 资金费率 (get_funding_rate 每60s)
            └──→ 多空情绪指标
```

### 指标变更推送规则

Agent 1 不每秒钟都 push，只在**有意义的变更**时通知：

| 信号 | 推送条件 | 优先级 |
|------|----------|--------|
| MACD 金叉/死叉 | 首次出现 | 🔴 高 |
| MACD 柱方向反转 | 正值↔负值 | 🔴 高 |
| KDJ K/D 交叉 | 首次出现 | 🟡 中 |
| 价格突破布林带上/下轨 | 收盘价超出轨道 | 🔴 高 |
| 布林带挤压结束 | 带宽从挤压扩张 >30% | 🟡 中 |
| 多周期信心分变化 | 超过 ±15% | 🟡 中 |
| 订单簿出现大额挂墙 | 单价位 > 1000 ETH | 🔴 高 |
| 资金费率极端 | >0.05% 或 < -0.05% | 🟢 低 |

### 依赖模块

- `agents/agent1_technical.py` — 主协程
- `agents/kline_builder.py` — WebSocket 滴答→K线聚合
- `agents/indicator_engine.py` — 指标计算（MACD/KDJ/BOLL）— 可复用 `eth_ai_analysis.py` 函数
- `agents/orderbook_monitor.py` — 订单簿深度监控
- `agents/change_detector.py` — 信号变化检测与事件生成
- `okx_client.py` — OKX REST API 调用

---

## IV. Agent 2 — 信息收集员

### 职责

持续收集市场情绪和链上信息，评估影响权重，向 Agent 3 推送高价值事件。

### 数据流

```
定时任务循环 (每 30-60s)
    │
    ├── 新闻 RSS (复用 eth_news.py)
    │   ├── CoinDesk → 解析 → 去重 → 权重评分
    │   ├── CoinTelegraph → 解析 → 去重 → 权重评分
    │   ├── Decrypt → 解析 → 去重 → 权重评分
    │   └── PANews → 解析 → 去重 → 权重评分
    │
    ├── 链上大额转账 (Etherscan API / OKX API)
    │   ├── > 1000 ETH 转入交易所 → 🔴 高权重卖出信号
    │   ├── > 1000 ETH 转出交易所 → 🟢 高权重买入信号
    │   └── 巨鲸钱包异动 → 🟡 中权重
    │
    ├── Gas 费监控 (Etherscan API)
    │   ├── Gas > 100 gwei → 链上活跃，行情可能启动
    │   └── Gas < 10 gwei → 市场冷清
    │
    └── OKX 资金费率 (get_funding_rate)
        ├── 正费率持续高位 → 多头拥挤警示
        └── 负费率持续低位 → 空头拥挤警示
```

### 影响权重评分规则

| 事件类型 | 基础权重 | 衰减因子 |
|----------|----------|----------|
| ETH 大额转入交易所 (>5000 ETH) | 0.9 | 24h |
| ETH 大额转出交易所 (>5000 ETH) | 0.85 | 24h |
| 重大监管新闻 | 0.8 | 48h |
| ETH2.0/升级相关 | 0.7 | 48h |
| 巨鲸地址异动 | 0.6 | 12h |
| 普通市场新闻 | 0.3 | 6h |
| Gas 费异常 | 0.4 | 1h |
| 资金费率极端 | 0.5 | 4h |

### 依赖模块

- `agents/agent2_news.py` — 主协程
- `agents/news_collector.py` — RSS 多源抓取（复用 `eth_news.py`）
- `agents/onchain_monitor.py` — 链上数据/Etherscan
- `agents/weight_scorer.py` — 权重评分引擎
- `agents/funding_monitor.py` — 资金费率

---

## V. Agent 3 — 资深交易员

### 职责

消费 Agent 1 和 Agent 2 的事件，综合分析，做出交易决策，执行实盘操作。

### 决策流程

```
Queue A 事件 ──┐
               ├──→ 事件缓冲合并 (同一时间窗的事件聚合)
Queue B 事件 ──┘       │
                       v
                ┌──────────────────┐
                │  是否需要分析？    │
                │  • 高优先级事件 → 立即
                │  • 同方向信号累积 → 立即
                │  • 低优先级/单独 → 攒批
                └────────┬─────────┘
                         v 是
                ┌──────────────────┐
                │  Layer 1: 交易前   │
                │  基础准入风控      │
                │  • 是否在交易时间？ │
                │  • 有可用资金？     │
                │  • 未超当日限额？   │
                │  • 与现有仓位不冲突？│
                │  • BTC 无剧烈波动？ │
                └────────┬─────────┘
                         v 通过
                ┌──────────────────┐
                │  DeepSeek 综合分析  │
                │  • 注入当前市场全景 │
                │  • 历史K线 + 指标  │
                │  • 新闻 + 链上数据  │
                │  • 现有仓位上下文   │
                │  • 要求输出:       │
                │    - 方向(多/空/持币)│
                │    - 入场价格区间   │
                │    - 仓位比例      │
                │    - 止损位       │
                │    - 止盈位       │
                │    - 理由(100字内) │
                └────────┬─────────┘
                         v 结果为交易
                ┌──────────────────┐
                │  Layer 2: 交易中   │
                │  • 限价单 → 吃单不追│
                │  • 滑点 > 0.3% 取消 │
                │  • 重试机制(3次)   │
                └────────┬─────────┘
                         v 成交
                ┌──────────────────┐
                │  Layer 3: 交易后   │
                │  • 记录交易到 SQLite│
                │  • 更新风控状态     │
                │  • 设置止盈止损单   │
                │  • 推送通知        │
                └──────────────────┘
```

### DeepSeek Prompt 上下文注入

```python
_SYSTEM_PROMPT = """
你是一位有15年经验的以太坊资深交易员，管理过亿美元的资金。
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
{
    "action": "buy|sell|hold",
    "confidence": 0-100,
    "entry_price_min": "...",
    "entry_price_max": "...",
    "position_size_pct": "建议仓位占总资金百分比",
    "stop_loss": "...",
    "take_profit": "...",
    "reason": "..."
}
"""
```

### 依赖模块

- `agents/agent3_trader.py` — 主协程
- `agents/decision_engine.py` — 事件缓冲合并与决策触发
- `agents/risk_layer.py` — 三层风控检查
- `agents/deepseek_caller.py` — DeepSeek API 调用
- `agents/trade_executor.py` — OKX 实盘下单
- `agents/trade_journal.py` — 交易记录 SQLite

---

## VI. 风控体系（Layer 1-3 详细设计）

### Layer 1: 交易前检查

| 检查项 | 规则 | 超标处理 |
|--------|------|----------|
| 最小间隔 | 距上次交易 > 5 分钟 | 拒绝交易 |
| 单笔上限 | ≤ 0.5 ETH（可配置） | 拒绝交易 |
| 每日交易次数 | ≤ 10 次 | 暂停至次日 |
| 每日亏损上限 | ≤ 100 USDT | 暂停至次日 |
| 连续亏损 | ≤ 3 次 | 降低 50% 仓位 |
| 方向冲突 | 已有同方向仓位 | 累加不超上限 |
| BTC 波动 | BTC 15m 涨跌幅 > 3% | 延迟 5 分钟 |
| 市场深度 | 订单簿买卖价差 < 0.1% | 限价单绕过 |

### Layer 2: 交易中保护

- 限价单优先：挂单后等待 10s，未成交则撤销
- 滑点保护：实际成交价偏离信号价 > 0.3% 则取消剩余
- 网络熔断：连续 3 次 API 超时 → 暂停交易 5 分钟
- 部分成交：部分成交后 10s 未完全成交 → 撤销剩余

### Layer 3: 交易后监控

- 持仓监控：每 5 秒检查止盈止损是否触发
- 浮动止损：价格朝有利方向移动时，止损位上移
- 每日清算：北京时间 00:00 重置当日计数器
- 周报生成：每周自动统计胜率、盈亏比、最大回撤

---

## VII. 四阶段实施路线图

### 阶段一：MVP — 三 Agent 骨架 + 实盘交易（当前执行）

**目标**：最小可行系统，跑通端到端自动交易

**预计改动文件：**

```
新增:
  agents/__init__.py
  agents/agent1_technical.py    # Agent 1 主协程
  agents/agent2_news.py         # Agent 2 主协程
  agents/agent3_trader.py       # Agent 3 主协程
  agents/event_bus.py           # asyncio.Queue 定义 & 事件格式
  agents/kline_builder.py       # WebSocket → 1s K线构建
  agents/change_detector.py     # 信号变化检测
  agents/risk_layer.py          # 三层风控（基础版）
  agents/trade_executor.py      # OKX 实盘下单封装
  agents/config.py              # Agent 系统配置

复用/改造:
  okx_client.py                 # 已有 — 加 WebSocket 支持
  frontend/utils/eth_ai_analysis.py  # 复用 _calc_macd/_calc_kdj/_calc_boll
  frontend/utils/eth_news.py         # 复用 _fetch_crypto_news
  execution/ai_executor.py       # 基础风控逻辑参考

改动:
  main.py（新增）               # asyncio 入口：启动三个 agent
  requirements.txt              # 增加 websockets / aiohttp
```

**交付检查清单：**
- [ ] 三个 Agent 独立协程能同时运行
- [ ] Agent 1 能从 WebSocket 接收实时行情
- [ ] Agent 1 能构建 1s K 线并聚合到标准周期
- [ ] Agent 1 调用 `eth_ai_analysis` 计算指标
- [ ] Agent 1 检测到变化时 push 到 Queue A
- [ ] Agent 2 能抓取新闻并评分
- [ ] Agent 3 消费队列并调 DeepSeek 分析
- [ ] Agent 3 通过 OKX 实盘下单
- [ ] Layer 1 基础风控生效
- [ ] 交易记录写入 SQLite

**不包含在阶段一：**
- 订单簿深度监控（阶段三）
- 链上大额转账（阶段三）
- 资金费率（阶段一用简单版）
- 自学习/周报（阶段四）
- Streamlit 页面改造（保持只读查看现有页面）

---

### 阶段二：风控加固

**目标**：实盘安全锁，防止极端行情下的风险

**内容：**
- Layer 1-3 全部风控规则上线
- 滑点保护 / 限价单
- 自动熔断
- 日亏损限额
- 交易后持仓监控

---

### 阶段三：链上数据 + 市场深度

**目标**：ETH 专属数据优势

**内容：**
- Whale Alert（Etherscan 大额转账）
- Gas 费监控
- 订单簿深度 / 吃单比
- 交易所净流量
- OKX 资金费率完善

---

### 阶段四：自学习 + 信号对齐

**目标**：系统自我优化

**内容：**
- 多周期对齐评分模型
- 交易复盘报告
- 动态策略偏好调整
- Agent 1 参数自适应（根据历史胜率调整指标参数）

---

## VIII. 运行方式

```bash
# 启动三个 Agent（前台）
python main.py

# 查看状态（新终端）
streamlit run frontend/app.py
```

`main.py` 入口：
```python
async def main():
    # 1. 加载配置
    # 2. 初始化 OKX WebSocket 连接
    # 3. 启动三个 Agent 协程
    # 4. 初始化 SQLite 连接
    # 5. 等待所有协程（永不退出）
    asyncio.gather(
        agent1.run(),
        agent2.run(),
        agent3.run(),
    )
```

---

## IX. 文件结构（阶段一完成后）

```
okx-quant-agent/
├── main.py                          # 新增：asyncio 入口
├── okx_client.py                    # 已有：加 WebSocket 方法
├── config.py                        # 已有
│
├── agents/                          # 新增目录
│   ├── __init__.py
│   ├── config.py                    # Agent 系统配置
│   ├── event_bus.py                 # 队列定义 + 事件格式
│   ├── agent1_technical.py          # Agent 1 主协程
│   ├── agent2_news.py              # Agent 2 主协程
│   ├── agent3_trader.py            # Agent 3 主协程
│   ├── kline_builder.py            # WebSocket 滴答→K线
│   ├── change_detector.py          # 信号变化检测
│   ├── risk_layer.py               # 三层风控
│   ├── trade_executor.py           # OKX 实盘下单
│   └── deepseek_caller.py          # DeepSeek 调用封装
│
├── frontend/
│   └── ...                          # 保持现有页面（只读监控）
│
├── execution/
│   └── ...                          # 已有
│
├── risk/
│   └── ...                          # 已有（参考）
│
└── docs/
    └── superpowers/specs/
        └── 2026-06-24-ai-three-agent-arch-design.md  # 本文档
```

---

## X. 技术选型

| 组件 | 技术 | 理由 |
|------|------|------|
| 异步框架 | `asyncio` | Python 原生，三个协程轻量并行 |
| WebSocket | `websockets` 库 | 业界标准，OKX 原生支持 |
| HTTP | `httpx` | 已有依赖，支持 async |
| 消息队列 | `asyncio.Queue` | 内置，零依赖，内存通信 |
| 持久化 | `SQLite`(aiosqlite) | 已有，足够轻量 |
| AI | `openai` SDK → DeepSeek | 已有，复用现有配置 |
| 数据分析 | `pandas` + `numpy` | 已有 |
| 显示 | Streamlit（只读） | 已有 |

---

## XI. 风险提示

1. **实盘交易风险**：Agent 3 自动下单具备真实的资金风险。阶段一上线后建议先用最小仓位（0.001 ETH）运行观察一周
2. **WebSocket 断线重连**：Agent 1 需要有指数退避重连逻辑
3. **DeepSeek API 不稳定**：需要考虑超时 + 降级策略（调用失败时不交易）
4. **OKX API 限频**：`place_order` 有频率限制，需要维护请求计数器
5. **本地电脑关机**：所有 Agent 停止运行，需要开机自启方案
