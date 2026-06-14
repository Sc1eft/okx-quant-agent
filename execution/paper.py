"""
本地模拟盘引擎
"""

from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from pathlib import Path

from config import Config

logger = logging.getLogger("execution.paper")


class PaperAccount:
    """模拟账户"""

    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position = 0.0  # BTC 持仓数量
        self.trades: list[dict] = []
        self.equity_history: list[dict] = []

    @property
    def equity(self) -> float:
        return self.balance + self.position * self.last_price if hasattr(self, "last_price") else self.balance

    def save_state(self, path: str = "data/paper_state.json"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        state = {
            "balance": self.balance,
            "position": self.position,
            "trades": self.trades[-100:],  # 保留最近 100 笔
        }
        with open(path, "w") as f:
            json.dump(state, f, indent=2)

    def report(self):
        print(f"\n📋 模拟盘状态")
        print(f"  余额: ${self.balance:,.2f}")
        print(f"  持仓: {self.position:.6f} BTC")
        print(f"  总交易: {len(self.trades)} 笔")
        if self.trades:
            winning = [t for t in self.trades if t.get("pnl", 0) > 0]
            print(f"  胜率: {len(winning)/len(self.trades)*100:.1f}%")


class PaperEngine:
    """模拟盘引擎"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.account = PaperAccount()

    def run(self):
        """运行模拟盘（持续从数据源获取新数据并执行策略）"""
        logger.info("🚀 启动模拟盘模式")
        logger.info(f"  初始资金: ${self.account.initial_balance}")
        logger.info(f"  交易对: {self.cfg.trading.symbol}")
        logger.info(f"  周期: {self.cfg.trading.primary_timeframe}")
        print("\n⚠️  模拟盘模式需要连接 OKX API 获取实时行情")
        print("   运行方式:")
        print("   python main.py --mode paper")
        print("   或通过 main.py 的事件循环驱动\n")

        # 第一版：打印占位
        self.account.report()
