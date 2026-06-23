"""
Agent 系统配置 — 三 Agent 的独立参数
继承根 Config 中的已有配置，补充 Agent 专用参数
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentSystemConfig:
    """三 Agent 系统配置（与根 config.py 互补）"""

    # ── Agent 1: Technical Analyst ──
    agent1_enabled: bool = True
    agent1_timeframes: list[str] = field(default_factory=lambda: ["15m", "1h", "1d"])
    agent1_change_cooldown_seconds: float = 60.0  # 同一信号的最小推送间隔

    # ── Agent 2: News Collector ──
    agent2_enabled: bool = True
    agent2_fetch_interval_seconds: int = 60  # RSS 抓取间隔
    agent2_max_news_per_fetch: int = 5
    agent2_min_weight_threshold: float = 0.3  # 低于此权重不推送

    # ── Agent 3: Trader ──
    agent3_enabled: bool = True
    agent3_debounce_seconds: float = 30.0  # 事件缓冲窗口
    agent3_min_interval_between_trades: int = 300  # 最小交易间隔：5分钟
    agent3_max_daily_trades: int = 10
    agent3_max_daily_loss_usdt: float = 100.0
    agent3_max_consecutive_losses: int = 3
    agent3_max_position_eth: float = 0.5  # 单笔最大 0.5 ETH

    # ── WebSocket ──
    ws_symbol: str = "ETH-USDT"
    ws_channel: str = "tickers"  # 订阅频道
    ws_reconnect_delay_base: float = 1.0
    ws_reconnect_delay_max: float = 60.0

    # ── SQLite ──
    db_path: str = "data/agent_trades.db"

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

    # ── Logging ──
    log_level: str = "INFO"
    log_file: str = "logs/agent_system.log"
