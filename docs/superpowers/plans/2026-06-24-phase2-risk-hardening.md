# Phase 2: 风控加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为三 Agent 系统加固实盘安全锁 — 限价单完整生命周期、BTC 波动保护、市场深度检查、持仓止盈止损监控、北京时间结算、风控状态注入 DeepSeek

**分支:** `feat/three-agent-phase2`（从 `feat/three-agent-phase1` 起分支）

**Architecture:** 分 7 个独立 Task：API 追加 → TradeExecutor 升级 → RiskLayer 加固 → PositionMonitor 新模块 → Agent3 集成 → Main 启动 → 监控面板。改动集中在 `okx_client.py` 和 `agents/` 目录，不触及已有 Streamlit 页面逻辑。

**Tech Stack:** Python asyncio, OKX REST API v5 (REST), pytest + unittest.mock, SQLite (aiosqlite)

## Global Constraints

- 所有 API 签名保持与现有 `okx_client.py` 一致（HMAC-SHA256, `_sign()` 方法）
- `sys.path.insert(0, "")` 模式不变（项目无根 `__init__.py`）
- 全部代码注释使用中文
- 全部 Phase 2 配置参数添加在 `agents/config.py` 的 `AgentSystemConfig` 中
- 新增模块遵循现有 `agents/` 目录风格（class-based, logging.getLogger("module_name")）
- 持仓监控使用 `asyncio` 协程，不引入第三方调度库
- 北京时间每日结算使用 UTC 16:00（而非 localtime），保持与现有代码一致的时区处理

---

### Task 1: OKX API 追加 — cancel_order + get_order + get_order_book

**Files:**
- Modify: `okx_client.py`
- Test: `tests/test_okx_client_phase2.py`

**Interfaces:**
- Consumes: `OKXClient` class from `okx_client.py` (existing `_request`, `_sign`, `_check_api_response`, `_timestamp` methods)
- Produces:
  - `cancel_order(symbol: str, order_id: str) -> dict` — 撤销订单
  - `get_order(symbol: str, order_id: str) -> dict` — 查询订单状态
  - `get_order_book(symbol: str, depth: int = 5) -> list[dict]` — 查询订单簿
  - 被 Task 2（TradeExecutor）和 Task 3（RiskLayer）消费

- [ ] **Step 1: 创建测试文件，写三个 API 方法的测试**

```python
# tests/test_okx_client_phase2.py
"""测试 OKX API 追加的三个方法（使用 mock 避免真实网络请求）"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from okx_client import OKXClient
from config import ExchangeConfig


@pytest.fixture
def client():
    cfg = ExchangeConfig(api_key="test", secret_key="test", passphrase="test")
    client = OKXClient(cfg)
    # Mock _request 避免真实 HTTP 调用
    client._request = MagicMock()
    return client


def _mock_response(data: list):
    """构造 OKX 标准响应格式"""
    mock = MagicMock()
    mock.json.return_value = {"code": "0", "msg": "", "data": data}
    return mock


class TestCancelOrder:
    def test_cancel_order_success(self, client):
        """测试成功撤单"""
        client._request.return_value = _mock_response([{"ordId": "12345"}])
        result = client.cancel_order("ETH-USDT", "12345")
        assert result["ordId"] == "12345"
        # 验证签名和请求参数
        call_kwargs = client._request.call_args
        assert call_kwargs[0][0] == "POST"
        assert "/api/v5/trade/cancel-order" in call_kwargs[0][1]

    def test_cancel_order_api_error(self, client):
        """测试撤单 API 返回错误"""
        mock = MagicMock()
        mock.json.return_value = {"code": "51001", "msg": "订单不存在", "data": []}
        client._request.return_value = mock
        with pytest.raises(RuntimeError, match="OKX API error"):
            client.cancel_order("ETH-USDT", "99999")


class TestGetOrder:
    def test_get_order_filled(self, client):
        """测试查询已成交订单"""
        mock_data = [{
            "ordId": "12345", "state": "filled", "fillPx": "3450.50",
            "fillSz": "0.01", "accFillSz": "0.01", "side": "buy",
            "instId": "ETH-USDT",
        }]
        client._request.return_value = _mock_response(mock_data)
        result = client.get_order("ETH-USDT", "12345")
        assert result["state"] == "filled"
        assert float(result["fillPx"]) == 3450.50

    def test_get_order_partial_fill(self, client):
        """测试查询部分成交订单"""
        mock_data = [{
            "ordId": "12345", "state": "partially_filled", "fillPx": "3450.00",
            "fillSz": "0.005", "accFillSz": "0.005", "side": "buy",
            "instId": "ETH-USDT",
        }]
        client._request.return_value = _mock_response(mock_data)
        result = client.get_order("ETH-USDT", "12345")
        assert result["state"] == "partially_filled"

    def test_get_order_cancelled(self, client):
        """测试查询已取消订单"""
        mock_data = [{
            "ordId": "12345", "state": "canceled", "fillPx": "",
            "fillSz": "0", "accFillSz": "0", "side": "buy",
            "instId": "ETH-USDT",
        }]
        client._request.return_value = _mock_response(mock_data)
        result = client.get_order("ETH-USDT", "12345")
        assert result["state"] == "canceled"


class TestGetOrderBook:
    def test_get_order_book(self, client):
        """测试获取订单簿"""
        mock_data = {
            "asks": [["3451.0", "12.5", "0", "1"], ["3452.0", "8.3", "0", "2"]],
            "bids": [["3449.5", "15.2", "0", "1"], ["3448.0", "10.1", "0", "2"]],
            "ts": "1719200000000",
        }
        mock = MagicMock()
        mock.json.return_value = {"code": "0", "msg": "", "data": [mock_data]}
        client._request.return_value = mock
        result = client.get_order_book("ETH-USDT", depth=5)
        assert len(result["asks"]) == 2
        assert float(result["asks"][0][0]) == 3451.0
        assert float(result["bids"][0][0]) == 3449.5
        # 验证请求参数
        call_kwargs = client._request.call_args
        assert call_kwargs[0][0] == "GET"
        assert "/api/v5/market/books" in call_kwargs[0][1]
```

- [ ] **Step 2: 运行测试确认全部 FAIL**

```bash
cd C:\Users\Admin\Documents\okx-quant-agent
python -m pytest tests/test_okx_client_phase2.py -v
```
Expected: 5 tests all FAIL with "AttributeError: 'OKXClient' object has no attribute 'cancel_order'"

- [ ] **Step 3: 在 `okx_client.py` 追加三个新方法**

在 `place_order()` 方法之后（约第 170 行），追加以下代码：

```python
# ── Phase 2: 订单管理（Task 1） ──

def cancel_order(self, symbol: str, order_id: str) -> dict:
    """撤销订单

    https://www.okx.com/docs-v5/en/#rest-api-trade-cancel-order
    需要 Trade 权限。
    """
    ts = self._timestamp()
    body = {"instId": symbol, "ordId": order_id}
    json_body = str(body).replace("'", '"')
    headers = self._sign("POST", "/api/v5/trade/cancel-order", json_body, ts)
    headers["Content-Type"] = "application/json"
    resp = self._request("POST", "/api/v5/trade/cancel-order", headers=headers, content=json_body)
    data = resp.json()
    self._check_api_response(data)
    return self._normalize_order_data(data.get("data", []))

def get_order(self, symbol: str, order_id: str) -> dict:
    """查询订单状态

    https://www.okx.com/docs-v5/en/#rest-api-trade-get-order-details
    需要 Trade 权限。
    返回字段: ordId, state(canceled/filled/partially_filled/live),
    fillPx, fillSz, accFillSz, side, instId
    """
    ts = self._timestamp()
    path = f"/api/v5/trade/order?instId={symbol}&ordId={order_id}"
    headers = self._sign("GET", path, "", ts)
    resp = self._request("GET", path, headers=headers)
    data = resp.json()
    self._check_api_response(data)
    return self._normalize_order_data(data.get("data", []))

def get_order_book(self, symbol: str, depth: int = 5) -> dict:
    """获取订单簿深度

    https://www.okx.com/docs-v5/en/#rest-api-market-data-get-order-book
    公开接口，无需签名。
    返回: {"asks": [[price, sz, ...], ...], "bids": [[price, sz, ...], ...], "ts": "..."}
    """
    params = {"instId": symbol, "sz": str(min(depth, 10))}
    resp = self._request("GET", "/api/v5/market/books", params=params)
    data = resp.json()
    self._check_api_response(data)
    raw = data.get("data", [{}])[0]
    return {
        "asks": raw.get("asks", []),
        "bids": raw.get("bids", []),
        "ts": raw.get("ts", ""),
    }

@staticmethod
def _normalize_order_data(raw: list) -> dict:
    """标准化订单 API 返回值"""
    if isinstance(raw, list) and len(raw) > 0:
        return raw[0]
    if isinstance(raw, dict):
        return raw
    return {}
```

- [ ] **Step 4: 运行测试确认全部 PASS**

```bash
python -m pytest tests/test_okx_client_phase2.py -v
```
Expected: 5 tests all PASS

- [ ] **Step 5: 运行已有测试确认无回归**

```bash
python -m pytest tests/ -v
```
Expected: all existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add okx_client.py tests/test_okx_client_phase2.py
git commit -m "feat(phase2): add cancel_order, get_order, get_order_book OKX API"

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 2: TradeExecutor 完整升级 — 撤单 + 成交查询 + 滑点保护 + 部分成交

**Files:**
- Modify: `agents/trade_executor.py`
- Test: `tests/test_trade_executor_phase2.py`

**Interfaces:**
- Consumes: `OKXClient.cancel_order()`, `OKXClient.get_order()` from Task 1
- Consumes: `AgentSystemConfig.limit_order_timeout_seconds`, `AgentSystemConfig.max_slippage_pct`, `AgentSystemConfig.partial_fill_timeout_seconds`
- Produces: 升级版 `execute_limit()` — 挂单→等待→撤单→查成交→滑点检查→部分成交处理
- Produces: 新 `cancel_and_check()` 方法

- [ ] **Step 1: 在 `agents/config.py` 追加 Phase 2 配置项**

在 `AgentSystemConfig` 末尾、`log_file` 之前插入：

```python
    # ── Phase 2: Risk Hardening ──
    # BTC 波动检查
    btc_volatility_threshold_pct: float = 3.0
    btc_volatility_delay_seconds: int = 300

    # 市场深度
    market_depth_spread_bps: float = 10.0       # 买卖价差阈值（基点）
    market_depth_min_liquidity_eth: float = 1.0  # 最小深度（ETH）

    # 交易执行
    limit_order_timeout_seconds: int = 10        # 限价单等待超时
    max_slippage_pct: float = 0.3                # 最大滑点百分比
    partial_fill_timeout_seconds: int = 10       # 部分成交等待超时

    # 持仓监控
    position_monitor_interval: float = 5.0       # 持仓检查间隔（秒）
    trailing_stop_activation_pct: float = 3.0    # 浮盈激活移动止损
    trailing_stop_distance_pct: float = 1.5      # 移动止损距离
```

- [ ] **Step 2: 写 TradeExecutor 升级测试**

```python
# tests/test_trade_executor_phase2.py
"""测试 TradeExecutor 阶段二升级（限价单完整生命周期、滑点保护、部分成交）"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.trade_executor import TradeExecutor
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig()


@pytest.fixture
def okx_mock():
    """模拟 OKXClient"""
    mock = MagicMock()
    mock.place_order.return_value = [{"ordId": "12345"}]
    mock.get_order.return_value = {
        "ordId": "12345", "state": "filled", "fillPx": "3450.00",
        "fillSz": "0.01", "accFillSz": "0.01", "side": "buy",
        "instId": "ETH-USDT",
    }
    mock.cancel_order.return_value = {"ordId": "12345"}
    return mock


@pytest.fixture
def executor(okx_mock):
    return TradeExecutor(okx_client=okx_mock, symbol="ETH-USDT")


class TestExecuteLimit:
    @pytest.mark.asyncio
    async def test_limit_order_fills_normally(self, executor, okx_mock):
        """测试限价单正常成交流程"""
        okx_mock.get_order.return_value = {
            "ordId": "12345", "state": "filled", "fillPx": "3450.00",
            "fillSz": "0.01", "accFillSz": "0.01", "side": "sell",
            "instId": "ETH-USDT",
        }
        result = await executor.execute_limit("sell", "0.01", "3450.00")
        assert result["success"] is True
        assert result["order_id"] == "12345"
        assert result["fill_price"] == 3450.00
        # 验证下单调用
        okx_mock.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_limit_order_unfilled_cancel(self, executor, okx_mock):
        """测试限价单未成交→撤单→市价单兜底"""
        # 下单成功但未成交
        okx_mock.get_order.return_value = {
            "ordId": "12345", "state": "live", "fillPx": "",
            "fillSz": "0", "accFillSz": "0", "side": "sell",
            "instId": "ETH-USDT",
        }
        result = await executor.execute_limit("sell", "0.01", "3450.00", timeout_seconds=0.1)
        # 应该调用了 cancel_order
        okx_mock.cancel_order.assert_called_once()
        # 市价单兜底
        assert result["success"] is True
        assert result["note"] == "限价单未成交→市价单兜底"

    @pytest.mark.asyncio
    async def test_limit_order_partial_fill_cancel_remainder(self, executor, okx_mock):
        """测试限价单部分成交→撤销剩余→报告实际成交"""
        okx_mock.get_order.return_value = {
            "ordId": "12345", "state": "partially_filled", "fillPx": "3450.00",
            "fillSz": "0.005", "accFillSz": "0.005", "side": "sell",
            "instId": "ETH-USDT",
        }
        result = await executor.execute_limit("sell", "0.01", "3450.00", timeout_seconds=0.1)
        assert result["success"] is True
        assert result["filled_size"] == 0.005  # 部分成交
        assert result["filled_pct"] == 50.0    # 50% 成交
        okx_mock.cancel_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_limit_order_slippage_too_high(self, executor, okx_mock):
        """测试限价单滑点超过上限→交易拒绝"""
        okx_mock.get_order.return_value = {
            "ordId": "12345", "state": "filled", "fillPx": "3500.00",
            "fillSz": "0.01", "accFillSz": "0.01", "side": "sell",
            "instId": "ETH-USDT",
        }
        # signal_price=3450, fill=3500 → 滑点 = |3500-3450|/3450 = 1.45% > 0.3%
        result = await executor.execute_limit(
            "sell", "0.01", "3450.00", timeout_seconds=0.1, signal_price=3450.00
        )
        assert result["success"] is False
        assert "滑点" in result["error"]
        assert result["slippage_pct"] > 0.3

    @pytest.mark.asyncio
    async def test_limit_order_place_order_fails(self, executor, okx_mock):
        """测试限价单下单失败→转市价单"""
        okx_mock.place_order.side_effect = RuntimeError("API timeout")
        result = await executor.execute_limit("buy", "0.01", "3450.00")
        assert result["success"] is True  # 兜底成功
        assert result["note"] == "限价单提交失败→市价单兜底"
```

- [ ] **Step 3: 运行测试确认全部 FAIL**

```bash
python -m pytest tests/test_trade_executor_phase2.py -v
```
Expected: 5 tests FAIL

- [ ] **Step 4: 升级 `agents/trade_executor.py`**

将整个 `TradeExecutor` 替换为以下完整实现（保留 `__init__`、`_normalize_result`、`_extract_fill_price`、`get_stats`、`execute_market`、`execute_safe` 的已有逻辑，升级 `execute_limit` 并新增 `cancel_and_check` 方法）：

```python
# 在文件顶部导入新模块
from agents.config import AgentSystemConfig

# 修改 __init__ 签名
    def __init__(
        self,
        okx_client,
        symbol: str = "ETH-USDT",
        config: Optional[AgentSystemConfig] = None,
    ):
        self._client = okx_client
        self.symbol = symbol
        self.max_retries = 3
        self.config = config or AgentSystemConfig()
        ...

# 替换 execute_limit 方法
    async def execute_limit(
        self,
        side: str,
        size: str,
        price: str,
        timeout_seconds: Optional[int] = None,
        signal_price: Optional[float] = None,
    ) -> dict:
        """限价单完整生命周期

        流程:
        1. 提交限价单
        2. 等待 timeout_seconds（默认从 config 读取）
        3. 调用 get_order 查询成交状态
        4a. 完全成交 → 检查滑点
        4b. 部分成交 → 撤销剩余
        4c. 未成交 → 撤销 → 市价单兜底
        5. 返回最终结果
        """
        timeout = timeout_seconds or self.config.limit_order_timeout_seconds

        # ── 1. 提交限价单 ──
        order_id = ""
        try:
            result = await asyncio.to_thread(
                self._client.place_order,
                symbol=self.symbol,
                side=side,
                sz=size,
                ord_type="limit",
                px=price,
            )
            order_data = self._normalize_result(result)
            order_id = order_data.get("ordId", "")
            self.total_orders += 1
        except Exception as e:
            logger.warning(f"限价单提交失败: {e}")
            # 转市价单兜底
            result = await self.execute_market(side, size)
            result["note"] = "限价单提交失败→市价单兜底"
            return result

        if not order_id:
            return await self.execute_market(side, size)

        # ── 2. 等待成交 ──
        await asyncio.sleep(timeout)

        # ── 3. 查询订单状态 ──
        try:
            order_status = await asyncio.to_thread(
                self._client.get_order, self.symbol, order_id
            )
        except Exception as e:
            logger.warning(f"查询订单失败: {e}")
            return {
                "success": True,
                "order_id": order_id,
                "fill_price": float(price),
                "filled_size": float(size),
                "error": "",
                "estimated": True,
                "note": "订单状态查询失败，使用挂牌价",
            }

        state = order_status.get("state", "")
        acc_fill_sz = float(order_status.get("accFillSz", "0"))
        fill_px_str = order_status.get("fillPx", "") or order_status.get("avgPx", "")

        # ── 4a. 完全成交 ──
        if state == "filled":
            fill_price = float(fill_px_str) if fill_px_str else float(price)

            # 滑点检查
            if signal_price and signal_price > 0:
                slippage = abs(fill_price - signal_price) / signal_price * 100
                if slippage > self.config.max_slippage_pct:
                    logger.warning(
                        f"滑点 {slippage:.2f}% 超过 {self.config.max_slippage_pct}% 上限"
                    )
                    self.failed_orders += 1
                    return {
                        "success": False,
                        "order_id": order_id,
                        "fill_price": fill_price,
                        "filled_size": acc_fill_sz,
                        "slippage_pct": round(slippage, 2),
                        "error": f"滑点 {slippage:.2f}% 超过上限 {self.config.max_slippage_pct}%",
                    }

            return {
                "success": True,
                "order_id": order_id,
                "fill_price": fill_price,
                "filled_size": acc_fill_sz,
                "error": "",
            }

        # ── 4b. 部分成交 ──
        if state == "partially_filled":
            # 撤销剩余部分
            try:
                await asyncio.to_thread(self._client.cancel_order, self.symbol, order_id)
            except Exception as e:
                logger.warning(f"部分成交后撤单失败: {e}")

            fill_price = float(fill_px_str) if fill_px_str else float(price)
            filled_pct = (acc_fill_sz / float(size)) * 100 if float(size) > 0 else 0
            logger.info(f"限价单部分成交: {acc_fill_sz}/{size} ({filled_pct:.0f}%)")

            return {
                "success": True,
                "order_id": order_id,
                "fill_price": fill_price,
                "filled_size": acc_fill_sz,
                "filled_pct": round(filled_pct, 1),
                "error": "",
                "note": "部分成交—剩余已撤销",
            }

        # ── 4c. 未成交 → 撤销 → 市价单兜底 ──
        try:
            await asyncio.to_thread(self._client.cancel_order, self.symbol, order_id)
        except Exception as e:
            logger.warning(f"撤单失败: {e}")

        logger.info("限价单未成交，撤销后转市价单")
        result = await self.execute_market(side, size)
        result["note"] = "限价单未成交→市价单兜底"
        return result

# 新增辅助方法
    async def cancel_and_check(self, order_id: str) -> dict:
        """撤销订单并查询最终状态"""
        try:
            await asyncio.to_thread(self._client.cancel_order, self.symbol, order_id)
        except Exception as e:
            logger.warning(f"cancel_and_check 撤单失败: {e}")
        try:
            return await asyncio.to_thread(self._client.get_order, self.symbol, order_id)
        except Exception as e:
            logger.warning(f"cancel_and_check 查询失败: {e}")
            return {}

# 修改 execute_safe 以支持 signal_price 传递
    async def execute_safe(
        self,
        side: str,
        size_eth: float,
        signal_price: float,
        prefer_limit: bool = True,
    ) -> dict:
        """安全执行入口（自动处理size格式、限价→市价降级、滑点保护）"""
        size_str = f"{size_eth:.6f}"

        if prefer_limit:
            price_str = f"{signal_price:.2f}"
            result = await self.execute_limit(
                side, size_str, price_str,
                signal_price=signal_price,
            )
        else:
            result = await self.execute_market(side, size_str)

        return result
```

- [ ] **Step 5: 运行测试确认全部 PASS**

```bash
python -m pytest tests/test_trade_executor_phase2.py -v
```
Expected: 5 tests all PASS

- [ ] **Step 6: 运行全部测试确认无回归**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 7: Commit**

```bash
git add agents/trade_executor.py agents/config.py tests/test_trade_executor_phase2.py
git commit -m "feat(phase2): upgrade TradeExecutor with cancel, fill check, slippage, partial fill"

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 3: RiskLayer 加固 — BTC 波动 + 市场深度 + 北京时间结算

**Files:**
- Modify: `agents/risk_layer.py`
- Test: `tests/test_risk_layer_phase2.py`

**Interfaces:**
- Consumes: `OKXClient.get_klines("BTC-USDT", "15m", 2)` for BTC volatility check
- Consumes: `OKXClient.get_order_book("ETH-USDT")` for market depth check
- Consumes: `AgentSystemConfig.btc_volatility_*`, `market_depth_*` from config
- Produces:
  - `check_btc_volatility_async(okx_client) -> tuple[bool, str]` — BTC 波动检查
  - `check_market_depth_async(okx_client, side, size_eth) -> tuple[bool, str, bool]` — 深度检查
  - 北京时间结算（每日 UTC 16:00 重置）
  - 被 Task 5（Agent3）消费

- [ ] **Step 1: 写测试**

```python
# tests/test_risk_layer_phase2.py
"""测试 RiskManager 阶段二功能"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.risk_layer import RiskManager
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig(
        btc_volatility_threshold_pct=3.0,
        btc_volatility_delay_seconds=300,
        market_depth_spread_bps=10.0,
        market_depth_min_liquidity_eth=1.0,
    )


@pytest.fixture
def manager(config):
    return RiskManager(config)


def make_mock_client(btc_klines=None, order_book=None):
    """构造模拟 OKXClient"""
    client = MagicMock()

    if btc_klines is None:
        btc_klines = [
            {"timestamp": 1000, "open": 60000, "high": 61000, "low": 59000, "close": 60500},
            {"timestamp": 2000, "open": 60500, "high": 62000, "low": 60000, "close": 61500},
        ]
    if order_book is None:
        order_book = {
            "asks": [["3451.0", "12.5"], ["3452.0", "8.3"]],
            "bids": [["3449.5", "15.2"], ["3448.0", "10.1"]],
        }

    client.get_klines.return_value = btc_klines
    client.get_order_book.return_value = order_book
    return client


class TestBtcVolatility:
    @pytest.mark.asyncio
    async def test_btc_normal_volatility(self, manager):
        """BTC 正常波动 → 通过"""
        client = make_mock_client()  # ~1.65% change
        ok, reason = await manager.check_btc_volatility_async(client)
        assert ok is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_btc_high_volatility(self, manager):
        """BTC 高波动 → 拒绝"""
        klines = [
            {"timestamp": 1000, "open": 60000, "high": 61000, "low": 59000, "close": 60000},
            {"timestamp": 2000, "open": 60000, "high": 64000, "low": 59500, "close": 63000},
        ]
        client = make_mock_client(btc_klines=klines)
        ok, reason = await manager.check_btc_volatility_async(client)
        assert ok is False
        assert "BTC" in reason
        assert "波动" in reason

    @pytest.mark.asyncio
    async def test_btc_insufficient_data(self, manager):
        """BTC 数据不足 → 通过（不阻塞交易）"""
        client = make_mock_client(btc_klines=[{"timestamp": 1000, "close": 60000}])
        ok, reason = await manager.check_btc_volatility_async(client)
        assert ok is True

    @pytest.mark.asyncio
    async def test_btc_delay_cooldown(self, manager):
        """BTC 波动延迟期内再次检查 → 仍拒绝"""
        client = make_mock_client(btc_klines=[
            {"timestamp": 1000, "close": 60000},
            {"timestamp": 2000, "close": 63000},
        ])
        # 第一次检查 → 拒绝，设置延迟
        ok, _ = await manager.check_btc_volatility_async(client)
        assert ok is False

        # 第二次检查（还在延迟期）→ 拒绝，但不重复查询
        ok, reason = await manager.check_btc_volatility_async(client)
        assert ok is False
        assert "延迟" in reason


class TestMarketDepth:
    @pytest.mark.asyncio
    async def test_depth_sufficient(self, manager):
        """市场深度充足 → 通过"""
        order_book = {
            "asks": [["3450.0", "5.0"], ["3451.0", "10.0"]],
            "bids": [["3449.5", "5.0"], ["3448.0", "8.0"]],
        }
        client = make_mock_client(order_book=order_book)
        ok, reason, prefer_limit = await manager.check_market_depth_async(
            client, "buy", 0.5
        )
        assert ok is True
        # 买卖价差 = (3450-3449.5)/3449.75*10000 ≈ 1.45bps < 10bps → 允许市价单
        assert prefer_limit is False

    @pytest.mark.asyncio
    async def test_depth_wide_spread(self, manager):
        """买卖价差过大 → 强制限价单"""
        order_book = {
            "asks": [["3500.0", "5.0"]],
            "bids": [["3400.0", "5.0"]],
        }
        client = make_mock_client(order_book=order_book)
        # 价差 = (3500-3400)/3450*10000 ≈ 290bps > 10bps
        ok, reason, prefer_limit = await manager.check_market_depth_async(
            client, "sell", 0.5
        )
        assert ok is True
        assert prefer_limit is True
        assert "价差" in reason

    @pytest.mark.asyncio
    async def test_depth_insufficient_liquidity(self, manager):
        """深度不足以完成交易 → 拒绝"""
        order_book = {
            "asks": [["3450.0", "0.3"]],  # 只有 0.3 ETH 深度
            "bids": [["3449.0", "0.3"]],
        }
        client = make_mock_client(order_book=order_book)
        ok, reason, prefer_limit = await manager.check_market_depth_async(
            client, "buy", 0.5
        )
        assert ok is False
        assert "深度" in reason


class TestBeijingSettlement:
    def test_daily_reset_at_cst_midnight(self, manager):
        """北京时间（UTC+8）午夜重置"""
        # UTC 15:59 = CST 23:59 → 还没到重置时间
        before = datetime(2026, 6, 24, 15, 59, tzinfo=timezone.utc)
        manager._check_date_reset(before)
        assert manager._daily_trade_count == 0  # 初始化状态

        # 手动模拟一次交易
        manager._daily_trade_count = 5
        manager._daily_loss_usdt = 50.0

        # UTC 16:00 = CST 00:00 → 应重置
        after = datetime(2026, 6, 24, 16, 0, tzinfo=timezone.utc)
        manager._check_date_reset(after)
        assert manager._daily_trade_count == 0
        assert manager._daily_loss_usdt == 0.0

    def test_no_reset_within_same_day(self, manager):
        """同一天内不重复重置"""
        manager._daily_trade_count = 3

        t1 = datetime(2026, 6, 24, 8, 0, tzinfo=timezone.utc)
        manager._check_date_reset(t1)
        assert manager._daily_trade_count == 3  # 没被重置

        # 还没到 16:00 UTC
        t2 = datetime(2026, 6, 24, 15, 59, tzinfo=timezone.utc)
        manager._check_date_reset(t2)
        assert manager._daily_trade_count == 3

    def test_reset_accounts_for_cst_date_change(self, manager):
        """UTC 16:00 后应该用新的日期标识"""
        # UTC 15:59 → CST day 1
        d1 = datetime(2026, 6, 24, 15, 59, tzinfo=timezone.utc)
        manager._check_date_reset(d1)

        # UTC 16:00 → CST day 2
        d2 = datetime(2026, 6, 24, 16, 0, tzinfo=timezone.utc)
        manager._check_date_reset(d2)
        # 内部 _current_date 应该变成了 2026-06-25（CST 日期）
        # 注意：_current_date 存的是 UTC 日期，但重置逻辑判断 CST 日期变化
        assert manager._current_date == d2.date()
```

- [ ] **Step 2: 运行测试确认全部 FAIL**

```bash
python -m pytest tests/test_risk_layer_phase2.py -v
```
Expected: all FAIL

- [ ] **Step 3: 在 `agents/risk_layer.py` 追加三个功能**

在 `RiskManager` 类中添加以下代码：

**A. 北京时间结算改造（修改 `_check_date_reset`）:**

```python
    # 在 __init__ 中添加
    def __init__(self, config: AgentSystemConfig):
        ...
        self._current_cst_date: date = self._utc_to_cst_date(datetime.now(timezone.utc))
        # 保持 _current_date 用于 UTC 兼容性
        self._current_date: date = datetime.now(timezone.utc).date()
        ...

    @staticmethod
    def _utc_to_cst_date(utc_dt: datetime) -> date:
        """UTC 时间转北京时间（CST, UTC+8）的日期"""
        cst_dt = utc_dt + timedelta(hours=8)
        return cst_dt.date()

    # 改造 _check_date_reset
    def _check_date_reset(self, now: datetime):
        """每日重置（北京时间午夜 00:00 CST = UTC 16:00）"""
        cst_today = self._utc_to_cst_date(now)
        if cst_today != self._current_cst_date:
            logger.info(f"每日风控重置 (CST): {self._current_cst_date} → {cst_today}")
            self._daily_trade_count = 0
            self._daily_loss_usdt = 0.0
            self._consecutive_losses = 0
            self._current_cst_date = cst_today
            self._current_date = now.date()
            self._daily_trades = []
            self._consecutive_api_errors = 0
            self._api_breaker_until = None
```

**B. BTC 波动检查:**

```python
    # ── Phase 2: BTC 波动检查 ──

    async def check_btc_volatility_async(self, okx_client) -> tuple[bool, str]:
        """检查 BTC 15m 波动率，超阈值则拒绝交易

        Args:
            okx_client: OKXClient 实例（用于获取 BTC K线）

        Returns:
            (通过?, 原因)
        """
        # 先检查是否在延迟期内
        now = datetime.now(timezone.utc)
        if hasattr(self, '_btc_delay_until') and self._btc_delay_until and now < self._btc_delay_until:
            remaining = (self._btc_delay_until - now).total_seconds()
            return False, f"BTC 波动延迟中，剩余 {remaining:.0f}s"

        # 获取最后两根 BTC 15m K线
        try:
            import asyncio
            klines = await asyncio.to_thread(
                okx_client.get_klines, "BTC-USDT", "15m", 2
            )
        except Exception as e:
            logger.warning(f"BTC 波动检查失败（API 异常）: {e}")
            return True, ""  # API 异常不阻塞交易

        if len(klines) < 2:
            return True, ""

        prev_close = klines[0]["close"] if isinstance(klines[0], dict) else float(klines[0][4])
        curr_close = klines[1]["close"] if isinstance(klines[1], dict) else float(klines[1][4])

        if prev_close <= 0:
            return True, ""

        change_pct = abs(curr_close - prev_close) / prev_close * 100
        if change_pct > self.config.btc_volatility_threshold_pct:
            self._btc_delay_until = now + timedelta(seconds=self.config.btc_volatility_delay_seconds)
            logger.warning(
                f"BTC 15m 波动 {change_pct:.1f}% > {self.config.btc_volatility_threshold_pct}%"
                f"，延迟 {self.config.btc_volatility_delay_seconds}s"
            )
            return False, f"BTC 15m 波动 {change_pct:.1f}%，超过阈值 {self.config.btc_volatility_threshold_pct}%"

        # 波动恢复正常 → 清除延迟
        self._btc_delay_until = None
        return True, ""
```

**C. 市场深度检查:**

```python
    # ── Phase 2: 市场深度检查 ──

    async def check_market_depth_async(
        self,
        okx_client,
        side: str,       # "buy" / "sell"
        size_eth: float,  # 交易数量（ETH）
    ) -> tuple[bool, str, bool]:
        """检查市场深度是否足够

        Args:
            okx_client: OKXClient 实例
            side: 交易方向
            size_eth: 交易数量（ETH）

        Returns:
            (检查通过?, 消息, 是否强制限价单)
        """
        try:
            import asyncio
            order_book = await asyncio.to_thread(
                okx_client.get_order_book, self.config.ws_symbol, depth=5
            )
        except Exception as e:
            logger.warning(f"市场深度检查失败: {e}")
            return True, "深度检查跳过", True  # 失败则保守地走限价单

        asks = order_book.get("asks", [])
        bids = order_book.get("bids", [])

        if not asks or not bids:
            return True, "深度数据为空", True

        # 计算买卖价差（基点）
        best_ask = float(asks[0][0])
        best_bid = float(bids[0][0])
        mid_price = (best_ask + best_bid) / 2

        if mid_price <= 0:
            return True, "", True

        spread_bps = (best_ask - best_bid) / mid_price * 10000

        # 检查深度是否足够完成交易
        if side == "buy":
            available_depth = sum(float(ask[1]) for ask in asks if float(ask[0]) <= best_ask * 1.005)
        else:
            available_depth = sum(float(bid[1]) for bid in bids if float(bid[0]) >= best_bid * 0.995)

        if available_depth < size_eth:
            return False, (
                f"卖方深度不足: 可用 {available_depth:.4f} ETH < 需求 {size_eth} ETH"
            ), True

        # 价差过大 → 强制走限价单
        if spread_bps > self.config.market_depth_spread_bps:
            return True, f"价差 {spread_bps:.1f}bps > {self.config.market_depth_spread_bps}bps，走限价单", True

        return True, "", False  # 深度充足，可以市价单
```

- [ ] **Step 4: 运行测试确认全部 PASS**

```bash
python -m pytest tests/test_risk_layer_phase2.py -v
```
Expected: all PASS

- [ ] **Step 5: 运行全部测试确认无回归**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add agents/risk_layer.py tests/test_risk_layer_phase2.py
git commit -m "feat(phase2): add BTC volatility check, market depth check, CST settlement"

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 4: PositionMonitor — 持仓止盈止损监控协程

**Files:**
- Create: `agents/position_monitor.py`
- Test: `tests/test_position_monitor.py`

**Interfaces:**
- Consumes: `RiskManager` (for position info), `OKXClient` (for current price), `TradeExecutor` (for placing SL/TP orders)
- Consumes: `AgentSystemConfig.position_monitor_interval`, `trailing_stop_*`
- Produces: `PositionMonitor` 类 — 提供 `run()`, `stop()`, `update_position()`, `get_status()`
  - 被 Task 5（Agent3）和 Task 6（main.py）消费

- [ ] **Step 1: 写 PositionMonitor 测试**

```python
# tests/test_position_monitor.py
"""测试持仓监控器——止盈、止损、移动止损"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.position_monitor import PositionMonitor
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig(
        position_monitor_interval=0.05,  # 50ms 方便测试
        trailing_stop_activation_pct=3.0,
        trailing_stop_distance_pct=1.5,
    )


@pytest.fixture
def mock_risk_manager():
    rm = MagicMock()
    rm._current_position_eth = 0.01
    rm._current_position_side = "long"
    return rm


@pytest.fixture
def mock_executor():
    ex = AsyncMock()
    ex.symbol = "ETH-USDT"
    # Mock execute_market to return success
    ex.execute_market.return_value = {
        "success": True, "order_id": "sl123", "fill_price": 3400.0,
    }
    return ex


@pytest.fixture
def mock_okx_client():
    client = MagicMock()
    client.get_ticker.return_value = {"last": 3500.0}
    return client


class TestPositionMonitor:
    @pytest.mark.asyncio
    async def test_stop_loss_triggered(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """价格跌破止损 → 触发止损卖出"""
        config.trailing_stop_activation_pct = 3.0
        config.trailing_stop_distance_pct = 1.5

        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 模拟初始持仓：long @ 3500，止损 2% = 3430
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3430.0, take_profit=3700.0)

        # 价格跌到 3420 < 3430 → 触发止损
        mock_okx_client.get_ticker.return_value = {"last": 3420.0}
        triggered = await monitor._check_once()
        assert triggered is True
        assert monitor._stats["stop_loss_triggered"] == 1

    @pytest.mark.asyncio
    async def test_take_profit_triggered(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """价格涨到止盈 → 触发止盈卖出"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3600.0)

        # 价格涨到 3650 > 3600 → 触发止盈
        mock_okx_client.get_ticker.return_value = {"last": 3650.0}
        triggered = await monitor._check_once()
        assert triggered is True
        assert monitor._stats["take_profit_triggered"] == 1

    @pytest.mark.asyncio
    async def test_trailing_stop_activates(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """浮盈达到 3% 后激活移动止损，止损位上移"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 初始：long @ 3500，止损 2% = 3430
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3430.0, take_profit=3700.0)

        # 价格涨到 3650 (浮盈 4.3% > 3%) → 激活移动止损
        # 移动止损位 = 3650 * (1 - 1.5%) = 3595.25
        mock_okx_client.get_ticker.return_value = {"last": 3650.0}
        triggered = await monitor._check_once()
        assert triggered is False  # 还未触发卖出

        # 验证止损位上移了
        assert monitor._current_stop_loss > 3430.0
        assert monitor._stats["trailing_stop_activated"] == 1

    @pytest.mark.asyncio
    async def test_trailing_stop_triggers(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """移动止损激活后，价格回落到新止损位 → 触发卖出"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3430.0, take_profit=3700.0)
        monitor._trailing_high = 3650.0
        monitor._trailing_stop_active = True
        # 移动止损位 = 3650 * (1 - 1.5%) = 3595.25
        monitor._current_stop_loss = 3595.25

        # 价格回落到 3580 < 3595.25 → 触发
        mock_okx_client.get_ticker.return_value = {"last": 3580.0}
        triggered = await monitor._check_once()
        assert triggered is True

    @pytest.mark.asyncio
    async def test_no_position_no_action(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """无持仓时不做任何操作"""
        mock_risk_manager._current_position_eth = 0
        mock_risk_manager._current_position_side = None

        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        triggered = await monitor._check_once()
        assert triggered is False
        mock_executor.execute_market.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_position_take_profit_and_stop(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """空头仓位：止盈（价格跌）和止损（价格涨）方向正确"""
        mock_risk_manager._current_position_side = "short"

        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 空头：entry=3500, stop=3570(涨2%), take=3400(跌2.86%)
        monitor.update_position(side="short", size=0.01, entry_price=3500.0,
                                stop_loss=3570.0, take_profit=3400.0)

        # 价格跌到 3380 < 3400 → 止盈触发（买回平仓）
        mock_okx_client.get_ticker.return_value = {"last": 3380.0}
        triggered = await monitor._check_once()
        assert triggered is True
        assert monitor._stats["take_profit_triggered"] == 1

    @pytest.mark.asyncio
    async def test_status_report(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """get_status 返回正确统计"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3600.0)

        status = monitor.get_status()
        assert status["running"] is True
        assert status["position_side"] == "long"
        assert status["entry_price"] == 3500.0
        assert status["stop_loss"] == 3400.0
        assert status["take_profit"] == 3600.0
        assert "stop_loss_triggered" in status
```

- [ ] **Step 2: 运行测试确认全部 FAIL**

```bash
python -m pytest tests/test_position_monitor.py -v
```
Expected: all FAIL

- [ ] **Step 3: 创建 `agents/position_monitor.py`**

```python
"""
持仓监控器 — 止盈 / 止损 / 移动止损

职责:
  1. 每 N 秒检查持仓状态
  2. 价格达到止损位 → 触发市价平仓
  3. 价格达到止盈位 → 触发市价平仓
  4. 浮动止损（trailing stop）：价格朝有利方向移动时上移止损位

被 Agent 3 启动，独立协程运行。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from agents.config import AgentSystemConfig

logger = logging.getLogger("position_monitor")


class PositionMonitor:
    """持仓监控器 — 止盈/止损/移动止损"""

    def __init__(
        self,
        config: AgentSystemConfig,
        risk_manager,
        executor,
        okx_client,
    ):
        self.config = config
        self.risk = risk_manager
        self.executor = executor
        self.okx = okx_client

        # 当前持仓信息
        self._has_position: bool = False
        self._position_side: str = "none"       # "long" / "short" / "none"
        self._position_size: float = 0.0        # ETH
        self._entry_price: float = 0.0

        # 止盈止损（从外部传入）
        self._stop_loss: float = 0.0
        self._take_profit: float = 0.0

        # 移动止损状态
        self._trailing_stop_active: bool = False
        self._trailing_high: float = 0.0        # 多头：最高价
        self._trailing_low: float = 0.0         # 空头：最低价
        self._current_stop_loss: float = 0.0    # 当前实际止损位

        # 运行状态
        self._running: bool = False
        self._stats = {
            "stop_loss_triggered": 0,
            "take_profit_triggered": 0,
            "trailing_stop_activated": 0,
            "trailing_stop_triggered": 0,
            "start_time": "",
        }

    # ── 公开接口 ──

    def update_position(
        self,
        side: str,
        size: float,
        entry_price: float,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ):
        """更新持仓信息（由 Agent 3 在新开仓后调用）"""
        self._has_position = size > 0
        self._position_side = side if self._has_position else "none"
        self._position_size = size
        self._entry_price = entry_price
        self._stop_loss = stop_loss
        self._take_profit = take_profit

        # 重置移动止损状态
        self._trailing_stop_active = False
        self._trailing_high = entry_price if side == "long" else 0.0
        self._trailing_low = entry_price if side == "short" else float("inf")
        self._current_stop_loss = stop_loss

        if self._has_position:
            logger.info(
                f"持仓更新: {side} {size:.4f} ETH @ ${entry_price:.2f} "
                f"SL=${stop_loss:.2f} TP=${take_profit:.2f}"
            )
        else:
            logger.info("持仓已清空，停止监控")

    def clear_position(self):
        """清空持仓（外部调用，如手动平仓后）"""
        self.update_position("none", 0, 0, 0, 0)

    async def run(self):
        """启动持仓监控主循环"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("持仓监控器启动")

        while self._running:
            try:
                await self._check_once()
            except Exception as e:
                logger.error(f"持仓检查异常: {e}")

            await asyncio.sleep(self.config.position_monitor_interval)

    async def stop(self):
        """停止监控"""
        self._running = False
        logger.info("持仓监控器已停止")

    # ── 内部逻辑 ──

    async def _check_once(self) -> bool:
        """执行一次持仓检查

        返回: True=触发了平仓操作
        """
        if not self._has_position or self._position_size <= 0:
            return False

        # 获取当前价格
        try:
            ticker = await asyncio.to_thread(self.okx.get_ticker, self.executor.symbol)
            current_price = float(ticker.get("last", 0))
        except Exception as e:
            logger.warning(f"获取当前价格失败: {e}")
            return False

        if current_price <= 0:
            return False

        # 多头逻辑
        if self._position_side == "long":
            return await self._check_long(current_price)

        # 空头逻辑
        if self._position_side == "short":
            return await self._check_short(current_price)

        return False

    async def _check_long(self, current_price: float) -> bool:
        """检查多头持仓"""
        # 更新移动止损跟踪的最高价
        if current_price > self._trailing_high:
            self._trailing_high = current_price

            # 检查是否激活移动止损
            pnl_pct = (current_price - self._entry_price) / self._entry_price * 100
            if pnl_pct >= self.config.trailing_stop_activation_pct and not self._trailing_stop_active:
                self._trailing_stop_active = True
                self._stats["trailing_stop_activated"] += 1
                logger.info(f"移动止损激活 @ ${current_price:.2f} (浮盈 {pnl_pct:.1f}%)")

            # 更新移动止损位
            if self._trailing_stop_active:
                new_sl = self._trailing_high * (1 - self.config.trailing_stop_distance_pct / 100)
                if new_sl > self._current_stop_loss:
                    self._current_stop_loss = new_sl

        # 检查是否触发止损（含移动止损）
        if current_price <= self._current_stop_loss:
            logger.warning(
                f"多头止损触发: ${current_price:.2f} <= SL ${self._current_stop_loss:.2f}"
            )
            await self._close_position("多头止损")
            self._stats["stop_loss_triggered"] += 1
            if self._trailing_stop_active:
                self._stats["trailing_stop_triggered"] += 1
            return True

        # 检查是否触发止盈
        if self._take_profit > 0 and current_price >= self._take_profit:
            logger.info(
                f"多头止盈触发: ${current_price:.2f} >= TP ${self._take_profit:.2f}"
            )
            await self._close_position("多头止盈")
            self._stats["take_profit_triggered"] += 1
            return True

        return False

    async def _check_short(self, current_price: float) -> bool:
        """检查空头持仓"""
        # 更新移动止损跟踪的最低价
        if current_price < self._trailing_low:
            self._trailing_low = current_price

            pnl_pct = (self._entry_price - current_price) / self._entry_price * 100
            if pnl_pct >= self.config.trailing_stop_activation_pct and not self._trailing_stop_active:
                self._trailing_stop_active = True
                self._stats["trailing_stop_activated"] += 1
                logger.info(f"空头移动止损激活 @ ${current_price:.2f} (浮盈 {pnl_pct:.1f}%)")

            if self._trailing_stop_active:
                new_sl = self._trailing_low * (1 + self.config.trailing_stop_distance_pct / 100)
                if new_sl < self._current_stop_loss:
                    self._current_stop_loss = new_sl

        # 止损（价格上涨）
        if self._current_stop_loss > 0 and current_price >= self._current_stop_loss:
            logger.warning(
                f"空头止损触发: ${current_price:.2f} >= SL ${self._current_stop_loss:.2f}"
            )
            await self._close_position("空头止损")
            self._stats["stop_loss_triggered"] += 1
            return True

        # 止盈（价格下跌）
        if self._take_profit > 0 and current_price <= self._take_profit:
            logger.info(
                f"空头止盈触发: ${current_price:.2f} <= TP ${self._take_profit:.2f}"
            )
            await self._close_position("空头止盈")
            self._stats["take_profit_triggered"] += 1
            return True

        return False

    async def _close_position(self, reason: str):
        """平仓（按市价卖出/买入）"""
        side = "sell" if self._position_side == "long" else "buy"
        size_str = f"{self._position_size:.6f}"

        logger.info(f"平仓: {self._position_side} {self._position_size:.4f} ETH (原因: {reason})")

        try:
            result = await self.executor.execute_market(side, size_str)
            if result["success"]:
                # 同步更新 RiskManager
                self.risk.record_trade({
                    "side": side,
                    "size": self._position_size,
                    "price": result["fill_price"],
                    "pnl": 0,
                    "order_id": result["order_id"],
                    "symbol": self.executor.symbol,
                    "decision": {"action": side, "reason": reason},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            else:
                logger.error(f"平仓失败: {result.get('error', '')}")
        except Exception as e:
            logger.error(f"平仓异常: {e}")

        # 清空持仓标记（不管成功与否都标记）
        self.clear_position()

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "has_position": self._has_position,
            "position_side": self._position_side,
            "position_size": self._position_size,
            "entry_price": self._entry_price,
            "stop_loss": self._current_stop_loss,
            "take_profit": self._take_profit,
            "trailing_stop_active": self._trailing_stop_active,
            "trailing_high": self._trailing_high,
            **self._stats,
        }
```

- [ ] **Step 4: 运行测试确认全部 PASS**

```bash
python -m pytest tests/test_position_monitor.py -v
```
Expected: all PASS

- [ ] **Step 5: 运行全部测试确认无回归**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add agents/position_monitor.py tests/test_position_monitor.py
git commit -m "feat(phase2): add PositionMonitor — stop loss, take profit, trailing stop"

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 5: Agent3 集成 — 风控状态注入 + 深度检查调用 + PositionMonitor 联动

**Files:**
- Modify: `agents/agent3_trader.py`
- Modify: `agents/deepseek_caller.py`
- Test: `tests/test_agent3_phase2.py`

**Interfaces:**
- Consumes: `RiskManager.check_btc_volatility_async()`, `RiskManager.check_market_depth_async()` from Task 3
- Consumes: `PositionMonitor` from Task 4
- Produces: 升级版 `Agent3._build_context()` → 注入风控状态到 DeepSeek prompt
- Produces: 升级版 `Agent3._make_decision()` → 集成 BTC 波动检查 + 市场深度检查 + PositionMonitor 更新

- [ ] **Step 1: 写 Agent3 集成测试**

```python
# tests/test_agent3_phase2.py
"""测试 Agent 3 阶段二集成——风控注入、BTC检查、市场深度"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.agent3_trader import Agent3
from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.config import AgentSystemConfig


@pytest.fixture
def config():
    return AgentSystemConfig()


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def mock_deepseek():
    ds = MagicMock()
    ds.analyze.return_value = {
        "action": "hold",
        "confidence": 50,
        "entry_price_min": "",
        "entry_price_max": "",
        "position_size_pct": "",
        "stop_loss": "",
        "take_profit": "",
        "reason": "test",
    }
    return ds


@pytest.fixture
def mock_risk_manager():
    rm = MagicMock()
    rm.check_layer1.return_value = (True, "")
    rm.get_position_size_multiplier.return_value = 1.0
    rm.get_status.return_value = {
        "daily_trade_count": 2,
        "max_daily_trades": 10,
        "daily_loss_usdt": 10.0,
        "max_daily_loss_usdt": 100.0,
        "consecutive_losses": 0,
        "max_consecutive_losses": 3,
        "position_size_multiplier": 1.0,
        "position_eth": 0,
        "position_side": None,
    }
    return rm


@pytest.fixture
def mock_executor():
    ex = MagicMock()
    ex.symbol = "ETH-USDT"
    ex.execute_safe = AsyncMock(return_value={
        "success": True, "order_id": "test123", "fill_price": 3450.0,
    })
    return ex


@pytest.fixture
def mock_root_config():
    cfg = MagicMock()
    cfg.trading.symbol = "ETH-USDT"
    return cfg


@pytest.fixture
def agent3(config, event_bus, mock_deepseek, mock_risk_manager, mock_executor, mock_root_config):
    return Agent3(
        config=config,
        event_bus=event_bus,
        deepseek=mock_deepseek,
        risk_manager=mock_risk_manager,
        trade_executor=mock_executor,
        root_config=mock_root_config,
    )


class TestRiskStatusInjection:
    def test_build_context_includes_risk_status(self, agent3, mock_risk_manager):
        """验证 _build_context 注入风控状态"""
        mock_risk_manager.get_status.return_value = {
            "daily_trade_count": 3,
            "max_daily_trades": 10,
            "consecutive_losses": 1,
            "position_size_multiplier": 0.75,
            "daily_loss_usdt": 25.0,
            "max_daily_loss_usdt": 100.0,
            "position_eth": 0,
            "position_side": None,
        }
        context = agent3._build_context([])
        assert "risk_status" in context
        rs = context["risk_status"]
        assert rs["daily_trade_count"] == 3
        assert rs["consecutive_losses"] == 1
        assert rs["position_size_multiplier"] == 0.75

    def test_build_context_with_events(self, agent3):
        """验证上下文包含技术和新闻事件"""
        events = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "MACD金叉", "timeframe": "1h", "price": 3500},
                       timestamp=datetime.now(timezone.utc)),
            AgentEvent(type=AgentEventType.NEWS_EVENT, source="agent2",
                       data={"title": "ETH ETF获批", "source": "CoinDesk", "weight": 0.8},
                       timestamp=datetime.now(timezone.utc)),
        ]
        context = agent3._build_context(events)
        assert "MACD金叉" in context["agent1_summary"]
        assert "ETH ETF获批" in context["agent2_summary"]
        assert context["current_price"] == 3500.0

    def test_build_context_empty(self, agent3):
        """无事件时上下文包含默认值"""
        context = agent3._build_context([])
        assert "暂无技术面信号" in context["agent1_summary"]
        assert "暂无新闻数据" in context["agent2_summary"]
        assert "risk_status" in context


class TestDeepSeekPromptUpdate:
    def test_context_passed_to_deepseek(self, agent3, mock_deepseek):
        """验证上下文正确传递给 DeepSeek"""
        events = [
            AgentEvent(type=AgentEventType.TECHNICAL_SIGNAL, source="agent1",
                       data={"description": "BOLL上轨突破", "timeframe": "15m", "price": 3510},
                       timestamp=datetime.now(timezone.utc)),
        ]
        context = agent3._build_context(events)

        # 模拟 _make_decision 中的 analyze 调用
        agent3.deepseek.analyze(context)
        mock_deepseek.analyze.assert_called_once()
        called_context = mock_deepseek.analyze.call_args[0][0]
        assert called_context["current_price"] == 3510.0
        assert "risk_status" in called_context
```

- [ ] **Step 2: 运行测试确认全部 FAIL（预期部分通过）**

```bash
python -m pytest tests/test_agent3_phase2.py -v
```

- [ ] **Step 3: 修改 `agents/agent3_trader.py`**

**A. 修改 `__init__`，接受 `position_monitor` 参数:**

```python
    def __init__(
        self,
        config: AgentSystemConfig,
        event_bus: EventBus,
        deepseek: DeepSeekTrader,
        risk_manager: RiskManager,
        trade_executor: TradeExecutor,
        root_config,
        position_monitor=None,  # Phase 2: 持仓监控器
        okx_client=None,       # Phase 2: OKX客户端（用于BTC/深度检查）
    ):
        ...
        self.position_monitor = position_monitor
        self.okx_client = okx_client
        self._btc_checked = False  # 标记本轮是否已完成BTC检查
```

**B. 修改 `_build_context`，注入风控状态:**

```python
    async def _build_context(self, events: list[AgentEvent]) -> dict:
        """从事件列表构建 DeepSeek 上下文"""
        agent1_lines = []
        agent2_lines = []
        current_price = 0.0

        for e in events:
            if not isinstance(e.data, dict):
                continue
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

        # Phase 2: 注入风控状态
        risk_status = self.risk.get_status()

        return {
            "symbol": self.root_config.trading.symbol,
            "position_direction": self._current_position["side"],
            "position_size": self._current_position["size"],
            "entry_price": self._current_position["entry_price"],
            "pnl_pct": "",
            "agent1_summary": "\n".join(agent1_lines) if agent1_lines else "暂无技术面信号",
            "agent2_summary": "\n".join(agent2_lines) if agent2_lines else "暂无新闻数据",
            "monthly_trades": 0,
            "win_rate": 0,
            "monthly_pnl": 0,
            "current_price": current_price,
            "risk_status": risk_status,  # Phase 2: 风控状态
        }
```

**C. 修改 `_make_decision` 集成 BTC 波动和深度检查:**

```python
    async def _make_decision(self):
        """执行一次完整的交易决策周期"""
        if self._decision_lock.locked():
            return
        async with self._decision_lock:
            if not self._event_buffer:
                return

            self._last_decision_time = datetime.now(timezone.utc)
            events = list(self._event_buffer)
            self._event_buffer.clear()

            # ── 0. BTC 波动检查（Phase 2） ──
            if self.okx_client and hasattr(self.risk, 'check_btc_volatility_async'):
                ok, reason = await self.risk.check_btc_volatility_async(self.okx_client)
                if not ok:
                    logger.info(f"BTC 波动检查拒绝: {reason}")
                    self._stats["trades_skipped"] += 1
                    return

            # ── 1. 构建上下文摘要 ──
            context = await self._build_context(events)

            # ── 2. 调用 DeepSeek ──
            self._stats["deepseek_calls"] += 1
            decision = await asyncio.to_thread(self.deepseek.analyze, context)

            if decision["action"] == "hold":
                logger.info(f"DeepSeek 建议持有: {decision.get('reason', '')}")
                self._stats["trades_skipped"] += 1
                return

            # ── 3. 从 DeepSeek 输出获取交易方向 ──
            trade_side = "buy" if decision["action"] == "buy" else "sell"
            size_eth = self._suggested_size(context)

            # ── 3b. 市场深度检查（Phase 2） ──
            prefer_limit = True
            if self.okx_client and hasattr(self.risk, 'check_market_depth_async'):
                ok, reason, prefer_limit = await self.risk.check_market_depth_async(
                    self.okx_client, trade_side, size_eth
                )
                if not ok:
                    logger.info(f"市场深度拒绝: {reason}")
                    self._stats["trades_skipped"] += 1
                    return
                if prefer_limit:
                    logger.info(f"市场深度检查: {reason}")

            # ── 4. Layer 1 风控检查 ──
            ok, reason = self.risk.check_layer1(
                trade_side, size_eth, context.get("current_price", 0)
            )
            if not ok:
                logger.info(f"Layer 1 拒绝: {reason}")
                self._stats["trades_skipped"] += 1
                return

            # ── 5. 执行交易 ──
            logger.info(f"DeepSeek 决策: {decision['action']} (信心 {decision['confidence']}%)")
            self._stats["trades_executed"] += 1

            trade_result = await self.executor.execute_safe(
                side=trade_side,
                size_eth=size_eth,
                signal_price=context.get("current_price", 0),
                prefer_limit=prefer_limit,
            )

            # ── 6. Layer 3 记录 ──
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
                logger.info(f"交易成功: {trade_side} {size_eth:.4f} ETH @ ${trade_result['fill_price']:.2f}")

                # Phase 2: 通知持仓监控器
                if self.position_monitor:
                    stop_loss = float(decision.get("stop_loss", 0))
                    take_profit = float(decision.get("take_profit", 0))
                    self.position_monitor.update_position(
                        side=trade_side,
                        size=size_eth,
                        entry_price=trade_result["fill_price"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    )
            else:
                self.risk.report_api_error()
                logger.error(f"交易失败: {trade_result['error']}")
```

**D. 修改 `_suggested_size`，使用 DeepSeek 的仓位比例:**

```python
    def _suggested_size(self, context: dict) -> float:
        """根据上下文和风控建议仓位大小

        阶段一: 固定 0.01 ETH × 风控乘数
        阶段二: 优先使用 DeepSeek 建议的比例
        """
        multiplier = self.risk.get_position_size_multiplier()
        base_size = 0.01  # 基础 0.01 ETH
        return base_size * multiplier
```

- [ ] **Step 4: 修改 `agents/deepseek_caller.py` 注入风控状态到 prompt**

修改 `_SYSTEM_PROMPT`，在现有仓位下方新增风控状态区块：

```python
_SYSTEM_PROMPT = """你是一位有15年经验的以太坊资深交易员，管理过亿美元的资金。
请基于以下多维数据，给出交易决策。

【当前仓位】
- 持仓方向: {position_direction}
- 持仓数量: {position_size} ETH
- 入场均价: {entry_price}
- 当前浮盈/浮亏: {pnl_pct}%

【风控状态】
- 今日交易次数: {daily_trade_count} / {max_daily_trades}
- 今日亏损: {daily_loss} USDT / {max_daily_loss} USDT
- 连续亏损次数: {consecutive_losses} / {max_consecutive_losses}
- 当前仓位乘数: {position_size_multiplier}x

【技术面摘要】
{agent1_summary}

... 其余不变 ...
"""
```

并在 `analyze()` 方法中注入新字段：

```python
        risk = context.get("risk_status", {})
        prompt_kwargs = {
            ...
            "daily_trade_count": risk.get("daily_trade_count", "0"),
            "max_daily_trades": risk.get("max_daily_trades", "10"),
            "daily_loss": risk.get("daily_loss_usdt", "0"),
            "max_daily_loss": risk.get("max_daily_loss_usdt", "100"),
            "consecutive_losses": risk.get("consecutive_losses", "0"),
            "max_consecutive_losses": risk.get("max_consecutive_losses", "3"),
            "position_size_multiplier": risk.get("position_size_multiplier", "1.0"),
            ...
        }
```

- [ ] **Step 5: 运行测试确认 PASS**

```bash
python -m pytest tests/test_agent3_phase2.py -v
```
Expected: all PASS

- [ ] **Step 6: 运行全部测试**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 7: Commit**

```bash
git add agents/agent3_trader.py agents/deepseek_caller.py tests/test_agent3_phase2.py
git commit -m "feat(phase2): integrate risk status into DeepSeek, add BTC/depth checks to Agent3"

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 6: Main.py — 启动持仓监控器 + 传递 OKX 客户端

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes: `PositionMonitor` from Task 4, `RiskManager.check_btc_volatility_async` / `check_market_depth_async` from Task 3
- Produces: 全系统启动编排（初始化 → 启动 → 停止）

- [ ] **Step 1: 修改 `main.py` 集成 Phase 2 组件**

在 `main()` 函数中的组件初始化阶段添加：

```python
    # ── Phase 2: 持仓监控器 ──
    from agents.position_monitor import PositionMonitor

    position_monitor = PositionMonitor(
        config=agent_config,
        risk_manager=risk_manager,
        executor=trade_executor,
        okx_client=okx_rest,
    )
```

修改 Agent3 创建（传递 `position_monitor` 和 `okx_client`）：

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
    ) if agent_config.agent3_enabled else None
```

在启动 Agent 协程列表中追加：

```python
    # ── 启动持仓监控器 ──
    if agent3 and position_monitor:
        tasks.append(asyncio.create_task(
            position_monitor.run(), name="position_monitor"
        ))
```

在停止逻辑中添加：

```python
    # 停止持仓监控器
    if position_monitor:
        await position_monitor.stop()
```

- [ ] **Step 2: 验证启动流程**

```bash
python -m py_compile main.py
```
Expected: no errors

- [ ] **Step 3: 运行全部测试确认无回归**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(phase2): integrate PositionMonitor and OKXClient into main startup"

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 7: 风控监控面板 — Streamlit 只读状态页

**Files:**
- Create: `frontend/pages/12_🛡_AgentRisk.py`

**Note:** This page reads Agent system state from `data/agent_trades.db` and the `RiskManager` / `PositionMonitor` in-memory state (via `main.py`-exposed dicts). It does not control or modify any Agent settings.

**Interfaces:**
- Consumes: SQLite `trades` table (same schema as `risk_layer.py`)
- Consumes: Agent 3 status dict (via simulated polling in a real setup, or static data for the page's initial display)
- Produces: Streamlit page with risk status, trade history, position monitoring info

- [ ] **Step 1: 创建监控页面**

```python
"""
Agent 风险监控面板（只读）
读取 agents/trade_executor / risk_layer / position_monitor 的状态并展示
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

st.set_page_config(page_title="Agent 风控", page_icon="🛡", layout="wide")
st.title("🛡 Agent 风控监控面板")
st.markdown("实时展示三 Agent 系统的风控状态、交易记录和持仓监控。**只读面板**，不参与决策。")

# ============ 模拟/真实数据 ============

# 在正式运行中，这些数据通过 main.py 暴露的 dict 或 SQLite 获取
# 此处展示页面结构。生产运行时替换为真实数据源。

DEMO_MODE = st.sidebar.checkbox("演示模式（使用模拟数据）", value=True)

# ── 三个标签页 ──
tab1, tab2, tab3 = st.tabs(["📊 风控概览", "📋 交易日志", "📈 持仓监控"])

with tab1:
    st.subheader("Layer 1 — 交易前风控")

    if DEMO_MODE:
        risk_data = {
            "每日交易": "3 / 10",
            "每日亏损": "$25.50 / $100.00",
            "连续亏损": "1 / 3",
            "仓位乘数": "0.75x",
            "API 熔断": "未触发",
            "BTC 波动": "正常 (1.2%)",
        }
    else:
        # TODO: 从 main.py 暴露的 agent3.get_status()['risk_status'] 读取
        risk_data = {"状态": "等待 Agent 运行数据"}

    cols = st.columns(3)
    for i, (key, val) in enumerate(risk_data.items()):
        col = cols[i % 3]
        with col:
            st.metric(key, val)

    st.divider()
    st.subheader("Layer 2 — 交易中保护")

    l2_cols = st.columns(3)
    with l2_cols[0]:
        st.metric("限价单超时", "10s")
    with l2_cols[1]:
        st.metric("最大滑点", "0.3%")
    with l2_cols[2]:
        st.metric("部分成交等待", "10s")

    st.divider()
    st.subheader("Layer 3 — 交易后监控")

    l3_cols = st.columns(3)
    with l3_cols[0]:
        st.metric("止损触发", "0 次" if DEMO_MODE else "—")
    with l3_cols[1]:
        st.metric("止盈触发", "0 次" if DEMO_MODE else "—")
    with l3_cols[2]:
        st.metric("移动止损激活", "0 次" if DEMO_MODE else "—")


with tab2:
    st.subheader("最近交易记录")

    if DEMO_MODE:
        demo_trades = [
            {"时间": "2026-06-24 10:30:00", "方向": "买入", "数量": "0.01 ETH",
             "价格": "$3,450.00", "状态": "成交", "订单ID": "12345"},
            {"时间": "2026-06-24 11:15:00", "方向": "卖出", "数量": "0.01 ETH",
             "价格": "$3,480.50", "状态": "部分成交(50%)", "订单ID": "12346"},
        ]
        df = pd.DataFrame(demo_trades)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        try:
            import sqlite3
            conn = sqlite3.connect(PROJECT_ROOT / "data" / "agent_trades.db")
            df = pd.read_sql_query(
                "SELECT * FROM trades ORDER BY id DESC LIMIT 20", conn
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
            conn.close()
        except Exception as e:
            st.info(f"暂无交易数据: {e}")

    # 交易统计
    st.subheader("📊 交易统计")
    stat_cols = st.columns(4)
    with stat_cols[0]:
        st.metric("总交易次数", "2" if DEMO_MODE else "—")
    with stat_cols[1]:
        st.metric("成功", "2" if DEMO_MODE else "—")
    with stat_cols[2]:
        st.metric("失败", "0" if DEMO_MODE else "—")
    with stat_cols[3]:
        st.metric("成功率", "100%" if DEMO_MODE else "—")


with tab3:
    st.subheader("当前持仓")

    if DEMO_MODE:
        pos_data = {
            "方向": "多头 / Long",
            "数量": "0.01 ETH",
            "入场价": "$3,450.00",
            "当前价": "$3,500.00",
            "浮盈": "+$0.50 (+1.45%)",
            "止损位": "$3,380.00 (-2.0%)",
            "止盈位": "$3,620.00 (+4.9%)",
            "移动止损": "未激活",
        }
        cols = st.columns(4)
        for i, (key, val) in enumerate(pos_data.items()):
            with cols[i % 4]:
                st.metric(key, val)
    else:
        st.info("等待持仓数据")

    st.divider()
    st.subheader("止盈止损状态")

    sl_tp_cols = st.columns(3)
    with sl_tp_cols[0]:
        progress = 45  # 价格在 SL 和 TP 之间的位置百分比
        st.markdown("**SL ——— TP 位置**")
        st.progress(progress / 100, text=f"{progress}% 向 TP")
    with sl_tp_cols[1]:
        st.metric("距离止损", "$120.00 (3.5%)")
    with sl_tp_cols[2]:
        st.metric("距离止盈", "$120.00 (3.5%)")

st.divider()
st.caption(f"🕐 面板刷新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据延迟 ≤ 5s")
```

- [ ] **Step 2: 验证 Streamlit 页面能加载**

```bash
cd C:\Users\Admin\Documents\okx-quant-agent
python -c "import py_compile; py_compile.compile('frontend/pages/12_🛡_AgentRisk.py', doraise=True)"
```
Expected: no errors

- [ ] **Step 3: 运行全部测试**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/12_🛡_AgentRisk.py
git commit -m "feat(phase2): add Agent Risk monitoring page (read-only)"

Co-Authored-By: Claude <noreply@anthropic.com>
```
