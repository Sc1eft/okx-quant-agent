"""Configuration editing components for OKX Quant Agent frontend."""

import streamlit as st
from dataclasses import fields
from typing import Dict, Any, Optional, Tuple, List
from config import (
    Config, ExchangeConfig, TradingConfig, StrategyConfig,
    RiskConfig, DataConfig, AgentConfig, NotificationConfig,
)

# 存储为小数比例（0.10 = 10%）、UI 按百分比数值展示/编辑的字段: name -> (min%, max%)
_PCT_FRACTION_FIELDS = {"max_position_pct": (5, 100), "max_single_order_pct": (1, 50)}


def render_config_section(
    section_name: str,
    dataclass_obj: Any,
    key_prefix: str = "",
) -> Tuple[bool, Any]:
    """Render a configuration dataclass as Streamlit form inputs.

    Returns (changed, new_object) where changed is True if user modified any value.
    """
    changed = False
    modified = {}

    section_labels = {
        "ExchangeConfig": "交易所设置",
        "TradingConfig": "交易设置",
        "StrategyConfig": "策略设置",
        "RiskConfig": "风控设置",
        "DataConfig": "数据设置",
        "AgentConfig": "Agent 设置",
        "NotificationConfig": "通知设置",
    }

    label = section_labels.get(type(dataclass_obj).__name__, section_name)

    with st.expander(label, expanded=False):
        for field in fields(dataclass_obj):
            field_name = field.name
            current_value = getattr(dataclass_obj, field_name)
            key = f"{key_prefix}_{field_name}" if key_prefix else field_name

            # Skip sensitive fields like API keys
            if field_name in ("api_key", "secret_key", "passphrase"):
                masked = str(current_value)[:4] + "****" if current_value else "(空)"
                st.text(f"{_friendly_name(field_name)}: {masked}")
                continue

            # Render based on type
            new_val = _render_field(field_name, current_value, field.type, key)
            if new_val is not None and new_val != current_value:
                modified[field_name] = new_val
                changed = True

    if changed:
        import dataclasses
        new_obj = dataclasses.replace(dataclass_obj, **modified)
        return True, new_obj

    return False, dataclass_obj


def _render_field(name: str, value: Any, field_type, key: str) -> Optional[Any]:
    """Render a single field based on its type."""
    friendly = _friendly_name(name)

    # Boolean
    if isinstance(value, bool):
        return st.checkbox(friendly, value=value, key=key)

    # String with choices
    if isinstance(value, str):
        # Known enum-like fields
        choices = _get_choices(name)
        if choices:
            idx = choices.index(value) if value in choices else 0
            selected = st.selectbox(friendly, choices, index=idx, key=key)
            return selected
        return st.text_input(friendly, value=value, key=key)

    # Integer
    if isinstance(value, int):
        min_v, max_v = _get_range(name, value)
        return st.number_input(friendly, min_value=min_v, max_value=max_v,
                               value=value, step=1, key=key)

    # Float
    if isinstance(value, float):
        # 仓位比例字段：存储为小数（0.10 = 10%），UI 按百分比数值展示/编辑
        if name in _PCT_FRACTION_FIELDS:
            lo, hi = _PCT_FRACTION_FIELDS[name]
            pct = st.number_input(friendly, min_value=float(lo), max_value=float(hi),
                                  value=round(value * 100, 2), step=1.0, format="%.1f", key=key)
            return pct / 100.0
        min_v, max_v = _get_range(name, value)
        step = 0.01 if max_v - min_v < 1 else 0.1
        return st.number_input(friendly, min_value=min_v, max_value=max_v,
                               value=value, step=step, format="%.2f", key=key)

    # List
    if isinstance(value, list):
        val_str = ", ".join(str(v) for v in value)
        st.text(f"{friendly}: [{val_str}]")
        return None

    return None


def _friendly_name(name: str) -> str:
    """Convert snake_case to Chinese label."""
    labels = {
        # Exchange
        "base_url": "API 地址", "demo_url": "模拟盘地址",
        "timeout_seconds": "超时(秒)", "retry_count": "重试次数",
        "permissions": "权限",
        # Trading
        "symbol": "交易对", "market": "市场",
        "timeframes": "K线周期", "primary_timeframe": "主周期",
        "default_order_type": "默认订单类型",
        "slippage_pct": "滑点(%)", "maker_fee": "Maker 费率(%)",
        "taker_fee": "Taker 费率(%)",
        # Strategy
        "enabled_strategies": "启用的策略", "strategy_weights": "策略权重",
        "short_window": "短均线周期", "long_window": "长均线周期",
        "rsi_period": "RSI 周期", "oversold": "超卖阈值",
        "overbought": "超买阈值", "period": "突破周期",
        "atr_multiplier": "ATR 倍数",
        "stop_loss_pct": "止损(%)", "take_profit_pct": "止盈(%)",
        "trailing_stop_activation": "追踪止损激活(%)",
        "trailing_stop_distance": "追踪止损距离(%)",
        "position_timeout_bars": "持仓超时(根)",
        # Risk
        "max_position_pct": "最大仓位(%)",
        "max_single_order_pct": "单笔最大(%)",
        "max_daily_loss_pct": "日最大亏损(%)",
        "max_consecutive_losses": "连续止损次数",
        "cooldown_bars": "冷却(根)", "signal_expiry_bars": "信号过期(根)",
        "recovery_mode": "恢复模式",
        "recovery_cooldown_bars": "恢复冷却(根)",
        "max_switches": "最大切换次数",
        "max_daily_restarts": "日最大重启次数",
        # Data
        "max_klines_per_request": "每请求K线数",
        # Agent
        "enabled": "启用", "model": "模型",
        "temperature": "温度", "max_tokens": "最大Token数",
        "base_url": "API 地址",
        # Notification
        "smtp_server": "SMTP 服务器", "smtp_port": "SMTP 端口",
        "smtp_user": "SMTP 用户", "smtp_password": "SMTP 密码",
        "smtp_from": "发件地址", "notify_to": "收件地址",
        "webhook_url": "Webhook 地址",
        "notify_on": "通知事件",
    }
    return labels.get(name, name.replace("_", " ").title())


def _get_choices(name: str) -> Optional[List[str]]:
    """Get dropdown choices for enum-like fields."""
    choices = {
        "market": ["spot", "futures"],
        "permissions": ["read", "trade"],
        "default_order_type": ["market", "limit"],
        "recovery_mode": ["manual", "auto_cool", "switch_strategy"],
        "timeframe": ["15m", "1h", "4h", "1d"],
    }
    return choices.get(name)


def _get_range(name: str, current_value) -> Tuple[float, float]:
    """Get min/max range for numeric fields."""
    ranges = {
        "short_window": (2, 100), "long_window": (5, 200),
        "rsi_period": (3, 50), "oversold": (10, 45), "overbought": (55, 90),
        "period": (3, 100), "atr_multiplier": (0.5, 5.0),
        "stop_loss_pct": (0.1, 15.0), "take_profit_pct": (0.5, 30.0),
        "trailing_stop_activation": (0.5, 10.0),
        "trailing_stop_distance": (0.1, 8.0),
        "position_timeout_bars": (6, 200),
        "max_position_pct": (5, 100), "max_single_order_pct": (1, 50),
        "max_daily_loss_pct": (0.5, 20), "max_consecutive_losses": (1, 10),
        "cooldown_bars": (0, 50), "signal_expiry_bars": (0, 10),
        "recovery_cooldown_bars": (0, 100), "max_switches": (0, 10),
        "max_daily_restarts": (1, 10),
        "slippage_pct": (0.0, 1.0), "maker_fee": (0.0, 0.5),
        "taker_fee": (0.0, 0.5),
        "temperature": (0.0, 1.0), "max_tokens": (100, 8000),
        "timeout_seconds": (5, 120), "retry_count": (0, 10),
        "max_klines_per_request": (50, 500),
    }
    default = ranges.get(name)
    if default:
        return default
    if isinstance(current_value, int):
        return (0, 9999)
    if isinstance(current_value, float):
        return (0.0, 10000.0)
    return (0, 100)
