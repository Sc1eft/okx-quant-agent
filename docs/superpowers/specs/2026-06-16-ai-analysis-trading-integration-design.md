# AI 多空分析 → AI 交易执行 集成设计

## 概述

将已有的 AI 多空分析（DeepSeek 综合多维度数据给出方向判断）与 AI 交易执行（`AIStrategyExecutor`）打通，让实时分析结果可直接驱动一次性的交易决策。

## 方案选型

**方案 A：AI 信号作为一次性入场触发器**（已确认）

- DeepSeek 负责"判断方向 + 给依据"
- 用户点击确认才执行
- 执行器负责"开仓后的全部管理"（止损/止盈/多级移动止盈/超时）
- 一条信号做一次交易，平仓后不再自动重新入场

## 架构

```
用户点击「📊 开始分析」
    → 数据采集（K线 + 关联币种 + 新闻）
    → DeepSeek 返回 { direction, confidence, summary, key_evidence, risk_warnings, ... }
    → 页面展示分析结果卡片

用户点击「⚡ 按此信号交易」
    → SignalBridge.ai_signal_to_rules(signal) 转为 executor 规则 JSON
    → AIStrategyExecutor（现有，加一个 ai_signal 分支）
    → 开仓 → 自动管理退出（止损/止盈/移动止盈/超时）→ 平仓即结束
```

## 新增模块

### 1. `agent/signal_bridge.py` — 信号桥接层

将 DeepSeek 返回的 JSON 转换为 `AIStrategyExecutor` 可消费的结构化规则。

**输入**：DeepSeek 分析结果（direction, confidence, key_evidence, risk_warnings 等）
**输出**：含 `_strategy_type: "ai_signal"` 的标准规则 dict

转换规则：

| DeepSeek 字段 | → | Executor 字段 |
|---|---|---|
| confidence | → | `ai_signal.confidence`（展示用） |
| direction | → | 开仓方向 + `ai_signal.original_direction` |
| key_evidence | → | `ai_signal.key_evidence` |
| risk_warnings | → | `ai_signal.risk_warnings` |
| summary | → | `ai_signal.summary` |
| — | → | `strategy_name = "AI信号-看多/看空"` |
| — | → | 默认 stop_loss_pct=1.5, take_profit_pct=3.0, max_loss_pct=3.0 |
| — | → | position_timeout_bars=96（24h超时） |

### 2. `execution/ai_executor.py` — 新增 `ai_signal` 策略分支

在 `on_bar()` 的入场逻辑中新增（~20 行）：

```python
if strategy_type == "ai_signal" and not self.ai_signal_consumed:
    # AI 信号策略：首次运行即按信号方向开仓，只做一次
    direction = self.rules.get("ai_signal", {}).get("original_direction")
    if direction in ("long", "short"):
        dir_label = "多头" if direction == "long" else "空头"
        reason = f"AI信号开{dir_label}"
        self._execute_entry(float(bar["close"]), direction=direction, reason=reason)
        self.ai_signal_consumed = True
        signal = "buy" if direction == "long" else "short"
```

- 新增状态字段 `self.ai_signal_consumed = False`
- 平仓、reset 时重置此标志
- 入场方向来自 `ai_signal.original_direction`
- 入场后退出管理全部复用现有逻辑

### 3. 前端 `9_🟢_EthereumLive.py` — UI 修改

- 在 AI 分析结果卡片尾部新增 **「⚡ 按此信号交易」** 按钮
- 点击时调用 `SignalBridge` 转为规则
- 创建 Executor 实例（预热 + 启动），流程与现有 "启动 AI 交易" 一致
- 运行时展示同现有：方向/仓位/止损级/冷却/交易记录

## 已确认约束

- **半自动模式**：AI 只出建议，用户确认才执行
- **单次信号 → 单次交易**：平仓后不再自动入场
- **executor 核心不改**：退出管理全部复用
- **不影响现有波动率反向策略**：`_strategy_type` 区分，逻辑独立
