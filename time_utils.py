"""项目级时区常量与工具 — 统一 UTC / CST（UTC+8，北京时间）语义。

约定：
  - 运行时代码（agents / execution / risk）统一 UTC aware datetime
  - K 线 DataFrame / 前端展示用 CST（Asia/Shanghai，固定 +8，中国无夏令时）
  - 禁止再硬编码 ``+ timedelta(hours=8)``，统一走本模块
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
CST = timezone(timedelta(hours=8), name="CST")


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_cst() -> datetime:
    return datetime.now(CST)


def utc_to_cst(dt: datetime) -> datetime:
    """UTC → CST（naive 输入按 UTC 解释）"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(CST)
