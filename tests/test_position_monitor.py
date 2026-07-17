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
    ex = MagicMock()
    ex.symbol = "ETH-USDT"
    # Use explicit AsyncMock for async method to avoid auto-creation warnings
    ex.execute_market = AsyncMock(return_value={
        "success": True, "order_id": "sl123", "fill_price": 3400.0,
    })

    async def _fake_execute_safe(**kwargs):
        # 模拟盘语义：以 signal_price 成交
        return {
            "success": True,
            "order_id": "mock_ord",
            "fill_price": kwargs.get("signal_price", 0.0),
            "filled_size": kwargs.get("size_eth", 0.0),
        }

    ex.execute_safe = AsyncMock(side_effect=_fake_execute_safe)
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
        assert monitor._stats["trailing_stop_triggered"] == 1

    @pytest.mark.asyncio
    async def test_no_position_no_action(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """无持仓时不做任何操作"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True
        # Start with a position, then clear it
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3600.0)
        monitor.clear_position()
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
    async def test_short_trailing_stop_activates(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """空头：价格下跌触发移动止损激活"""
        mock_risk_manager._current_position_side = "short"
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True
        monitor.update_position(side="short", size=0.01, entry_price=3500.0,
                                stop_loss=3570.0, take_profit=3300.0)
        # 价格跌到 3350（浮盈 4.3% > 3%）→ 激活移动止损
        mock_okx_client.get_ticker.return_value = {"last": 3350.0}
        triggered = await monitor._check_once()
        assert triggered is False
        assert monitor._trailing_stop_active is True
        assert monitor._stats["trailing_stop_activated"] == 1

    @pytest.mark.asyncio
    async def test_short_trailing_stop_triggers(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """空头移动止损激活后价格回升 → 触发"""
        mock_risk_manager._current_position_side = "short"
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True
        monitor.update_position(side="short", size=0.01, entry_price=3500.0,
                                stop_loss=3570.0, take_profit=3400.0)
        monitor._trailing_low = 3350.0
        monitor._trailing_stop_active = True
        monitor._current_stop_loss = 3350.0 * (1 + config.trailing_stop_distance_pct / 100)  # ~3400.25

        # 价格回升到 3410 > 3400.25 → 触发移动止损
        mock_okx_client.get_ticker.return_value = {"last": 3410.0}
        triggered = await monitor._check_once()
        assert triggered is True
        assert monitor._stats["trailing_stop_triggered"] == 1

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

    @pytest.mark.asyncio
    async def test_accumulate_position(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """同方向补仓：累加 size + 加权均价"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 买 0.01 @ 3500
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3700.0)
        assert monitor._position_size == 0.01
        assert monitor._entry_price == 3500.0

        # 再次买 0.02 @ 3600（补仓）
        monitor.update_position(side="long", size=0.02, entry_price=3600.0,
                                stop_loss=3450.0, take_profit=3800.0, accumulate=True)

        # 总 size = 0.01 + 0.02 = 0.03
        # 均价 = (0.01*3500 + 0.02*3600) / 0.03 = (35 + 72) / 0.03 = 3566.67
        assert monitor._position_size == 0.03
        assert round(monitor._entry_price, 2) == 3566.67
        assert monitor._has_position is True
        assert monitor._position_side == "long"

        # SL/TP 应该被最新值覆盖
        assert monitor._stop_loss == 3450.0
        assert monitor._take_profit == 3800.0

    @pytest.mark.asyncio
    async def test_accumulate_reverse_direction(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """补仓时反方向不应累加，应触发反转"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 先开多 0.01 @ 3500
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3700.0)

        # 反方向开空，即使 accumulate=True 也不应累加
        mock_okx_client.get_ticker.return_value = {"last": 3600.0}
        # 应该触发反转 PnL 记录 + 覆盖为新方向
        monitor.update_position(side="short", size=0.02, entry_price=3600.0,
                                stop_loss=3700.0, take_profit=3400.0, accumulate=True)

        # 应该被覆盖为 short（不是累加成 0.03）
        assert monitor._position_size == 0.02
        assert monitor._position_side == "short"

    @pytest.mark.asyncio
    async def test_accumulate_then_close_pnl(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """补仓后平仓：PnL 计算中累计开仓费用正确"""
        config.maker_fee_rate = 0.001
        config.taker_fee_rate = 0.001

        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 开 0.01 @ 3500 (taker)
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3700.0,
                                opened_with_limit=False)

        # 补 0.02 @ 3600 (maker)
        monitor.update_position(side="long", size=0.02, entry_price=3600.0,
                                stop_loss=3450.0, take_profit=3800.0,
                                opened_with_limit=True, accumulate=True)

        # 累计费用 = 0.01*3500*0.001 + 0.02*3600*0.001 = 0.035 + 0.072 = 0.107
        assert round(monitor._total_open_fees, 4) == 0.107

        # 平仓 @ 3700 (taker)
        # 平仓费 = 0.03 * 3700 * 0.001 = 0.111
        # 总费 = 0.107 + 0.111 = 0.218
        # 毛利 = (3700-3566.67) * 0.03 = 4.0
        # 净利 = 4.0 - 0.218 = 3.78
        mock_okx_client.get_ticker.return_value = {"last": 3700.0}
        await monitor._close_position("止盈", 3700.0)

        # 验证 PnL 记录到了风控
        last_trade = mock_risk_manager.record_trade.call_args[0][0]
        assert last_trade["trade_type"] == "close"
        assert last_trade["pnl"] > 0  # 毛利 $4 > 总费 $0.218，应盈利

    @pytest.mark.asyncio
    async def test_accumulate_maker_taker_fees(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """混合 maker/taker 费率累计正确"""
        config.maker_fee_rate = 0.0002
        config.taker_fee_rate = 0.0005

        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 开 0.01 @ 3500 (taker) → 费用 = 0.01*3500*0.0005 = 0.0175
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3700.0,
                                opened_with_limit=False)

        # 补 0.02 @ 3600 (maker) → 费用 = 0.02*3600*0.0002 = 0.0144
        monitor.update_position(side="long", size=0.02, entry_price=3600.0,
                                stop_loss=3450.0, take_profit=3800.0,
                                opened_with_limit=True, accumulate=True)

        # 总费用 = 0.0175 + 0.0144 = 0.0319
        assert round(monitor._total_open_fees, 4) == 0.0319

        # _opened_with_limit 应保持 True（maker 覆盖了 taker）
        assert monitor._opened_with_limit is True

        # get_status 应包含 total_open_fees
        status = monitor.get_status()
        assert "total_open_fees" in status

    @pytest.mark.asyncio
    async def test_accumulate_non_existent_position_falls_back(self, config, mock_risk_manager, mock_executor, mock_okx_client):
        """无持仓时 accumulate=True 应退化为新开仓行为"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True

        # 无持仓时 accumulate=True 应该表现如正常开仓
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3700.0,
                                accumulate=True)

        assert monitor._position_size == 0.01
        assert monitor._entry_price == 3500.0
        assert monitor._has_position is True


class TestCloseRetryAndRestore:
    """平仓失败保留状态重试 / 启动状态恢复（P0 修复的回归锁定）"""

    @pytest.mark.asyncio
    async def test_close_failure_keeps_position_and_retries(
        self, config, mock_risk_manager, mock_executor, mock_okx_client
    ):
        """平仓 3 次全失败 → 保留持仓、不记账、计数失败；下次成功才清算"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True
        monitor.update_position(side="long", size=0.01, entry_price=3500.0,
                                stop_loss=3400.0, take_profit=3700.0)

        # 全部尝试失败
        mock_executor.execute_safe = AsyncMock(return_value={
            "success": False, "error": "API timeout",
        })
        # 跳过指数退避的真实等待
        with patch("agents.position_monitor.asyncio.sleep", new=AsyncMock()):
            await monitor._close_position("止损", current_price=3390.0)

        # 持仓保留、未记账、失败计数 +1
        assert monitor._has_position is True
        assert monitor._close_failures == 1
        mock_risk_manager.record_trade.assert_not_called()
        assert mock_executor.execute_safe.call_count == 3

        # 下一轮重试成功 → 正常清算
        mock_executor.execute_safe = AsyncMock(return_value={
            "success": True, "order_id": "retry1", "fill_price": 3390.0,
        })
        await monitor._close_position("止损", current_price=3390.0)
        assert monitor._has_position is False
        assert monitor._close_failures == 0
        mock_risk_manager.record_trade.assert_called_once()
        close_rec = mock_risk_manager.record_trade.call_args[0][0]
        assert close_rec["trade_type"] == "close"
        assert close_rec["side"] == "sell"  # 平多

    @pytest.mark.asyncio
    async def test_close_uses_close_only_no_reversal(
        self, config, mock_risk_manager, mock_executor, mock_okx_client
    ):
        """平仓必须走 close_only 通道，防止止损变反向开仓"""
        monitor = PositionMonitor(
            config=config,
            risk_manager=mock_risk_manager,
            executor=mock_executor,
            okx_client=mock_okx_client,
        )
        monitor._running = True
        monitor.update_position(side="short", size=0.02, entry_price=3500.0,
                                stop_loss=3600.0, take_profit=3300.0)
        await monitor._close_position("止损", current_price=3610.0)
        _, kwargs = mock_executor.execute_safe.call_args
        assert kwargs["close_only"] is True
        assert kwargs["side"] == "buy"  # 平空
        assert kwargs["prefer_limit"] is False  # 平仓用市价

    def test_restore_from_db_rebuilds_position(self, config, mock_executor, mock_okx_client, tmp_path):
        """重启后从 trades 表回放恢复未平仓持仓"""
        import json
        import sqlite3
        from agents.risk_layer import RiskManager

        db = str(tmp_path / "trades.db")
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, side TEXT, size REAL, price REAL,
                pnl REAL, order_id TEXT, symbol TEXT, decision TEXT,
                pnl_close REAL DEFAULT 0,
                trade_group_id TEXT DEFAULT '',
                trade_type TEXT DEFAULT 'open'
            )
        """)
        # 两笔同向开仓（加权均价）+ 一笔已平仓的不应恢复
        conn.execute(
            "INSERT INTO trades (timestamp, side, size, price, pnl, decision, trade_type) "
            "VALUES ('2026-07-17T01:00:00', 'buy', 0.2, 3000.0, 0, '{}', 'open')")
        conn.execute(
            "INSERT INTO trades (timestamp, side, size, price, pnl, decision, trade_type) "
            "VALUES ('2026-07-17T02:00:00', 'buy', 0.1, 2850.0, 0, '{}', 'open')")
        conn.execute(
            "INSERT INTO trades (timestamp, side, size, price, pnl, decision, trade_type) "
            "VALUES ('2026-07-17T03:00:00', 'sell', 0.05, 3100.0, 5, '{}', 'open')")
        conn.execute(
            "INSERT INTO trades (timestamp, side, size, price, pnl, decision, trade_type) "
            "VALUES ('2026-07-17T04:00:00', 'buy', 0.05, 3000.0, 0, '{}', 'close')")
        conn.commit()
        conn.close()

        config.db_path = db
        rm = RiskManager(config)
        monitor = PositionMonitor(
            config=config, risk_manager=rm,
            executor=mock_executor, okx_client=mock_okx_client,
        )
        # 最后一笔 close 平掉了 sell 空仓 → 无持仓可恢复
        assert monitor.restore_from_db() is False
        assert monitor._has_position is False

        # 去掉 close 记录再恢复 → 应恢复为 short 0.05
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM trades WHERE trade_type='close'")
        conn.commit()
        conn.close()
        from data.db_manager import DatabaseManager
        DatabaseManager._instances.pop(db, None)  # 清连接缓存避免脏读
        rm2 = RiskManager(config)
        monitor2 = PositionMonitor(
            config=config, risk_manager=rm2,
            executor=mock_executor, okx_client=mock_okx_client,
        )
        assert monitor2.restore_from_db() is True
        assert monitor2._has_position is True
        assert monitor2._position_side == "short"
        assert abs(monitor2._position_size - 0.05) < 1e-9
        assert abs(monitor2._entry_price - 3100.0) < 1e-9

    def test_restore_preserves_persisted_sltp(self, config, mock_executor, mock_okx_client, tmp_path):
        """开仓时入库的 SL/TP 在重启恢复后原样还原（不回退默认值）"""
        import sqlite3
        from agents.risk_layer import RiskManager

        db = str(tmp_path / "trades.db")
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, side TEXT, size REAL, price REAL,
                pnl REAL, order_id TEXT, symbol TEXT, decision TEXT,
                pnl_close REAL DEFAULT 0,
                trade_group_id TEXT DEFAULT '',
                trade_type TEXT DEFAULT 'open',
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO trades (timestamp, side, size, price, pnl, decision, trade_type, "
            "stop_loss, take_profit) "
            "VALUES ('2026-07-17T01:00:00', 'buy', 0.2, 3000.0, 0, '{}', 'open', 2800.0, 3300.0)")
        conn.commit()
        conn.close()

        config.db_path = db
        rm = RiskManager(config)
        monitor = PositionMonitor(
            config=config, risk_manager=rm,
            executor=mock_executor, okx_client=mock_okx_client,
        )
        assert monitor.restore_from_db() is True
        # 持久化值原样还原，而非默认 5%/10%（2850/3150）
        assert monitor._stop_loss == 2800.0
        assert monitor._take_profit == 3300.0
