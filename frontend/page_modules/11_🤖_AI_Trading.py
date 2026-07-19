"""
🤖 AI 自动交易 — 三 Agent 系统控制面板

将 Streamlit 前端与后端三 Agent 系统打通：

  Start → 启动 main.py (subprocess) → Agent 1/2/3 运行 → 写入 agent_status.json + SQLite
  Stop  → 停止 Agent 进程
  Display ← 从 agent_status.json + agent_trades.db 读取实时数据

保留独立 DeepSeek 一键分析 + 追问对话功能（不依赖 Agent 系统）。
"""
from __future__ import annotations

import logging
import os
import signal as _signal
import sqlite3
import subprocess
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# 项目路径必须在所有 frontend.* 模块导入前设置（Streamlit Cloud 依赖此路径）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.components.metrics_display import render_metric_card
from frontend.components.charts import equity_curve_chart
from frontend.utils.data_provider import fetch_klines_with_agg, fetch_ticker
from frontend.utils.session_state import get_config

from frontend.components.layout import inject_mask_hider_js
from frontend.utils.helpers import ss as _ss, fmt_change as _fmt_change, ETH_SYMBOL
from frontend.utils.eth_news import _fetch_crypto_news, _fmt_relative_time
from frontend.utils.eth_ai_analysis import (
    _call_ai_analysis,
    _call_ai_chat,
    _sanitize_ai_text,
    _ticker_summary,
    _summarize_klines,
)

# 单独包装 eth_charts 导入以暴露真实错误（Streamlit Cloud 默认隐藏）
try:
    from frontend.components.eth_charts import (
        TIMEFRAMES,
        TIMEFRAME_REFRESH_S,
        TV_INTERVAL_MAP,
        _build_tradingview_html,
    )
except Exception as _eth_charts_err:
    st.error(f"❌ eth_charts 组件导入失败: {type(_eth_charts_err).__name__}: {_eth_charts_err}")
    st.info("当前页面部分功能不可用。请截图错误信息并联系开发者。")
    st.stop()

# ════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════

DEFAULT_TF_LABEL = "15分钟"
PID_FILE = PROJECT_ROOT / "data" / "agent.pid"
STATUS_FILE = PROJECT_ROOT / "data" / "agent_status.json"
STOP_FLAG = PROJECT_ROOT / "data" / ".agent_stopped"
DB_FILE = PROJECT_ROOT / "data" / "agent_trades.db"
AGENT_FRESH_THRESHOLD_S = 30  # status.json 超过此秒数视为 Agent 已停止

logger = logging.getLogger("ai_trading_page")


# ════════════════════════════════════════════════════════════════
# AGENT PROCESS MANAGEMENT HELPERS
# ════════════════════════════════════════════════════════════════


def _pid_is_alive(pid: int) -> bool:
    """跨平台检测进程是否存活"""
    # Linux / macOS: os.kill(pid, 0) 发空信号，不杀死进程
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    # Windows: tasklist 更可靠
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return "python" in result.stdout.lower() and str(pid) in result.stdout
    except Exception:
        return False


def _agent_is_running() -> bool:
    """检查 Agent 系统是否在运行"""
    # 如果存在停止标记，一律返回 False（覆盖所有其他检查）
    if STOP_FLAG.exists():
        # 清理残留文件
        _cleanup_pid()
        return False

    # 方式 1: PID 文件 + 进程存活
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if _pid_is_alive(pid):
                # 进程存活 → 检查状态文件新鲜度
                if STATUS_FILE.exists():
                    age = _time.time() - STATUS_FILE.stat().st_mtime
                    return age < AGENT_FRESH_THRESHOLD_S
                # 有 PID 但还没出状态文件（刚启动 <30s）
                return True
            # 进程已死 → 清理
            _cleanup_pid()
        except (ValueError, OSError):
            _cleanup_pid()

    # 方式 2: 仅有状态文件且够新鲜（可能其他终端启动）
    if STATUS_FILE.exists():
        # 如果 STATUS_FILE 内容为空（被停止流程清空过），视为已停止
        try:
            content = STATUS_FILE.read_text(encoding="utf-8").strip()
            if content == "{}" or content == "":
                return False
        except Exception:
            pass
        age = _time.time() - STATUS_FILE.stat().st_mtime
        return age < AGENT_FRESH_THRESHOLD_S

    return False


def _cleanup_pid():
    """删除过期的 PID 文件"""
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


def _read_agent_status() -> dict:
    """读取 agent_status.json，失败返回空 dict"""
    try:
        import json
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _start_agent_process(mode: str = "paper") -> bool:
    """启动 main.py 子进程并等待健康确认"""
    if _agent_is_running():
        return False  # 已经在运行

    # 清除停止标记（如果有）
    try:
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()
    except Exception:
        pass

    script = PROJECT_ROOT / "main.py"
    if not script.exists():
        raise FileNotFoundError(f"未找到 {script}")

    # 启动前清理可能残留的状态文件
    if STATUS_FILE.exists():
        try:
            STATUS_FILE.unlink()
        except Exception:
            pass

    # stderr 输出到日志文件，崩溃后可查看原因
    stderr_log = PROJECT_ROOT / "logs" / "agent_startup_error.log"
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_fh = open(stderr_log, "a", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable or "python", str(script), "--mode", mode],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=stderr_fh,
    )
    logger.info(f"Agent 进程已启动 (PID={proc.pid}), stderr → {stderr_log}")

    # 写 PID 文件
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))

    # 等待确认进程活着 + 状态文件出现（最长 10s）
    for _ in range(20):
        if not _pid_is_alive(proc.pid):
            _cleanup_pid()
            raise RuntimeError("Agent 进程启动后立即崩溃，请检查 main.py 日志")
        if STATUS_FILE.exists():
            break
        _time.sleep(0.5)

    return True


def _stop_agent_process() -> bool:
    """停止 Agent 子进程（Windows 用 taskkill /F 确保彻底杀掉）"""
    # 先写停止标记，让 _agent_is_running() 立即返回 False
    STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
    try:
        STOP_FLAG.write_text("1", encoding="utf-8")
    except Exception:
        pass

    pid = None
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            pass

    # 方式 1: 按 PID 杀（更精准）
    if pid:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, text=True, timeout=10,
                )
            else:
                os.kill(pid, _signal.SIGTERM)
        except (OSError, subprocess.TimeoutExpired):
            pass

    # 方式 2: 补杀 — 按命令行查找 main.py 进程（避免误杀其他 Python 进程）
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "ProcessId,CommandLine", "/format:csv"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                if "main.py" not in line.lower():
                    continue
                parts = line.split(",")
                for part in parts:
                    try:
                        p = int(part.strip())
                        if p != pid and p != 0:
                            subprocess.run(
                                ["taskkill", "/F", "/PID", str(p)],
                                capture_output=True, text=True, timeout=5,
                            )
                    except ValueError:
                        continue
        except Exception:
            pass

    _cleanup_pid()

    # 注意：不覆写 agent_status.json，保留历史数据供前端刷新后查看。
    # STOP_FLAG 已足够让 _agent_is_running() 返回 False。

    # 验证进程真死了（最多等 3 秒）
    if pid:
        for _ in range(6):
            if not _pid_is_alive(pid):
                break
            _time.sleep(0.5)

    return True


def _overwrite_status_empty():
    """覆写 status.json 为空对象（比 unlink 更可靠）"""
    for _ in range(3):
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                f.write("{}")
            break
        except PermissionError:
            # Windows 文件锁，重试
            _time.sleep(0.3)
        except Exception:
            break


# ════════════════════════════════════════════════════════════════
# SQLITE HELPERS
# ════════════════════════════════════════════════════════════════


def _get_trades_df(limit: int = 50) -> pd.DataFrame:
    """从 agent_trades.db 读取最近交易记录"""
    if not DB_FILE.exists():
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(str(DB_FILE))
        df = pd.read_sql_query(
            "SELECT id, timestamp, side, size, price, pnl, pnl_close, "
            "trade_group_id, trade_type, order_id, symbol "
            "FROM trades ORDER BY id DESC LIMIT ?",
            conn, params=(limit,),
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


def _get_trade_stats() -> dict:
    """从 SQLite 统计交易数据（仅统计 close 记录，避免 _update_pnl_close 双倍计数）"""
    if not DB_FILE.exists():
        return {}
    try:
        conn = sqlite3.connect(str(DB_FILE))
        cur = conn.cursor()
        # 只用 close 记录统计已平仓交易
        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl_close), 0), "
            "COALESCE(SUM(fee), 0) "
            "FROM trades WHERE trade_type = 'close'"
        )
        total, total_pnl, total_fee = cur.fetchone()
        total = total or 0

        # 当 close 记录为 0 时回退到所有记录（兼容旧数据）
        if total == 0:
            cur.execute("SELECT COUNT(*), COALESCE(SUM(pnl), 0) FROM trades WHERE pnl != 0")
            total, total_pnl = cur.fetchone()
            total = total or 0
            cur.execute("SELECT COUNT(*) FROM trades WHERE pnl > 0")
            wins = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM trades WHERE pnl < 0")
            losses = cur.fetchone()[0] or 0
            conn.close()
            return {
                "total": total,
                "wins": wins,
                "losses": losses,
                "total_pnl": total_pnl or 0.0,
                "total_fee": 0.0,
                "net_pnl": total_pnl or 0.0,
                "win_rate": (wins / (wins + losses) * 100) if (wins + losses) else 0,
            }

        # 正常统计 close 记录
        cur.execute("SELECT COUNT(*) FROM trades WHERE trade_type = 'close' AND pnl_close > 0")
        wins = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM trades WHERE trade_type = 'close' AND pnl_close < 0")
        losses = cur.fetchone()[0] or 0
        conn.close()

        total_pnl = total_pnl or 0.0
        total_fee = total_fee or 0.0
        closed = wins + losses
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "total_pnl": total_pnl,
            "total_fee": total_fee,
            "net_pnl": total_pnl,  # pnl_close 已经是扣费后的净值
            "win_rate": (wins / closed * 100) if closed else 0,
        }
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════
# SESSION STATE INIT
# ════════════════════════════════════════════════════════════════


def _init_state():
    """Initialize all session state variables for this page."""
    _ss("ai_agent_process", None)
    _ss("ai_analysis_result", None)
    _ss("ai_news", None)
    _ss("ai_data", None)
    _ss("ai_ticker", None)
    _ss("ai_timeframe", DEFAULT_TF_LABEL)
    _ss("ai_data_count", 120)
    _ss("ai_auto_refresh", True)
    _ss("ai_initial_balance", 10000.0)
    _ss("ai_use_live_mode", False)
    _ss("ai_chat_context", None)
    _ss("ai_chat_messages", [])
    _ss("ai_chat_loading", False)
    _ss("ai_entry_markers", [])
    _ss("ai_exit_markers", [])
    _ss("ai_error", None)
    _ss("ai_market_mode", "futures")
    _ss("ai_leverage", 10)
    _ss("ai_clear_armed", False)


def _render_agent_dashboard(status_data: dict):
    """Render visual agent activity cards (delegates to shared component)."""
    from frontend.components.agent_dashboard import render_agent_cards
    render_agent_cards(status_data, show_recent=True)


# ════════════════════════════════════════════════════════════════
# PAGE LAYOUT
# ════════════════════════════════════════════════════════════════

st.markdown("""
    <div class="page-header">
        <h1>🤖 AI 自动交易</h1>
        <p>三 Agent 系统控制 · 实时监控 · 深度分析</p>
    </div>
""", unsafe_allow_html=True)

inject_mask_hider_js()

cfg = get_config()
_init_state()

# ════════════════════════════════════════════════════════════════
# CONTROL BAR
# ════════════════════════════════════════════════════════════════

st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-title">⚙ 控制面板</div>', unsafe_allow_html=True)

ctrl_cols = st.columns([1.5, 1.5, 1.0, 1.0, 1.2])

with ctrl_cols[0]:
    tf_labels = list(TIMEFRAMES.keys())
    cur_label = st.session_state.ai_timeframe
    default_idx = tf_labels.index(cur_label) if cur_label in tf_labels else tf_labels.index(DEFAULT_TF_LABEL)
    selected_tf = st.selectbox("K 线周期", tf_labels, index=default_idx, key="ai_tf_sel")

with ctrl_cols[1]:
    dc = st.slider("K 线数量", 20, 300, st.session_state.ai_data_count, step=10, key="ai_dc_slider")

with ctrl_cols[2]:
    st.caption("")
    refresh_btn = st.button("🔄 刷新", use_container_width=True)

with ctrl_cols[3]:
    auto = st.checkbox("自动刷新", key="ai_auto_refresh")

with ctrl_cols[4]:
    st.caption("")
    _live_checked = st.checkbox(
        "实盘模式", value=st.session_state.ai_use_live_mode,
        help="启用时 main.py 以 --mode live 启动（需 Trade 权限）",
    )

# 实盘模式涉及真实资金：醒目警示 + 二次确认后才真正生效，未确认按模拟盘处理
if _live_checked:
    st.warning("⚠️ 实盘模式将使用真实资金交易，可能造成实际亏损，请谨慎操作！")
    _live_confirmed = st.checkbox("我确认使用真实资金交易", key="ai_live_confirm")
    st.session_state.ai_use_live_mode = _live_confirmed
    if not _live_confirmed:
        st.caption("💡 未勾选确认项，启动时将按模拟盘（PAPER）模式运行")
else:
    st.session_state.ai_use_live_mode = False
    st.session_state.ai_live_confirm = False

st.markdown('</div>', unsafe_allow_html=True)

# ── Detect widget changes ──
tf_changed = selected_tf != st.session_state.ai_timeframe
count_changed = dc != st.session_state.ai_data_count
if tf_changed or count_changed or refresh_btn:
    st.session_state.ai_timeframe = selected_tf
    st.session_state.ai_data_count = dc
    st.session_state.ai_data = None
    st.rerun()

tf_label = st.session_state.ai_timeframe
tf_key = TIMEFRAMES.get(tf_label, "1d")
data_count = st.session_state.ai_data_count

# ════════════════════════════════════════════════════════════════
# CONTROL BUTTONS (Start / Stop / Clear / One-shot Analysis)
# ════════════════════════════════════════════════════════════════

st.markdown('<div class="section-card">', unsafe_allow_html=True)
btn_cols = st.columns([2, 2, 2, 2, 3])

agent_running = _agent_is_running()

with btn_cols[0]:
    if st.button("🚀 启动 Agent", type="primary", use_container_width=True,
                 disabled=agent_running):
        try:
            mode = "live" if st.session_state.ai_use_live_mode else "paper"
            _start_agent_process(mode)
            st.toast(f"✅ Agent 系统已启动（{mode.upper()} 模式）", icon="🚀")
            st.rerun()
        except Exception as e:
            st.error(f"❌ 启动失败: {e}")

with btn_cols[1]:
    if st.button("⏹ 停止 Agent", use_container_width=True, type="secondary",
                 disabled=not agent_running):
        if _stop_agent_process():
            st.toast("✅ Agent 系统已停止", icon="⏹")
        else:
            st.toast("⚠ Agent 进程未找到或已停止", icon="⚠")
        st.rerun()

with btn_cols[2]:
    # 二次点击确认，防止误清空分析与对话记录
    _clear_armed = st.session_state.get("ai_clear_armed", False)
    if st.button("⚠️ 再次点击确认清除" if _clear_armed else "🗑 清除",
                 use_container_width=True):
        if not _clear_armed:
            st.session_state.ai_clear_armed = True
        else:
            for k in ["ai_analysis_result", "ai_news", "ai_data", "ai_ticker",
                       "ai_entry_markers", "ai_exit_markers",
                       "ai_chat_context", "ai_chat_messages", "ai_error"]:
                st.session_state[k] = None
            st.session_state.ai_clear_armed = False
        st.rerun()

with btn_cols[3]:
    # 一键分析按钮（独立于 Agent，随时可用）
    if st.button("🔍 一键 AI 分析", use_container_width=True):
        st.session_state.ai_analysis_result = None
        st.session_state.ai_news = None
        st.session_state.ai_error = None
        try:
            with st.status("🤖 AI 分析中…", expanded=True) as status:
                status.update(label="📡 获取 ETH 行情…")
                tk = fetch_ticker(cfg, symbol=ETH_SYMBOL)
                st.session_state.ai_ticker = tk

                status.update(label="📊 获取技术指标…")
                k15 = fetch_klines_with_agg(cfg, limit=30, timeframe="15m", symbol=ETH_SYMBOL)
                k1h = fetch_klines_with_agg(cfg, limit=20, timeframe="1h", symbol=ETH_SYMBOL)
                k1d = fetch_klines_with_agg(cfg, limit=60, timeframe="1d", symbol=ETH_SYMBOL)

                status.update(label="🔄 关联币种…")
                btc = fetch_ticker(cfg, symbol="BTC-USDT")
                sol = fetch_ticker(cfg, symbol="SOL-USDT")
                doge = fetch_ticker(cfg, symbol="DOGE-USDT")

                status.update(label="📰 新闻采集…")
                news = _fetch_crypto_news()
                st.session_state.ai_news = news

                status.update(label="🧠 AI 综合分析…")
                result = _call_ai_analysis(
                    ticker=tk, klines_15m=k15, klines_1h=k1h, klines_1d=k1d,
                    btc_ticker=btc, sol_ticker=sol, doge_ticker=doge,
                    cfg=cfg, news=news,
                )
                st.session_state.ai_analysis_result = result

                # 保存对话上下文
                mk = (
                    f"### 实时行情\n{_ticker_summary('ETH', tk)}\n\n"
                    f"{_summarize_klines(k15, '短期(15分钟)')}\n"
                    f"{_summarize_klines(k1h, '中期(1小时)')}\n"
                    f"{_summarize_klines(k1d, '长期(日线)')}\n\n"
                    f"### 关联币种\n{_ticker_summary('BTC', btc)}\n"
                    f"{_ticker_summary('SOL', sol)}\n"
                    f"{_ticker_summary('DOGE', doge)}"
                )
                st.session_state.ai_chat_context = {
                    "market_summary": mk,
                    "news": news,
                    "analysis_result": result,
                }
                st.session_state.ai_chat_messages = []

                dir_text = {"long": "📈 看多", "short": "📉 看空", "neutral": "⚖️ 中性"}.get(
                    result.get("direction", ""), "-"
                )
                status.update(label=f"✅ 分析完成: {dir_text}", state="complete")
                st.rerun()
        except Exception as e:
            st.session_state.ai_error = str(e)
            st.rerun()

with btn_cols[4]:
    st.session_state.ai_initial_balance = st.number_input(
        "初始资金 (USDT)",
        min_value=100.0, max_value=10_000_000.0,
        value=st.session_state.ai_initial_balance,
        step=1000.0,
    )

# ── 交易模式 + 杠杆 ──
mode_cols = st.columns([1.5, 1.5, 4])
with mode_cols[0]:
    st.session_state.ai_market_mode = st.selectbox(
        "交易模式", ["spot", "futures"],
        index=0 if st.session_state.ai_market_mode == "spot" else 1,
        key="ai_mode_sel",
    )
with mode_cols[1]:
    is_futures = st.session_state.ai_market_mode == "futures"
    st.session_state.ai_leverage = st.number_input(
        "杠杆", min_value=1, max_value=125,
        value=st.session_state.ai_leverage,
        step=1, disabled=not is_futures,
        help="合约杠杆倍数（1-125x），杠杆越高强平风险越大，请谨慎设置",
        key="ai_lev_sel",
    )
with mode_cols[2]:
    st.caption(
        "💡 合约模式：USDT 本位永续合约，含杠杆、保证金、强平价模拟"
        if is_futures else "💡 现货模式：全额交易，无杠杆"
    )

if is_futures and st.session_state.ai_leverage > 10:
    st.warning("⚠️ 高杠杆风险：超过 10x 杠杆极易触发强制平仓，可能造成重大亏损，请谨慎设置！")

st.markdown('</div>', unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# AGENT SYSTEM STATUS — 独立 fragment，不触发全页重渲染
# ════════════════════════════════════════════════════════════════

@st.fragment(run_every=5 if auto else None)
def _agent_status_fragment():
    """独立读取 agent_status.json 渲染 Agent 卡片（不闪烁）"""
    _running = _agent_is_running()
    _sd = _read_agent_status() if _running else {}
    _a3 = _sd.get("agent3", {}) if _sd else {}

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📊 Agent 系统状态</div>', unsafe_allow_html=True)

    sc = st.columns(6)
    with sc[0]:
        _mt = _sd.get("mode", "—").upper() if _sd else "已停止"
        st.metric("系统模式", f"{'🟢 运行中' if _running else '⏸ 已停止'}", _mt)
    with sc[1]:
        st.metric("Agent 3 成交", str(_a3.get("trades_executed", "—")))
    with sc[2]:
        st.metric("跳过次数", str(_a3.get("trades_skipped", "—")))
    with sc[3]:
        _ds = _a3.get("deepseek_stats", {}) or {}
        st.metric("DeepSeek 调用", str(_ds.get("total_calls", "—")))
    with sc[4]:
        _pnl = _a3.get("last_monthly_pnl", "—")
        st.metric("月盈亏", f"{_pnl:+.2f}" if isinstance(_pnl, (int, float)) else str(_pnl))
    with sc[5]:
        _wr = _a3.get("last_win_rate", "—")
        st.metric("胜率", f"{_wr:.1f}%" if isinstance(_wr, (int, float)) else str(_wr))

    if _running and _sd:
        _render_agent_dashboard(_sd)

        # ── 合约模式 KPI（当 agent3 持仓方向非 none 且 market_mode=futures 时显示） ──
        _pos = _a3.get("position", {}) or {}
        _pos_side = _pos.get("side", "none")
        _market_mode = _pos.get("market_mode", "spot")
        if _market_mode == "futures" and _pos_side not in ("none", "flat", ""):
            fc = st.columns(4)
            with fc[0]:
                lev = _pos.get("leverage", 0)
                st.metric("杠杆", f"{lev}x")
            with fc[1]:
                liq = _pos.get("liquidation_price", 0)
                st.metric("强平价", f"${liq:,.2f}" if liq else "—")
            with fc[2]:
                mr = _pos.get("margin_rate", 0) or 0
                mr_color = "inverse" if mr < 5 else "off"
                st.metric("保证金率", f"{mr:.1f}%", delta_color=mr_color)
            with fc[3]:
                pv = _pos.get("position_value", 0)
                st.metric("仓位价值", f"${pv:,.2f}" if pv else "—")

    st.markdown('</div>', unsafe_allow_html=True)

_agent_status_fragment()

# 全页面共享的刷新间隔（供后续 fragment 使用）
refresh_interval_s = TIMEFRAME_REFRESH_S.get(tf_key, 5) if auto else None

# ════════════════════════════════════════════════════════════════
# P&L DASHBOARD — 持仓盈亏看板（独立 fragment，实时刷新）
# ════════════════════════════════════════════════════════════════


@st.fragment(run_every=refresh_interval_s)
def _pnl_dashboard_fragment():
    """持仓盈亏看板 — 独立刷新，不触发全页重渲染"""
    _running = _agent_is_running()
    _sd = _read_agent_status() if _running else {}
    a3 = _sd.get("agent3", {}) if _sd else {}
    pos = a3.get("position", {}) or {}
    risk = a3.get("risk_status", {}) or {}

    has_pos = pos.get("size", 0) > 0
    monthly_pnl = a3.get("last_monthly_pnl", 0) or 0
    daily_trades = risk.get("daily_trade_count", 0) or 0
    consec = risk.get("consecutive_losses", 0) or 0

    # 当内存无持仓时，从 SQLite 读取历史累积盈亏
    stats = _get_trade_stats() if not has_pos else {}
    db_total_pnl = stats.get("total_pnl", 0) or 0
    db_total_trades = stats.get("total", 0) or 0
    db_win_rate = stats.get("win_rate", 0) or 0
    db_wins = stats.get("wins", 0) or 0
    db_losses = stats.get("losses", 0) or 0

    # 取最大值：agent 内存累计 or 数据库累计
    effective_pnl = monthly_pnl if has_pos else (monthly_pnl or db_total_pnl)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">💰 持仓盈亏看板</div>', unsafe_allow_html=True)

    if has_pos:
        side = pos.get("side", "")
        size = pos.get("size", 0)
        entry = pos.get("entry_price", 0)
        cur = pos.get("current_price", 0)
        pnl = pos.get("pnl", 0) or 0
        pnl_pct = pos.get("pnl_pct", 0) or 0
        market_mode = pos.get("market_mode", "spot")

        cols = st.columns(5)

        with cols[0]:
            side_icon = "🟢" if side == "buy" else "🔴"
            side_text = "多头" if side == "buy" else "空头"
            st.markdown(f"<div style='font-size:0.8rem;color:#64748b;'>方向</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='font-size:1.2rem;font-weight:700;'>{side_icon} {side_text}</div>", unsafe_allow_html=True)
            st.caption(f"{size} ETH · {market_mode.upper()}")

        with cols[1]:
            st.metric("入场价", f"${entry:,.2f}")

        with cols[2]:
            price_diff = cur - entry
            price_dir = "▲" if price_diff >= 0 else "▼"
            st.metric("现价", f"${cur:,.2f}", f"{price_dir} ${abs(price_diff):,.2f}")

        with cols[3]:
            pnl_color = "normal" if pnl >= 0 else "inverse"
            st.metric("浮动盈亏", f"${pnl:+,.2f}", f"{pnl_pct:+,.2f}%", delta_color=pnl_color)

        with cols[4]:
            st.metric("月累计", f"${monthly_pnl:+,.2f}")

        # 止损止盈信息
        pm = _sd.get("position_monitor", {}) or {}
        sl = pm.get("stop_loss", 0)
        tp = pm.get("take_profit", 0)
        if sl or tp:
            extra = st.columns(4)
            with extra[0]:
                st.caption(f"🛑 止损: ${sl:.2f}" if sl else "🛑 止损: 未设置")
            with extra[1]:
                st.caption(f"🎯 止盈: ${tp:.2f}" if tp else "🎯 止盈: 未设置")
            with extra[2]:
                st.caption(f"📊 今日交易: {daily_trades} 笔")
            with extra[3]:
                mpt = risk.get("max_trades_per_hour", 4)
                st.caption(f"⏱ HFT上限: {mpt}笔/时")
    else:
        m_color = "#059669" if effective_pnl >= 0 else "#dc2626"
        # 如果只有数据库有数据，显示历史统计
        if db_total_trades > 0 and monthly_pnl == 0:
            # 有已平仓盈亏才显示胜率，否则只显示总笔数和历史盈亏
            if db_wins + db_losses > 0:
                win_str = f"胜率 {db_win_rate:.0f}% ({db_wins}胜/{db_losses}负) &nbsp;|&nbsp; "
            else:
                win_str = ""
            st.markdown(
                f"<div style='text-align:center;padding:8px 0;color:#64748b;'>"
                f"⚪ 当前无持仓 &nbsp;|&nbsp; "
                f"历史盈亏 <span style='color:{m_color};font-weight:600;'>${effective_pnl:+,.2f}</span>"
                f" &nbsp;|&nbsp; {win_str}累计 {db_total_trades} 笔"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='text-align:center;padding:8px 0;color:#64748b;'>"
                f"⚪ 无持仓 &nbsp;|&nbsp; "
                f"月累计 <span style='color:{m_color};font-weight:600;'>${monthly_pnl:+,.2f}</span>"
                f" &nbsp;|&nbsp; 今日 {daily_trades} 笔交易"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown('</div>', unsafe_allow_html=True)


_pnl_dashboard_fragment()

# 供后续 Phase 4 使用的 agent3 数据（页面加载时读取一次，增量更新走 fragment）
# 无论 Agent 是否运行都读取状态文件，保证历史数据跨刷新保留
status_data = _read_agent_status()
agent3 = status_data.get("agent3", {}) if status_data else {}

# ════════════════════════════════════════════════════════════════
# DISPLAY — K-line chart + ticker（独立 fragment，不触发全页刷新）
# ════════════════════════════════════════════════════════════════

@st.fragment(run_every=refresh_interval_s)
def _ticker_fragment():
    """独立刷新 ticker 价格条"""
    _t = st.session_state.get("ai_ticker")
    if _t:
        _c24 = _t.get("change_24h", 0) or 0
        _pc = "green" if _c24 >= 0 else "red"
        _lp = _t.get("last", 0) or 0
        st.markdown(f"""
        <div class="ticker-bar">
        <div class="ticker-item"><span class="ticker-label">ETH-USDT</span><span class="ticker-value {_pc}">${_lp:,.2f} {_fmt_change(_c24)}</span></div>
        <div class="ticker-item"><span class="ticker-label">买一 / 卖一</span><span class="ticker-value">{'${:,.2f}'.format(_t['bid']) if _t.get('bid') else "N/A"} / {'${:,.2f}'.format(_t['ask']) if _t.get('ask') else "N/A"}</span></div>
        <div class="ticker-item"><span class="ticker-label">24h 最高 / 最低</span><span class="ticker-value">{'${:,.2f}'.format(_t['high_24h']) if _t.get('high_24h') else "N/A"} / {'${:,.2f}'.format(_t['low_24h']) if _t.get('low_24h') else "N/A"}</span></div>
        <div class="ticker-item"><span class="ticker-label">24h 成交量</span><span class="ticker-value">{f'{_t["volume_24h"]:,.0f} ETH' if _t.get("volume_24h") else "N/A"}</span></div>
        </div>
        """, unsafe_allow_html=True)
    elif not _t:
        st.info("⏳ 加载行情数据…")


_ticker_fragment()

# ── TRADINGVIEW 专业图表（独立 iframe，仅切换周期时重建） ──
_tfl = st.session_state.get("ai_timeframe", "15分钟")
_tk = TIMEFRAMES.get(_tfl, "1d")
_tv_interval = TV_INTERVAL_MAP.get(_tk, "15")

_tv_theme = st.session_state.get("theme_mode", "light")
components.html(
    _build_tradingview_html(interval=_tv_interval, theme=_tv_theme),
    height=560,
)

# ════════════════════════════════════════════════════════════════
# DATA FRAGMENT — 数据更新 + Agent 交易标记读取
# ════════════════════════════════════════════════════════════════


@st.fragment(run_every=refresh_interval_s)
def _data_fragment():
    """仅获取数据写入 session_state，不触发全页重渲染。"""
    # Ticker（供顶部价格条使用）
    try:
        st.session_state.ai_ticker = fetch_ticker(cfg, symbol=ETH_SYMBOL)
    except Exception:
        pass

    # 如果 Agent 在运行，从 SQLite 读取交易标记
    if _agent_is_running():
        try:
            trades_df = _get_trades_df(limit=20)
            if not trades_df.empty:
                entries = trades_df[trades_df["trade_type"] == "open"]
                exits = trades_df[trades_df["trade_type"] == "close"]
                if not entries.empty:
                    markers = []
                    for _, r in entries.iterrows():
                        try:
                            t = pd.to_datetime(r["timestamp"])
                            markers.append({"time": t, "price": float(r["price"]), "side": r["side"]})
                        except Exception:
                            pass
                    if markers:
                        st.session_state.ai_entry_markers = markers
                if not exits.empty:
                    markers = []
                    for _, r in exits.iterrows():
                        try:
                            t = pd.to_datetime(r["timestamp"])
                            markers.append({"time": t, "price": float(r["price"]), "side": r["side"]})
                        except Exception:
                            pass
                    if markers:
                        st.session_state.ai_exit_markers = markers
        except Exception:
            pass


_data_fragment()

# ════════════════════════════════════════════════════════════════
# AI 分析结果展示
# ════════════════════════════════════════════════════════════════

_err = st.session_state.get("ai_error")
if _err:
    st.error(f"❌ {_err}")

_raw_news = st.session_state.get("ai_news")
_res = st.session_state.get("ai_analysis_result")
if _res:
    st.markdown("---")
    st.markdown("### 🤖 AI 分析结果")

    _dir = _res.get("direction", "neutral")
    _conf = _res.get("confidence", 0)
    if _dir == "long":
        _dir_color = "#059669"
        _dir_icon = "📈"
        _dir_text = "看多"
    elif _dir == "short":
        _dir_color = "#dc2626"
        _dir_icon = "📉"
        _dir_text = "看空"
    else:
        _dir_color = "#64748b"
        _dir_icon = "⚖️"
        _dir_text = "中性"
    _conf_color = "#059669" if _conf >= 70 else "#f59e0b" if _conf >= 40 else "#94a3b8"

    _ev_html = "".join(
        f'<li style="margin-bottom:0.3rem;">{_sanitize_ai_text(e)}</li>' for e in _res.get("key_evidence", []))
    _risk_html = "".join(
        f'<li style="margin-bottom:0.3rem;">{_sanitize_ai_text(r)}</li>' for r in _res.get("risk_warnings", []))

    _fund_news_html = ""
    if _raw_news:
        _news_items = "".join(
            f'<li style="margin-bottom:0.25rem;color:#64748b;font-size:0.85rem;">'
            f'<span style="color:#0f172a;font-weight:500;">[{_sanitize_ai_text(n["source"])}]</span>'
            f'<span style="color:#94a3b8;font-size:0.75rem;">{_fmt_relative_time(n.get("timestamp", ""))}</span> '
            f'{_sanitize_ai_text(n["title"])}</li>'
            for n in _raw_news
        )
        _fund_news_html = "\n".join([
            '<details style="margin-top:0.75rem;">',
            '<summary style="color:#64748b;font-size:0.85rem;cursor:pointer;">',
            f'  📡 参考新闻（{len(_raw_news)}条）',
            '</summary>',
            f'<ul style="margin:0.5rem 0 0 0;padding-left:1.2rem;">{_news_items}</ul>',
            '</details>',
        ])

    st.markdown(f"""
    <div style="border:1px solid #e2e8f0;border-radius:12px;padding:1.25rem;background:white;margin-top:0.5rem;">
        <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;">
            <span style="font-size:1.8rem;">{_dir_icon}</span>
            <span style="font-size:1.5rem;font-weight:700;color:{_dir_color};">{_dir_text}</span>
            <div style="margin-left:auto;display:flex;align-items:center;gap:0.5rem;">
                <span style="color:#64748b;font-size:0.85rem;">信心指数</span>
                <span style="font-size:1.3rem;font-weight:700;color:{_conf_color};">{_conf}%</span>
            </div>
        </div>
        <p style="color:#475569;font-size:0.95rem;margin-bottom:1rem;">{_sanitize_ai_text(_res.get("summary", ""))}</p>
        <div style="margin-bottom:1rem;">
            <p style="font-weight:600;color:#0f172a;margin-bottom:0.4rem;">📌 关键依据</p>
            <ul style="margin:0;padding-left:1.2rem;color:#475569;font-size:0.9rem;">{_ev_html}</ul>
        </div>
        <div style="margin-bottom:1rem;">
            <p style="font-weight:600;color:#0f172a;margin-bottom:0.4rem;">⚠️ 风险提示</p>
            <ul style="margin:0;padding-left:1.2rem;color:#dc2626;font-size:0.9rem;">{_risk_html}</ul>
        </div>
        <div style="display:flex;gap:1rem;flex-wrap:wrap;">
            <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">🔬 技术面</p>
                <p style="color:#475569;font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("technical_analysis", "")) or "—"}</p>
            </div>
            <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">🌊 市场情绪</p>
                <p style="color:#475569;font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("market_sentiment", "")) or "—"}</p>
            </div>
            <div style="flex:1;min-width:200px;background:#f8fafc;border-radius:8px;padding:0.75rem;">
                <p style="font-weight:600;color:#0f172a;font-size:0.85rem;margin-bottom:0.3rem;">📰 基本面</p>
                <p style="color:#475569;font-size:0.85rem;margin:0;">{_sanitize_ai_text(_res.get("fundamental_analysis", "")) or "—"}</p>
            </div>
        </div>
        {_fund_news_html}
    </div>
    """, unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════
    # AI 追问对话
    # ════════════════════════════════════════════════════════════════

    st.markdown("---")
    st.markdown("#### 💬 追问分析")

    chat_container = st.container()
    with chat_container:
        for i, msg in enumerate(st.session_state.ai_chat_messages):
            with st.chat_message(msg["role"]):
                if msg["role"] == "assistant" and i == len(st.session_state.ai_chat_messages) - 1:
                    st.markdown(
                        f'<div class="fade-in-answer">{_sanitize_ai_text(msg["content"])}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(_sanitize_ai_text(msg["content"]))

    if st.session_state.get("ai_chat_loading"):
        with st.chat_message("assistant"):
            st.markdown(
                '<div style="display:flex;align-items:center;gap:8px;">'
                '<span>🤔</span>'
                '<span class="typing-indicator">'
                '<span class="dot"></span>'
                '<span class="dot"></span>'
                '<span class="dot"></span>'
                '</span>'
                '</div>',
                unsafe_allow_html=True,
            )

    user_input = st.chat_input("对当前市场分析提问…（例如：为什么看空？ETH支撑位在哪？）",
                               disabled=st.session_state.get("ai_chat_loading", False))
    if user_input and not st.session_state.get("ai_chat_loading", False):
        st.session_state.ai_chat_messages.append({"role": "user", "content": user_input})
        st.session_state.ai_chat_loading = True
        st.rerun()

    if st.session_state.get("ai_chat_loading") and st.session_state.get("ai_chat_messages") and st.session_state.ai_chat_messages[-1]["role"] == "user":
        pending_question = st.session_state.ai_chat_messages[-1]["content"]
        context = st.session_state.get("ai_chat_context")
        answer = _call_ai_chat(
            pending_question, context,
            st.session_state.ai_chat_messages, cfg,
        )
        st.session_state.ai_chat_messages.append({"role": "assistant", "content": answer})
        st.session_state.ai_chat_loading = False
        st.rerun()

# ════════════════════════════════════════════════════════════════
# PHASE 4 — 自学习指标展示（只要有历史数据就显示）
# ════════════════════════════════════════════════════════════════

if agent3 and any(k in agent3 for k in ("last_composite_score", "last_monthly_pnl", "last_win_rate")):
    st.markdown("---")
    st.markdown("### 📊 Phase 4 — 自学习指标")
    p4_cols = st.columns(4)

    with p4_cols[0]:
        cs = agent3.get("last_composite_score", 0)
        cc = agent3.get("last_composite_confidence", 0)
        if isinstance(cs, (int, float)):
            cs_color = "#059669" if cs > 0 else "#dc2626" if cs < 0 else "#64748b"
            cs_label = "📈 偏多" if cs > 0.2 else "📉 偏空" if cs < -0.2 else "⚖️ 中性"
            st.metric(f"综合方向 {cs_label}", f"{cs:+.2f}", f"信心 {cc:.0%}" if isinstance(cc, (int, float)) else "—")
        else:
            st.metric("综合方向", str(cs))

    with p4_cols[1]:
        al_score = agent3.get("last_alignment_score", "—")
        if isinstance(al_score, (int, float)):
            al_color = "#059669" if al_score >= 0.7 else "#f59e0b" if al_score >= 0.4 else "#dc2626"
            al_label = "🤝 共识" if al_score >= 0.7 else "⚠️ 分歧" if al_score < 0.4 else "⚪ 中性"
            st.metric(f"信号对齐 {al_label}", f"{al_score:.2f}")
        else:
            st.metric("信号对齐", str(al_score))

    with p4_cols[2]:
        pnl_val = agent3.get("last_monthly_pnl", 0)
        if isinstance(pnl_val, (int, float)):
            st.metric("月盈亏", f"${pnl_val:+,.2f}", delta_color="normal" if pnl_val >= 0 else "inverse")
        else:
            st.metric("月盈亏", str(pnl_val))

    with p4_cols[3]:
        wr = agent3.get("last_win_rate", "—")
        md = agent3.get("max_drawdown", "—")
        if isinstance(wr, (int, float)):
            st.metric("胜率", f"{wr:.1f}%",
                      f"回撤 {md:.2f}%" if isinstance(md, (int, float)) else None)
        else:
            st.metric("胜率", str(wr))

    # 信号对齐摘要文本
    sa_text = agent3.get("signal_alignment", "")
    if sa_text and sa_text != "暂无对齐数据":
        al_score = agent3.get("last_alignment_score", 0)
        if isinstance(al_score, (int, float)):
            icon = "✅" if al_score >= 0.7 else "⚠️" if al_score < 0.4 else "ℹ️"
        else:
            icon = "ℹ️"
        st.markdown(
            f'<div style="background:#f8fafc;border-radius:8px;padding:0.75rem;'
            f'margin-top:0.5rem;border-left:4px solid '
            f'{"#059669" if isinstance(al_score,(int,float)) and al_score>=0.7 else "#f59e0b"};'
            f'font-size:0.9rem;color:#334155;">'
            f'{icon} 信号对齐: {sa_text}</div>',
            unsafe_allow_html=True,
        )

    # 自适应参数
    amt = agent3.get("adjusted_max_trades")
    adb = agent3.get("adjusted_debounce")
    ait = agent3.get("adjusted_trade_interval")
    if any(v is not None for v in [amt, adb, ait]):
        st.caption(
            f"⚙️ 自适应参数 — 最大日交易: {amt} / "
            f"信号采集: {adb}s / "
            f"交易间隔: {ait}s"
        )

    # 参数调整历史
    p4 = agent3.get("phase4", {})
    if p4 and p4.get("param_changes"):
        with st.expander("📋 参数调整记录", expanded=False):
            for entry in p4["param_changes"][-10:]:  # 最近10条
                ts = entry.get("timestamp", "—")
                changes = entry.get("changes", {})
                reason = entry.get("reason", "")
                st.markdown(
                    f"**{ts}** — {reason}  "
                    f"{'  '.join(f'{k}: {v}' for k, v in changes.items())}"
               )


# ════════════════════════════════════════════════════════════════
# 交易记录（从 SQLite 读取）
# ════════════════════════════════════════════════════════════════

_trades_df = _get_trades_df(limit=50)

if not _trades_df.empty:
    st.markdown("---")
    st.markdown("### 📋 交易记录")

    df_display = _trades_df[["timestamp", "side", "size", "price", "pnl_close", "trade_type"]].copy()
    df_display["side"] = df_display["side"].map(
        {"buy": "🟢 买入", "sell": "🔴 卖出", "long": "🟢 买入", "short": "🔴 卖出"}
    ).fillna(df_display["side"])
    df_display["trade_type"] = df_display["trade_type"].map(
        {"open": "开仓", "close": "平仓"}
    ).fillna(df_display["trade_type"])
    df_display["pnl_close"] = df_display["pnl_close"].apply(
        lambda x: f"${x:+,.2f}" if isinstance(x, (int, float)) and x != 0 else "-"
    )
    df_display.columns = ["时间", "方向", "数量", "价格", "盈亏", "类型"]
    st.dataframe(df_display, use_container_width=True, hide_index=True)

    # 统计
    stats = _get_trade_stats()
    if stats.get("total"):
        total_fee = stats.get("total_fee", 0) or 0
        net_pnl = stats.get("net_pnl", stats["total_pnl"])
        gross_pnl = net_pnl + total_fee  # 还原毛盈亏
        stat_cols = st.columns(6)
        stat_cols[0].metric("净盈亏", f"${net_pnl:+,.2f}")
        stat_cols[1].metric("手续费", f"${total_fee:+,.2f}")
        stat_cols[2].metric("胜率", f"{stats['win_rate']:.1f}%")
        stat_cols[3].metric("盈利次数", stats["wins"])
        stat_cols[4].metric("亏损次数", stats["losses"])
        stat_cols[5].metric("总交易", stats["total"])
elif agent_running:
    st.info("💡 Agent 系统运行中，暂无交易记录。等待 Agent 3 执行交易…")
else:
    st.info("💡 Agent 系统未运行。点启动 Agent 开始交易。")

# ════════════════════════════════════════════════════════════════
# 权益曲线
# ════════════════════════════════════════════════════════════════

if not _trades_df.empty and "pnl_close" in _trades_df.columns:
    # 从 SQLite 计算每日权益
    try:
        close_df = _trades_df[_trades_df["trade_type"] == "close"].copy()
        if close_df.empty:
            st.caption("📈 暂无已平仓交易，权益曲线待数据积累后显示")
        elif not close_df["pnl_close"].notna().any():
            st.caption("📈 平仓交易盈亏数据为空")
        else:
            close_df["timestamp"] = pd.to_datetime(close_df["timestamp"])
            close_df = close_df.sort_values("timestamp")
            cum_pnl = close_df["pnl_close"].cumsum()
            equity_points = []
            init = st.session_state.get("ai_initial_balance", 10000)
            for i, (_, r) in enumerate(close_df.iterrows()):
                equity_points.append({
                    "time": r["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                    "equity": init + float(cum_pnl.iloc[i]),
                })
            if len(equity_points) >= 2:
                st.markdown("---")
                st.markdown("### 📈 权益曲线")
                fig_eq = equity_curve_chart(
                    equity_points,
                    title="Agent 交易权益曲线",
                    theme=st.session_state.get("theme_mode", "light"),
                )
                st.plotly_chart(fig_eq, use_container_width=True, config={"displayModeBar": False})
            else:
                st.caption("📈 权益曲线至少需要 2 笔平仓交易")
    except Exception as e:
        st.warning(f"权益曲线渲染异常: {e}")

# ════════════════════════════════════════════════════════════════
# FOOTER
# ════════════════════════════════════════════════════════════════

st.divider()
st.caption(
    f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC | "
    f"Agent {'🟢 运行中' if agent_running else '⏸ 已停止'} | "
    f"K线: {tf_label} | 数据延迟 ≤ 5s"
)
