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

    # ── Phase 3: 链上数据监控 ──
    agent2_onchain_enabled: bool = True
    agent2_onchain_interval_seconds: int = 300  # 链上数据抓取间隔（5分钟）

    # Gas 费
    agent2_gas_enabled: bool = True
    agent2_gas_high_threshold_gwei: float = 100.0  # >100 Gwei = 高
    agent2_gas_extreme_threshold_gwei: float = 200.0  # >200 Gwei = 极高

    # Whale Alert
    agent2_whale_enabled: bool = True
    agent2_whale_min_value_usdt: float = 1_000_000.0  # $1M 以上视为巨鲸
    agent2_whale_alert_api_key: str = ""  # whale-alert.io API key（可选）

    # 吃单比
    agent2_taker_volume_enabled: bool = True
    agent2_taker_volume_buy_ratio_threshold: float = 0.6  # 买占比 >60% 视为偏多

    # 资金费率
    agent2_funding_rate_enabled: bool = True
    agent2_funding_rate_high_threshold: float = 0.01  # 0.01% = 高费率信号

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

    # ── Phase 4: 自学习 + 信号对齐 ──

    # ConfidenceScorer（多周期信心分）
    confidence_scorer_enabled: bool = True
    confidence_timeframe_weights: dict = field(
        default_factory=lambda: {"15m": 0.3, "1h": 0.5, "1d": 0.7}
    )
    confidence_signal_directions: dict = field(
        default_factory=lambda: {
            "macd_bullish_cross": 0.8,
            "macd_bearish_cross": -0.8,
            "macd_hist_positive": 0.5,
            "macd_hist_negative": -0.5,
            "kdj_bullish_cross": 0.6,
            "kdj_bearish_cross": -0.6,
            "kdj_overbought": -0.4,
            "kdj_oversold": 0.4,
            "boll_break_upper": 0.3,
            "boll_break_lower": -0.3,
            "boll_squeeze": 0.0,
        }
    )

    # ReviewGenerator（复盘报告）
    review_generator_enabled: bool = True
    review_daily_hour_utc: int = 16  # 00:00 CST
    review_report_dir: str = "data/reviews"
    review_report_min_trades: int = 5

    # ParamAdapter（参数自适应）
    param_adapter_enabled: bool = True
    param_adapter_min_trades_for_adjust: int = 10
    param_adapter_adjust_interval_hours: int = 24
    param_adapter_max_trades_range: list = field(default_factory=lambda: [5, 20])
    param_adapter_win_rate_target: float = 0.50

    # ── Agent 1（新增可调参数，原写死在 change_detector.py）──
    agent1_change_cooldown: float = 60.0

    # ── Agent 3（新增，供 Agent 4 调整）──
    agent3_position_size_multiplier: float = 1.0
    agent3_default_stop_loss_pct: float = 2.0
    agent3_default_take_profit_pct: float = 4.0

    # ── Agent 4 ──
    agent4_enabled: bool = True
    agent4_review_interval_trades: int = 5
    agent4_min_adjust_interval_seconds: int = 300
    agent4_deepseek_model: str = "deepseek-v4-pro"
    agent4_max_param_adjustments: int = 5

    # SignalAligner（信号对齐）
    signal_aligner_enabled: bool = True
    signal_aligner_conflict_threshold: float = 0.5

    # ── Logging ──
    log_level: str = "INFO"
    log_file: str = "logs/agent_system.log"
