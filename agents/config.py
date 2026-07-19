"""
Agent 系统配置 — 三 Agent 的独立参数
继承根 Config 中的已有配置，补充 Agent 专用参数
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from config import Config


@dataclass
class AgentSystemConfig:
    """三 Agent 系统配置（与根 config.py 互补）

    共享字段（与 root Config 重叠）统一通过 from_root_config() 填充，
    不要手动同步，避免遗漏。
    """

    # ── Agent 1: Technical Analyst ──
    agent1_enabled: bool = True
    agent1_timeframes: list[str] = field(default_factory=lambda: ["15m", "1h", "1d"])
    agent1_change_cooldown_seconds: float = 60.0  # 同一信号的最小推送间隔
    # 各周期触发指标计算所需最小 K 线数（数据不足时不计算，避免无效信号）
    agent1_min_bars: dict[str, int] = field(
        default_factory=lambda: {"3m": 5, "5m": 5, "15m": 8, "1h": 10, "1d": 20}
    )
    # 信号生成冷却时间（秒），按信号类型和周期
    # 格式: { "signal_type": { "tf": seconds, "*": default } }
    # 覆盖 change_detector.py 中的默认冷却配置
    agent1_signal_cooldowns: dict = field(
        default_factory=lambda: {
            "kdj_*": {"3m": 300, "5m": 180, "15m": 120, "1h": 60, "1d": 30},
            "macd_*": {"3m": 180, "5m": 120, "15m": 90, "1h": 60, "1d": 30},
            "boll_*": {"3m": 180, "5m": 120, "15m": 90, "1h": 60, "1d": 30},
        }
    )

    # ── Agent 2: News Collector ──
    agent2_enabled: bool = True
    agent2_fetch_interval_seconds: int = 60  # RSS 抓取间隔
    agent2_max_news_per_fetch: int = 5
    agent2_min_weight_threshold: float = 0.3  # 低于此权重不推送

    # ── Agent 3: Trader ──
    agent3_enabled: bool = True
    agent3_debounce_seconds: float = 60.0  # 事件缓冲窗口（原30s → 60s，减少决策频率）
    agent3_min_interval_between_trades: int = 600  # 最小交易间隔：10分钟（原5min，减少信号翻转）
    agent3_min_holding_time_seconds: int = 120  # 最小持仓时间：120秒内反转不记录平仓（防零持仓刷单）
    agent3_max_daily_trades: int = 20  # 每日最大交易次数（实盘建议 10-20）
    agent3_max_daily_loss_usdt: float = 100.0
    agent3_max_consecutive_losses: int = 3
    agent3_max_position_eth: float = 0.5  # 单笔最大 0.5 ETH
    agent3_min_position_for_loss_tracking: float = 0.1  # 低于此仓位的亏损不计入日亏损警戒（试探仓不触发风控）
    agent3_idle_decision_interval_seconds: int = 900  # 空闲定时决策：15分钟（原 600）
    agent3_idle_decision_price_change_pct: float = 0.5  # 空闲决策触发所需的最小价格变化 %

    # ── 规则决策器（RuleDecider，替代 DeepSeek 实时决策）──
    # 2026-07 参数扫描（OKX ETH 1h）：mc 0.6→0.8 落入收益高原区（与 th=0.4 等效），
    # 过滤低一致性共振信号，样本内 9→7 笔、胜率 33%→43%、收益由负转正
    agent3_rule_score_threshold: float = 0.3   # 综合评分超过 ±此值才交易
    agent3_rule_min_confidence: float = 0.8    # 方向一致性信心下限（0~1）
    agent3_rule_base_position_pct: float = 50.0  # 基础仓位 %（按信心缩放，钳位 5~100）

    # ── LLM 影子决策（D12：与规则决策并行记录对比，不参与执行）──
    llm_shadow_enabled: bool = False
    llm_shadow_min_interval_s: int = 300  # 影子调用最小间隔（成本控制）

    # ── 手续费率（OKX 标准费率）──
    # Spot: maker 0.08%, taker 0.10%（ETH-USDT Group 1）
    taker_fee_rate: float = 0.001    # 0.10%
    maker_fee_rate: float = 0.0008   # 0.08%

    # ── HFT 防护：交易频率上限 ──
    max_trades_per_hour: int = 4     # 每小时最多 4 笔有效交易

    # ── Phase 3: 链上数据监控 ──
    agent2_onchain_enabled: bool = True
    agent2_onchain_interval_seconds: int = 300  # 链上数据抓取间隔（5分钟）

    # Gas 费（Etherscan API，需 ETHERSCAN_API_KEY 环境变量）
    agent2_gas_enabled: bool = True
    agent2_gas_high_threshold_gwei: float = 100.0  # >100 Gwei = 高
    agent2_gas_extreme_threshold_gwei: float = 200.0  # >200 Gwei = 极高
    agent2_etherscan_api_key: str = ""  # 从 ETHERSCAN_API_KEY 环境变量读取

    # Whale Alert（需 WHALE_ALERT_API_KEY 环境变量）
    agent2_whale_enabled: bool = True
    agent2_whale_min_value_usdt: float = 1_000_000.0  # $1M 以上视为巨鲸
    agent2_whale_alert_api_key: str = ""  # 从 WHALE_ALERT_API_KEY 环境变量读取

    # 吃单比
    agent2_taker_volume_enabled: bool = True
    agent2_taker_volume_buy_ratio_threshold: float = 0.6  # 买占比 >60% 视为偏多

    # 资金费率
    agent2_funding_rate_enabled: bool = True
    agent2_funding_rate_high_threshold: float = 0.01  # 高费率信号阈值（百分数：0.01 = 0.01%）

    # OI 持仓量（OKX 永续，事件触发层）
    agent2_oi_enabled: bool = True
    agent2_oi_min_change_pct: float = 0.5  # |ΔOI%| 低于此值视为噪声，不入分布不触发

    # 事件触发层：历史分位极端检测（资金费率/吃单比/OI 共用）
    agent2_percentile_enabled: bool = True
    agent2_percentile_window: int = 2016       # 300s × 2016 ≈ 7 天滚动窗口
    agent2_percentile_min_samples: int = 200   # ≈ 17h 预热，不足不判极端
    agent2_percentile_upper: float = 0.95
    agent2_percentile_lower: float = 0.05
    agent2_percentile_state_path: str = "data/onchain_percentiles.json"

    # ── WebSocket ──
    ws_symbol: str = "ETH-USDT"
    ws_channel: str = "tickers"  # 订阅频道
    ws_reconnect_delay_base: float = 1.0
    ws_reconnect_delay_max: float = 60.0

    # ── SQLite ──
    db_path: str = "data/agent_trades.db"

    # ── Phase 2: Risk Hardening ──
    # 波动检查（交易品种 15m K 线）
    volatility_threshold_pct: float = 3.0
    volatility_delay_seconds: int = 300

    # 市场深度
    market_depth_spread_bps: float = 10.0       # 买卖价差阈值（基点）
    market_depth_min_liquidity_eth: float = 1.0  # 最小深度（ETH）

    # 交易执行
    limit_order_timeout_seconds: int = 10        # 限价单等待超时
    max_slippage_pct: float = 0.3                # 最大滑点百分比
    partial_fill_timeout_seconds: int = 10       # 部分成交等待超时

    # 持仓监控
    position_monitor_interval: float = 5.0       # 持仓检查间隔（秒）
    trailing_stop_activation_pct: float = 6.0    # 浮盈激活移动止损
    trailing_stop_distance_pct: float = 3.0      # 移动止损距离

    # ── Phase 4: 自学习 + 信号对齐 ──

    # ConfidenceScorer（多周期信心分）
    confidence_scorer_enabled: bool = True
    confidence_timeframe_weights: dict = field(
        default_factory=lambda: {
            "3m": 0.05, "5m": 0.10, "15m": 0.25, "1h": 0.40, "1d": 0.20,
        }
    )
    confidence_signal_directions: dict = field(
        default_factory=lambda: {
            # ── MACD（强信号）──
            "macd_bullish_cross": 0.8,
            "macd_bearish_cross": -0.8,
            "macd_hist_positive": 0.5,
            "macd_hist_negative": -0.5,
            # 移除 hist_momentum_up/down（±0.2 太噪，无实际价值）

            # ── KDJ（中等信号，仅在 >=15m 生效）──
            "kdj_bullish_cross": 0.55,
            "kdj_bearish_cross": -0.55,
            "kdj_overbought": -0.3,   # 超买 = 看空（逆向）
            "kdj_oversold": 0.3,      # 超卖 = 看多（逆向）
            # 移除 kdj_k_above/below_50（纯噪音）

            # ── 布林（强信号，提升权重）──
            "boll_break_upper": 0.6,   # 原 0.3，提升至 0.6
            "boll_break_lower": -0.6,  # 原 -0.3，提升至 -0.6
            "boll_expansion": 0.0,     # 仅提示变盘可能，无方向倾向
            "boll_bandwidth_expanding": 0.0,  # 仅提示市场可能启动
            # 移除 boll_upper/lower_approach（太噪）
        }
    )

    # ReviewGenerator（复盘报告）
    review_generator_enabled: bool = True
    review_daily_hour_utc: int = 16  # 00:00 CST
    review_report_dir: str = "data/reviews"
    review_report_min_trades: int = 5

    # ── 交易报告 + ServerChan 推送 ──
    report_enabled: bool = True
    report_dir: str = "data/reports"
    report_min_trades_for_analysis: int = 1  # 最少几笔交易才做 AI 分析
    serverchan_enabled: bool = False
    serverchan_sendkey: str = field(
        default_factory=lambda: os.getenv("SERVERCHAN_SENDKEY", "")
    )

    # ── Agent 1（新增可调参数，原写死在 change_detector.py）──
    agent1_change_cooldown: float = 60.0

    # ── Agent 3（新增，供 Agent 4 调整）──
    agent3_position_size_multiplier: float = 1.0
    agent3_default_stop_loss_pct: float = 5.0
    agent3_default_take_profit_pct: float = 10.0

    # ── Agent 4 ──
    agent4_enabled: bool = True
    agent4_review_interval_trades: int = 5
    agent4_min_adjust_interval_seconds: int = 300
    agent4_deepseek_model: str = "deepseek-v4-pro"
    agent4_max_param_adjustments: int = 5

    # SignalAligner（信号对齐）
    signal_aligner_enabled: bool = True
    signal_aligner_conflict_threshold: float = 0.5

    # ── 市场模式（决定 TradeExecutor 使用现货还是合约模拟）──
    market_mode: str = "futures"  # "spot" | "futures"
    futures_leverage: int = 10  # 1x-125x

    # ── 合约费率（OKX USDT 本位永续合约标准费率）──
    # Futures: maker 0.02%, taker 0.05%（ETH-USDT 永续）
    futures_taker_fee_rate: float = 0.0005   # 0.05%
    futures_maker_fee_rate: float = 0.0002   # 0.02%

    # ── 交易所权限（决定 TradeExecutor 是否模拟成交）──
    exchange_permissions: str = "read"  # "read" → 模拟成交, "trade" → 调真实 API

    # ── Logging ──
    log_level: str = "INFO"
    log_file: str = "logs/agent_system.log"

    # ── 从根配置初始化共享字段 ──

    @classmethod
    def from_root_config(cls, root: "Config") -> "AgentSystemConfig":
        """从根 Config 创建 AgentSystemConfig，自动填充共享字段。

        使用方式:
            agent_config = AgentSystemConfig.from_root_config(root_config)
            agent_config.agent2_etherscan_api_key = os.getenv("ETHERSCAN_API_KEY", "")

        共享字段（与 root Config 重叠，自动同步）：
        - market_mode      ← root.trading.market
        - futures_leverage  ← root.futures.leverage
        - exchange_permissions ← root.exchange.permissions
        - taker_fee_rate    ← root.trading.taker_fee
        - maker_fee_rate    ← root.trading.maker_fee
        """
        cfg = cls()
        cfg.market_mode = root.trading.market
        cfg.futures_leverage = root.futures.leverage
        cfg.exchange_permissions = root.exchange.permissions
        # 费率：注意根 Config 用的是 taker_fee / maker_fee 命名
        cfg.taker_fee_rate = root.trading.taker_fee
        cfg.maker_fee_rate = root.trading.maker_fee
        # 止盈止损也跟随策略配置
        cfg.agent3_default_stop_loss_pct = root.strategy.stop_loss_pct
        cfg.agent3_default_take_profit_pct = root.strategy.take_profit_pct
        cfg.trailing_stop_activation_pct = root.strategy.trailing_stop_activation
        cfg.trailing_stop_distance_pct = root.strategy.trailing_stop_distance
        return cfg
