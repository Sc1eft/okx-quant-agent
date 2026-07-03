"""
复盘报告生成 — Phase 4

从 SQLite trades 表中读取交易记录, 计算统计指标, 生成每日/每周复盘报告。

统计指标:
  - 总交易次数 / 胜率 / 总盈亏 / 平均盈亏
  - 最大回撤 (峰值到谷底的百分比)
  - 按方向 (多/空) 拆分的胜率
  - 最佳/最差单笔交易

报告输出: JSON 文件到 data/reviews/
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from agents.config import AgentSystemConfig

logger = logging.getLogger("review_generator")


class ReviewGenerator:
    """复盘报告生成器"""

    def __init__(self, config: AgentSystemConfig, db_path: str):
        self.config = config
        self.db_path = db_path

    # ── 公共查询方法 ──

    def compute_monthly_stats(self) -> dict[str, Any]:
        """计算本月至今的统计

        Returns:
            dict: trades, wins, losses, win_rate, total_pnl, max_drawdown_pct, avg_trade_pnl
        """
        conn = self._get_conn()
        try:
            return self._compute_range_stats(conn, days=30)
        finally:
            conn.close()

    def compute_daily_stats(self, date_str: str | None = None) -> dict[str, Any]:
        """计算指定日期的统计"""
        conn = self._get_conn()
        try:
            target = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return self._compute_range_stats(conn, since=f"{target}T00:00:00", until=f"{target}T23:59:59")
        finally:
            conn.close()

    def compute_weekly_stats(self) -> dict[str, Any]:
        """计算过去 7 天的统计"""
        conn = self._get_conn()
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            return self._compute_range_stats(conn, since=since)
        finally:
            conn.close()

    # ── 报告生成 ──

    def generate_daily_report(self) -> dict[str, Any]:
        """生成每日复盘报告并写入 JSON"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = self.compute_daily_stats(today)
        report = self._build_report(stats, "daily", today)
        self._write_report(report, "daily", today)
        logger.info(f"每日复盘报告: 胜率 {stats['win_rate']:.1f}%, 盈亏 {stats['total_pnl']:+.2f} USDT")
        return report

    def generate_weekly_report(self) -> dict[str, Any]:
        """生成每周复盘报告并写入 JSON"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = self.compute_weekly_stats()
        report = self._build_report(stats, "weekly", today)
        self._write_report(report, "weekly", today)
        logger.info(f"每周复盘报告: 胜率 {stats['win_rate']:.1f}%, 盈亏 {stats['total_pnl']:+.2f} USDT")
        return report

    # ── 内部方法 ──

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # 确保 trades 表存在 (使用 RiskManager 相同的建表语句)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, side TEXT, size REAL, price REAL,
                pnl REAL, order_id TEXT, symbol TEXT, decision TEXT,
                pnl_close REAL DEFAULT 0,
                trade_group_id TEXT DEFAULT '',
                trade_type TEXT DEFAULT 'open'
            )
        """)
        conn.commit()
        return conn

    def _compute_range_stats(
        self,
        conn: sqlite3.Connection,
        days: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        """计算一个时间范围内的交易统计

        使用 pnl_close (平仓盈亏) 而非 pnl (开仓记录中的 0)。
        回退: 若 pnl_close 全为 0, 则使用 pnl 字段。
        """
        conditions = ["pnl_close != 0"]
        params: list = []

        if days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            conditions.append("timestamp >= ?")
            params.append(cutoff)
        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            conditions.append("timestamp <= ?")
            params.append(until)

        where = " AND ".join(conditions) if conditions else "1=1"

        # 总览统计
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_close > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_close < 0 THEN 1 ELSE 0 END) as losses,
                ROUND(SUM(pnl_close), 2) as total_pnl,
                ROUND(AVG(pnl_close), 2) as avg_pnl,
                MAX(pnl_close) as best_trade,
                MIN(pnl_close) as worst_trade
            FROM trades WHERE {where}
            """,
            params,
        ).fetchone()

        total = row["total_trades"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        total_pnl = row["total_pnl"] or 0.0

        # 如果 pnl_close 全部为 0 (旧数据), 回退到 pnl 字段
        if total == 0 or (total > 0 and total_pnl == 0):
            # 确认是否有任何交易记录
            any_trades = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()
            if any_trades and any_trades["c"] > 0:
                return self._fallback_to_pnl(conn, days, since, until)

        win_rate = round(wins / total * 100, 1) if total > 0 else 0.0

        # 按方向拆分
        side_row = conn.execute(
            f"""
            SELECT side,
                COUNT(*) as cnt,
                SUM(CASE WHEN pnl_close > 0 THEN 1 ELSE 0 END) as side_wins,
                ROUND(SUM(pnl_close), 2) as side_pnl
            FROM trades WHERE {where} AND pnl_close != 0
            GROUP BY side
            """,
            params,
        ).fetchall()

        by_side = {}
        for sr in side_row:
            sc = sr["cnt"] or 0
            sw = sr["side_wins"] or 0
            by_side[sr["side"]] = {
                "trades": sc,
                "win_rate": round(sw / sc * 100, 1) if sc > 0 else 0.0,
                "pnl": sr["side_pnl"] or 0.0,
            }

        # 最大回撤
        max_dd = self._compute_max_drawdown(conn, where, params)

        return {
            "trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": row["avg_pnl"] or 0.0,
            "best_trade": row["best_trade"] or 0.0,
            "worst_trade": row["worst_trade"] or 0.0,
            "max_drawdown_pct": max_dd,
            "by_side": by_side,
        }

    def _fallback_to_pnl(self, conn, days=None, since=None, until=None) -> dict:
        """当 pnl_close 全部为 0 时使用 pnl 字段"""
        conditions: list[str] = []
        params: list = []
        if days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            conditions.append("timestamp >= ?")
            params.append(cutoff)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)
        where = " AND ".join(conditions) if conditions else "1=1"

        row = conn.execute(
            f"""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                ROUND(SUM(pnl), 2) as total_pnl,
                ROUND(AVG(pnl), 2) as avg_pnl
            FROM trades WHERE {where}
            """,
            params,
        ).fetchone()
        total = row["total_trades"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        total_pnl = row["total_pnl"] or 0.0
        win_rate = round(wins / total * 100, 1) if total > 0 else 0.0
        max_dd = self._compute_max_drawdown(conn, where, params, pnl_field="pnl")
        return {
            "trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": row["avg_pnl"] or 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "max_drawdown_pct": max_dd,
            "by_side": {},
            "_fallback": True,
        }

    def _compute_max_drawdown(
        self,
        conn: sqlite3.Connection,
        where: str,
        params: list,
        pnl_field: str = "pnl_close",
    ) -> float:
        """计算最大回撤 (以百分比计)"""
        rows = conn.execute(
            f"""
            SELECT DATE(timestamp) as day, SUM({pnl_field}) as daily_pnl
            FROM trades WHERE {where} AND {pnl_field} != 0
            GROUP BY day ORDER BY day ASC
            """,
            params,
        ).fetchall()

        if not rows:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in rows:
            cumulative += r["daily_pnl"]
            if cumulative > peak:
                peak = cumulative
            if peak > 0:
                dd = (peak - cumulative) / peak * 100
                max_dd = max(max_dd, dd)
        return round(max_dd, 2)

    def _build_report(self, stats: dict, report_type: str, date_str: str) -> dict:
        """构建完整的报告字典"""
        return {
            "type": report_type,
            "date": date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
            "summary": self._generate_summary_text(stats),
        }

    def _generate_summary_text(self, stats: dict) -> str:
        """生成可读的中文总结"""
        if stats["trades"] < self.config.review_report_min_trades:
            return f"交易次数不足 ({stats['trades']} < {self.config.review_report_min_trades}), 暂不生成总结"
        parts = [
            f"共 {stats['trades']} 笔交易 | 胜率 {stats['win_rate']:.1f}% "
            f"({stats['wins']}胜/{stats['losses']}负)",
        ]
        if stats["total_pnl"] >= 0:
            parts.append(f"总盈亏 +{stats['total_pnl']:.2f} USDT")
        else:
            parts.append(f"总盈亏 {stats['total_pnl']:.2f} USDT")
        parts.append(f"最大回撤 {stats['max_drawdown_pct']:.2f}%")
        if stats.get("by_side"):
            for side, s in stats["by_side"].items():
                emoji = "🟢" if side == "buy" else "🔴"
                parts.append(f"{emoji} {side}: {s['trades']}笔 胜率{s['win_rate']:.0f}% 盈亏{s['pnl']:+.1f}")
        return " | ".join(parts)

    def _write_report(self, report: dict, report_type: str, date_str: str):
        """写入 JSON 文件"""
        report_dir = Path(self.config.review_report_dir)
        os.makedirs(str(report_dir), exist_ok=True)
        suffix = f"daily_{date_str}" if report_type == "daily" else f"weekly_{date_str}"
        path = report_dir / f"{suffix}.json"
        with open(str(path), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.debug(f"复盘报告已写入: {path}")
