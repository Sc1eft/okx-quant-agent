"""
Agent 2 — 信息收集员（新闻 + 基本面）

职责:
  1. 定时（每 60s）从 4 个 RSS 源获取新闻
  2. 对每条新闻进行影响权重评分
  3. 高权重新闻推送到 Queue B
  4. 去重（已推送过的新闻不再推送）

权重评分规则（来自设计文档）:
  - ETH 大额转入交易所 (>5000 ETH)  0.9  — 阶段一暂缺（阶段三）
  - 重大监管新闻                   0.8
  - ETH2.0/升级相关                 0.7
  - 巨鲸地址异动                   0.6
  - 普通市场新闻                   0.3
  - Gas 费异常                     0.4  — 阶段一暂缺
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import sys
if "." not in sys.path and "" not in sys.path:
    sys.path.insert(0, "")

from agents.event_bus import EventBus, AgentEvent, AgentEventType
from agents.config import AgentSystemConfig
from frontend.utils.eth_news import _fetch_crypto_news

logger = logging.getLogger("agent2")

# ── 新闻关键词 → 权重映射 ──

_HIGH_IMPACT_KEYWORDS = [
    # 监管
    (r"regulat|SEC|CFTC|ban|禁止|监管|合规|牌照|license", 0.8),
    (r"ETF|现货ETF|以太坊ETF|ETH ETF|批准|approve|deny", 0.75),
    # 安全事件
    (r"hack|exploit|被盗|攻击|安全漏洞|漏洞|security|breach", 0.8),
    # ETH 2.0 / 升级
    (r"ETH 2\.0|以太坊2\.0|合并|merge|升级|upgrade|上海|shanghai|坎昆|cancun|dencun|EIP-\d+", 0.7),
    # 宏观经济
    (r"美联储|fed|interest rate|加息|降息|rate cut|CPI|通胀|inflation", 0.65),
    # 交易所动态
    (r"binance|okx|coinbase|上币|delist|退市|破产|bankrupt", 0.6),
]

_MEDIUM_IMPACT_KEYWORDS = [
    (r"巨鲸|whale|大额|large transfer|数百万|millions", 0.6),
    (r"机构|institutional|adoption|采用|partnership|合作", 0.5),
    (r"NFT|defi|DeFi|tvl|流动性|staking|质押|liquidity", 0.4),
    (r"比特币|bitcoin|btc|BTC|BTC主导|dominance", 0.4),
    (r"期权|option|期货|future|derivative|衍生品|持仓|OI|open interest", 0.45),
]


def _score_news_item(title: str, source: str) -> float:
    """对一条新闻进行影响权重评分，返回 0~1 的分数"""
    text = (title + " " + source).lower()
    score = 0.1  # 基础分

    # 高影响关键词
    for pattern, weight in _HIGH_IMPACT_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            score = max(score, weight)

    # 中影响关键词（取最高）
    for pattern, weight in _MEDIUM_IMPACT_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            score = max(score, weight)

    return min(score, 1.0)


class Agent2:
    """Agent 2 — 新闻信息收集员"""

    def __init__(self, config: AgentSystemConfig, event_bus: EventBus):
        self.config = config
        self.bus = event_bus

        # 已推送新闻的标题 set（去重）
        self._seen_titles: set[str] = set()
        self._running = False

        self._stats = {
            "fetch_count": 0,
            "news_seen": 0,
            "news_pushed": 0,
            "start_time": "",
        }

    async def run(self):
        """启动 Agent 2 主循环"""
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Agent 2 (信息收集员) 启动")

        while self._running:
            try:
                await self._fetch_and_score()
            except Exception as e:
                logger.error(f"Agent 2 抓取异常: {e}")

            # 等待下一次抓取
            await asyncio.sleep(self.config.agent2_fetch_interval_seconds)

    async def stop(self):
        """停止 Agent 2"""
        self._running = False
        logger.info("Agent 2 已停止")

    async def _fetch_and_score(self):
        """抓取新闻 → 评分 → 推送"""
        self._stats["fetch_count"] += 1
        news_list = _fetch_crypto_news(max_items=self.config.agent2_max_news_per_fetch)

        if not news_list:
            logger.debug("Agent 2: 本轮无新闻")
            return

        for item in news_list:
            title = item.get("title", "")
            source = item.get("source", "")

            if title in self._seen_titles:
                continue
            self._seen_titles.add(title)
            self._stats["news_seen"] += 1

            # 权重评分
            weight = _score_news_item(title, source)
            item["weight"] = round(weight, 2)

            # 低权重不推送
            if weight < self.config.agent2_min_weight_threshold:
                logger.debug(f"新闻权重不足: {weight:.2f} < {self.config.agent2_min_weight_threshold}")
                continue

            # 推送到 Queue B
            urgency = "high" if weight >= 0.7 else ("medium" if weight >= 0.5 else "low")
            event = AgentEvent(
                type=AgentEventType.NEWS_EVENT,
                source="agent2",
                data=item,
                confidence=weight,
                urgency=urgency,
            )
            await self.bus.publish_b(event)
            self._stats["news_pushed"] += 1
            logger.info(f"\U0001f4f0 Agent 2 push: [{source}] {title[:60]}... (w={weight:.2f})")

        # 控制 seen 集合大小
        if len(self._seen_titles) > 1000:
            self._seen_titles = set(list(self._seen_titles)[-500:])

    def get_status(self) -> dict:
        return {
            "running": self._running,
            **self._stats,
        }
