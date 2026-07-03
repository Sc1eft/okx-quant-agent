"""
ServerChan 推送封装 — 通过 ServerChan 将交易报告推送到微信

使用方式:
    notifier = ServerChanNotifier(sendkey="SCTxxxxx")
    ok = notifier.push_report("daily", "2026-07-03", report_dict)
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
import urllib.parse
from typing import Any

logger = logging.getLogger("notifier")


class ServerChanNotifier:
    """ServerChan 微信推送"""

    BASE_URL = "https://sctapi.ftqq.com"

    def __init__(self, sendkey: str):
        self._sendkey = sendkey

    def push_report(self, report_type: str, date_str: str,
                    report: dict[str, Any]) -> bool:
        """推送交易报告到微信

        Args:
            report_type: "daily" | "weekly" | "monthly"
            date_str: 如 "2026-07-03" / "2026-W27" / "2026-07"
            report: 完整报告 dict

        Returns:
            推送成功返回 True, 否则 False
        """
        stats = report.get("stats", {})
        ai = report.get("ai_analysis", {})

        # 构建标题
        type_labels = {"daily": "日报", "weekly": "周报", "monthly": "月报"}
        type_label = type_labels.get(report_type, "报告")
        title = f"📋 ETH 交易{type_label} | {date_str}"

        # 构建内容
        parts = [
            f"📊 总览",
            f"交易 {stats.get('trades', 0)} 笔 | "
            f"盈利 {stats.get('wins', 0)} 笔 亏损 {stats.get('losses', 0)} 笔",
            f"胜率 {stats.get('win_rate', 0)}% | 总盈亏: {stats.get('total_pnl', 0):+.2f} USDT",
            f"最大回撤: {stats.get('max_drawdown_pct', 0):.1f}%",
            "",
        ]

        # 盈利分析
        wins = ai.get("wins", {})
        if wins.get("patterns"):
            parts.append("🟢 盈利亮点")
            for p in wins["patterns"][:3]:
                parts.append(
                    f"• {p['pattern']}: {p.get('wins_count', 0)}笔 "
                    f"+{p.get('avg_profit', 0):.1f}"
                )
                if p.get("takeaway"):
                    parts.append(f"  → {p['takeaway']}")
            parts.append("")

        # 亏损分析
        losses = ai.get("losses", {})
        if losses.get("patterns"):
            parts.append("🔴 亏损分析")
            for p in losses["patterns"][:3]:
                parts.append(
                    f"• {p['pattern']}: {p.get('loss_count', 0)}笔 "
                    f"{p.get('avg_loss', 0):.1f}"
                )
                if p.get("cause"):
                    parts.append(f"  原因: {p['cause']}")
                if p.get("suggestion"):
                    parts.append(f"  建议: {p['suggestion']}")
            parts.append("")

        # 总结
        summary = ai.get("summary", "") or report.get("summary", "")
        if summary:
            parts.append(f"💡 {summary}")

        desp = "\n".join(parts)
        return self._send(title, desp)

    def push_text(self, title: str, content: str) -> bool:
        """发送纯文本消息"""
        return self._send(title, content)

    def _send(self, title: str, desp: str) -> bool:
        """调用 ServerChan API"""
        if not self._sendkey:
            logger.warning("ServerChan sendkey 未配置")
            return False
        url = f"{self.BASE_URL}/{self._sendkey}.send"
        data = urllib.parse.urlencode({"title": title, "desp": desp}).encode()
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                result = json.loads(body)
                if result.get("code") == 0:
                    logger.info(f"ServerChan 推送成功: {title[:30]}")
                    return True
                else:
                    logger.warning(
                        f"ServerChan 推送失败: {result.get('message', body[:100])}"
                    )
                    return False
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            logger.warning(f"ServerChan 请求异常: {e}")
            return False
