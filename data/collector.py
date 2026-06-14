"""
行情数据采集
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from config import Config
from okx_client import OKXClient
from data.storage import DataStore

logger = logging.getLogger("data.collector")


class DataCollector:
    """行情数据采集器"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = DataStore(cfg)
        self.client = OKXClient(cfg.exchange)

    def download_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 300,
        after: Optional[int] = None,
    ) -> int:
        """
        下载 K 线并存入数据库
        支持增量：先查已有数据的最新时间戳，只下载缺失部分
        """
        # 增量：查已有数据的最新时间戳
        if not after:
            _, max_ts = self.store.get_date_range(symbol, timeframe)
            if max_ts:
                after = max_ts
                logger.info(f"增量更新，已有数据截至 {max_ts}")

        raw = self.client.get_klines(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            after=after,
        )

        if not raw:
            logger.info(f"无新 K 线数据")
            return 0

        inserted = self.store.insert_klines(symbol, timeframe, raw)
        logger.info(f"下载 {symbol} {timeframe}: {len(raw)} 条，新增 {inserted} 条")
        return inserted

    def download_historical(
        self,
        symbol: str,
        timeframe: str = "1h",
        total_candles: int = 1000,
    ) -> int:
        """
        下载历史 K 线（OKX 单次最多 300 条，需要分页）
        使用 before 参数向前翻页
        """
        total_inserted = 0
        fetched = 0
        before = None

        while fetched < total_candles:
            batch_size = min(300, total_candles - fetched)
            raw = self.client.get_klines(
                symbol=symbol,
                timeframe=timeframe,
                limit=batch_size,
                before=before,
            )
            if not raw:
                break

            inserted = self.store.insert_klines(symbol, timeframe, raw)
            total_inserted += inserted
            fetched += len(raw)

            # 获取最早一条的时间戳作为下次翻页的 before
            oldest_ts = raw[-1]["timestamp"]
            before = oldest_ts

            logger.info(f"历史下载进度: {fetched}/{total_candles} (新增 {inserted})")
            time.sleep(0.2)  # API 限速

        logger.info(f"历史下载完成: {symbol} {timeframe}，共获取 {fetched} 条，新增 {total_inserted}")
        return total_inserted

    def close(self):
        self.client.close()
        self.store.close()
