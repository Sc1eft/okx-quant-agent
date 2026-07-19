"""Strategies page - view, configure, and tune strategy parameters."""

import sys
from pathlib import Path
from dataclasses import replace

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.utils.session_state import get_config, save_config
from frontend.components.config_editor import render_config_section
from strategies.base import get_available_strategies
from config import Config


st.title("⚙ 策略配置")
st.markdown("查看和调整交易策略参数")


cfg = get_config()

# ============ Strategy Info ============
st.subheader("📖 可用策略")

strategies = get_available_strategies()
if not strategies:
    st.warning("无可用的策略")
    st.stop()

# Strategy descriptions
descriptions = {
    "ma_cross": {
        "name": "MA 均线交叉",
        "desc": "短周期均线上穿长周期均线时买入，下穿时卖出。趋势跟踪策略，适合有明显趋势的市场。",
        "params_desc": {
            "short_window": "短均线周期 (越小越敏感)",
            "long_window": "长均线周期 (越大越平滑)",
        },
    },
    "rsi_mean_reversion": {
        "name": "RSI 均值回归",
        "desc": "RSI 从超卖区(<30)回升时买入，从超买区(>70)回落时卖出。震荡市表现较好。",
        "params_desc": {
            "rsi_period": "RSI 计算周期",
            "oversold": "超卖阈值 (越低越不容易触发)",
            "overbought": "超买阈值 (越高越不容易触发)",
        },
    },
    "breakout": {
        "name": "突破策略",
        "desc": "价格突破 N 周期高点买入，跌破 N 周期低点卖出。ATR 动态止损跟随波动率调整。",
        "params_desc": {
            "period": "突破周期 (越大过滤越多假突破)",
            "atr_multiplier": "ATR 止损倍数 (越大止损越宽)",
        },
    },
}

for strategy_name, info in strategies.items():
    desc = descriptions.get(strategy_name, {})
    with st.expander(f"{desc.get('name', strategy_name)} — {desc.get('desc', '')}", expanded=True):
        default_params = info.get("default_params", {})

        # Show parameter descriptions
        params_desc = desc.get("params_desc", {})
        if params_desc:
            for k, v in params_desc.items():
                if k in default_params:
                    st.markdown(f"- **{k}** ({default_params[k]}): {v}")

        st.markdown(f"**默认参数:** {default_params}")
        st.markdown(f"**数据需求:** {info.get('data_requirements', 'OHLCV')}")


# ============ Strategy Enable/Disable ============
st.subheader("🔌 启用策略")

enabled = list(cfg.strategy.enabled_strategies)
weights = dict(cfg.strategy.strategy_weights) if cfg.strategy.strategy_weights else {}

enabled_cols = st.columns(len(strategies))
all_strategy_names = list(strategies.keys())

weight_changed = False
new_enabled = []

for i, name in enumerate(all_strategy_names):
    with enabled_cols[i]:
        is_on = st.checkbox(name, value=name in enabled, key=f"enable_{name}")
        if is_on:
            new_enabled.append(name)
            w = st.slider(
                f"{name} 权重",
                0.0, 1.0,
                value=weights.get(name, 1.0 / max(len(enabled), 1)),
                key=f"weight_{name}",
            )
            if w != weights.get(name):
                weights[name] = w
                weight_changed = True
        elif name in weights:
            del weights[name]

if new_enabled != enabled or weight_changed:
    # Normalize weights
    if weights and sum(weights.values()) > 0:
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}
    cfg.strategy.enabled_strategies = new_enabled
    cfg.strategy.strategy_weights = weights

    from frontend.utils.session_state import update_config
    update_config(cfg)


# ============ Parameter Editor ============
st.subheader("🔧 策略参数调整")

# Make editable copies of strategy params
editable = {
    "ma_cross": {
        "short_window": cfg.strategy.ma_short_window,
        "long_window": cfg.strategy.ma_long_window,
        "stop_loss_pct": cfg.strategy.stop_loss_pct,
        "take_profit_pct": cfg.strategy.take_profit_pct,
        "trailing_stop_activation": cfg.strategy.trailing_stop_activation,
        "trailing_stop_distance": cfg.strategy.trailing_stop_distance,
        "position_timeout_bars": cfg.strategy.position_timeout_bars,
    },
    "rsi_mean_reversion": {
        "rsi_period": cfg.strategy.rsi_period,
        "oversold": cfg.strategy.rsi_oversold,
        "overbought": cfg.strategy.rsi_overbought,
        "stop_loss_pct": cfg.strategy.stop_loss_pct,
        "take_profit_pct": cfg.strategy.take_profit_pct,
        "trailing_stop_activation": cfg.strategy.trailing_stop_activation,
        "trailing_stop_distance": cfg.strategy.trailing_stop_distance,
        "position_timeout_bars": cfg.strategy.position_timeout_bars,
    },
    "breakout": {
        "period": cfg.strategy.breakout_period,
        "atr_multiplier": cfg.strategy.breakout_atr_multiplier,
        "stop_loss_pct": cfg.strategy.stop_loss_pct,
        "take_profit_pct": cfg.strategy.take_profit_pct,
        "trailing_stop_activation": cfg.strategy.trailing_stop_activation,
        "trailing_stop_distance": cfg.strategy.trailing_stop_distance,
        "position_timeout_bars": cfg.strategy.position_timeout_bars,
    },
}

# Ranges for params
ranges = {
    "short_window": (2, 100), "long_window": (5, 200),
    "rsi_period": (3, 50), "oversold": (10, 45), "overbought": (55, 90),
    "period": (3, 100), "atr_multiplier": (0.5, 5.0),
    "stop_loss_pct": (0.1, 15.0), "take_profit_pct": (0.5, 30.0),
    "trailing_stop_activation": (0.5, 10.0),
    "trailing_stop_distance": (0.1, 8.0),
    "position_timeout_bars": (6, 200),
}

param_changed = False
for strategy_name in all_strategy_names:
    if strategy_name not in editable:
        continue

    with st.expander(strategy_name, expanded=False):
        params = editable[strategy_name]
        new_vals = {}
        for param_name, current_val in params.items():
            min_v, max_v = ranges.get(param_name, (0, 999))
            step = 1 if isinstance(current_val, int) else 0.1
            fmt = "%d" if isinstance(current_val, int) else "%.1f"

            new_val = st.number_input(
                param_name,
                min_value=min_v, max_value=max_v,
                value=current_val, step=step, format=fmt,
                key=f"{strategy_name}_{param_name}",
            )
            if new_val != current_val:
                new_vals[param_name] = new_val

        if new_vals:
            param_changed = True
            for k, v in new_vals.items():
                setattr(cfg.strategy, k, v)

            from frontend.utils.session_state import update_config
            update_config(cfg)

            st.success(f"{strategy_name} 参数已更新!")


# ============ Save Button ============
st.divider()
save_cols = st.columns([1, 1, 3])
with save_cols[0]:
    if st.button("💾 保存配置", type="primary", use_container_width=True):
        save_config()
        st.success("配置已保存到 configs/default.json!")
with save_cols[1]:
    if st.button("↩ 恢复默认", use_container_width=True):
        default_cfg = Config()
        from frontend.utils.session_state import update_config
        update_config(default_cfg)
        st.success("已恢复默认配置!")
        st.rerun()
