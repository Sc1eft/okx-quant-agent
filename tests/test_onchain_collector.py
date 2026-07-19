"""测试 Phase 3 链上数据收集器

测试范围:
  1. Gas 费解析和级别归类
  2. Whale Alert 解析
  3. 吃单比计算和触发逻辑
  4. 资金费率触发逻辑
  5. 事件推送
  6. 无数据时的优雅跳过
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.onchain_collector import (
    OnchainCollector,
    _parse_gas_from_api_response,
    _categorize_gas,
    _parse_whale_from_response,
)
from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.config import AgentSystemConfig


# ── 辅助函数 ──

@pytest.fixture
def config():
    return AgentSystemConfig(
        agent2_onchain_enabled=True,
        agent2_onchain_interval_seconds=300,
        agent2_gas_enabled=True,
        agent2_gas_high_threshold_gwei=100.0,
        agent2_gas_extreme_threshold_gwei=200.0,
        agent2_whale_enabled=True,
        agent2_whale_min_value_usdt=1_000_000.0,
        agent2_whale_alert_api_key="",
        agent2_taker_volume_enabled=True,
        agent2_taker_volume_buy_ratio_threshold=0.6,
        agent2_funding_rate_enabled=True,
        agent2_funding_rate_high_threshold=0.01,
        # OI 默认关：生产默认为开，但 test_run_disabled_modules 要求"全禁用即无协程"
        agent2_oi_enabled=False,
        # 测试默认关闭分位层，避免把测试分布写进生产状态文件
        agent2_percentile_enabled=False,
    )


def _mock_okx_client(taker_data=None, funding_data=None):
    """构造模拟 OKXClient"""
    client = MagicMock()
    client.get_taker_volume.return_value = taker_data or {
        "buy_vol_ccy": "1000000",
        "sell_vol_ccy": "800000",
        "buy_vol": "100",
        "sell_vol": "80",
        "ts": "1234567890",
    }
    client.get_funding_rate.return_value = funding_data or {
        "funding_rate": "0.000021",
        "funding_time": "1234567890",
        "next_funding_rate": "0.000025",
        "next_funding_time": "1234568890",
    }
    return client


def _mock_http_client(json_data: dict | list | None = None, status: int = 200):
    """构造模拟 httpx.AsyncClient"""
    client = AsyncMock()
    # 用普通 MagicMock 而非 AsyncMock，确保 .json() 同步返回
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data if json_data is not None else {}
    client.get.return_value = resp
    return client


# ── Gas 费解析测试 ──

class TestGasParsing:
    def test_parse_gas_normal(self):
        """正常 Gas API 响应"""
        data = {
            "result": {
                "SafeGasPrice": "15",
                "ProposeGasPrice": "20",
                "FastGasPrice": "30",
                "suggestBaseFee": "18.5",
            }
        }
        result = _parse_gas_from_api_response(data)
        assert result is not None
        assert result["safe_gwei"] == 15
        assert result["propose_gwei"] == 20
        assert result["fast_gwei"] == 30
        assert result["base_fee"] == "18.5"

    def test_parse_gas_empty(self):
        """空结果"""
        result = _parse_gas_from_api_response({})
        assert result is None

    def test_parse_gas_zero_values(self):
        """全部为零"""
        data = {"result": {"SafeGasPrice": "0", "ProposeGasPrice": "0", "FastGasPrice": "0"}}
        result = _parse_gas_from_api_response(data)
        assert result is None

    def test_parse_gas_malformed(self):
        """畸形数据"""
        result = _parse_gas_from_api_response({"result": "not_a_dict"})
        assert result is None

    def test_categorize_low(self, config):
        assert _categorize_gas(10, config) == "low"
        assert _categorize_gas(49, config) == "low"

    def test_categorize_medium(self, config):
        assert _categorize_gas(50, config) == "medium"
        assert _categorize_gas(99, config) == "medium"

    def test_categorize_high(self, config):
        assert _categorize_gas(100, config) == "high"
        assert _categorize_gas(150, config) == "high"

    def test_categorize_extreme(self, config):
        assert _categorize_gas(200, config) == "extreme"
        assert _categorize_gas(500, config) == "extreme"


# ── Whale Alert 解析测试 ──

class TestWhaleParsing:
    def test_parse_whale_normal(self):
        """正常 Whale Alert 数据"""
        raw = [
            {
                "hash": "0xabc123",
                "blockchain": "ethereum",
                "symbol": "ETH",
                "amount": "5000",
                "amount_usd": "15000000",
                "from": {"owner": "Unknown", "address": "0xfrom"},
                "to": {"owner": "Binance", "address": "0xto"},
            }
        ]
        result = _parse_whale_from_response(raw)
        assert len(result) == 1
        assert result[0]["hash"] == "0xabc123"
        assert result[0]["amount_usdt"] == 15000000
        assert result[0]["to_owner"] == "Binance"

    def test_parse_whale_empty(self):
        """空列表"""
        result = _parse_whale_from_response([])
        assert result == []

    def test_parse_whale_missing_fields(self):
        """缺少字段"""
        raw = [{"hash": "0xabc"}]
        result = _parse_whale_from_response(raw)
        assert len(result) == 1
        assert result[0]["amount_usdt"] == 0

    def test_parse_whale_non_dict_items(self):
        """不是 dict 的项被跳过"""
        raw = [{"hash": "0x1"}, "not_a_dict", {"hash": "0x2"}]
        result = _parse_whale_from_response(raw)
        assert len(result) == 2


# ── 吃单比监控测试 ──

class TestTakerVolume:
    @pytest.mark.asyncio
    async def test_taker_volume_push(self, config):
        """吃单比推送事件"""
        event_bus = EventBus()
        okx = _mock_okx_client(taker_data={
            "buy_vol_ccy": "1000000",
            "sell_vol_ccy": "800000",
            "buy_vol": "100",
            "sell_vol": "80",
            "ts": "1",
        })
        collector = OnchainCollector(
            okx_client=okx, config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        # 运行一次抓取
        await collector._fetch_and_push_taker()

        # 应该有一个事件在队列中
        assert collector._stats["events_pushed"] >= 1
        assert collector._stats["last_taker_buy_ratio"] > 0.55  # 100/180 ≈ 0.556

        # 验证事件内容
        event = await event_bus.consume_b()
        assert event.source == "agent2_taker"
        assert event.data["buy_ratio"] > 0.5
        assert event.data["sentiment"] in ("bullish", "neutral")

    @pytest.mark.asyncio
    async def test_taker_volume_no_duplicate(self, config):
        """重复相近值不推送"""
        event_bus = EventBus()
        okx = _mock_okx_client()
        collector = OnchainCollector(
            okx_client=okx, config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        # 第一次触发
        collector._last_taker_ratio = 0.55
        collector._stats["events_pushed"] = 1

        # 第二次同样值（差异 < 0.05）
        okx.get_taker_volume.return_value = {
            "buy_vol_ccy": "1100000", "sell_vol_ccy": "900000",
            "buy_vol": "110", "sell_vol": "90", "ts": "2",
        }
        await collector._fetch_and_push_taker()

        # 不应推送（buy_ratio 0.55，上次也是 0.55）
        assert collector._stats["events_pushed"] == 1

    @pytest.mark.asyncio
    async def test_taker_volume_api_error(self, config):
        """API 异常时优雅跳过"""
        event_bus = EventBus()
        okx = MagicMock()
        okx.get_taker_volume.side_effect = RuntimeError("API Error")
        collector = OnchainCollector(
            okx_client=okx, config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        await collector._fetch_and_push_taker()

        # 不应推送
        assert collector._stats["events_pushed"] == 0
        assert collector._stats["taker_fetches"] == 1


# ── 资金费率监控测试 ──

class TestFundingRate:
    @pytest.mark.asyncio
    async def test_funding_rate_push(self, config):
        """资金费率推送事件"""
        event_bus = EventBus()
        okx = _mock_okx_client(funding_data={
            "funding_rate": "0.0001",  # 0.01%
            "funding_time": "1",
            "next_funding_rate": "0.00012",
        })
        collector = OnchainCollector(
            okx_client=okx, config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        await collector._fetch_and_push_funding()

        assert collector._stats["events_pushed"] >= 1
        assert collector._stats["last_funding_rate"] > 0

        event = await event_bus.consume_b()
        assert event.source == "agent2_funding"
        assert abs(event.data["funding_rate_pct"]) > 0

    @pytest.mark.asyncio
    async def test_funding_rate_api_error(self, config):
        """API 异常时跳过"""
        event_bus = EventBus()
        okx = MagicMock()
        okx.get_funding_rate.side_effect = RuntimeError("API Error")
        collector = OnchainCollector(
            okx_client=okx, config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        await collector._fetch_and_push_funding()
        assert collector._stats["events_pushed"] == 0


# ── 集成测试 ──

class TestOnchainCollectorIntegration:
    @pytest.mark.asyncio
    async def test_run_disabled_modules(self, config):
        """全部模块禁用时不启动任何协程"""
        config.agent2_gas_enabled = False
        config.agent2_whale_enabled = False
        config.agent2_taker_volume_enabled = False
        config.agent2_funding_rate_enabled = False

        event_bus = EventBus()
        collector = OnchainCollector(
            okx_client=MagicMock(), config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        # 不应报错
        await collector.run()

    @pytest.mark.asyncio
    async def test_get_status(self, config):
        """状态返回包含所有监控字段"""
        event_bus = EventBus()
        collector = OnchainCollector(
            okx_client=MagicMock(), config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        status = collector.get_status()
        assert "running" in status
        assert status["gas_fetches"] == 0
        assert status["taker_fetches"] == 0
        assert status["funding_fetches"] == 0
        assert status["whale_fetches"] == 0
        assert status["events_pushed"] == 0

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, config):
        """stop 关闭 HTTP 客户端"""
        http = _mock_http_client()
        event_bus = EventBus()
        collector = OnchainCollector(
            okx_client=MagicMock(), config=config, event_bus=event_bus,
            http_client=http,
        )

        await collector.stop()

        # HTTP 客户端应被关闭
        http.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gas_without_api_key_skips(self, config):
        """无 API key 时不抓取 Gas"""
        config.agent2_whale_alert_api_key = ""  # 无 key
        event_bus = EventBus()
        http = _mock_http_client()
        collector = OnchainCollector(
            okx_client=MagicMock(), config=config, event_bus=event_bus,
            http_client=http,
        )

        await collector._fetch_and_push_gas()

        # 不应调用 http
        assert collector._stats["gas_fetches"] == 1
        assert collector._stats["events_pushed"] == 0

    @pytest.mark.asyncio
    async def test_gas_with_api_key(self, config):
        """有 API key 时抓取 Gas 并推送"""
        config.agent2_etherscan_api_key = "test_key"
        event_bus = EventBus()
        # 120 Gwei → high 级别
        http = _mock_http_client(json_data={
            "result": {
                "SafeGasPrice": "100",
                "ProposeGasPrice": "120",
                "FastGasPrice": "150",
                "suggestBaseFee": "110",
            }
        })
        collector = OnchainCollector(
            okx_client=MagicMock(), config=config, event_bus=event_bus,
            http_client=http,
        )

        await collector._fetch_and_push_gas()

        assert collector._stats["events_pushed"] >= 1
        assert collector._stats["last_gas_gwei"] == 120

        event = await event_bus.consume_b()
        assert event.source == "agent2_gas"
        assert event.data["gas_gwei"] == 120
        assert event.data["level"] == "high"

    @pytest.mark.asyncio
    async def test_taker_bullish_signal(self, config):
        """买占比超过阈值触发偏多信号"""
        event_bus = EventBus()
        okx = _mock_okx_client(taker_data={
            "buy_vol_ccy": "2000000",
            "sell_vol_ccy": "800000",
            "buy_vol": "200",
            "sell_vol": "80",
            "ts": "1",
        })
        collector = OnchainCollector(
            okx_client=okx, config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        await collector._fetch_and_push_taker()

        assert collector._stats["events_pushed"] >= 1

        event = await event_bus.consume_b()
        assert event.data["sentiment"] == "bullish"
        assert event.data["buy_ratio"] > 0.6
        assert event.urgency == "high"

    @pytest.mark.asyncio
    async def test_whale_requires_api_key(self, config):
        """无 Whale Alert API key 时跳过"""
        config.agent2_whale_alert_api_key = ""
        event_bus = EventBus()
        collector = OnchainCollector(
            okx_client=MagicMock(), config=config, event_bus=event_bus,
            http_client=_mock_http_client(),
        )

        await collector._fetch_and_push_whale()

        assert collector._stats["whale_fetches"] == 1
        assert collector._stats["events_pushed"] == 0


# ── Agent2 扩展测试 ──

class TestAgent2WithOnchain:
    def test_agent2_accepts_okx_client(self, config):
        """Agent2 接受 okx_client 后创建 OnchainCollector"""
        from agents.agent2_news import Agent2

        event_bus = EventBus()
        agent = Agent2(config=config, event_bus=event_bus, okx_client=MagicMock())
        assert agent._onchain is not None
        assert agent._onchain.cfg == config

    def test_agent2_without_okx_client(self, config):
        """无 okx_client 时不创建 OnchainCollector"""
        from agents.agent2_news import Agent2

        event_bus = EventBus()
        agent = Agent2(config=config, event_bus=event_bus, okx_client=None)
        assert agent._onchain is None

    def test_agent2_onchain_disabled(self, config):
        """onchain 禁用时不创建 OnchainCollector"""
        config.agent2_onchain_enabled = False
        from agents.agent2_news import Agent2

        event_bus = EventBus()
        agent = Agent2(config=config, event_bus=event_bus, okx_client=MagicMock())
        assert agent._onchain is None


# ── 事件触发层：分位极端 + OI 监控 ──

def _pct_config(tmp_path):
    """启用分位检测的 config：小窗口快速预热，状态文件指向 tmp（不碰生产数据）"""
    return AgentSystemConfig(
        agent2_percentile_enabled=True,
        agent2_percentile_window=50,
        agent2_percentile_min_samples=5,
        agent2_percentile_state_path=str(tmp_path / "pct.json"),
        agent2_oi_enabled=True,
        agent2_oi_min_change_pct=0.5,
    )


def _drain_b(event_bus) -> list:
    """非阻塞掏空 Queue B"""
    out = []
    while event_bus.qsize_b():
        out.append(event_bus.queue_b.get_nowait())
    return out


class TestPercentileTrigger:
    @pytest.mark.asyncio
    async def test_taker_extreme_event(self, tmp_path):
        """吃单比突破历史 P95 → 高优 extreme 事件（去抖不拦截）"""
        event_bus = EventBus()
        okx = _mock_okx_client()
        collector = OnchainCollector(
            okx_client=okx, config=_pct_config(tmp_path),
            event_bus=event_bus, http_client=_mock_http_client(),
        )
        # 预热：买方占比在 0.50~0.525 内震荡（单调递增会自己成为新高=P95 极端，
        # 必须用「内部震荡」序列；变化 <0.05 被去抖，但分布照常累计）
        for i, r in enumerate([0.50, 0.52, 0.505, 0.525, 0.51,
                               0.515, 0.50, 0.52, 0.505, 0.515]):
            okx.get_taker_volume.return_value = {
                "buy_vol_ccy": str(r * 100), "sell_vol_ccy": str((1 - r) * 100),
                "buy_vol": "1", "sell_vol": "1", "ts": str(i),
            }
            await collector._fetch_and_push_taker()
        assert collector._stats["extreme_events"] == 0

        # 极端：买方占比 0.75（远高于 0.50~0.545 的分布 → rank=1.0 ≥ P95）
        okx.get_taker_volume.return_value = {
            "buy_vol_ccy": "75", "sell_vol_ccy": "25",
            "buy_vol": "1", "sell_vol": "1", "ts": "x",
        }
        await collector._fetch_and_push_taker()

        assert collector._stats["extreme_events"] == 1
        events = _drain_b(event_bus)
        assert events, "极端事件应突破去抖被推送"
        ev = events[-1]
        assert ev.source == "agent2_taker"
        assert ev.urgency == "high"
        assert ev.data["extreme"] is True
        assert ev.data["sentiment"] == "bullish"

    @pytest.mark.asyncio
    async def test_oi_extreme_event(self, tmp_path):
        """OI 激增突破 P95 → agent2_oi 高优事件，方向跟随吃单比"""
        event_bus = EventBus()
        okx = _mock_okx_client()
        collector = OnchainCollector(
            okx_client=okx, config=_pct_config(tmp_path),
            event_bus=event_bus, http_client=_mock_http_client(),
        )
        cur = 100000.0
        okx.get_open_interest.return_value = {
            "oi": str(cur), "oi_ccy": "", "oi_usd": "", "ts": "0",
        }
        await collector._fetch_and_push_oi()  # 首个样本只建基准
        assert event_bus.qsize_b() == 0

        # 预热：±0.5~0.7% 内部震荡（单调或创新低会自触发 P95/P5，必须内部震荡；
        # ≥0.5 噪声线才入分布）
        for c in [0.6, -0.6, 0.7, -0.7, 0.55, -0.55, 0.65, -0.65, 0.5, -0.5]:
            cur *= 1 + c / 100
            okx.get_open_interest.return_value = {
                "oi": str(cur), "oi_ccy": "", "oi_usd": "", "ts": "x",
            }
            await collector._fetch_and_push_oi()
        assert collector._stats["extreme_events"] == 0

        # 极端：+3% 激增；吃单比 0.55（买方主导）→ 新多进场 = bullish
        collector._last_taker_ratio = 0.55
        cur *= 1.03
        okx.get_open_interest.return_value = {
            "oi": str(cur), "oi_ccy": "", "oi_usd": "", "ts": "y",
        }
        await collector._fetch_and_push_oi()

        assert collector._stats["extreme_events"] == 1
        events = _drain_b(event_bus)
        assert len(events) == 1
        ev = events[0]
        assert ev.source == "agent2_oi"
        assert ev.urgency == "high"
        assert ev.data["extreme"] == "high"
        assert ev.data["sentiment"] == "bullish"

    @pytest.mark.asyncio
    async def test_oi_plunge_is_neutral(self, tmp_path):
        """OI 骤降（去杠杆）→ 事件方向中性"""
        event_bus = EventBus()
        okx = _mock_okx_client()
        collector = OnchainCollector(
            okx_client=okx, config=_pct_config(tmp_path),
            event_bus=event_bus, http_client=_mock_http_client(),
        )
        cur = 100000.0
        okx.get_open_interest.return_value = {
            "oi": str(cur), "oi_ccy": "", "oi_usd": "", "ts": "0",
        }
        await collector._fetch_and_push_oi()
        for c in [0.6, -0.6, 0.7, -0.7, 0.55, -0.55]:
            cur *= 1 + c / 100
            okx.get_open_interest.return_value = {
                "oi": str(cur), "oi_ccy": "", "oi_usd": "", "ts": "x",
            }
            await collector._fetch_and_push_oi()

        cur *= 0.97  # -3% 骤降
        okx.get_open_interest.return_value = {
            "oi": str(cur), "oi_ccy": "", "oi_usd": "", "ts": "y",
        }
        await collector._fetch_and_push_oi()

        events = _drain_b(event_bus)
        assert len(events) == 1
        assert events[0].data["extreme"] == "low"
        assert events[0].data["sentiment"] == "neutral"

    @pytest.mark.asyncio
    async def test_state_persisted(self, tmp_path):
        """分位状态落盘 → 新实例加载后免预热"""
        cfg = _pct_config(tmp_path)
        okx = _mock_okx_client()
        c1 = OnchainCollector(
            okx_client=okx, config=cfg,
            event_bus=EventBus(), http_client=_mock_http_client(),
        )
        for i in range(6):
            r = 0.50 + i * 0.01
            okx.get_taker_volume.return_value = {
                "buy_vol_ccy": str(r * 100), "sell_vol_ccy": str((1 - r) * 100),
                "buy_vol": "1", "sell_vol": "1", "ts": str(i),
            }
            await c1._fetch_and_push_taker()

        c2 = OnchainCollector(
            okx_client=okx, config=cfg,
            event_bus=EventBus(), http_client=_mock_http_client(),
        )
        assert c2._trackers["taker"].n == c1._trackers["taker"].n
        assert c2._trackers["taker"].rank(0.5) is not None  # 已预热
