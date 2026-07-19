"""LLM 影子决策（D12）— 与规则决策并行运行，只记录不执行。

DeepSeek 已移出实时决策环（见 agent3_trader.py：仅用于盘后复盘/报告）。
本模块把它以影子方式接回：Agent3 每次规则决策后，后台线程让 LLM 对同
一上下文做一次决策，双决策落盘 sandbox_decisions 表，供事后对比评估
（一致率 / LLM 错误率 / 时延），不影响任何真实/模拟下单。

线程模型：fire-and-forget daemon 线程。LLM 调用是同步 OpenAI SDK
（数秒级），绝不能在 Agent3 的决策锁内 await。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

from data.db_manager import DatabaseManager, ensure_sandbox_schema

logger = logging.getLogger(__name__)

_MAX_RAW_LEN = 2000  # llm_raw 原文截断长度


class LLMShadow:
    """LLM 影子决策记录器。

    maybe_record() 非阻塞：限频检查通过后，LLM 调用 + 落盘在 daemon
    线程完成。所有计数器 thread-safe。
    """

    def __init__(self, deepseek, db_path: str, min_interval_s: int = 300):
        self._deepseek = deepseek
        self._db = DatabaseManager(str(db_path))
        self._min_interval_s = max(int(min_interval_s), 0)
        self._last_call_at = 0.0
        self._lock = threading.Lock()
        self.total_recorded = 0
        self.total_skipped = 0  # 被限频跳过
        self.total_errors = 0   # 落盘失败
        ensure_sandbox_schema(self._db.conn)

    def maybe_record(self, context: dict, rule_decision: dict) -> bool:
        """限频通过则后台异步记录一次影子对比。返回是否已发起。"""
        now = time.monotonic()
        with self._lock:
            if now - self._last_call_at < self._min_interval_s:
                self.total_skipped += 1
                return False
            self._last_call_at = now
        # 浅拷贝：线程执行期间调用方可能复用/修改原 dict
        threading.Thread(
            target=self._run, args=(dict(context), dict(rule_decision)), daemon=True,
        ).start()
        return True

    def _run(self, context: dict, rule_decision: dict):
        t0 = time.monotonic()
        llm_decision, llm_error = None, None
        try:
            llm_decision = self._deepseek.analyze(context)
        except Exception as e:  # analyze 内部已兜底，这里是双保险
            llm_error = f"{type(e).__name__}: {e}"
        latency_ms = int((time.monotonic() - t0) * 1000)
        try:
            self._insert(context, rule_decision, llm_decision, llm_error, latency_ms)
            with self._lock:
                self.total_recorded += 1
        except Exception as e:
            with self._lock:
                self.total_errors += 1
            logger.warning(f"LLM 影子决策落盘失败: {e}")

    def _insert(self, context, rule_decision, llm_decision, llm_error, latency_ms):
        rule_action = str(rule_decision.get("action", "hold"))
        if llm_error:
            llm_action = "error"
        else:
            llm_action = str((llm_decision or {}).get("action", ""))
        llm_raw = ""
        if llm_decision:
            llm_raw = str(llm_decision.pop("_raw", "") or "")[:_MAX_RAW_LEN]
        # 动作一致才算 agree；LLM 出错/空动作恒为不一致
        agree = 1 if (llm_action and llm_action == rule_action) else 0

        row = (
            datetime.now(timezone.utc).isoformat(),
            float(context.get("current_price", 0) or 0),
            rule_action,
            float(rule_decision.get("confidence", 0) or 0),
            json.dumps(rule_decision, ensure_ascii=False, default=str),
            llm_action,
            float((llm_decision or {}).get("confidence", 0) or 0),
            json.dumps(llm_decision, ensure_ascii=False, default=str) if llm_decision else "",
            llm_raw,
            latency_ms,
            llm_error or "",
            agree,
        )
        with self._db.write_lock:
            self._db.conn.execute(
                """INSERT INTO sandbox_decisions
                   (timestamp, price, rule_action, rule_confidence, rule_decision,
                    llm_action, llm_confidence, llm_decision, llm_raw,
                    llm_latency_ms, llm_error, agree)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            self._db.conn.commit()

    def stats(self) -> dict:
        """对比统计：样本数、一致率(%)、LLM 平均时延、LLM 错误数。"""
        row = self._db.conn.execute(
            """SELECT COUNT(*), AVG(agree), AVG(llm_latency_ms),
                      SUM(CASE WHEN llm_error != '' THEN 1 ELSE 0 END)
               FROM sandbox_decisions"""
        ).fetchone()
        return {
            "total": row[0] or 0,
            "agree_rate": round((row[1] or 0) * 100, 1),
            "avg_latency_ms": int(row[2] or 0),
            "llm_errors": row[3] or 0,
            "recorded": self.total_recorded,
            "skipped": self.total_skipped,
        }
