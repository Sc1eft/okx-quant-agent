"""
Agent 状态写入器 — 供 main.py 定期将 Agent 运行状态写入 JSON 文件

Streamlit 监控面板通过读取此文件获取实时状态，避免直接进程间通信。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("status_writer")

_STATUS_FILE = "data/agent_status.json"


def write_agent_status(
    agent1_status: dict | None = None,
    agent2_status: dict | None = None,
    agent3_status: dict | None = None,
    agent4_reviewer_status: dict | None = None,
    position_monitor_status: dict | None = None,
    mode: str = "paper",
    reports: dict | None = None,
):
    """将各 Agent 状态写入 JSON 文件（供 Streamlit 面板读取）"""
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "agent1": agent1_status or {},
        "agent2": agent2_status or {},
        "agent3": agent3_status or {},
        "agent4_reviewer": agent4_reviewer_status or {},
        "position_monitor": position_monitor_status or {},
        "reports": {
            "last_daily": "",
            "last_weekly": "",
            "last_monthly": "",
            "last_push_ok": False,
            "last_push_time": "",
        },
    }
    if reports:
        data["reports"].update(reports)
    Path(_STATUS_FILE).parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.warning(f"写入状态文件失败: {e}")


def read_agent_status() -> dict:
    """读取 Agent 状态 JSON 文件（供 Streamlit 面板使用）"""
    try:
        with open(_STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def get_status_file_path() -> str:
    """返回状态文件路径（供外部判断使用）"""
    return os.path.abspath(_STATUS_FILE)
