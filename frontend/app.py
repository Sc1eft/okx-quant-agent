"""OKX Quant Agent - Streamlit Frontend Main Entry Point.

Run with: streamlit run app.py
"""

import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as _comps

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
    initial_sidebar_state="auto",
)

# Load custom CSS
css_path = Path(__file__).parent / "assets" / "style.css"
if css_path.exists():
    with open(css_path, encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# ── Load Streamlit secrets into environment variables ──
# 本地开发: .streamlit/secrets.toml
# Streamlit Cloud: Dashboard → Settings → Secrets
# config.py 各子配置的 __post_init__ 会从 os.getenv() 读取
import os as _os
for _key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE", "DEEPSEEK_API_KEY"):
    try:
        _val = st.secrets[_key]
        if not _val:
            continue
        # DEEPSEEK_API_KEY 以 "sk-" 开头，OKX 密钥不以 sk- 开头
        if _key == "DEEPSEEK_API_KEY" and not _val.startswith("sk-"):
            continue  # 非 sk- 前缀的 DeepSeek Key 跳过（可能是占位符）
        _os.environ[_key] = _val
    except KeyError:
        pass  # 未配置 secrets 时使用本地默认值

# Initialize session state
init_all()

# ── Theme mode (light/dark only, no system) ──
# Restore from file if session state is fresh (survives full page navigation)
THEME_FILE = PROJECT_ROOT / "data" / ".theme"
if "theme_mode" not in st.session_state:
    st.session_state.theme_mode = "light"
    try:
        if THEME_FILE.exists():
            saved = THEME_FILE.read_text(encoding="utf-8").strip()
            if saved in ("light", "dark"):
                st.session_state.theme_mode = saved
    except Exception:
        pass

# Sidebar navigation
st.sidebar.markdown("""
    <div class="sidebar-header">
        <h1>📊 OKX Quant Agent</h1>
        <p>虚拟币量化交易系统</p>
    </div>
""", unsafe_allow_html=True)

# ── Theme toggle (light / dark only, no system) ──
theme_mode = st.sidebar.radio(
    "主题",
    ["☀️ 亮色", "🌙 暗色"],
    horizontal=True,
    index=0 if st.session_state.theme_mode == "light" else 1,
    key="theme_radio",
    label_visibility="collapsed",
)
st.session_state.theme_mode = "light" if "亮" in theme_mode else "dark"
# Persist to file (survives full page navigation across pages)
THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
THEME_FILE.write_text(st.session_state.theme_mode, encoding="utf-8")

# ── Inject dark mode body class (before pg.run() so it runs on every MPA page load) ──
# 暗色样式全部在 frontend/assets/style.css 的 body.dark-mode 段（单一来源），
# 这里只负责切换 body class。
_st_dark = st.session_state.theme_mode == "dark"
_comps.html(f"""<script>
try {{ parent.document.body.classList.toggle('dark-mode', {'true' if _st_dark else 'false'}); }} catch(e) {{}}
try {{ document.body.classList.toggle('dark-mode', {'true' if _st_dark else 'false'}); }} catch(e) {{}}
</script>""", height=0)

# Navigation pages (grouped)
# 注意：页面目录必须叫 page_modules 而不是 pages。
# 若目录名为 pages，Streamlit 会启用 MPA v1 兼容模式（PagesManager.uses_pages_directory），
# 导致服务刚启动、尚未执行 st.navigation 时直接访问子页面 URL 会绕过本文件单独运行页面，
# 从而丢失主题/侧边栏等全局 UI。
pages = {
    "总览": [
        ("📊 仪表盘", "page_modules/1_📊_Dashboard.py"),
    ],
    "行情": [
        ("🟢 以太坊", "page_modules/9_🟢_EthereumLive.py"),
        ("💓 ETH 心跳", "page_modules/10_💓_ETHHeartbeat.py"),
    ],
    "模拟验证": [
        ("💰 模拟交易", "page_modules/8_💰_PaperTrading.py"),
        ("📈 回测", "page_modules/2_📈_Backtest.py"),
        ("🔬 滚动优化", "page_modules/4_🔬_WalkForward.py"),
        ("📋 回测日志", "page_modules/6_📋_TradeLog.py"),
    ],
    "实盘 Agent": [
        ("🤖 AI 交易", "page_modules/11_🤖_AI_Trading.py"),
        ("🛡 Agent 风控", "page_modules/12_🛡_AgentRisk.py"),
        ("📋 交易报告", "page_modules/13_📋_TradeReport.py"),
    ],
    "配置": [
        ("⚙ 策略", "page_modules/3_⚙_Strategies.py"),
    ],
}

# 默认首页：仪表盘（用户打开应用先看到全局状态，而不是直接跳进某个功能页）
DEFAULT_PAGE = "page_modules/1_📊_Dashboard.py"

# Use page navigation
page_groups = {}
for group_title, group_pages in pages.items():
    page_objects = []
    for title, path in group_pages:
        full_path = Path(__file__).resolve().parent / path
        if full_path.exists():
            page_objects.append(
                st.Page(str(full_path), title=title, default=(path == DEFAULT_PAGE))
            )
    if page_objects:
        page_groups[group_title] = page_objects

if page_groups:
    pg = st.navigation(page_groups, position="sidebar")
    pg.run()
else:
    st.error("未找到页面文件。请确保 page_modules/ 目录存在。")

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
