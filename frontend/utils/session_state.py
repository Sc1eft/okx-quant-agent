"""Streamlit session_state management for the OKX Quant Agent."""

import sys
import os
from pathlib import Path
from typing import Optional
import streamlit as st

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config, StrategyConfig


def init_config() -> Config:
    """Load and cache Config in session_state."""
    if "config" not in st.session_state:
        cfg_path = PROJECT_ROOT / "configs" / "default.json"
        cfg = Config()
        if cfg_path.exists():
            cfg = Config.load(str(cfg_path))
        st.session_state.config = cfg
    return st.session_state.config


def get_config() -> Config:
    """Get the cached Config."""
    return init_config()


def update_config(cfg: Config) -> None:
    """Update config in session_state and optionally save to file."""
    st.session_state.config = cfg


def save_config() -> None:
    """Save current config to default.json."""
    cfg = get_config()
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.save(str(cfg_path))


def init_backtest_results() -> None:
    """Initialize backtest results storage in session_state."""
    if "backtest_results" not in st.session_state:
        st.session_state.backtest_results = {}
    if "last_backtest_strategy" not in st.session_state:
        st.session_state.last_backtest_strategy = None
    if "comparison_results" not in st.session_state:
        st.session_state.comparison_results = {}


def get_backtest_result(strategy_name: str):
    """Get cached backtest result for a strategy."""
    init_backtest_results()
    return st.session_state.backtest_results.get(strategy_name)


def set_backtest_result(strategy_name: str, result) -> None:
    """Cache a backtest result."""
    init_backtest_results()
    st.session_state.backtest_results[strategy_name] = result
    st.session_state.last_backtest_strategy = strategy_name


def init_walkforward_results() -> None:
    """Initialize walk-forward results storage."""
    if "wf_results" not in st.session_state:
        st.session_state.wf_results = {}
    if "param_sweep_results" not in st.session_state:
        st.session_state.param_sweep_results = {}
    if "oos_results" not in st.session_state:
        st.session_state.oos_results = {}


def init_agent_state() -> None:
    """Initialize agent analysis state."""
    if "agent_analysis" not in st.session_state:
        st.session_state.agent_analysis = {}


def init_risk_state() -> None:
    """Initialize risk engine state (in-memory)."""
    if "risk_engine" not in st.session_state:
        st.session_state.risk_engine = None
    if "risk_paused" not in st.session_state:
        st.session_state.risk_paused = False
    if "risk_pause_reason" not in st.session_state:
        st.session_state.risk_pause_reason = ""


def init_paper_state() -> None:
    """Initialize paper trading state."""
    if "paper_running" not in st.session_state:
        st.session_state.paper_running = False
    if "paper_engine" not in st.session_state:
        st.session_state.paper_engine = None
    if "paper_strategy" not in st.session_state:
        st.session_state.paper_strategy = ""
    if "paper_strategy_instance" not in st.session_state:
        st.session_state.paper_strategy_instance = None
    if "paper_state" not in st.session_state:
        st.session_state.paper_state = None
    if "paper_data" not in st.session_state:
        st.session_state.paper_data = None
    if "paper_refresh_counter" not in st.session_state:
        st.session_state.paper_refresh_counter = 0


def init_eth_state() -> None:
    """Initialize Ethereum live data state."""
    if "eth_running" not in st.session_state:
        st.session_state.eth_running = False
    if "eth_data" not in st.session_state:
        st.session_state.eth_data = None
    if "eth_ticker" not in st.session_state:
        st.session_state.eth_ticker = None
    if "eth_timeframe" not in st.session_state:
        st.session_state.eth_timeframe = "1h"
    if "eth_data_count" not in st.session_state:
        st.session_state.eth_data_count = 100
    if "eth_last_refresh" not in st.session_state:
        st.session_state.eth_last_refresh = None
    if "eth_auto_refresh" not in st.session_state:
        st.session_state.eth_auto_refresh = True
    if "eth_refresh_counter" not in st.session_state:
        st.session_state.eth_refresh_counter = 0


def init_all() -> None:
    """Initialize all session state keys."""
    init_config()
    init_backtest_results()
    init_walkforward_results()
    init_agent_state()
    init_risk_state()
    init_paper_state()
    init_eth_state()
