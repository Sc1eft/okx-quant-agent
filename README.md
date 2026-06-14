# OKX 虚拟币量化交易系统

BTC-USDT 现货量化交易，Python 实现。

> ⚠️ **风险提示**: 加密货币交易有极高风险。本系统仅供学习和研究用途，不构成任何投资建议。实盘交易前请充分了解风险。

---

## 🔧 优化概览（对比原计划）

| 原计划 | 本版本优化 | 优先级 |
|--------|-----------|--------|
| 1 个策略 (MA交叉) | **3 个策略池**: MA交叉 + RSI均值回归 + 突破策略 | P0 |
| 无止盈/止损 | **完整止盈/止损/移动止损/ATR动态止损** | P0 |
| 无过拟合检测 | **Walk-forward 验证 + 蒙特卡洛参数扫描** | P0 |
| SQLite 默认 | **WAL 模式 + 性能调优** (读写不冲突) | P1 |
| 无数据质量检查 | **K 线连续性 + 异常价格 + 完整性检测** | P1 |
| 无测试 | **策略/风控/回测/数据 4 套单元测试** | P1 |
| 无订单类型分析 | **Market vs Limit 订单对比** | P2 |
| 无通知 | **邮件 + Webhook + 本地日志通知** | P2 |
| Agent 模糊描述 | **Agent 只做回测后分析，不下单** | P3 |
| 暂停后无恢复策略 | **3 种恢复模式 (手动/自动冷却/切换策略)** | P3 |

## 项目结构

```
okx-quant-agent/
├── main.py                 # 主入口
├── config.py               # 配置管理（dataclass + JSON）
├── okx_client.py           # OKX REST API 客户端
├── requirements.txt        # 依赖
├── configs/default.json    # 默认配置
├── data/
│   ├── storage.py          # SQLite 存储（WAL 模式）
│   ├── collector.py        # 行情采集
│   └── quality.py          # 🔧 数据完整性检查
├── strategies/
│   ├── base.py             # 策略基类
│   ├── ma_cross.py         # MA 均线交叉（含止盈止损）
│   ├── rsi_mean_reversion.py  # RSI 均值回归
│   └── breakout.py         # 突破策略（ATR 自适应）
├── backtest/
│   ├── engine.py           # 回测引擎
│   ├── metrics.py          # 绩效指标
│   └── analyzer.py         # 🔧 Walk-forward + 参数扫描
├── risk/
│   ├── rules.py            # 风控规则引擎
│   ├── stop_loss.py        # 🔧 止盈止损计算
│   └── recovery.py         # 🔧 暂停恢复策略
├── execution/
│   ├── order.py            # 🔧 订单类型分析
│   └── paper.py            # 模拟盘
├── agent/
│   ├── report_analyzer.py  # DeepSeek 回测分析
│   └── audit.py            # 边界审计
├── notification/
│   └── notifier.py         # 🔧 通知系统
├── tests/
│   ├── test_strategy.py    # 策略测试
│   ├── test_risk.py        # 风控测试
│   ├── test_backtest.py    # 回测测试
│   └── test_data.py        # 数据层测试
└── logs/                   # 日志目录
```

## 快速开始

### 1. 安装

```bash
pip install -r requirements.txt
```

### 2. 下载数据

```bash
python -c "
from config import Config
from data.collector import DataCollector
c = Config()
d = DataCollector(c)
d.download_historical('BTC-USDT', '1h', total_candles=1000)
d.close()
"
```

### 3. 运行回测

```bash
# 默认策略回测
python main.py --mode backtest

# Walk-forward 验证
python main.py --walk-forward

# 参数扫描
python main.py --param-sweep

# 列出所有策略
python main.py --list-strategies
```

### 4. 数据质量检查

```bash
python -c "
from config import Config
from data.quality import DataQualityChecker
c = Config()
q = DataQualityChecker(c)
r = q.full_check('BTC-USDT', '1h')
print(r)
q.close()
"
```

### 5. 运行测试

```bash
pytest tests/ -v
```

## 风控说明

第一版**不做实盘下单**，执行路径：

```
历史回测 → Walk-forward验证 → 本地模拟盘 → OKX模拟盘 → 小资金实盘
```

每个阶段有独立验收标准，**前一阶段未通过不准进入下一阶段**。

### 恢复策略

风控暂停后的 3 种恢复模式：

| 模式 | 说明 | 适用 |
|------|------|------|
| `manual` | 暂停后必须手动确认恢复 | 新手，最安全 |
| `auto_cool` | 冷却 N 根 K 线后自动恢复 | 有经验后 |
| `switch_strategy` | 自动切换到其他策略 | 多策略运行 |

## Agent 边界

DeepSeek Agent **只能做**：
- ✓ 回测报告解读
- ✓ 过拟合迹象检测
- ✓ 风险配置检查

**不能做**：
- ✗ 直接下单
- ✗ 绕过风控
- ✗ 修改 API Key
- ✗ 自动扩大仓位

## 通知配置

在 `configs/default.json` 中配置:

```json
{
  "notification": {
    "enabled": true,
    "email_enabled": true,
    "smtp_host": "smtp.qq.com",
    "smtp_user": "your@qq.com",
    "notify_email": "your@qq.com",
    "webhook_enabled": false,
    "notify_on": ["signal", "trade", "error", "daily_report"]
  }
}
```

## 许可

MIT
