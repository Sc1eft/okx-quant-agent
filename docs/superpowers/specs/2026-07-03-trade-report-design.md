# 交易周期汇总报告设计

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this spec task-by-task.

**Goal:** 每日/每周/每月自动生成交易汇总报告，盈亏全量分析，通过 ServerChan 推送到微信，并在前端提供报告浏览页面。

**Architecture:** 在现有 ReviewGenerator 基础上扩展，新增月度统计、亏损交易提取、DeepSeek 亏损原因分析、ServerChan 推送。尽量复用 Agent 3 已有的 `_review_scheduler()` 定时框架。

**Tech Stack:** Python 3.12+, SQLite (`agent_trades.db`), DeepSeek V4 Pro API, ServerChan API, Streamlit

---

## Global Constraints

- 所有报告 JSON 存入 `data/reports/{daily,weekly,monthly}/` 目录
- ServerChan 推送失败不阻塞主流程，记录失败状态下次重试
- DeepSeek 分析亏损原因使用 `temperature=0.4`，与复盘分析一致
- 亏损分析仅在报告周期内存在亏损交易时触发
- 报告不重复推送——已推送标记 `pushed: true` 不再推送
- 每日报告触发时间 UTC 16:00（= 北京时间午夜），沿用已有 `review_daily_hour_utc` 配置
- 每周报告在周日 UTC 16:00 触发
- 每月报告在每月 1 日 UTC 16:00 触发

---

## 文件结构

### 新建文件

| 文件 | 用途 | 估算行数 |
|------|------|---------|
| `agents/notifier.py` | ServerChan 推送封装 | ~80 |
| `frontend/pages/13_📋_TradeReport.py` | 前端报告浏览页面 | ~250 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `agents/review_generator.py` | 新增 `generate_monthly_report()`、`_extract_loss_trades()`、`_analyze_trades_with_deepseek()`、盈亏双向模式分析 |
| `agents/deepseek_caller.py` | 新增 `analyze_trade_report()` 方法，用于亏损原因和盈亏模式分析 |
| `agents/agent3_trader.py` | 在 `_review_scheduler()` 中加月报触发 + 推送调用 |
| `agents/config.py` | 加 ServerChan、推送开关、报告目录等配置字段 |
| `agents/status_writer.py` | 加报告生成/推送状态到 JSON |
| `frontend/app.py` | 加"交易报告"导航入口 |

---

## 详细设计

### 1. ReviewGenerator 扩展

**新增方法：**

#### `generate_monthly_report() -> dict`

- 查询 SQLite: `SELECT * FROM trades WHERE trade_type='close' AND timestamp >= month_start AND timestamp < next_month_start`
- 用已有 `_compute_range_stats()` 算统计
- 调用 `_analyze_trades_with_deepseek(rows, stats)` 分析盈亏模式
- 写 JSON 到 `data/reports/monthly/YYYY-MM.json`
- 返回完整 report dict

#### `_extract_loss_trades(rows) -> list[dict]`

- 筛选 `pnl_close < 0` 的交易
- 每条返回: `{ trade_id, time, pnl, side, entry_price, exit_price, original_reason (从 decision 字段解析) }`
- 同样提取盈利交易 `pnl_close > 0` 用于盈利分析

#### `_analyze_trades_with_deepseek(all_rows, stats) -> dict`

- 准备 context：周期内的交易统计，盈利交易列表，亏损交易列表
- 调用 `deepseek_caller.analyze_trade_report(context)`
- DeepSeek prompt 要求返回 JSON 格式：

```json
{
  "wins": {
    "count": 5,
    "total_profit": 98.0,
    "patterns": [
      { "pattern": "MACD 金叉 + KDJ 超卖共振做多", "wins_count": 3, "avg_profit": 22.5, "takeaway": "..." }
    ]
  },
  "losses": {
    "count": 3,
    "total_loss": -12.5,
    "patterns": [
      { "pattern": "布林带上轨突破追多", "loss_count": 2, "avg_loss": -5.2, "cause": "追高被套", "suggestion": "..." }
    ]
  },
  "summary": "做多 5 胜 3 负，胜率 62.5%。..."
}
```

- 如果无亏损交易，losses 部分返回 `{ "count": 0, "total_loss": 0, "patterns": [] }`
- 如果无盈利交易，wins 部分类似处理

#### 三种报告统一输出格式

```json
{
  "type": "daily|weekly|monthly",
  "date": "2026-07-03",
  "generated_at": "2026-07-03T16:00:00+00:00",
  "period": { "start": "...", "end": "..." },
  "stats": { ... },
  "trades": {
    "wins": [ ... ],
    "losses": [ ... ]
  },
  "ai_analysis": {
    "wins": { ... },
    "losses": { ... },
    "summary": "..."
  },
  "pushed": false,
  "push_time": null
}
```

### 2. DeepSeekCaller 扩展

**新增方法 `analyze_trade_report(context: dict) -> dict`:**

```python
def analyze_trade_report(self, context: dict) -> dict:
    """分析一段周期内的交易盈亏模式。
    
    context 包含:
      - period_type: "daily" | "weekly" | "monthly"
      - period: { start, end }
      - stats: { trades, wins, losses, win_rate, total_pnl, ... }
      - win_trades: [{ pnl, side, reason, entry_price, exit_price }, ...]
      - loss_trades: [{ pnl, side, reason, entry_price, exit_price }, ...]
    
    返回: { wins: {...}, losses: {...}, summary: "..." }
    """
```

- `temperature=0.4`, `max_tokens=2000`
- 使用 structured JSON prompt，与 `analyze()` 相同的重试/回退模式

### 3. Notifier (新建)

```python
"""ServerChan 推送封装。"""

class ServerChanNotifier:
    def __init__(self, sendkey: str):
        self._sendkey = sendkey
    
    def push_report(self, report_type: str, date_str: str, 
                    stats: dict, ai_analysis: dict) -> bool:
        """推送报告到微信。
        
        1. 根据 report_type 选择模板（日报/周报/月报）
        2. 用 stats + ai_analysis 填充模板
        3. GET https://sctapi.ftqq.com/{sendkey}.send?title=...&desp=...
        4. 返回成功/失败
        """
```

**推送模板（hardcoded 多行字符串，保证微信内可读）：**

日报模板（前文设计已定，Markdown 格式，~20 行文本）

周报模板（含趋势对比 section，~30 行文本）

月报模板（含月度趋势和评估 section，~35 行文本）

### 4. Agent 3 调度集成

在 `agent3_trader.py` 的 `_review_scheduler()` 中，已有每小时检查逻辑：

```python
async def _review_scheduler(self):
    while True:
        await asyncio.sleep(3600)
        now = datetime.now(timezone.utc)
        
        # 已有: 每日报告 (UTC 16:00)
        if now.hour == self.config.review_daily_hour_utc and ...:
            report = self.review_gen.generate_daily_report()
            if report:
                self._push_if_needed(report)
        
        # 新增: 每周报告 (周日 + UTC 16:00)
        if now.weekday() == 6 and now.hour == self.config.review_daily_hour_utc:
            report = self.review_gen.generate_weekly_report()
            if report:
                self._push_if_needed(report)
        
        # 新增: 每月报告 (1日 + UTC 16:00)
        if now.day == 1 and now.hour == self.config.review_daily_hour_utc:
            report = self.review_gen.generate_monthly_report()
            if report:
                self._push_if_needed(report)
```

**`_push_if_needed(report)`** — 检查 `pushed` 标记，未推送则调用 `notifier.push_report()`，成功后标记 `pushed: true` 并写回文件。

### 5. 配置扩展

在 `agents/config.py` 的 `AgentSystemConfig` 中新增：

```python
# ── 交易报告 + ServerChan 推送 ──
report_enabled: bool = True
report_daily_hour_utc: int = 16   # 每日/周/月报告触发小时
report_dir: str = "data/reports"
report_min_trades_for_analysis: int = 1  # 最少几笔交易才做 DeepSeek 分析
serverchan_enabled: bool = False
serverchan_sendkey: str = ""
```

### 6. 前端"交易报告"页面

新增 `frontend/pages/13_📋_TradeReport.py`：

**页面布局：**

```
┌─────────────────────────────────────┐
│  📋 交易报告                         │
│  [📅 日报] [📅 周报] [📅 月报] 标签  │
│  [生成日报] [生成周报] [生成月报] 按钮 │
├─────────────────────────────────────┤
│                                      │
│  2026-07-03 日报          ✅ 已推送   │
│  交易 8笔 · +85.5 USDT · 胜率62.5%   │
│  盈利: MACD共振 +67.5                 │
│  亏损: 追高 -10.4 → 建议等待回踩     │
│  ─── [查看详情] [重新推送] ───        │
│                                      │
│  2026-W27 周报          ✅ 已推送     │
│  交易 42笔 · +320 USDT · 胜率59.5%   │
│  ...                                 │
│                                      │
│  (报告列表按时间倒序)                  │
└─────────────────────────────────────┘
```

**功能：**
- 从 `data/reports/{daily,weekly,monthly}/` 读取所有 JSON 文件
- 按时间倒序渲染报告卡片
- 标签切换过滤报告类型（日报/周报/月报/全部）
- 查看详情展开完整 JSON + AI 分析
- 重新推送按钮（调用 ServerChan）
- 手动生成按钮（触发 ReviewGenerator 立即生成并推送）

### 7. status_writer 扩展

在 `write_agent_status()` 的 dict 中新增：

```python
"reports": {
    "last_daily": "2026-07-03",
    "last_weekly": "2026-W27",
    "last_monthly": "2026-07",
    "last_push_ok": true,
    "last_push_time": "2026-07-03T16:00:05+00:00"
}
```

### 8. 导航入口

在 `frontend/app.py` 的 pages 列表末尾新增：

```python
("📋 交易报告", "pages/13_📋_TradeReport.py"),
```

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| SQLite 无交易数据 | 生成空报告，`stats` 全零，不调 DeepSeek，推送"本周期无交易" |
| DeepSeek API 超时/失败 | 跳过 AI 分析，报告仍生成（不含 `ai_analysis`），下次调度重试 |
| ServerChan 推送失败 | 记录失败，`pushed` 保持 `false`，下次调度重试 |
| 报告 JSON 文件写入失败 | 记录日志，不影响下次生成 |
| 同时触发日+周/日+月 | 顺序执行不冲突，各写各的文件 |

---

## 测试策略

覆盖以下场景：
- 周期内无交易 → 空报告
- 周期内全盈利 → 仅有 wins 分析
- 周期内全亏损 → wins 为空
- 盈亏混合 → 完整的双向分析
- ServerChan 推送成功/失败
- 推送去重（重复调用不重复推送）
- 三种报告类型的日期/周期计算正确（边界：月末、跨年、ISO 周）
