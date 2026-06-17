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

    同时读取多个源（RSS），按时间倒序排列，确保最新的新闻排最前。
    无需 API Key，全部失败时返回空列表——调用方自行降级。
    每条新闻附 timestamp 字段供动态权重计算。
    """
    import xml.etree.ElementTree as _ET
    import requests as _req

    pool: list[dict] = []
    seen: set[str] = set()

    def _add(title: str, source: str, timestamp: str = "") -> None:
        """添加到 pool（去重）。"""
        title = title.strip()
        if title and title not in seen and len(title) > 5:
            seen.add(title)
            pool.append({"title": title, "source": source, "timestamp": timestamp})

    # ── 从各源最多取的数量（分散来源，避免被单一日期的旧闻刷满）──
    _per_source = max(3, max_items)

    # ── 1. CoinDesk RSS（英文，更新快）──
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
                _add(title, "CoinDesk", timestamp=pub_date)
                if len(pool) >= _per_source * 3:
                    break
    except Exception:
        pass

    # ── 2. CoinTelegraph RSS（英文，更新快）──
    try:
        resp = _req.get(
            "https://cointelegraph.com/rss",
            timeout=8,
            headers={"User-Agent": _READER_AGENT},
        )
        if resp.status_code == 200:
            root = _ET.fromstring(resp.content)
            for item in root.findall(".//item"):
                title = item.findtext("title") or ""
                pub_date = item.findtext("pubDate") or ""
                _add(title, "CoinTelegraph", timestamp=pub_date)
                if len(pool) >= _per_source * 4:
                    break
    except Exception:
        pass

    # ── 3. Decrypt RSS（英文）──
    try:
        resp = _req.get(
            "https://decrypt.co/feed",
            timeout=8,
            headers={"User-Agent": _READER_AGENT},
        )
        if resp.status_code == 200:
            root = _ET.fromstring(resp.content)
            for item in root.findall(".//item"):
                title = item.findtext("title") or ""
                pub_date = item.findtext("pubDate") or ""
                _add(title, "Decrypt", timestamp=pub_date)
                if len(pool) >= _per_source * 5:
                    break
    except Exception:
        pass

    # ── 4. PANews sitemap.xml（中文，备选）──
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
                    _add(title_elem.text, "PANews", timestamp=pub_date)
                    if len(pool) >= _per_source * 3:
                        break
    except Exception:
        pass

    if not pool:
        return []

    # ── 按时间倒序排列（新版排前）──
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime as _pdt

    def _parse_ts(item: dict) -> datetime:
        ts = item.get("timestamp", "")
        if not ts:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d
        except (ValueError, TypeError):
            try:
                d = _pdt(ts)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return d
            except Exception:
                return datetime.min.replace(tzinfo=timezone.utc)

    pool.sort(key=_parse_ts, reverse=True)
    return pool[:max_items]
