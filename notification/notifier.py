"""
🔧 P2: 通知系统

支持渠道：
  1. 邮件 (SMTP)
  2. Webhook (企业微信/钉钉/Slack)
  3. 本地日志（最低成本方案）

触发事件：
  - signal: 新交易信号
  - trade: 成交
  - error: 异常
  - daily_report: 每日报告
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import httpx

from config import NotificationConfig

logger = logging.getLogger("notification")


class Notifier:
    """通知发送器"""

    def __init__(self, config: NotificationConfig):
        self.config = config
        self._http_client: Optional[httpx.Client] = None

    @property
    def http_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=10)
        return self._http_client

    def send(self, event_type: str, title: str, message: str) -> bool:
        """发送通知（按配置路由到不同渠道）"""
        if not self.config.enabled:
            logger.debug(f"[通知已禁用] {title}: {message[:50]}...")
            return False

        if event_type not in self.config.notify_on:
            return False

        sent = False

        if self.config.email_enabled:
            try:
                self._send_email(title, message)
                sent = True
            except Exception as e:
                logger.error(f"邮件通知失败: {e}")

        if self.config.webhook_enabled:
            try:
                self._send_webhook(title, message)
                sent = True
            except Exception as e:
                logger.error(f"Webhook 通知失败: {e}")

        if not sent:
            # 最低成本：写入日志文件
            self._log_to_file(event_type, title, message)
            sent = True

        return sent

    def notify_signal(self, strategy: str, signal: str, price: float, reason: str = ""):
        """新交易信号通知"""
        title = f"📊 信号: {strategy} → {signal.upper()}"
        message = f"策略: {strategy}\n信号: {signal}\n价格: {price}\n原因: {reason}"
        self.send("signal", title, message)

    def notify_trade(self, strategy: str, side: str, price: float, size: float, pnl: Optional[float] = None):
        """成交通知"""
        if pnl is not None:
            title = f"💰 交易: {side.upper()} {'✅' if pnl > 0 else '❌'} ${pnl:+.2f}"
        else:
            title = f"💰 交易: {side.upper()} @ ${price:.2f}"
        message = f"策略: {strategy}\n方向: {side}\n价格: {price}\n数量: {size}"
        if pnl:
            message += f"\n盈亏: ${pnl:+.2f}"
        self.send("trade", title, message)

    def notify_error(self, error_msg: str):
        """异常通知"""
        self.send("error", f"🚨 异常: {error_msg[:50]}", error_msg)

    def notify_daily_report(self, report: dict):
        """每日报告"""
        title = f"📈 日报: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        message = json.dumps(report, indent=2, ensure_ascii=False)
        self.send("daily_report", title, message)

    # ── 内部实现 ──

    def _send_email(self, subject: str, body: str):
        """通过 SMTP 发送邮件"""
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = f"[OKX Quant] {subject}"
        msg["From"] = self.config.smtp_user
        msg["To"] = self.config.notify_email

        context = ssl.create_default_context()
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
            server.starttls(context=context)
            server.login(self.config.smtp_user, self.config.smtp_pass)
            server.send_message(msg)

        logger.info(f"📧 邮件通知已发送: {subject}")

    def _send_webhook(self, title: str, message: str):
        """通过 Webhook 发送（钉钉/企微/Slack 兼容格式）"""
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"### {title}\n\n{message}\n\n---\nOKX Quant Agent",
            },
        }
        resp = self.http_client.post(
            self.config.webhook_url,
            json=payload,
        )
        resp.raise_for_status()
        logger.info(f"🔔 Webhook 通知已发送: {title}")

    @staticmethod
    def _log_to_file(event_type: str, title: str, message: str):
        """写入本地日志文件（最低成本通知）"""
        log_path = Path(f"logs/notifications.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{event_type}] {title}\n{message}\n\n")
        logger.info(f"📝 通知已写入日志: {title}")

    def close(self):
        if self._http_client:
            self._http_client.close()
