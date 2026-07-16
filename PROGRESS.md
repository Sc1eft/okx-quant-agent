# OKX AI 量化交易系统 — 进度与规划

> 最后更新：2026-07-13

---

## 当前状态

**系统持续亏损，未实现盈利。** 核心原因不是架构问题，是决策信号层缺乏 edge。

| 层面 | 状态 |
|------|------|
| 架构（4 Agent + EventBus + 风控） | ✅ 有效，能承载盈利策略 |
| 数据流水线（WS → KlineBuilder → 指标） | ✅ 有效 |
| 风控（3 层 + PositionMonitor） | ✅ 有效 |
| Agent 4 复盘 + 参数自适应 | ✅ 有效 |
| **决策信号层（Agent 3）** | ❌ 需要重构 |

---

## 已完成的工作（22 项修复，横跨 17 个文件）

### Phase 1 — P0 Production Safety
- `okx_client.py`: JSON 序列化修复
- `main.py`: PID 锁原子创建
- `deepseek_caller.py`: 线程安全 + UTF-8 编码
- `execution/order.py`: numpy.random → random

### Phase 2 — P1 Data Integrity & Architecture
- `config.py` + `main.py`: 双配置合并 `from_root_config()`
- `event_bus.py`: Agent 3 与 Agent 1 解耦（TECHNICAL_STATE → 后移除）
- `okx_ws.py` + `agent1`: WebSocket 重连数据回填
- `kline_builder.py` + `agent1`: vol24h 差量推算
- `trade_executor.py` + `agent4_reviewer.py`: 5 处 `except:pass` → 日志

### Phase 3 — P2-P3 Code Optimization
- `risk_layer.py`: `_check_common_pre()` 抽取，消除 90% 重复代码
- `data/db_manager.py`（新）+ 4 个文件: DatabaseManager 共享连接 + WAL 模式
- `execution/order.py`: 限价未成交方向感知滑点
- `agent3_trader.py`: 移除冗余 `_decision_lock.locked()` 检查
- `agent3_trader.py`: `TradingContext` TypedDict

### Phase 4 — P4 Details
- `config.py` + `agent1_technical.py`: `_MIN_BARS` → 配置化
- 其余 4 项评估为无需修改

### 架构简化（最近一次改动）
- 移除了 EventBus 的 TECHNICAL_STATE 事件和 Agent 3 的缓存层（解耦方案取消）
- 回到简洁模式：Agent 3 持有 `self.agent1` 引用，需要时同步调用
- 减少了 40 行死代码，Queue A 流量减半

---

## 核心问题：当前策略不盈利的原因

### 信号问题
| 现有 | 问题 |
|------|------|
| 15m MACD/KDJ 为主 | 假信号率 60-70%，ETH 震荡市左右打脸 |
| DeepSeek 做实时决策 | 无新增信息、1-3s 延迟、$0.01-0.05/次、不可回测 |
| 多周期信号无优先级 | 15m 高频低质信号淹没了 1h/1d 有效信号 |

### DeepSeek 在实时交易中的角色
- LLM 的输入 `_build_context()` 所有字段都来自系统内指标
- DeepSeek 不做价格预测，只是重述现有数据
- 每次调用增加延迟和成本，不增加预测能力
- **结论：LLM 不应参与实时买卖决策**

---

## 下一步规划：RuleEngine 替换 DeepSeek 实时决策

### 核心思路
DeepSeek 留在 Agent 4 做非实时复盘 + 参数调优，Agent 3 实时决策改为确定性规则引擎。

### 架构变化

```
改动前:
  Agent3._make_decision()
    ├─ _build_context() → 30+字段
    ├─ deepseek.analyze() → API 1-3s ($0.03/次)
    └─ _execute_trade()

改动后:
  Agent3._make_decision()
    ├─ _build_context() → 仅做日志
    ├─ RuleEngine.evaluate() → 0ms 纯本地
    └─ _execute_trade()
  
  DeepSeek 仅用于:
    Agent 4 → _run_review() → 参数调优
```

### 规则设计（ETH 专用）

#### 1. 趋势过滤器（第一道门）
评分系统决定允许交易的方向：
```
+0.5   1h MACD 柱 > 0
+0.5   1h MACD crossover == bullish
+0.75  1d MACD 柱 > 0
+0.25  价格 > 1h EMA50

总分 > +0.15 → 只看多
总分 < -0.15 → 只看空
中间 → 不交易
```

#### 2. 入场信号（按优先级）
在趋势方向内检查：

1. **Bollinger Squeeze 突破**（最稀有，置信度 75-90）
2. **回调到 EMA**（主要模式，置信度 60-80）
3. **KDJ 极端回归**（置信度 50-70）
4. **多周期 MACD 交叉共识**（置信度 55-75）

#### 3. 不交易区间（硬性覆盖）
- 15m 信号无 1h 确认 → **关键改动**
- 1h KDJ 反向极端
- 高波动 + 非趋势行情
- 信号源冲突
- BTC 波动 > 3%（5 分钟延迟）

#### 4. 离场
PositionMonitor 不动，增加：趋势反转平仓、KDJ 极端反转、链上风险硬性平仓

### 文件改动清单

| 文件 | 操作 | 内容 |
|------|------|------|
| `agents/rule_engine.py` | **新建** | RuleEngine 纯函数模块，无 asyncio，无 API 依赖 |
| `agents/agent3_trader.py` | 修改 | DeepSeek 调用 → RuleEngine，新增 `_build_rule_engine_input()`、`_suggested_size_rule()` |
| `agents/config.py` | 修改 | 新增 RuleEngine 参数（可被 Agent 4 调优） |
| `agents/agent4_reviewer.py` | 修改 | 新增 RuleEngine 参数到 `_PARAM_BOUNDS` |
| `main.py` | 修改 | 创建 RuleEngine 实例传入 Agent 3 |
| `strategies/base.py` | 可选 | 注册 RuleEngineStrategy 用于回测 |

### 迁移策略（4 阶段）
1. **创建 RuleEngine + 单元测试** — 纯函数，可单独测试
2. **双路径并行对比** — 同时跑 RuleEngine 和 DeepSeek，只执行 RuleEngine，DeepSeek 记日志（24 小时）
3. **切换默认路径** — `rule_engine_enabled = True`，DeepSeek 热路径移除
4. **回测 + 参数优化** — 6 个月历史数据跑回测，Agent 4 调参

### 现有结构保持不变的部分
- EventBus（Queue A/B）
- Agent 1（WS → KlineBuilder → 指标计算）
- Agent 2（新闻/链上）
- Agent 4 复盘流程
- RiskManager 三层检查
- PositionMonitor 止盈止损
- TradeExecutor 下单
- `_decision_lock` / `_maybe_debounce()` / `_idle_decision_loop()`
- `_suggested_size()` 中的风控乘数/胜率乘数

---

## 关键文件索引

| 文件 | 说明 |
|------|------|
| `agents/agent3_trader.py` | Agent 3 交易引擎，核心决策逻辑在此 |
| `agents/rule_engine.py` | **待创建** 规则引擎核心 |
| `agents/deepseek_caller.py` | DeepSeek API 封装（未来仅 Agent 4 用） |
| `agents/agent4_reviewer.py` | Agent 4 复盘 + 参数调优 |
| `agents/risk_layer.py` | 三层风控 |
| `agents/event_bus.py` | 事件总线（当前 5 种事件类型） |
| `agents/config.py` | AgentSystemConfig 配置（所有可调参数） |
| `agents/confidence_scorer.py` | 信心分计算 |
| `agents/signal_aligner.py` | 信号对齐（技术/新闻/链上） |
| `agents/agent1_technical.py` | Agent 1 技术分析（指标计算入口） |
| `agents/market_state.py` | 市场状态分类 |
| `agents/change_detector.py` | 信号变化检测 |
| `agents/kline_builder.py` | K 线聚合 |
| `execution/order.py` | 订单模拟 |
| `execution/trade_executor.py` | 交易执行 |
| `data/db_manager.py` | DatabaseManager 单例 |

---

## 技术要点记录

### 为何 Agent 3 可以直接引用 Agent 1
最初尝试通过 EventBus 解耦（TECHNICAL_STATE 事件 + 缓存），但：
- 增加复杂度（每条 K 线发两次事件）
- 数据窗口问题（缓存与触发信号不在同一时间切面）
- 单进程 asyncio 架构不需要微服务级别的解耦
- 结论：简洁优先，Agent 3 直接调 `self.agent1.get_indicators_table()`

### 为何 DeepSeek 不适合实时交易
1. 没有私有数据输入 → 没有 edge
2. 延迟不可控（1-3 秒）→ ETH 波动快
3. 成本累加（$10-30/月）→ 纯亏损
4. 不可回测 → 无法系统优化
5. 不稳定性 → 同条件不同输出

### ETH 交易特性
- 机构主导，15m 噪音大
- 1h/4h/1d 趋势可靠性显著提升
- 强均值回归特性（KDJ 极端位置）
- 与 BTC 高相关性（~0.8）
- 链上数据（Gas/资金费率/巨鲸）有信号价值
