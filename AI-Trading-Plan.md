# AI 自动交易功能 — 重构 + 实现方案

## 背景

当前 `9_🟢_EthereumLive.py` 是一个 **1,937 行/85KB** 的巨文件，每次加载消耗约 **28K-30K tokens**。
在此基础上直接加 AI 自动交易会导致每轮对话 45K+ tokens，频繁触发上下文压缩。

**目标**：重构拆模块 → 新建独立 AI 交易页 `11_🤖_AI_Trading.py`

---

## Phase 1 — 从 9_EthereumLive.py 抽取共享模块

**原则**：不改页面行为，只移动代码为 import，确保抽取后页面完全一致。

### 1.1 `frontend/utils/eth_news.py` (~100 行)

从 Live 抽出：
- `_fetch_crypto_news()` — 从 PANews sitemap/CoinDesk RSS 采集新闻
- `_fmt_relative_time()` — ISO/RFC 2822 → 中文相对时间（"9小时前"）

### 1.2 `frontend/components/eth_charts.py` (~150 行)

从 Live 抽出：
- `_build_candlestick_fig(df, height=400)` — Plotly 交互式 K 线图
- `_build_sparkline(ticks, height=110)` — 心跳模式 sparkline

### 1.3 `frontend/utils/eth_ai_analysis.py` (~250 行)

从 Live 抽出：
- `_AI_SYSTEM_PROMPT` — DeepSeek 系统提示词（含动态权重策略表）
- `_call_ai_analysis(ticker, klines_15m, klines_1h, klines_1d, btc_ticker, sol_ticker, doge_ticker, news, cfg)` — 调用 AI API 返回 JSON 分析结果
- `_build_ai_analysis_prompt(…)` — 拼接喂给 AI 的数据
- `_sanitize_ai_text(text)` — 清除 AI 回复中的 markdown 代码围栏
- `_ticker_summary(label, data)` — 格式化 ticker 文本
- `_summarize_klines(df, label)` — 格式化 K 线摘要文本

### 1.4 改造后 9_EthereumLive.py (~500 行)

- 上述函数改为 `from frontend.utils.eth_news import ...` 等
- 保留：页面布局、st.fragment 数据流、TradingView 组件、AI 按钮触发逻辑、AI 结果渲染、AI 聊天上下文
- 保留 CSS/JS 蒙版隐藏
- 导入方式示例：
  ```python
  from frontend.utils.eth_ai_analysis import (
      _call_ai_analysis, _sanitize_ai_text, _fmt_relative_time
  )
  from frontend.utils.eth_news import _fetch_crypto_news
  ```

---

## Phase 2 — 新建 `frontend/pages/11_🤖_AI_Trading.py` (~700-900 行)

### 2.1 功能概述

将 DeepSeek AI 多空分析结果（来自 Live 页的已有分析能力）与 `AIStrategyExecutor`（`execution/ai_executor.py`）打通，实现半自动/全自动的 AI 交易执行。

### 2.2 流程

```
用户点击「开始AI交易」
  ↓
自动采集：行情 + 技术指标 + 关联币种 + 新闻
  ↓
调用 DeepSeek AI 分析（复用 eth_ai_analysis 模块）
  ↓
AI 返回：direction（long/short/hold）, strength, entry_price_range, stop_loss, take_profit
  ↓
组装为 AIStrategyExecutor 可消费的 rules JSON
  ↓
Executor 在实时 K 线数据流中等待入场条件
  ↓
入场 → 持仓监控（止盈止损/移动止盈）→ 出场 → 记录
```

### 2.3 页面 UI 结构

```
┌─────────────────────────────────────────────┐
│  🤖 AI 自动交易                               │
│  [开始监控] [停止] [清除结果]                   │
├─────────────────────────────────────────────┤
│  交易状态卡片                                  │
│  ┌──────┬──────┬──────┬──────┬──────┐        │
│  │ 余额  │ 持仓  │ 浮动盈亏 │ 信号 │ 交易次数 │
│  └──────┴──────┴──────┴──────┴──────┘        │
├─────────────────────────────────────────────┤
│  K 线图（复用 eth_charts）+ 信号标记          │
├─────────────────────────────────────────────┤
│  AI 分析面板                                  │
│  - 最新 AI 信号方向 + 理由                     │
│  - 参考新闻（折叠）                            │
├─────────────────────────────────────────────┤
│  交易记录列表                                  │
│  ┌────┬──────┬──────┬──────┬──────┬──────┐  │
│  │ #  │ 时间  │ 方向  │ 价格  │ 数量  │ PnL │  │
│  └────┴──────┴──────┴──────┴──────┴──────┘  │
├─────────────────────────────────────────────┤
│  权益曲线图（复用 equity_curve_chart）        │
└─────────────────────────────────────────────┘
```

### 2.4 关键实现点

1. **AIExecutor 集成**：已有 `AIStrategyExecutor`（873行，`execution/ai_executor.py`），支持：
   - 多空双向
   - 硬性止盈止损（方向感知）
   - 多级移动止盈（1.25% → 保本, 2.5% → +1.25%, 5% → 平50% + 2.5%）
   - 波动率反向策略
   - AI 信号策略模式（已有 `ai_signal` strategy_type！）

2. **数据流**：复用 Live 页的 `@st.fragment(run_every=…)` 自动刷新模式
   - fragment 内喂新 K 线给 Executor
   - `on_bar()` 返回状态 → 更新 UI

3. **风控**：复用 `RiskEngine`（`risk/rules.py`，126行），支持：
   - 连续亏损暂停
   - 日内亏损上限
   - 冷却恢复

4. **模拟账户**：复用 `PaperAccount`（`execution/paper.py`，348行），支持多空

### 2.5 与现有 Paper Trading 页的区别

| 维度 | 8_PaperTrading（已有） | 11_AI_Trading（新建） |
|------|----------------------|----------------------|
| 信号源 | 传统策略（MA/RSI/突破） | DeepSeek AI 多空分析 |
| 入场逻辑 | 条件引擎自动触发 | AI 信号驱动，结合实时行情确认 |
| 新闻因子 | 无 | ✅ AI 分析新闻影响 |
| 分析维度 | 纯技术面 | 技术面 + 基本面 + 新闻 |
| 适合场景 | 量化策略回测/模拟 | AI 辅助主观交易 |

---

## 涉及的现有文件一览

| 文件 | 行数 | 作用 | 本次是否改动 |
|------|------|------|------------|
| `frontend/pages/9_🟢_EthereumLive.py` | 1,937 | 以太坊实时行情 + AI 分析 | ✅ 抽取共享模块后精简 |
| `execution/ai_executor.py` | 873 | AI 交易执行引擎 | 🔄 可能需添加 ai_signal 触发策略 |
| `execution/paper.py` | 348 | 模拟账户（多空） | 不改 |
| `risk/rules.py` | 126 | 风控引擎 | 不改 |
| `frontend/pages/8_💰_PaperTrading.py` | 750 | 现有模拟交易页 | 不改 |
| `config.py` | 247 | 配置管理 | 不改 |
| `frontend/utils/data_provider.py` | — | OKX 数据获取 | 不改 |
| `frontend/utils/session_state.py` | — | 配置读取 | 不改 |
| `frontend/components/metrics_display.py` | — | 指标卡片渲染 | 不改 |
| `frontend/components/charts.py` | — | 权益曲线图 | 不改 |

## 实现顺序

1. **Phase 1a**: 抽出 `utils/eth_news.py` + `utils/eth_ai_analysis.py`
2. **Phase 1b**: 抽出 `components/eth_charts.py`
3. **Phase 1c**: 改造 `9_EthereumLive.py` 为 import 模式 → 验证页面一致
4. **Phase 2a**: 新建 `11_🤖_AI_Trading.py` 基础框架（UI + 数据流）
5. **Phase 2b**: 集成 AIStrategyExecutor + 信号执行
6. **Phase 2c**: 前端渲染（交易记录、权益曲线、状态卡片）

## 蒙版问题当前修复（已推送）

CSS 选择器从精确匹配改为模糊匹配 + MutationObserver，部署后即可验证。
commit: `e740106`，已推送 `master`。
