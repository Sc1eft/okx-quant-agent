"""
OKX 量化交易系统 — 配置管理
支持环境变量覆盖，YAML 配置文件
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional

# ──────────────────────────────────────────────
# 合约配置
# ──────────────────────────────────────────────

@dataclass
class FuturesConfig:
    """合约（交割/永续）参数"""
    leverage: int = 10
    margin_mode: Literal["isolated", "cross"] = "isolated"
    maintenance_margin_ratio: float = 0.005  # 维持保证金率 0.5%（≤10x）

    def __post_init__(self):
        if self.leverage < 1:
            raise ValueError("杠杆倍数不能小于 1")
        if self.margin_mode not in ("isolated", "cross"):
            raise ValueError("保证金模式必须是 isolated 或 cross")


# ──────────────────────────────────────────────
# 交易所配置
# ──────────────────────────────────────────────

@dataclass
class ExchangeConfig:
    """OKX 交易所连接配置"""
    api_key: str = ""
    secret_key: str = ""
    passphrase: str = ""
    # API 权限（永远不开提现）
    permissions: Literal["read", "trade"] = "read"
    base_url: str = "https://www.okx.com"
    demo_url: str = "https://www.okx.com"  # 模拟盘
    timeout_seconds: int = 30
    retry_count: int = 3

    @property
    def is_readonly(self) -> bool:
        return self.permissions == "read"


# ──────────────────────────────────────────────
# 交易配置
# ──────────────────────────────────────────────

@dataclass
class TradingConfig:
    """交易参数"""
    symbol: str = "ETH-USDT"
    market: Literal["spot", "futures"] = "spot"
    timeframes: list[str] = field(default_factory=lambda: ["15m", "1h"])
    primary_timeframe: str = "1h"
    # 订单类型偏好
    default_order_type: Literal["market", "limit"] = "market"
    slippage_pct: float = 0.05  # 滑点百分比（回测用）
    maker_fee: float = 0.0008   # 挂单费率 0.08%
    taker_fee: float = 0.0010   # 吃单费率 0.10%


# ──────────────────────────────────────────────
# 策略配置
# ──────────────────────────────────────────────

@dataclass
class StrategyConfig:
    """策略池配置（多个策略并行）"""
    # 启用的策略列表
    enabled_strategies: list[str] = field(
        default_factory=lambda: ["ma_cross", "rsi_mean_reversion", "breakout"]
    )
    # 策略权重（信号投票用）
    strategy_weights: dict[str, float] = field(
        default_factory=lambda: {"ma_cross": 0.4, "rsi_mean_reversion": 0.3, "breakout": 0.3}
    )
    # MA 交叉
    ma_short_window: int = 7
    ma_long_window: int = 25
    # RSI
    rsi_period: int = 14
    rsi_oversold: int = 30
    rsi_overbought: int = 70
    # 突破
    breakout_period: int = 20
    breakout_atr_multiplier: float = 2.0

    # ── 止盈止损（激进模式 2026-07-10） ──
    stop_loss_pct: float = 5.0        # 止损百分比
    take_profit_pct: float = 10.0     # 止盈百分比
    trailing_stop_activation: float = 6.0  # 浮盈达到此比例后激活移动止损
    trailing_stop_distance: float = 3.0    # 移动止损距离
    position_timeout_bars: int = 72   # 持仓超过 72 根 K 线自动退出


# ──────────────────────────────────────────────
# 风控配置
# ──────────────────────────────────────────────

@dataclass
class RiskConfig:
    """风控参数"""
    max_position_pct: float = 0.50       # 最大持仓 50%
    max_single_order_pct: float = 0.10   # 单笔最大 10%
    max_daily_loss_pct: float = 2.0      # 单日最大亏损 2%
    max_consecutive_losses: int = 3      # 连续亏损暂停
    cooldown_bars: int = 4               # 亏损后冷却 K 线数
    signal_expiry_bars: int = 1          # 信号过期（根 K 线）

    # ── 恢复策略（P3 优化） ──
    recovery_mode: Literal["manual", "auto_cool", "switch_strategy"] = "auto_cool"
    recovery_cooldown_bars: int = 24     # 恢复冷却：24 根 K 线后才可重启
    recovery_switch_threshold: int = 2   # 连续 2 次暂停后自动切换策略
    max_daily_starts: int = 3            # 每天最大重启次数


# ──────────────────────────────────────────────
# 数据配置
# ──────────────────────────────────────────────

@dataclass
class DataConfig:
    """数据存储配置"""
    db_path: str = "data/market_data.db"
    kline_table: str = "klines"
    ticker_table: str = "tickers"
    max_klines_per_request: int = 100
    # 数据质量
    max_price_deviation_std: float = 5.0  # 超过 5σ 视为异常
    max_kline_gap_minutes: int = 10        # K 线缺失检测阈值


# ──────────────────────────────────────────────
# Agent 配置
# ──────────────────────────────────────────────

@dataclass
class AgentConfig:
    """DeepSeek Agent 配置"""
    enabled: bool = True
    api_key: str = ""
    model: str = "deepseek-v4-pro"
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.3
    # Agent 范围：第一版只做回测后分析
    modes: list[str] = field(
        default_factory=lambda: ["backtest_report"]
    )
    max_tokens: int = 2000

    def __post_init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", self.api_key)


# ──────────────────────────────────────────────
# 通知配置
# ──────────────────────────────────────────────

@dataclass
class NotificationConfig:
    """通知配置（P2 优化）"""
    enabled: bool = False
    # 邮件
    email_enabled: bool = False
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    notify_email: str = ""
    # Webhook
    webhook_enabled: bool = False
    webhook_url: str = ""
    # 通知事件
    notify_on: list[str] = field(
        default_factory=lambda: ["signal", "trade", "error", "daily_report"]
    )


# ──────────────────────────────────────────────
# 总配置
# ──────────────────────────────────────────────

@dataclass
class Config:
    """系统总配置"""
    mode: Literal["backtest", "paper", "demo", "live"] = "backtest"
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    data: DataConfig = field(default_factory=DataConfig)
    futures: FuturesConfig = field(default_factory=FuturesConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    log_dir: str = "logs"

    def __post_init__(self):
        self.exchange.api_key = os.getenv("OKX_API_KEY", self.exchange.api_key)
        self.exchange.secret_key = os.getenv("OKX_SECRET_KEY", self.exchange.secret_key)
        self.exchange.passphrase = os.getenv("OKX_PASSPHRASE", self.exchange.passphrase)

    def save(self, path: str):
        """保存配置到 JSON"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "Config":
        """从 JSON 文件加载配置"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k) and isinstance(getattr(cfg, k), object):
                sub = getattr(cfg, k)
                if hasattr(sub, "__dataclass_fields__"):
                    for sk, sv in v.items():
                        if hasattr(sub, sk):
                            setattr(sub, sk, sv)
                else:
                    setattr(cfg, k, v)
            else:
                setattr(cfg, k, v)
        # __post_init__ 中可能触发校验
        cfg.futures.__post_init__()
        # 环境变量覆盖配置文件中对应的值（最高优先级）
        for _env_key, _cfg_path in [
            ("OKX_API_KEY", ("exchange", "api_key")),
            ("OKX_SECRET_KEY", ("exchange", "secret_key")),
            ("OKX_PASSPHRASE", ("exchange", "passphrase")),
            ("DEEPSEEK_API_KEY", ("agent", "api_key")),
        ]:
            _val = os.getenv(_env_key)
            if _val:
                _sub = getattr(cfg, _cfg_path[0])
                setattr(_sub, _cfg_path[1], _val)
        return cfg

    @property
    def db_path(self) -> str:
        return str(Path(self.data.db_path).resolve())

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


# ──────────────────────────────────────────────
# 默认配置实例
# ──────────────────────────────────────────────

DEFAULT_CONFIG = Config()

# 可通过 configs/default.json 覆盖
CONFIG_PATH = "configs/default.json"


def _test_futures_config() -> None:
    """快速验证合约配置的 __post_init__ 校验"""
    _ = FuturesConfig(leverage=10, margin_mode="isolated")
    try:
        FuturesConfig(leverage=-1)
    except ValueError:
        pass
    else:
        raise AssertionError("负杠杆未触发校验")
if Path(CONFIG_PATH).exists():
    DEFAULT_CONFIG = Config.load(CONFIG_PATH)
