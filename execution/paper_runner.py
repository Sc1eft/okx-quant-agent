"""无头模拟盘运行器 — 脱离 Streamlit 会话独立运行的模拟交易循环。

PaperTrading 页通过 start_runner() 拉起本进程，浏览器关闭/刷新/Streamlit 重启
都不影响交易循环；页面只通过状态文件观察和控制：

- 配置: data/paper_runner_config.json （页面写入，runner 启动时读取）
- 状态: data/paper_runner_state.json（runner 原子写入，页面轮询读取）
- PID:  data/paper_runner.pid

进程管理模式与 data/heartbeat_db.py 的心跳采集器一致。
直接运行: python execution/paper_runner.py
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from strategies.base import create_strategy
from risk.rules import RiskEngine
from execution.paper import PaperEngine
from execution.futures_paper import FuturesPaperEngine
from frontend.utils.data_provider import fetch_okx_data

logger = logging.getLogger("paper_runner")

DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = DATA_DIR / "paper_runner_config.json"
STATE_PATH = DATA_DIR / "paper_runner_state.json"
PID_PATH = DATA_DIR / "paper_runner.pid"
LOG_PATH = PROJECT_ROOT / "logs" / "paper_runner.log"

_FUNDING_REFRESH_S = 1800  # 资金费率 30min 刷新一次（OKX 每 8h 才更新）


def _atomic_json_write(path: Path, data: dict):
    """原子写入 JSON：先写 .tmp 再 replace，防止页面读到写一半的文件。"""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    tmp.replace(path)


# ─────────────────────────────────────────────
# 配置 / 状态文件（页面 ↔ runner 的交换协议）
# ─────────────────────────────────────────────

def write_config(config: dict):
    """页面写入运行配置（启动前调用）。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(CONFIG_PATH, config)


def read_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def clear_config():
    CONFIG_PATH.unlink(missing_ok=True)


def read_state() -> dict | None:
    """页面读取 runner 状态（phase/progress/config/paper_state/updated_at）。"""
    if not STATE_PATH.exists():
        return None
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(**fields):
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), **fields}
    _atomic_json_write(STATE_PATH, payload)


# ─────────────────────────────────────────────
# 进程管理（与 data/heartbeat_db.py 同一模式）
# ─────────────────────────────────────────────

def is_runner_running() -> bool:
    """通过 PID 文件 + tasklist 判断 runner 进程是否存活。"""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def start_runner() -> bool:
    """拉起后台 runner 进程（幂等：已在运行则直接返回 True）。"""
    if is_runner_running():
        logger.info("paper runner 已在运行")
        return True
    script = Path(__file__).resolve()
    try:
        subprocess.Popen(
            [sys.executable, str(script)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("已启动模拟盘 runner")
        return True
    except Exception as e:
        logger.error(f"启动模拟盘 runner 失败: {e}")
        return False


def stop_runner() -> bool:
    """停止后台 runner 进程。"""
    if not PID_PATH.exists():
        return True
    try:
        pid = int(PID_PATH.read_text().strip())
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        PID_PATH.unlink(missing_ok=True)
        logger.info(f"已停止模拟盘 runner (PID {pid})")
        return True
    except Exception as e:
        logger.error(f"停止模拟盘 runner 失败: {e}")
        return False


# ─────────────────────────────────────────────
# 主循环
# ─────────────────────────────────────────────

def _poll_interval_s(tf: str) -> int:
    return {"15m": 5, "1h": 10, "4h": 30, "1d": 60}.get(tf, 10)


def _fetch_funding_rate(app_cfg: Config) -> float | None:
    """OKX 永续当前资金费率（小数）；失败返回 None（引擎按不结算处理）。"""
    try:
        from okx_client import OKXClient
        client = OKXClient(app_cfg.exchange)
        try:
            info = client.get_funding_rate("ETH-USDT-SWAP")
        finally:
            client.close()
        raw = info.get("funding_rate", "")
        return float(raw) if raw not in ("", None) else None
    except Exception as e:
        logger.warning(f"获取资金费率失败: {e}")
        return None


def _read_heartbeat() -> dict | None:
    """读心跳状态文件（采集器未运行/读取失败 → None）。"""
    try:
        from data.heartbeat_db import read_status
        return read_status()
    except Exception:
        return None


def _maybe_tick_exit(engine, risk_engine) -> tuple[dict, float] | None:
    """心跳 tick 级退出检查；触发返回 (state, price)，未触发返回 None。

    时间戳取自状态文件的 last_tick_at 字段（采集器 monitor 线程每秒写入）。
    """
    hb = _read_heartbeat()
    if not hb or not hb.get("last_price"):
        return None
    price = float(hb["last_price"])
    ts = hb.get("last_tick_at")
    trade = engine.check_tick_exit(price, ts=ts, risk_engine=risk_engine)
    if not trade:
        return None
    logger.info(f"⚡ tick 级 {trade.get('reason')} 触发 @ {price:.2f}")
    return _make_tick_exit_state(price, ts, trade, engine), price


def _make_tick_exit_state(price: float, ts: str | None, trade: dict, engine) -> dict:
    """tick 级退出后的状态快照（与 run_bar 返回结构保持一致，供页面统一渲染）。"""
    return {
        "timestamp": ts,
        "price": price,
        "signal": "tick_exit",
        "risk_ok": True,
        "risk_reason": "",
        "trade": trade,
        "liquidation": trade if trade.get("side") == "liquidation" else None,
        "funding_events": [],
        "funding_rate": getattr(engine, "_funding_rate", None),
        "account": engine.account.to_dict(),
    }


_FUNDING_FETCH = object()  # _build_engine 未显式收到资金费率时自行拉取


def _build_engine(config: dict, app_cfg: Config, funding_rate=_FUNDING_FETCH):
    """按页面配置创建合约/现货引擎（合约注入资金费率）。"""
    exit_params = config.get("exit_params") or {}
    if config.get("mode") == "futures":
        engine = FuturesPaperEngine(
            app_cfg,
            wallet_balance=float(config.get("wallet_balance", 10000.0)),
            leverage=int(config.get("leverage", 10)),
            position_size_pct=float(config.get("position_size_pct", 0.1)),
            exit_params=exit_params,
        )
        engine.set_funding_rate(_fetch_funding_rate(app_cfg) if funding_rate is _FUNDING_FETCH else funding_rate)
        return engine
    return PaperEngine(
        app_cfg,
        initial_balance=float(config.get("wallet_balance", 10000.0)),
        position_size_pct=float(config.get("position_size_pct", 0.1)),
        exit_params=exit_params,
    )


def _build_slots(config: dict, app_cfg: Config) -> list[dict]:
    """按配置构建交易 slot：每个 slot 独立的策略实例 / 引擎 / RiskEngine
    （共用 RiskEngine 会把彼此盈亏混进连亏/日亏统计）。
    config 带非空 strategies 数组 → 多 slot；否则单 slot（旧单策略行为）。"""
    slot_cfgs = config.get("strategies") or [config]
    # 资金费率共享一次拉取，注入每个引擎（失败 → None，引擎按不结算处理）
    funding = _fetch_funding_rate(app_cfg) if config.get("mode") == "futures" else None
    slots = []
    for sc in slot_cfgs:
        slots.append({
            "label": sc.get("label") or sc["strategy"],
            "strategy": create_strategy(sc["strategy"], sc.get("strategy_params")),
            "engine": _build_engine({**config, **sc}, app_cfg, funding_rate=funding),
            "risk": RiskEngine(app_cfg.risk),
        })
    return slots


def _slot_state(slot_states: dict, labels: list):
    """单 slot 保持原单策略 state 结构（向后兼容）；多 slot 按 label 分桶。"""
    if len(labels) == 1:
        return slot_states.get(labels[0])
    return dict(slot_states)


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )

    config = read_config()
    if not config:
        logger.error(f"未找到配置文件 {CONFIG_PATH}，退出")
        return 1

    PID_PATH.write_text(str(os.getpid()))

    def _cleanup():
        PID_PATH.unlink(missing_ok=True)
        logger.info("runner 退出")

    atexit.register(_cleanup)

    tf = config.get("timeframe", "1h")
    interval = _poll_interval_s(tf)
    app_cfg = Config.load(str(PROJECT_ROOT / "configs" / "default.json"))
    slots = _build_slots(config, app_cfg)
    labels = [s["label"] for s in slots]
    # 多 slot 时 state.json 附带 slots 标签列表；单 slot 不写（保持旧 state 结构）
    slots_field = {"slots": labels} if len(labels) > 1 else {}
    is_futures = config.get("mode") == "futures"

    # ⚡ 秒级止损依赖心跳采集器（幂等，已在运行则直接返回）
    if config.get("tick_exit", True):
        try:
            from data.heartbeat_db import start_collector as _start_hb
            _start_hb()
        except Exception as e:
            logger.warning(f"心跳采集器启动失败，秒级止损不可用: {e}")

    logger.info(
        f"runner 启动: mode={config.get('mode')} strategies={labels} "
        f"tf={tf} leverage={config.get('leverage', '-')} balance={config.get('wallet_balance')}"
    )

    # ── 回放历史 K 线 ──
    slot_states: dict = {}
    try:
        df = fetch_okx_data(app_cfg, limit=int(config.get("initial_bars", 100)), timeframe=tf)
    except Exception as e:
        logger.exception("初始 K 线获取失败")
        _write_state(phase="error", error=f"初始 K 线获取失败: {e}", config=config)
        return 1

    total = len(df)
    bars_processed = 0
    for i, (_, bar) in enumerate(df.iterrows(), 1):
        for s in slots:
            slot_states[s["label"]] = s["engine"].run_bar(bar, s["strategy"], s["risk"])
        bars_processed = i
        if i % 10 == 0 or i == total:
            _write_state(phase="replaying",
                         progress={"processed": i, "total": total},
                         bars_processed=bars_processed,
                         paper_state=_slot_state(slot_states, labels), config=config,
                         **slots_field)
    last_bar_ts = df.index[-1]
    _write_state(phase="running",
                 progress={"processed": total, "total": total},
                 bars_processed=bars_processed,
                 paper_state=_slot_state(slot_states, labels), config=config,
                 **slots_field)
    logger.info(f"回放完成 ({total} 根)，进入实时循环 (tick 1s / K 线每 {interval}s)")

    # ── 实时轮询：1s 循环驱动秒级 tick 止损，K 线按周期拉取 ──
    last_funding_refresh = time.monotonic()
    next_kline_fetch = 0.0
    kline_backoff = interval
    tick_exit_enabled = config.get("tick_exit", True) and any(
        hasattr(s["engine"], "check_tick_exit") for s in slots)
    logger.info(f"tick 止损: {'启用 (1s)' if tick_exit_enabled else '关闭'}")
    while True:
        try:
            now = time.monotonic()

            # K 线按周期拉取（失败指数退避，不阻塞 tick 止损）
            if now >= next_kline_fetch:
                try:
                    latest = fetch_okx_data(app_cfg, limit=5, timeframe=tf)
                    new_bars = latest[latest.index > last_bar_ts]
                    for ts, bar in new_bars.iterrows():
                        for s in slots:
                            slot_states[s["label"]] = s["engine"].run_bar(bar, s["strategy"], s["risk"])
                        last_bar_ts = ts
                        bars_processed += 1
                        _sig = (slot_states[labels[0]]["signal"] if len(labels) == 1
                                else {lb: st["signal"] for lb, st in slot_states.items()})
                        logger.info(f"新 K 线 {ts} close={float(bar['close']):.2f} signal={_sig}")
                    next_kline_fetch = now + interval
                    kline_backoff = interval
                except Exception as e:
                    logger.exception(f"K 线轮询异常: {e}")
                    next_kline_fetch = now + min(kline_backoff, 60)
                    kline_backoff = min(kline_backoff * 2, 60)

            # ⚡ tick 级止损（心跳秒级价格，每秒检查；每个 slot 独立检查）
            if tick_exit_enabled:
                for s in slots:
                    if not hasattr(s["engine"], "check_tick_exit"):
                        continue
                    result = _maybe_tick_exit(s["engine"], s["risk"])
                    if result:
                        slot_states[s["label"]], _ = result

            # 资金费率定期刷新（仅合约；共享一次拉取注入每个引擎）
            if is_futures and now - last_funding_refresh > _FUNDING_REFRESH_S:
                _rate = _fetch_funding_rate(app_cfg)
                for s in slots:
                    s["engine"].set_funding_rate(_rate)
                last_funding_refresh = now

            _write_state(phase="running",
                         progress={"processed": total, "total": total},
                         bars_processed=bars_processed,
                         paper_state=_slot_state(slot_states, labels), config=config,
                         **slots_field)
        except Exception as e:
            logger.exception(f"轮询异常: {e}")
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
