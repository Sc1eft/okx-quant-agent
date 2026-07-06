---
title: 三 Agent 系统支持永续合约交易
date: 2026-07-05
status: approved
---

# 三 Agent 系统支持永续合约交易

## 1. 动机

当前 4 Agent 自动交易系统仅支持现货（spot）模式。已完成的模拟交易页面支持了 USDT 本位永续合约，但 Agent 系统与之独立运行，无法利用合约的杠杆特性。

目标：让 Agent 系统（Agent 1/2/3/4）支持逐仓 USDT 永续合约交易，Agent 感知杠杆、强平价、保证金率，做出更准确的风控决策。

## 2. 设计原则

- **零破坏**：现有现货行为完全不变，`market_mode="spot"` 是默认值
- **复用已验证代码**：直接使用 `execution/futures_paper.py`（FuturesAccount），不重复实现强平/保证金逻辑
- **最小改造成本**：~120 行净增，改 5 个文件

## 3. 架构变更

```
Agent 3 (DeepSeek → "buy"/"sell"/"hold")
    │
    ├─ _build_context() ──→ 上下文中携带杠杆/强平价/保证金率
    │
    └─ executor.execute_safe()
           │
           ├─ market_mode="spot"    → random fill (不变)
           │
           └─ market_mode="futures" → FuturesAccount
                ├─ open_long / open_short / flip
                ├─ liquidation check
                └─ return {fill_price, leverage, margin, liquidation_price, ...}
```

## 4. 改动清单

### 4.1 `agents/config.py` — AgentSystemConfig (+3 行)

```
market_mode: str = "spot"          # "spot" | "futures"
futures_leverage: int = 10         # 1x-125x
```

默认 `"spot"`，现货用户无感知。

### 4.2 `okx_client.py` — place_order (+15 行)

| 改前 | 改后 |
|------|------|
| `tdMode: "cash"` 硬编码 | 接受 `td_mode` 和 `lever` 参数 |
| 无杠杆参数 | 非 cash 时传 `lever` |

实盘预备：当前 paper 模式不调真实 API，但代码结构已合规。

### 4.3 `agents/trade_executor.py` — 模拟成交路径 (+80 行)

当前 paper 模拟路径（~30 行随机成交）保留为 `"spot"` 分支。

**`"futures"` 分支（~50 行）：**

```
execute_safe()
  ├─ market_mode="spot"    → 原有 random fill
  └─ market_mode="futures" → FuturesAccount 模拟
       ├─ buy  → open_long (若持空则平空翻多)
       ├─ sell → open_short (若持多则平多翻空)
       └─ 返回 {success, fill_price, filled_size, leverage,
                  margin, liquidation_price, position_value,
                  margin_rate, position_side}
```

`TradeExecutor.__init__` 新增 `market_mode: str = "spot"` 和 `leverage: int = 10`。

FuturesAccount 实例由 Agent 3 创建并传入，TradeExecutor 不直接依赖 execution/futures_paper。

### 4.4 `agents/agent3_trader.py` — 合约感知 (+30 行)

#### 持仓状态

```python
self._current_position = {
    "side": "none",
    "size": 0.0,
    "entry_price": 0.0,
    "market_mode": "spot",
    "leverage": 0,
    "margin": 0.0,
    "liquidation_price": 0.0,
    "position_value": 0.0,
    "margin_rate": 0.0,
}
```

#### PnL 百分比（杠杆感知）

```
合约 PnL% = (current_price - entry_price) / entry_price × leverage × 100
现货 PnL% = (current_price - entry_price) / entry_price × 100（不变）
```

#### DeepSeek 上下文注入

在 `_build_context()` 返回的 dict 中新加：

```python
"市场模式": "合约" if market_mode == "futures" else "现货"
"杠杆": f"{leverage}x"
"强平价": f"${liquidation_price:.2f} (距当前 {distance_pct:.1f}%)"
"保证金率": f"{margin_rate:.1%}"
```

### 4.5 `frontend/pages/11_🤖_AI_Trading.py` — 前端 KPI (+40 行)

控制面板新增：

- 交易模式选择器（现货/合约）
- 杠杆滑块（1x-125x，合约模式可用）

Agent 状态卡片新增（合约模式）：

| KPI | 显示 |
|-----|------|
| 方向 | 🟢 多头 10x / 🔴 空头 10x |
| 盈亏 | `+12.3%`（含杠杆乘数） |
| 强平价 | `$2,443.50`（距当前 <10% 时标红 ⚠️） |
| 保证金率 | `12.1%` |

## 5. 数据流（完整决策周期）

```
Agent 1 (技术) ──→ Queue A ──┐
                              ├── Agent 3 ──→ DeepSeek
Agent 2 (新闻) ──→ Queue B ──┘                   │
                                          "buy ETH, confidence 75%"
                                                  │
                                    executor.execute_safe("buy", 0.5, price)
                                                  │
                                     ┌────────────┴────────────┐
                                     │ market_mode="spot"       │ market_mode="futures"
                                     │ random fill              │ FuturesAccount.open_long()
                                     │ return {fill_price}      │ return {fill_price,
                                     │                          │         leverage: 10,
                                     │                          │         margin: 135.75,
                                     │                          │         liquidation_price: 2443.5,
                                     │                          │         margin_rate: 0.121}
                                     └────────────┬────────────┘
                                                  │
                                   Agent 3 更新持仓状态 (+杠杆/强平价)
                                                  │
                                   Agent 4 复盘时参考合约盈亏
```

## 6. 强平风控（被动安全）

FuturesAccount 内部自动检查 `is_liquidated`（`margin_rate ≤ maintenance_margin_ratio`）：

- Agent 3 **每次收到事件**检查一次强平状态（`FuturesAccount.liquidate()`）
- 但不会让 DeepSeek "决定爆仓"——强平是自动的，不受 AI 延迟影响
- 强平发生时记录一条 `close` trade，标明 `side: "liquidation"`

Agent 3 层面不做主动强平决策，那是引擎层的职责。

## 7. 边界情况

| 场景 | 行为 |
|------|------|
| 合约模式持多，Agent 3 收到 "sell" | 平多 → 开空（翻仓），FuturesAccount 内部先 close 再 open |
| 合约模式持空，Agent 3 收到 "buy" | 平空 → 开多（翻仓） |
| 合约模式，balance 不足以开仓 | 风控拒绝（与现货逻辑一致）|
| 杠杆 1x | 正常执行，PnL 不放大（等价于现货但走合约公式）|
| `agent3_config.market_mode == "spot"` | 全部旧逻辑，一行代码不跑 |
| Agent 重启 | FuturesAccount 状态重新初始化，从 OKX 查持仓（实盘阶段）|

## 8. 测试策略

在 `tests/test_futures_paper.py` 已有 41 个测试基础上，新增：

1. `TradeExecutor.execute_safe()` futures 分支 — 开多/开空/翻仓模拟成交
2. `Agent3._calc_pnl_pct()` 合约模式 — 杠杆乘数正确
3. `Agent3._build_context()` — 合约模式下包含 liquidation_price 等字段
4. 前端 fragment — 合约模式下 KPI 显示正确

## 9. 实施顺序

1. `agents/config.py` — +3 行
2. `okx_client.py` — +15 行
3. `agents/trade_executor.py` — +80 行（核心）
4. `agents/agent3_trader.py` — +30 行
5. `frontend/pages/11_🤖_AI_Trading.py` — +40 行
6. 运行全部测试验证：`pytest tests/ -v`
