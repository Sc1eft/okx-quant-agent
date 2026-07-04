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

# ── Inject dark mode CSS + body class (before pg.run() so it runs on every MPA page load) ──
# Use components.html to set body.dark-mode class — script runs in parent document
_st_dark = st.session_state.theme_mode == "dark"
_comps.html(f"""<script>
try {{ parent.document.body.classList.toggle('dark-mode', {'true' if _st_dark else 'false'}); }} catch(e) {{}}
try {{ document.body.classList.toggle('dark-mode', {'true' if _st_dark else 'false'}); }} catch(e) {{}}
</script>""", height=0)

if st.session_state.theme_mode == "dark":
    st.markdown("""<style>
    :root {
        --bg-page: #0f172a;
        --bg-card: #1e293b;
        --bg-card-hover: #253349;
        --bg-input: #1e293b;
        --text-primary: #f1f5f9;
        --text-secondary: #94a3b8;
        --text-muted: #64748b;
        --border: #334155;
        --border-light: #1e293b;
        --primary-light: #1e3a5f;
        --primary-ring: rgba(37,99,235,0.4);
        --green-bg: #064e3b;
        --green-border: #065f46;
        --red-bg: #7f1d1d;
        --red-border: #991b1b;
        --amber-bg: #78350f;
        --amber-border: #92400e;
        --purple-bg: #3b0764;
        --shadow-xs: 0 1px 2px rgba(0,0,0,0.2);
        --shadow-sm: 0 1px 3px rgba(0,0,0,0.25);
        --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.3);
        --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.4);
    }
    .stApp { background-color: #0f172a !important; }
    .stDataFrame thead tr th { background: #1e293b !important; border-color: #334155 !important; }
    .stDataFrame tbody tr:hover { background: #253349 !important; }
    div[data-baseweb="tag"] { background: #334155 !important; }
    code { background: #1e293b; color: #f87171; }
    .stProgress > div > div { background-color: #334155 !important; }
    .sidebar-footer { color: #64748b; border-top-color: rgba(255,255,255,0.06); }
    .sidebar-footer-mode { color: #64748b; }
    .sidebar-footer-ver { color: #334155; }
    .stAlert[data-baseweb="notification"][kind="info"] { background: rgba(37,99,235,0.15) !important; }
    .stAlert[data-baseweb="notification"][kind="success"] { background: rgba(5,150,105,0.15) !important; }
    .stAlert[data-baseweb="notification"][kind="warning"] { background: rgba(217,119,6,0.15) !important; }
    .stAlert[data-baseweb="notification"][kind="error"] { background: rgba(220,38,38,0.15) !important; }
    .status-bar { background: #064e3b !important; border-color: #065f46 !important; }
    div[style*="background: white"],div[style*="background:white"],div[style*="background:#fff"],div[style*="background-color: white"],div[style*="background-color:white"] { background: #1e293b !important; }
    div[style*="background: #f8fafc"],div[style*="background:#f8fafc"] { background: #253349 !important; }
    div[style*="background: #f1f5f9"],div[style*="background:#f1f5f9"] { background: #1e293b !important; }
    *[style*="color: #0f172a"],*[style*="color:#0f172a"] { color: #f1f5f9 !important; }
    *[style*="color: #475569"],*[style*="color:#475569"] { color: #94a3b8 !important; }
    *[style*="border-color: #e2e8f0"],*[style*="border-color:#e2e8f0"] { border-color: #334155 !important; }
    *[style*="border: 1px solid #e2e8f0"],*[style*="border:1px solid #e2e8f0"] { background: #1e293b !important; border-color: #334155 !important; }
    .agent-card { background: #1e293b !important; border-color: #334155 !important; }
    .agent-card.running { border-color: #065f46 !important; }
    .agent-name,.metric-item .value { color: #f1f5f9 !important; }
    .metric-item .label,.tag,.tag.neutral { color: #94a3b8 !important; background: #1e293b !important; }
    .tag.bullish { background: #064e3b !important; color: #6ee7b7 !important; }
    .tag.bearish { background: #7f1d1d !important; color: #fca5a5 !important; }
    .agent-footer,.uptime { color: #64748b !important; }
    .agent-header,.tag-row,.agent-footer { border-color: #334155 !important; }
    .section-title { color: #f1f5f9 !important; }
    .agent-activity { background: rgba(255,255,255,0.06) !important; }
    .agent-activity .act-text { color: #f1f5f9 !important; }
    .agent-activity .act-text.highlight { color: #34d399 !important; }
    .agent-activity .act-time { color: #64748b !important; }
    .agent-details { border-top-color: #334155 !important; }
    .agent-details summary:hover { background: rgba(255,255,255,0.05) !important; color: #94a3b8 !important; }
    .detail-section-title { color: #94a3b8 !important; }
    .detail-row .label { color: #94a3b8 !important; }
    .detail-row .value { color: #e2e8f0 !important; }
    .detail-sep { background: #334155 !important; }
    .pipeline-step { background: rgba(255,255,255,0.05) !important; }
    .pipeline-step .step-label { color: #94a3b8 !important; }
    .pipeline-step .step-value { color: #e2e8f0 !important; }
    .tf-name { color: #e2e8f0 !important; }
    .tf-status { color: #94a3b8 !important; }
    .tf-bar-bg { background: #334155 !important; }
    .stPlotlyChart svg { background: #1e293b; }
    .stPlotlyChart .bg { fill: #1e293b !important; }
    /* Extra overrides from style.css body.dark-mode that need direct selectors */
    *[style*="color: #1e293b"],*[style*="color:#1e293b"] { color: #f1f5f9 !important; }
    *[style*="color: #334155"],*[style*="color:#334155"] { color: #94a3b8 !important; }
    </style>""", unsafe_allow_html=True)

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
    ("🤖 AI 交易", "pages/11_🤖_AI_Trading.py"),
    ("📋 交易报告", "pages/13_📋_TradeReport.py"),
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
