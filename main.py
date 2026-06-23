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
from agents.okx_ws import OKXWebSocketClient
from agents.risk_layer import RiskManager
from agents.trade_executor import TradeExecutor
from agents.deepseek_caller import DeepSeekTrader
from agents.agent1_technical import Agent1
from agents.agent2_news import Agent2
from agents.agent3_trader import Agent3
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
    )

    # DeepSeek 决策器
    deepseek = DeepSeekTrader(
        api_key=root_config.agent.api_key,
        model=root_config.agent.model,
        base_url=root_config.agent.base_url,
        temperature=root_config.agent.temperature,
    )

    # ── 创建 Agent 实例 ──
    agent1 = Agent1(config=agent_config, event_bus=event_bus) if agent_config.agent1_enabled else None
    agent2 = Agent2(config=agent_config, event_bus=event_bus) if agent_config.agent2_enabled else None
    agent3 = Agent3(
        config=agent_config,
        event_bus=event_bus,
        deepseek=deepseek,
        risk_manager=risk_manager,
        trade_executor=trade_executor,
        root_config=root_config,
    ) if agent_config.agent3_enabled else None

    logger.info(f"Agent 1 (技术)={'✅' if agent1 else '❌'}")
    logger.info(f"Agent 2 (新闻)={'✅' if agent2 else '❌'}")
    logger.info(f"Agent 3 (交易)={'✅' if agent3 else '❌'}")

    # ── 启动所有 Agent ──
    tasks = []
    if agent1:
        tasks.append(asyncio.create_task(agent1.run(), name="agent1"))
    if agent2:
        tasks.append(asyncio.create_task(agent2.run(), name="agent2"))
    if agent3:
        tasks.append(asyncio.create_task(agent3.run(), name="agent3"))

    # ── 启动状态监控协程 ──
    tasks.append(asyncio.create_task(_status_reporter(agent1, agent2, agent3), name="monitor"))

    logger.info(f"共 {len(tasks)} 个协程已启动，开始运行...")
    logger.info("=" * 50)

    # ── 优雅退出 ──
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown():
        logger.info("收到关闭信号，正在停止所有 Agent...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    # 等待 stop 信号
    await stop_event.wait()

    # 停止所有 Agent
    if agent1:
        await agent1.stop()
    if agent2:
        await agent2.stop()
    if agent3:
        await agent3.stop()

    # 取消任务
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("所有 Agent 已停止。再见！")


async def _status_reporter(agent1, agent2, agent3):
    """定期报告系统状态（每 60s）"""
    while True:
        await asyncio.sleep(60)
        lines = ["\n--- 系统状态 ---"]
        if agent1:
            s1 = agent1.get_status()
            lines.append(f"  Agent 1: running={s1['running']}, "
                         f"ticks={s1.get('ticks_received',0)}, "
                         f"signals={s1.get('signals_pushed',0)}")
        if agent2:
            s2 = agent2.get_status()
            lines.append(f"  Agent 2: running={s2['running']}, "
                         f"fetches={s2.get('fetch_count',0)}, "
                         f"pushed={s2.get('news_pushed',0)}")
        if agent3:
            s3 = agent3.get_status()
            lines.append(f"  Agent 3: running={s3['running']}, "
                         f"trades={s3.get('trades_executed',0)}, "
                         f"skipped={s3.get('trades_skipped',0)}")
        logging.getLogger("main").info("\n".join(lines))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
