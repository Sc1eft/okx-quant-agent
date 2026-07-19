"""日线趋势策略 — 因子 IC 评估证据驱动

证据链（scripts/factor_ic_eval.py，3 年 1h 数据）：
  - 1h 层事件 IC≈0 是噪声；1d 层事件有真预测力
  - boll_break_upper 1d：r24=+112bps（基线仅 5.4），t=2.72，ICIR=0.56
  - kdj_bullish_cross 1d：r24=+40.7bps，t=2.14，ICIR=0.42
  - 日线 EMA50 regime：站上/跌破次日条件收益 +16.2 / -4.3bps
  - 纯 regime 基线（scripts/daily_trend_baseline.py）3 年 +62%

规则（全部基于「已收盘」日线，无前视）：
  - 闸门：日线收盘 > 日线 EMA(trend_span) → 多头 regime
  - 入场 entry_mode="regime" ：进入多头 regime 即上车（基线行为）
         entry_mode="trigger"：多头 regime 内，日线布林上轨突破或 KDJ 金叉
           （trigger_lookback_days 天内触发过即可）再上车
  - 出场：日线收盘跌破 EMA(trend_span) → 卖出
  - 仅多头/空仓：做空统计支持不足（扩展实验 F）

信号落点：决策日在日线收盘时刻，信号落在当天最后一根 intraday K 线上，
引擎 next-bar 执行 = 次日第一根 K 线开盘成交（≈次日开盘价，无未来函数）。
增量模式（模拟盘）在日界后的第一根 K 线返回决策，成交价同样 ≈ 次日开盘。

引擎级止损/止盈/移动止损按分钟线噪声设计，对周线级持仓是纯干扰，
本策略声明 use_engine_stops = False，出场完全由 regime 翻转负责。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, PositionInfo, Signal, StrategyResult

_BOLL_WINDOW = 20
_KDJ_PERIOD = 9


def _build_daily(df: pd.DataFrame, span: int) -> pd.DataFrame:
    """intraday K 线 → 日线 + 指标列：ema / bull / trig(触发名)"""
    ohlc = df.resample("1D", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    close, high, low = ohlc["close"], ohlc["high"], ohlc["low"]

    ohlc["ema"] = close.ewm(span=span, adjust=False).mean()
    ohlc["bull"] = close > ohlc["ema"]

    # BOLL 上轨突破（与 IC 评估同口径：20, 2σ）
    mid = close.rolling(_BOLL_WINDOW).mean()
    upper = mid + 2.0 * close.rolling(_BOLL_WINDOW).std()
    break_up = (close >= upper) & ~(close >= upper).shift(1, fill_value=False)

    # KDJ 金叉（9, ewm3, ewm3 — 与 macd_agent/IC 评估同口径）
    low_n = low.rolling(_KDJ_PERIOD).min()
    high_n = high.rolling(_KDJ_PERIOD).max()
    rsv = (close - low_n) / (high_n - low_n + 1e-10) * 100
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()
    kdj_bull = (k.shift(1) <= d.shift(1)) & (k > d)

    trig = pd.Series("", index=ohlc.index)
    trig[break_up.fillna(False)] = "boll"
    trig[kdj_bull.fillna(False)] = "kdj"
    trig[break_up.fillna(False) & kdj_bull.fillna(False)] = "boll+kdj"
    ohlc["trig"] = trig
    return ohlc


def _decide(ohlc: pd.DataFrame, warmup: int, mode: str, lookback: int) -> dict:
    """逐日状态机。返回 {日期: (Signal, reason)}，仅含非 HOLD 决策。

    ohlc 必须全是「已收盘」日线（调用方负责剔除未完成日）。
    """
    decisions: dict = {}
    pos: str | None = None
    trigs = ohlc["trig"].to_numpy()
    bulls = ohlc["bull"].to_numpy()
    idx = ohlc.index
    for i in range(warmup, len(ohlc)):
        bull = bool(bulls[i])
        if pos is None:
            if not bull:
                continue
            if mode == "trigger":
                lo = max(0, i - lookback + 1)
                fired = [t for t in trigs[lo:i + 1] if t]
                if not fired:
                    continue
                reason = f"日线触发入场（{'/'.join(fired)}）"
            else:
                reason = "EMA 多头 regime 入场"
            decisions[idx[i]] = (Signal.BUY, reason)
            pos = "long"
        elif not bull:
            decisions[idx[i]] = (Signal.SELL, "跌破 EMA 离场")
            pos = None
    return decisions


class DailyTrendStrategy(BaseStrategy):
    """日线趋势策略 — EMA 闸门 + 日线触发，跌破离场（多头/空仓）"""

    # 出场由 regime 翻转负责，引擎的分钟级止损模型对本策略是噪声
    use_engine_stops = False

    def __init__(self, name: str, params: dict):
        super().__init__(name, params)
        self._span = int(params.get("trend_span", 50))
        self._mode = str(params.get("entry_mode", "trigger"))
        if self._mode not in {"regime", "trigger"}:
            raise ValueError(f"entry_mode 仅支持 regime/trigger，收到 {self._mode!r}")
        self._lookback = int(params.get("trigger_lookback_days", 3))
        self._warmup_days = max(self._span, _BOLL_WINDOW)
        self._min_bars = 2
        self._pos_state: str | None = None  # None=空仓, "long"=多（增量模式跨调用持久化）
        # 增量模式缓冲：warmup + 130 天（EMA 收敛余量）。根数按基础周期换算（懒初始化）
        self._bars_per_day: int | None = None

    @property
    def description(self) -> str:
        return (
            f"日线趋势 — EMA{self._span} 定方向，{self._mode} 入场，跌破离场"
            f"（IC 证据驱动，仅多头）"
        )

    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        signals = pd.Series(Signal.HOLD, index=df.index)
        reasons = pd.Series("", index=df.index)

        daily = _build_daily(df, self._span)
        # 最后一天视为未完成（无法从数据区分「恰在 23:00 收齐」），不决策
        completed = daily.iloc[:-1]
        n_trades = 0
        if len(completed) > self._warmup_days:
            decisions = _decide(completed, self._warmup_days, self._mode, self._lookback)
            # 决策日 → 当天最后一根 intraday K 线（引擎次日开盘成交）
            day_key = df.index.normalize()
            last_bar_of_day = (
                pd.Series(np.arange(len(df)), index=day_key).groupby(level=0).last().to_dict()
            )
            for day, (sig, reason) in decisions.items():
                pos = last_bar_of_day.get(day)
                if pos is not None:
                    signals.iloc[pos] = sig
                    reasons.iloc[pos] = reason
                    n_trades += 1

        out = df.copy()
        out["signal"] = signals
        out["reason"] = reasons
        return StrategyResult(
            signals=out,
            metadata={
                "strategy": self.name,
                "trend_span": self._span,
                "entry_mode": self._mode,
                "decisions": n_trades,
            },
        )

    # ── 增量模式（模拟盘用） ──

    def _infer_bars_per_day(self) -> int:
        secs = self._bar_buffer.index.to_series().diff().dropna().dt.total_seconds().median()
        if not np.isfinite(secs) or secs <= 0:
            return 24
        return max(1, round(86400 / secs))

    def on_bar(self, bar: pd.Series) -> Signal:
        """逐根 K 线处理：日界后的第一根 K 线到来时，上一自然日确认收盘，
        对「刚收盘日」做一次增量决策。

        状态机与批处理 _decide 完全同规则：空仓遇多头 regime（trigger 模式
        还需近 trigger_lookback_days 天内有触发）→ BUY；持仓遇跌破 EMA → SELL。
        持仓状态跨调用持久化（self._pos_state），日界每天只出现一次，
        天然去重。缓冲区仅用于指标预热，截断不影响状态机。
        """
        new_df = bar.to_frame().T.infer_objects(copy=False)
        if self._bar_buffer is None:
            self._bar_buffer = new_df
        else:
            self._bar_buffer = pd.concat([self._bar_buffer, new_df])

        if len(self._bar_buffer) < 2:
            return Signal.HOLD
        if self._bars_per_day is None and len(self._bar_buffer) >= 30:
            self._bars_per_day = self._infer_bars_per_day()
        # 缓冲 = warmup + 130 天：EMA 为无限记忆，截断会改变其取值，
        # 留 ~3.6×span 的收敛余量可让增量模式与批处理（全量历史）的
        # EMA 差异降到 0.1% 以下，避免边界日决策漂移
        cap = (self._warmup_days + 130) * (self._bars_per_day or 24)
        if len(self._bar_buffer) > cap:
            self._bar_buffer = self._bar_buffer.iloc[-cap:]

        days = self._bar_buffer.index.normalize()
        if days[-1] == days[-2]:
            return Signal.HOLD  # 当日未收盘，无新决策

        daily = _build_daily(self._bar_buffer, self._span)
        completed = daily.iloc[:-1]
        if len(completed) <= self._warmup_days:
            return Signal.HOLD

        i = len(completed) - 1  # 刚收盘日
        bull = bool(completed["bull"].iloc[i])
        sig = Signal.HOLD
        if self._pos_state is None:
            enter = bull
            if enter and self._mode == "trigger":
                lo = max(0, i - self._lookback + 1)
                enter = bool((completed["trig"].iloc[lo:i + 1] != "").any())
            if enter:
                sig = Signal.BUY
                self._pos_state = "long"
                self.position = PositionInfo(
                    entry_price=float(bar["close"]),
                    entry_time=self._bar_buffer.index[-1],
                    size=1.0,
                    highest_price=float(bar["close"]),
                )
        elif not bull:
            sig = Signal.SELL
            self._pos_state = None
            self.position = None
        return sig
