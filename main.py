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
import io
import logging
import signal
import sys
import os
import atexit
import subprocess
import time
from pathlib import Path
from argparse import ArgumentParser
from datetime import datetime, timezone

# 修正导入路径（项目尚无根 __init__.py）
sys.path.insert(0, "")

# ── PID 文件锁：防止多实例冲突 ──
_BASE_DIR = Path(__file__).parent.resolve()
_PID_FILE = _BASE_DIR / "data" / "agent.pid"

def _pid_belongs_to_agent(pid: int) -> bool | None:
    """确认 PID 对应的进程是否是本 agent（main.py），防止 PID 复用误杀。

    Returns:
        True  — 进程存在且命令行包含 main.py（是本 agent）
        False — 进程不存在，或命令行与本 agent 无关（陈旧锁/被复用）
        None  — 无法确定（查询失败，调用方应保守处理）
    """
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    cmdline = (out.stdout or "").strip()
    if not cmdline:
        return False  # 进程不存在
    return "main.py" in cmdline


def _acquire_pid_lock():
    """用原子性文件创建 (O_EXCL) 实现 PID 锁，消除 TOCTOU 竞争条件。

    O_CREAT | O_EXCL 保证文件创建是原子操作：
    - 成功 → 获得锁，写入 PID
    - 失败 → 另一实例持有锁；先核实旧 PID 身份，仅当确属本 agent 才结束它
    """
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            fd = os.open(
                str(_PID_FILE),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            break  # 成功获取锁
        except FileExistsError:
            # 原子创建失败 → 另一实例占用
            try:
                old_pid = int(_PID_FILE.read_text().strip())
            except (ValueError, OSError):
                old_pid = 0
            if old_pid > 0 and old_pid != os.getpid():
                belongs = _pid_belongs_to_agent(old_pid)
                if belongs is True:
                    print(f"检测到旧 agent 实例 (PID {old_pid})，正在结束…", file=sys.stderr)
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(old_pid)],
                            capture_output=True, timeout=10,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                elif belongs is None:
                    # 无法确认旧 PID 身份 — 保守起见不杀任何进程
                    raise SystemExit(
                        f"❌ 无法确认 PID {old_pid} 是否为本 agent 进程，"
                        f"为防误杀已放弃启动。请人工检查后删除 {_PID_FILE}"
                    )
                # belongs is False → 进程不存在或 PID 被无关进程复用，属陈旧锁，直接清理
            # 清理旧锁后重试
            try:
                _PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.2)

    atexit.register(_release_pid_lock)

def _release_pid_lock():
    """退出时清理 PID 文件（仅当是自己写的才删）。"""
    try:
        if _PID_FILE.exists() and _PID_FILE.read_text().strip() == str(os.getpid()):
            _PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass

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
    # 终端编码修正：Windows GBK 无法输出 emoji
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
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


async def _preflight_check(root_config, okx_rest, mode: str) -> bool:
    """启动前检查：把"第一笔真单才炸"提前到启动即报错

    live 模式下任一失败都拒绝启动；其他模式仅跳过（不检查）。
    """
    if mode != "live":
        return True

    logger = logging.getLogger("main")
    problems: list[str] = []

    ex = root_config.exchange
    if not ex.api_key or not ex.secret_key or not ex.passphrase:
        problems.append("OKX API 凭证不完整 (api_key/secret_key/passphrase)")
    if ex.permissions != "trade":
        problems.append(
            f"OKX 权限为 '{ex.permissions}'，live 模式会静默走模拟成交，必须改为 'trade'"
        )
    if not root_config.agent.api_key:
        problems.append("DeepSeek API key 未设置 (DEEPSEEK_API_KEY)")

    # 账户连通性 + 签名验证（真实请求一次）
    if not problems:
        try:
            balances = await asyncio.to_thread(okx_rest.get_balance)
            logger.info(f"✅ Preflight: OKX 账户连接正常（{len(balances)} 个资产条目）")
        except Exception as e:
            problems.append(f"OKX 账户查询失败（凭证/权限/网络）: {e}")

    for p in problems:
        logger.error(f"Preflight: {p}")
    if problems:
        logger.error("❌ Preflight 未通过，live 模式终止启动")
        return False
    return True


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

    # ── PID 文件锁（确保唯一实例） ──
    _acquire_pid_lock()

    # ── 加载配置 ──
    config_path = Path(args.config)
    if config_path.exists():
        root_config = Config.load(args.config)
    else:
        root_config = Config()
        print(f"[WARN] 配置文件 {args.config} 不存在，使用默认配置。")
        print(f"[WARN] 通过环境变量 OKX_API_KEY / DEEPSEEK_API_KEY 设置密钥。")
    root_config.mode = args.mode
    agent_config = AgentSystemConfig.from_root_config(root_config)

    # ── 从环境变量加载链上数据 API Key ──
    etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
    whale_alert_key = os.getenv("WHALE_ALERT_API_KEY", "")
    if etherscan_key:
        agent_config.agent2_etherscan_api_key = etherscan_key
    if whale_alert_key:
        agent_config.agent2_whale_alert_api_key = whale_alert_key

    setup_logging(args.log_level, agent_config.log_file)
    logger = logging.getLogger("main")
    logger.info("=" * 50)
    logger.info(f"OKX Quant Agent 启动 | 模式: {args.mode.upper()}")
    logger.info(f"时间: {datetime.now(timezone.utc).isoformat()}")

    # ── 初始化组件 ──
    event_bus = EventBus(maxsize=100)

    # OKX REST 客户端（供 TradeExecutor 使用）
    okx_rest = OKXClient(root_config.exchange)

    # ── 启动前检查（live 模式：凭证/权限/连通性，不过则退出） ──
    if not await _preflight_check(root_config, okx_rest, args.mode):
        raise SystemExit(1)

    # 风控
    risk_manager = RiskManager(agent_config)

    # RuleEngine — 可插拔规则引擎（替代 RiskManager 硬编码检查）
    from agents.rule_engine import RuleEngine
    rule_engine = RuleEngine()
    rule_engine.load_defaults(agent_config)

    # 合约账户（合约模拟模式使用）
    futures_account = None
    if agent_config.market_mode == "futures":
        from execution.futures_paper import FuturesAccount
        futures_account = FuturesAccount(
            wallet_balance=10000.0,
            taker_fee_rate=agent_config.futures_taker_fee_rate,
            maker_fee_rate=agent_config.futures_maker_fee_rate,
        )

    # 交易执行器
    trade_executor = TradeExecutor(
        okx_client=okx_rest,
        symbol=root_config.trading.symbol,
        config=agent_config,
        market_mode=agent_config.market_mode,
        leverage=agent_config.futures_leverage,
        futures_account=futures_account,
    )

    # ── Phase 2: 持仓监控器 ──
    from agents.position_monitor import PositionMonitor

    position_monitor = PositionMonitor(
        config=agent_config,
        risk_manager=risk_manager,
        executor=trade_executor,
        okx_client=okx_rest,
    ) if agent_config.agent3_enabled else None

    # 仓位唯一事实源：RiskManager 的持仓检查/状态均从 PositionMonitor 读取
    if position_monitor:
        risk_manager.position_monitor = position_monitor

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
    agent1 = Agent1(config=agent_config, event_bus=event_bus, okx_client=okx_rest) if agent_config.agent1_enabled else None
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
        rule_engine=rule_engine,  # 可插拔规则引擎
        agent1=agent1,  # 用于读取多周期指标 + 市场状态
        review_generator=review_gen,  # Phase 4
        agent4_reviewer=agent4_reviewer,  # Agent 4（替代 param_adapter）
        notifier=notifier,
    ) if agent_config.agent3_enabled else None

    logger.info(f"Agent 1 (技术)={'✅' if agent1 else '❌'}")
    logger.info(f"Agent 2 (新闻)={'✅' if agent2 else '❌'}")
    logger.info(f"Agent 3 (交易)={'✅' if agent3 else '❌'}")
    logger.info(f"Agent 4 (复盘)={'✅' if agent4_reviewer else '❌'}")

    # ── 启动恢复：从 DB 重建未平仓持仓（重启后止损监控不丢仓） ──
    if position_monitor and position_monitor.restore_from_db():
        pm_status = position_monitor.get_status()
        logger.warning(
            f"⚠️ 检测到未平仓持仓: {pm_status['position_side']} "
            f"{pm_status['position_size']:.4f} ETH @ ${pm_status['entry_price']:.2f} — "
            f"已恢复监控（止损/止盈为配置默认值）"
        )
        if agent3:
            agent3.update_position(
                side=pm_status["position_side"],
                size=pm_status["position_size"],
                entry_price=pm_status["entry_price"],
            )

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

        # ── 采集各 Agent 状态 ──
        s1 = s2 = s3 = s4 = pm = {}
        if agent1: s1 = agent1.get_status()
        if agent2: s2 = agent2.get_status()
        if agent3: s3 = agent3.get_status()
        if agent4_reviewer: s4 = agent4_reviewer.get_status()
        if position_monitor: pm = position_monitor.get_status()

        # ── 构建可视化面板 ──
        lines = ["\n" + "=" * 60]

        # 头部：整体状态
        pos = s3.get("position", {})
        has_pos = pos.get("size", 0) > 0
        dir_icon = "🟢" if pos.get("side") == "buy" else "🔴" if pos.get("side") == "sell" else "⚪"

        price_str = ""
        if s1:
            indicators = s1.get("latest_indicators", {})
            for tf in ("3m", "5m", "15m", "1h"):
                if tf in indicators and indicators[tf] and indicators[tf].get("close"):
                    price_str = f"ETH ${indicators[tf]['close']}"
                    break

        lines.append(f"  OKX Quant Agent  |  模式: {mode}  |  {price_str}")

        # 第二行：Agent 运行状态
        a1 = f"{'✅' if s1.get('running') else '❌'} Tech"
        a2 = f"{'✅' if s2.get('running') else '❌'} News"
        a3 = f"{'✅' if s3.get('running') else '❌'} Trader"
        a4 = f"{'✅' if s4.get('running') else '❌'} Review"
        lines.append(f"  Agent  {a1}  |  {a2}  |  {a3}  |  {a4}")
        lines.append("-" * 60)

        # ── 持仓面板（放在最显眼位置） ──
        risk = s3.get("risk_status", {})
        pnl = pos.get("pnl", 0)
        pnl_pct = pos.get("pnl_pct", 0)
        if has_pos:
            side = pos.get("side", "")
            size = pos.get("size", 0)
            entry = pos.get("entry_price", 0)
            cur = pos.get("current_price", 0)
            mode_label = "🟢 多头" if side == "buy" else "🔴 空头"
            sl = pm.get("stop_loss", 0)
            tp = pm.get("take_profit", 0)
            daily_trades = risk.get("daily_trade_count", 0)

            # PnL 颜色指示
            pnl_icon = "💰" if pnl > 0 else "📉" if pnl < 0 else "⚪"
            pnl_sign = "+" if pnl > 0 else ""

            lines.append(f"  📈 持仓  {mode_label}  {size} ETH  |  入场 ${entry}  |  现价 ${cur}")
            lines.append(f"  {pnl_icon} 浮动盈亏  ${pnl_sign}{pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)")
            if sl or tp:
                lines.append(f"      🛑 止损 ${sl:.2f}  {'⚠️ 触发!' if pm.get('stop_loss_triggered') else ''}  |  🎯 止盈 ${tp:.2f}  {'💰 触发!' if pm.get('take_profit_triggered') else ''}")
            lines.append(f"      今日交易: {daily_trades} 笔")
        else:
            lines.append(f"  ⚪ 无持仓  |  今日交易: {risk.get('daily_trade_count', 0)} 笔")
        lines.append("-" * 60)

        # ── Agent 最近活动 ──
        # 活动文本开头自带 emoji，和前缀图标重复时去掉
        def _show(a):
            return a[1:].strip()[:55] if a and len(a) > 1 and ord(a[0]) > 8000 else (a or "")[:55]
        if s1.get("running"): lines.append(f"  📡 {_show(s1.get('current_activity', ''))}")
        if s2.get("running"): lines.append(f"  📰 {_show(s2.get('current_activity', ''))}")
        if s3.get("running"): lines.append(f"  🤖 {_show(s3.get('current_activity', ''))}{' 🌙' if s3.get('paused_for_daily_limit') else ''}")
        if s4.get("running"): lines.append(f"  📋 {_show(s4.get('current_activity', ''))}")

        # 每日交易上限暂停提示
        if s3.get("paused_for_daily_limit"):
            count = risk.get("daily_trade_count", 0)
            limit = risk.get("max_daily_trades", 20)
            lines.append(f"  🌙 交易达上限 {count}/{limit} — 暂停至北京时间午夜自动恢复")

        # ── 汇总面板：今日数据 + 月度历史 ──
        ds = s3.get("daily_stats", {})
        ms = s3.get("monthly_stats", {})

        # 今日数据
        daily_trades_count = ds.get("trades", 0)
        daily_max = ds.get("max_trades", 20)
        daily_pnl = ds.get("realized_pnl", 0)
        daily_wins = ds.get("wins", 0)
        daily_losses = ds.get("losses", 0)
        daily_wr = ds.get("win_rate", 0)
        pnl_icon = "💰" if daily_pnl > 0 else "📉" if daily_pnl < 0 else "⚪"
        lines.append(f"  📊 今日数据  |  交易 {daily_trades_count}/{daily_max}  |  盈亏 {pnl_icon} ${daily_pnl:+.2f}  |  {daily_wins}胜/{daily_losses}负 ({daily_wr}%)")

        # 月度历史
        mt = ms.get("trades", 0)
        m_pnl = ms.get("total_pnl", 0)
        m_wr = ms.get("win_rate", 0)
        m_dd = ms.get("max_drawdown_pct", 0)
        m_pnl_icon = "💰" if m_pnl > 0 else "📉" if m_pnl < 0 else ""
        lines.append(f"  📈 月度历史  |  交易 {mt} 笔  |  累计 {m_pnl_icon} ${m_pnl:+.2f}  |  胜率 {m_wr}%  |  最大回撤 {m_dd:.2f}%")

        lines.append("=" * 60)
        logging.getLogger("main").info("\n".join(lines))

        # 写入状态 JSON 供 Streamlit 面板读取
        write_agent_status(
            agent1_status=s1,
            agent2_status=s2,
            agent3_status=s3,
            agent4_reviewer_status=s4,
            position_monitor_status=pm,
            mode=mode,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
