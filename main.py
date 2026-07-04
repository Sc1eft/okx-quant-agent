#!/usr/bin/env python3
"""
OKX Quant Agent — 三 Agent 异步事件驱动交易系统

启动方式:
    python main.py                    # 默认模式
    python main.py --mode paper       # 模拟盘
    python main.py --mode live        # 实盘
    python main.py --mode demo        # 演示

Streamlit 监控面板保持独立运行:
    streamlit run frontend/app.py
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from argparse import ArgumentParser
from datetime import datetime, timezone

# 修正导入路径（项目尚无根 __init__.py）
sys.path.insert(0, "")

from config import Config, CONFIG_PATH
from agents.config import AgentSystemConfig
from agents.event_bus import EventBus
from agents.risk_layer import RiskManager
from agents.trade_executor import TradeExecutor
from agents.deepseek_caller import DeepSeekTrader
from agents.agent1_technical import Agent1
from agents.agent2_news import Agent2
from agents.agent3_trader import Agent3
from agents.status_writer import write_agent_status
from okx_client import OKXClient


def setup_logging(level: str = "INFO", log_file: str = ""):
    """配置日志"""
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        import os
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
    )


def _install_signal_handlers(loop, shutdown_cb):
    """Install signal handlers — works on both Unix and Windows."""
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown_cb)
    except NotImplementedError:
        # Windows fallback: signal.signal works but not on the event loop
        import signal as _signal
        _signal.signal(_signal.SIGINT, lambda *_: loop.call_soon_threadsafe(shutdown_cb))
        _signal.signal(_signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(shutdown_cb))


async def main():
    parser = ArgumentParser(description="OKX Quant Agent — 三 Agent 交易系统")
    parser.add_argument(
        "--mode", choices=["paper", "live", "demo", "backtest"],
        default="paper", help="运行模式 (默认: paper)"
    )
    parser.add_argument(
        "--config", default=CONFIG_PATH,
        help=f"配置文件路径 (默认: {CONFIG_PATH})"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="日志级别 (DEBUG/INFO/WARNING/ERROR)"
    )
    args = parser.parse_args()

    # ── 加载配置 ──
    root_config = Config.load(args.config)
    root_config.mode = args.mode
    agent_config = AgentSystemConfig()
    agent_config.exchange_permissions = root_config.exchange.permissions

    setup_logging(args.log_level, agent_config.log_file)
    logger = logging.getLogger("main")
    logger.info("=" * 50)
    logger.info(f"OKX Quant Agent 启动 | 模式: {args.mode.upper()}")
    logger.info(f"时间: {datetime.now(timezone.utc).isoformat()}")

    # ── 初始化组件 ──
    event_bus = EventBus(maxsize=100)

    # OKX REST 客户端（供 TradeExecutor 使用）
    okx_rest = OKXClient(root_config.exchange)

    # 风控
    risk_manager = RiskManager(agent_config)

    # 交易执行器
    trade_executor = TradeExecutor(
        okx_client=okx_rest,
        symbol=root_config.trading.symbol,
        config=agent_config,
    )

    # ── Phase 2: 持仓监控器 ──
    from agents.position_monitor import PositionMonitor

    position_monitor = PositionMonitor(
        config=agent_config,
        risk_manager=risk_manager,
        executor=trade_executor,
        okx_client=okx_rest,
    ) if agent_config.agent3_enabled else None

    # DeepSeek 决策器
    deepseek = DeepSeekTrader(
        api_key=root_config.agent.api_key,
        model=root_config.agent.model,
        base_url=root_config.agent.base_url,
        temperature=root_config.agent.temperature,
    )

    # ── Phase 4: 复盘报告生成器 + Agent 4 复盘改进 ──
    from agents.review_generator import ReviewGenerator
    from agents.agent4_reviewer import Agent4Reviewer

    review_gen = ReviewGenerator(
        config=agent_config, db_path=agent_config.db_path,
        deepseek=deepseek,
    ) if agent_config.review_generator_enabled else None

    # ── ServerChan 推送器 ──
    from agents.notifier import ServerChanNotifier

    notifier = ServerChanNotifier(
        sendkey=agent_config.serverchan_sendkey,
    ) if agent_config.serverchan_enabled else None

    # ── 创建 Agent 实例 ──
    agent1 = Agent1(config=agent_config, event_bus=event_bus) if agent_config.agent1_enabled else None
    agent2 = Agent2(
        config=agent_config, event_bus=event_bus,
        okx_client=okx_rest,  # Phase 3: 链上数据
    ) if agent_config.agent2_enabled else None

    # Agent 4：先创建复盘报告生成器，再创建 Agent 4 复盘改进
    agent4_reviewer = Agent4Reviewer(
        config=agent_config,
        deepseek=deepseek,
        db_path=agent_config.db_path,
        kline_builder=agent1.kline_builder if agent1 else None,
        agent1=agent1,
        agent2=agent2,
    ) if agent_config.agent4_enabled else None

    agent3 = Agent3(
        config=agent_config,
        event_bus=event_bus,
        deepseek=deepseek,
        risk_manager=risk_manager,
        trade_executor=trade_executor,
        root_config=root_config,
        position_monitor=position_monitor,
        okx_client=okx_rest,
        review_generator=review_gen,  # Phase 4
        agent4_reviewer=agent4_reviewer,  # Agent 4（替代 param_adapter）
        notifier=notifier,
    ) if agent_config.agent3_enabled else None

    logger.info(f"Agent 1 (技术)={'✅' if agent1 else '❌'}")
    logger.info(f"Agent 2 (新闻)={'✅' if agent2 else '❌'}")
    logger.info(f"Agent 3 (交易)={'✅' if agent3 else '❌'}")
    logger.info(f"Agent 4 (复盘)={'✅' if agent4_reviewer else '❌'}")

    # ── 启动所有 Agent ──
    tasks = []
    if agent1:
        tasks.append(asyncio.create_task(agent1.run(), name="agent1"))
    if agent2:
        tasks.append(asyncio.create_task(agent2.run(), name="agent2"))
    if agent3:
        tasks.append(asyncio.create_task(agent3.run(), name="agent3"))
    if agent4_reviewer:
        tasks.append(asyncio.create_task(agent4_reviewer.run(), name="agent4"))

    # ── 启动持仓监控器（Phase 2） ──
    if position_monitor:
        tasks.append(asyncio.create_task(position_monitor.run(), name="position_monitor"))

    # ── 启动状态监控协程 ──
    tasks.append(asyncio.create_task(
        _status_reporter(agent1, agent2, agent3, agent4_reviewer=agent4_reviewer, position_monitor=position_monitor, mode=args.mode),
        name="monitor",
    ))

    logger.info(f"共 {len(tasks)} 个协程已启动，开始运行...")
    logger.info("=" * 50)

    # ── 优雅退出 ──
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown():
        logger.info("收到关闭信号，正在停止所有 Agent...")
        stop_event.set()

    _install_signal_handlers(loop, _shutdown)

    # 等待 stop 信号
    await stop_event.wait()

    # 停止所有 Agent
    if agent1:
        await agent1.stop()
    if agent2:
        await agent2.stop()
    if agent3:
        await agent3.stop()
    if agent4_reviewer:
        await agent4_reviewer.stop()

    # 停止持仓监控器
    if position_monitor:
        await position_monitor.stop()

    # 取消任务
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("所有 Agent 已停止。再见！")


async def _status_reporter(agent1, agent2, agent3, agent4_reviewer=None, position_monitor=None, mode="paper"):
    """定期报告系统状态并写入 JSON（每 5s，保证前端实时更新）"""
    while True:
        await asyncio.sleep(5)
        lines = ["\n--- 系统状态 ---"]
        s1 = s2 = s3 = s4 = pm = {}
        if agent1:
            s1 = agent1.get_status()
            lines.append(f"  Agent 1: running={s1['running']}, "
                         f"ticks={s1.get('ticks_received',0)}, "
                         f"signals={s1.get('signals_pushed',0)}")
        if agent2:
            s2 = agent2.get_status()
            lines.append(f"  Agent 2: running={s2['running']}, "
                         f"fetches={s2.get('fetch_count',0)}, "
                         f"pushed={s2.get('news_pushed',0)}, "
                         f"onchain={s2.get('onchain_events_pushed',0)}")
        if agent3:
            s3 = agent3.get_status()
            lines.append(f"  Agent 3: running={s3['running']}, "
                         f"trades={s3.get('trades_executed',0)}, "
                         f"skipped={s3.get('trades_skipped',0)}, "
                         f"composite={s3.get('last_composite_score','—')}, "
                         f"win_rate={s3.get('last_win_rate','—')}%")
        if position_monitor:
            pm = position_monitor.get_status()
            lines.append(f"  Position Monitor: running={pm['running']}, "
                         f"has_position={pm['has_position']}, "
                         f"SL={pm['stop_loss_triggered']} TP={pm['take_profit_triggered']} "
                         f"trailing={pm['trailing_stop_triggered']}")
        if agent4_reviewer:
            s4 = agent4_reviewer.get_status()
            lines.append(f"  Agent 4: running={s4['running']}, "
                         f"reviews={s4.get('total_reviews',0)}, "
                         f"adjustments={s4.get('total_adjustments',0)}, "
                         f"errors={s4.get('total_adjustment_errors',0)}")
        logging.getLogger("main").info("\n".join(lines))

        # 写入状态 JSON 供 Streamlit 面板读取
        write_agent_status(
            agent1_status=s1 if agent1 else None,
            agent2_status=s2 if agent2 else None,
            agent3_status=s3 if agent3 else None,
            agent4_reviewer_status=s4 if agent4_reviewer else None,
            position_monitor_status=pm if position_monitor else None,
            mode=mode,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
