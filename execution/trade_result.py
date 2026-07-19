"""
统一交易结果（trade dict）定义 — 所有模拟执行层的返回形状

背景：PaperAccount（现货沙盒）与 FuturesAccount（合约沙盒）此前各自拼装
trade dict，字段不一致（balance_after / wallet_after、拒绝时缺 time/size），
消费方只能靠 duck-typing 猜测。本模块提供唯一构造函数，两个账户层统一从
这里产出，撮合/记账逻辑仍归各账户自己。

Canonical schema
----------------
成交（成功）:
  time   str   ISO 时间戳（UTC）
  side   str   buy/sell/short/cover（现货）｜ open_long/open_short/
               close_long/close_short/liquidation（合约）
  price  float 成交价（round 2）
  size   float 成交量 ETH（round 6）
  fee    float 手续费 USDT（round 4）
  pnl    float 已实现盈亏（仅平仓/强平时存在，round 2）——开仓不带此键
  ...           账户层附加字段（cost/revenue/margin/leverage/
               balance_after/wallet_after 等，原样透传）

拒绝（未成交）:
  time/side 同上；price=0.0, size=0.0, fee=0.0；note=str 拒绝原因

约定:
  - 拒绝用 note 字段表达（实盘 TradeExecutor 结果层用的是 success/error，
    两套语义不同，不要混用 is_rejected 判断实盘结果）
  - "pnl" 键只出现在平仓类交易上——ai_executor / PaperEngine 以
    "pnl" in trade 判定平仓，开仓不得携带该键
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def make_trade(
    side: str,
    price: float,
    size: float,
    *,
    fee: float = 0.0,
    pnl: Optional[float] = None,
    time: Optional[str] = None,
    **extra: Any,
) -> dict:
    """构造成交 trade dict（core 字段 + 账户层附加字段）

    time: ISO 时间戳（通常为该笔交易对应的 K 线时间）；
          缺省用当前墙钟时间（实时单）。模拟盘回放历史 K 线时必须传入
          bar 时间，否则权益曲线/交易标记全部塌缩到回放时刻。
    """
    trade = {
        "time": time or datetime.now(timezone.utc).isoformat(),
        "side": side,
        "price": round(price, 2),
        "size": round(size, 6),
        "fee": round(fee, 4),
    }
    if pnl is not None:
        trade["pnl"] = round(pnl, 2)
    trade.update(extra)
    return trade


def reject_trade(side: str, note: str, *, time: Optional[str] = None) -> dict:
    """构造拒绝 trade dict（未成交，note 为原因）"""
    return {
        "time": time or datetime.now(timezone.utc).isoformat(),
        "side": side,
        "price": 0.0,
        "size": 0.0,
        "fee": 0.0,
        "note": note,
    }


def is_rejected(trade: Optional[dict]) -> bool:
    """模拟层 trade dict 是否为拒绝（未成交）"""
    return not trade or bool(trade.get("note"))
