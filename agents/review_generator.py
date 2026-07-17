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
from agents.deepseek_caller import DeepSeekTrader
from data.db_manager import DatabaseManager

logger = logging.getLogger("review_generator")


class ReviewGenerator:
    """复盘报告生成器"""

    def __init__(self, config: AgentSystemConfig, db_path: str,
                 deepseek: DeepSeekTrader | None = None):
        self.config = config
        self.db_path = db_path
        self._db = DatabaseManager(db_path)
        self.deepseek = deepseek

    # ── 公共查询方法 ──

    def compute_monthly_stats(self) -> dict[str, Any]:
        """计算本月至今的统计

        Returns:
            dict: trades, wins, losses, win_rate, total_pnl, max_drawdown_pct, avg_trade_pnl
        """
        conn = self._get_conn()
        return self._compute_range_stats(conn, days=30)

    def compute_daily_stats(self, date_str: str | None = None) -> dict[str, Any]:
        """计算指定日期的统计"""
        conn = self._get_conn()
        target = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._compute_range_stats(conn, since=f"{target}T00:00:00", until=f"{target}T23:59:59")

    def compute_weekly_stats(self) -> dict[str, Any]:
        """计算过去 7 天的统计"""
        conn = self._get_conn()
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return self._compute_range_stats(conn, since=since)

    # ── 报告生成 ──

    def generate_daily_report(self) -> dict[str, Any]:
        """生成每日复盘报告并写入 JSON"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = self.compute_daily_stats(today)
        report = self._build_report(stats, "daily", today)
        report["period"] = {
            "start": f"{today}T00:00:00",
            "end": f"{today}T23:59:59",
        }

        # 获取该时间范围的交易行
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM trades WHERE trade_type='close' AND timestamp >= ? AND timestamp <= ?",
            (f"{today}T00:00:00", f"{today}T23:59:59"),
        ).fetchall()

        if rows:
            win_trades, loss_trades = self.extract_wins_and_losses(rows, conn)
            report["trades"] = {"wins": win_trades, "losses": loss_trades}
            if self.deepseek and len(rows) >= self.config.report_min_trades_for_analysis:
                report["ai_analysis"] = self._analyze_trades_with_deepseek(
                    win_trades, loss_trades, stats, "daily",
                    f"{today}T00:00:00", f"{today}T23:59:59",
                )
        else:
            report["trades"] = {"wins": [], "losses": []}

        report["pushed"] = False
        report["push_time"] = None
        self._write_report(report, "daily", today)
        logger.info(f"每日复盘报告: 胜率 {stats['win_rate']:.1f}%, 盈亏 {stats['total_pnl']:+.2f} USDT")
        return report

    def generate_weekly_report(self) -> dict[str, Any]:
        """生成每周复盘报告并写入 JSON"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        stats = self.compute_weekly_stats()
        report = self._build_report(stats, "weekly", today)
        report["period"] = {
            "start": week_ago,
            "end": datetime.now(timezone.utc).isoformat(),
        }

        # 获取该时间范围的交易行
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM trades WHERE trade_type='close' AND timestamp >= ?",
            (week_ago,),
        ).fetchall()

        if rows:
            win_trades, loss_trades = self.extract_wins_and_losses(rows, conn)
            report["trades"] = {"wins": win_trades, "losses": loss_trades}
            if self.deepseek and len(rows) >= self.config.report_min_trades_for_analysis:
                report["ai_analysis"] = self._analyze_trades_with_deepseek(
                    win_trades, loss_trades, stats, "weekly",
                    week_ago, datetime.now(timezone.utc).isoformat(),
                )
        else:
            report["trades"] = {"wins": [], "losses": []}

        report["pushed"] = False
        report["push_time"] = None
        self._write_report(report, "weekly", today)
        logger.info(f"每周复盘报告: 胜率 {stats['win_rate']:.1f}%, 盈亏 {stats['total_pnl']:+.2f} USDT")
        return report

    def get_recent_trades_summary(self, n: int = 5) -> str:
        """获取最近 N 笔已平仓交易的格式化摘要（供 DeepSeek 上下文注入）

        Returns:
            多行字符串，每行一笔交易
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE trade_type='close' ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
            if not rows:
                return "暂无近期交易"
            lines = []
            for r in reversed(rows):  # 正序呈现（最早的在前）
                pnl = r["pnl_close"] or r["pnl"] or 0
                side = r["side"]
                price = r["price"] or 0
                ts = r["timestamp"][:16] if r["timestamp"] else ""
                emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                lines.append(
                    f"  {emoji} {ts} | {side} @ ${price:.2f} | PnL {pnl:+.2f} USDT"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"获取近期交易摘要失败: {e}")
            return "近期交易数据不可用"
        finally:
            pass  # 共享连接，不关闭

    # ── 内部方法 ──

    def _get_conn(self) -> sqlite3.Connection:
        """返回共享连接（由 DatabaseManager 缓存，不要 close）"""
        conn = self._db.conn
        # 确保 trades 表 schema 完整（唯一权威定义在 data.db_manager）
        from data.db_manager import ensure_trades_schema
        ensure_trades_schema(conn)
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
        conditions = ["trade_type = 'close'"]
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

        # 总览统计（只统计 close 记录，避免 open 被 _update_pnl_close 回填后双倍计数）
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_close > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_close < 0 THEN 1 ELSE 0 END) as losses,
                ROUND(SUM(pnl_close), 2) as total_pnl,
                ROUND(SUM(fee), 2) as total_fee,
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
        total_fee = row["total_fee"] or 0.0

        # 如果 pnl_close 全部为 0 (旧数据), 回退到 pnl 字段
        if total == 0 or (total > 0 and total_pnl == 0):
            # 确认是否有任何交易记录
            any_trades = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()
            if any_trades and any_trades["c"] > 0:
                return self._fallback_to_pnl(conn, days, since, until)

        win_rate = round(wins / total * 100, 1) if total > 0 else 0.0

        # 按持仓方向拆分（close 行的 side 是平仓单方向：sell=平多, buy=平空）
        side_row = conn.execute(
            f"""
            SELECT CASE WHEN side='sell' THEN 'long' ELSE 'short' END as pos_side,
                COUNT(*) as cnt,
                SUM(CASE WHEN pnl_close > 0 THEN 1 ELSE 0 END) as side_wins,
                ROUND(SUM(pnl_close), 2) as side_pnl
            FROM trades WHERE {where}
            GROUP BY pos_side
            """,
            params,
        ).fetchall()

        by_side = {}
        for sr in side_row:
            sc = sr["cnt"] or 0
            sw = sr["side_wins"] or 0
            by_side[sr["pos_side"]] = {
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
            "total_fee": total_fee,
            "avg_pnl": row["avg_pnl"] or 0.0,
            "best_trade": row["best_trade"] or 0.0,
            "worst_trade": row["worst_trade"] or 0.0,
            "max_drawdown_pct": max_dd,
            "by_side": by_side,
        }

    def extract_wins_and_losses(
        self,
        rows: list[sqlite3.Row],
        conn: sqlite3.Connection | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """从 SQLite Row 列表中提取盈利和亏损交易详情

        close 行的 price 是平仓价（exit_price），side 是平仓单方向
        （sell=平多, buy=平空）。持仓方向与 entry_price 通过
        trade_group_id 关联的开仓行补全（需传入 conn）。

        Returns:
            (win_trades, loss_trades) — 每个元素是 dict:
            { pnl, side, reason, entry_price, exit_price, time }
            side 为持仓方向 "long" / "short"
        """
        # 批量查询关联开仓行: trade_group_id → (price, side)
        open_map: dict[str, tuple] = {}
        if conn is not None:
            group_ids = list({
                r["trade_group_id"] for r in rows
                if "trade_group_id" in r.keys() and r["trade_group_id"]
            })
            if group_ids:
                marks = ",".join("?" * len(group_ids))
                try:
                    for orow in conn.execute(
                        f"SELECT trade_group_id, price, side FROM trades "
                        f"WHERE trade_type='open' AND trade_group_id IN ({marks})",
                        group_ids,
                    ):
                        open_map[orow["trade_group_id"]] = (
                            orow["price"] or 0, orow["side"] or "",
                        )
                except sqlite3.Error as e:
                    logger.debug(f"查询关联开仓行失败: {e}")

        win_trades = []
        loss_trades = []
        for r in rows:
            pnl = r["pnl_close"] or r["pnl"] or 0
            reason = ""
            if r["decision"] and r["decision"] != "{}":
                try:
                    dec = json.loads(r["decision"])
                    reason = dec.get("reason", "")
                except (json.JSONDecodeError, TypeError):
                    reason = r["decision"][:100] if isinstance(r["decision"], str) else ""

            close_side = r["side"] or ""
            group_id = r["trade_group_id"] if "trade_group_id" in r.keys() else ""
            open_price, open_side = open_map.get(group_id, (0, ""))
            # 持仓方向：优先取开仓行（buy=long, sell=short），
            # 找不到时按平仓单方向推断（sell=平多→long, buy=平空→short）
            if open_side:
                pos_side = "long" if open_side == "buy" else "short"
            else:
                pos_side = "long" if close_side == "sell" else "short"

            trade = {
                "trade_id": r["id"],
                "pnl": pnl,
                "side": pos_side,
                "entry_price": open_price,
                "exit_price": r["price"] or 0,
                "reason": reason,
                "time": r["timestamp"],
            }
            if pnl > 0:
                win_trades.append(trade)
            elif pnl < 0:
                loss_trades.append(trade)
        return win_trades, loss_trades

    def _analyze_trades_with_deepseek(
        self,
        win_trades: list[dict],
        loss_trades: list[dict],
        stats: dict,
        period_type: str,
        period_start: str,
        period_end: str,
    ) -> dict:
        """调用 DeepSeek 分析盈亏模式"""
        if not self.deepseek:
            return {
                "wins": {"count": len(win_trades), "total_profit": sum(t["pnl"] for t in win_trades),
                         "patterns": []},
                "losses": {"count": len(loss_trades), "total_loss": sum(t["pnl"] for t in loss_trades),
                           "patterns": []},
                "summary": "",
            }

        context = {
            "period_type": period_type,
            "period_start": period_start,
            "period_end": period_end,
            "stats": {
                "trades": stats["trades"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": stats["win_rate"],
                "total_pnl": stats["total_pnl"],
                "total_fee": stats.get("total_fee", 0),
                "max_drawdown_pct": stats["max_drawdown_pct"],
            },
            "win_trades": win_trades[:10],
            "loss_trades": loss_trades[:10],
        }
        return self.deepseek.analyze_trade_report(context)

    def generate_monthly_report(self) -> dict[str, Any]:
        """生成月度复盘报告并写入 JSON"""
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1)
        else:
            next_month = now.replace(month=now.month + 1, day=1)

        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE trade_type='close' AND timestamp >= ? AND timestamp < ?",
                (month_start.isoformat(), next_month.isoformat()),
            ).fetchall()
        finally:
            pass  # 共享连接，不关闭

        stats = self.compute_monthly_stats()
        date_str = now.strftime("%Y-%m")
        report = self._build_report(stats, "monthly", date_str)
        report["period"] = {
            "start": month_start.isoformat(),
            "end": now.isoformat(),
        }

        # 提取盈亏交易
        if rows:
            win_trades, loss_trades = self.extract_wins_and_losses(rows, conn)
            report["trades"] = {
                "wins": win_trades,
                "losses": loss_trades,
            }
            # AI 分析
            if (self.deepseek and
                len(rows) >= self.config.report_min_trades_for_analysis):
                report["ai_analysis"] = self._analyze_trades_with_deepseek(
                    win_trades, loss_trades, stats, "monthly",
                    month_start.isoformat(), now.isoformat(),
                )
        else:
            report["trades"] = {"wins": [], "losses": []}

        report["pushed"] = False
        report["push_time"] = None

        self._write_report(report, "monthly", date_str)
        logger.info(
            f"月度交易报告: {stats['trades']}笔 胜率{stats['win_rate']:.1f}% "
            f"盈亏{stats['total_pnl']:+.2f} USDT"
        )
        return report

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
            "total_fee": 0.0,
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
            return (f"交易次数不足 ({stats['trades']} < "
                    f"{self.config.review_report_min_trades}), 暂不生成总结")
        parts = [
            f"共 {stats['trades']} 笔交易 | 胜率 {stats['win_rate']:.1f}% "
            f"({stats['wins']}胜/{stats['losses']}负)",
        ]
        if stats["total_pnl"] >= 0:
            parts.append(f"总盈亏 +{stats['total_pnl']:.2f} USDT")
        else:
            parts.append(f"总盈亏 {stats['total_pnl']:.2f} USDT")
        total_fee = stats.get("total_fee", 0)
        if total_fee > 0:
            parts.append(f"手续费 {total_fee:.2f} USDT")
        parts.append(f"最大回撤 {stats['max_drawdown_pct']:.2f}%")
        if stats.get("by_side"):
            for side, s in stats["by_side"].items():
                emoji = "🟢" if side == "buy" else "🔴"
                parts.append(f"{emoji} {side}: {s['trades']}笔 胜率{s['win_rate']:.0f}% 盈亏{s['pnl']:+.1f}")
        return " | ".join(parts)

    def _write_report(self, report: dict, report_type: str, date_str: str):
        """写入 JSON 文件到 data/reports/{type}/"""
        base_dir = Path(self.config.report_dir) / report_type
        os.makedirs(str(base_dir), exist_ok=True)

        if report_type == "daily":
            filename = f"daily_{date_str}.json"
        elif report_type == "weekly":
            filename = f"weekly_{date_str}.json"
        elif report_type == "monthly":
            filename = f"monthly_{date_str}.json"
        else:
            filename = f"{report_type}_{date_str}.json"

        path = base_dir / filename
        with open(str(path), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.debug(f"交易报告已写入: {path}")
