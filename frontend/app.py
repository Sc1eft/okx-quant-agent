"""OKX Quant Agent - Streamlit Frontend Main Entry Point.

Run with: streamlit run app.py
"""

import sys
from pathlib import Path

import streamlit as st

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from frontend.utils.session_state import init_all, get_config

# Page config must be the first Streamlit command
st.set_page_config(
    page_title="OKX Quant Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Load custom CSS
css_path = Path(__file__).parent / "assets" / "style.css"
if css_path.exists():
    with open(css_path, encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# ── Viewport meta tag for mobile responsiveness ──
st.markdown(
    "<meta name='viewport' content='width=device-width, initial-scale=1.0, maximum-scale=1.0'>",
    unsafe_allow_html=True,
)

# ── Load Streamlit secrets into environment variables ──
# 本地开发: .streamlit/secrets.toml
# Streamlit Cloud: Dashboard → Settings → Secrets
# config.py 各子配置的 __post_init__ 会从 os.getenv() 读取
import os as _os
for _key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE", "DEEPSEEK_API_KEY"):
    try:
        _val = st.secrets[_key]
        if _val and _val.startswith("sk-"):
            _os.environ[_key] = _val
    except KeyError:
        pass  # 未配置 secrets 时使用本地默认值

# Initialize session state
init_all()

# Sidebar navigation
st.sidebar.markdown("""
    <div class="sidebar-header">
        <h1>📊 OKX Quant Agent</h1>
        <p>虚拟币量化交易系统</p>
    </div>
""", unsafe_allow_html=True)

# Navigation pages
pages = [
    ("📊 仪表盘", "pages/1_📊_Dashboard.py"),
    ("📈 回测", "pages/2_📈_Backtest.py"),
    ("⚙ 策略", "pages/3_⚙_Strategies.py"),
    ("🔬 滚动优化", "pages/4_🔬_WalkForward.py"),
    ("🛡 风控", "pages/5_🛡_Risk.py"),
    ("📋 交易日志", "pages/6_📋_TradeLog.py"),
    ("🤖 智能分析", "pages/7_🤖_AgentAnalysis.py"),
    ("💰 模拟交易", "pages/8_💰_PaperTrading.py"),
    ("🟢 以太坊", "pages/9_🟢_EthereumLive.py"),
    ("💓 ETH 心跳", "pages/10_💓_ETHHeartbeat.py"),
]

# Use page navigation
nav = st.Page
page_objects = []
for title, path in pages:
    full_path = Path(__file__).parent / path
    if full_path.exists():
        page_objects.append(nav(str(full_path), title=title))

if page_objects:
    pg = st.navigation(page_objects, position="sidebar")
    pg.run()
else:
    st.error("未找到页面文件。请确保 pages/ 目录存在。")

# Sidebar footer
with st.sidebar:
    cfg = get_config()
    st.markdown(
        f"<div class='sidebar-footer'>"
        f"<span class='sidebar-footer-mode'>{cfg.mode}</span>"
        f"<span class='sidebar-footer-ver'>v0.1</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
