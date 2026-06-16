"""Crypto news fetching and time formatting utilities.

Extracted from 9_EthereumLive.py for reuse across multiple pages.
"""
from __future__ import annotations
from datetime import datetime, timezone

__all__ = [
    "_fetch_crypto_news",
    "_fmt_relative_time",
    "_READER_AGENT",
]

_READER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fmt_relative_time(ts_str: str) -> str:
    """将 ISO / RFC 2822 时间戳转为相对时间（"2小时前"）。"""
    if not ts_str:
        return ""
    dt = None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            from email.utils import parsedate_to_datetime as _pdt
            dt = _pdt(ts_str)
        except Exception:
            return ""
    if dt is None:
        return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    hours = diff.total_seconds() / 3600
    if hours < 0:
        return "刚刚"
    if hours < 1:
        mins = int(diff.total_seconds() / 60)
        return f"{mins}分钟前" if mins > 0 else "刚刚"
    if hours < 24:
        return f"{int(hours)}小时前"
    days = int(hours / 24)
    if days < 30:
        return f"{days}天前"
    return dt.strftime("%m-%d")


def _fetch_crypto_news(max_items: int = 5) -> list[dict]:
    """获取近期加密货币新闻及政策。

    尝试多个源（sitemap / RSS），无需 API Key。
    全部失败时返回空列表——调用方自行降级。
    每条新闻附 timestamp 字段供动态权重计算。
    """
    import xml.etree.ElementTree as _ET
    import requests as _req

    pool: list[dict] = []
    seen: set[str] = set()

    def _add(title: str, source: str, timestamp: str = "") -> bool:
        """添加到 pool（去重），返回 True 表示已满。"""
        title = title.strip()
        if title and title not in seen and len(title) > 5:
            seen.add(title)
            pool.append({"title": title, "source": source, "timestamp": timestamp})
            return len(pool) >= max_items
        return False

    # ── 1. PANews sitemap.xml（Google News 标准 XML，稳定）──
    try:
        resp = _req.get(
            "https://www.panewslab.com/sitemap.xml",
            timeout=8,
            headers={"User-Agent": _READER_AGENT},
        )
        if resp.status_code == 200:
            root = _ET.fromstring(resp.content)
            _ns = {
                "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
                "news": "http://www.google.com/schemas/sitemap-news/0.9",
            }
            for url_elem in root.findall(".//s:url", _ns):
                title_elem = url_elem.find("news:news/news:title", _ns)
                if title_elem is not None and title_elem.text:
                    pub_elem = url_elem.find("news:news/news:publication_date", _ns)
                    pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ""
                    if _add(title_elem.text, "PANews", timestamp=pub_date):
                        return pool[:max_items]
    except Exception:
        pass

    # ── 2. CoinDesk RSS（英文备份）──
    if len(pool) < max_items:
        try:
            resp = _req.get(
                "https://www.coindesk.com/arc/outboundfeeds/rss/",
                timeout=8,
                headers={"User-Agent": _READER_AGENT},
            )
            if resp.status_code == 200:
                root = _ET.fromstring(resp.content)
                for item in root.findall(".//item"):
                    title = item.findtext("title") or ""
                    pub_date = item.findtext("pubDate") or ""
                    if _add(title, "CoinDesk", timestamp=pub_date):
                        return pool[:max_items]
        except Exception:
            pass

    return pool[:max_items]
