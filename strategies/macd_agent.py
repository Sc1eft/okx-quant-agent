"""
策略 4: MACD 多周期共振（与实盘 Agent 决策链路同源）

这是实盘 agents/rule_decider.py 规则决策器的回测移植版：
  - 指标/信号定义与 ChangeDetector 完全一致（MACD 金叉死叉/柱线零轴、
    KDJ 穿越（≥15m）/超买超卖、布林破轨，squeeze 期间抑制 KDJ）
  - 信号冷却时间与实盘相同（秒，按信号类型 × 周期）
  - 评分与实盘共用 agents.confidence_scorer.score_signals 纯函数
    （方向映射 + 周期权重默认值来自 AgentSystemConfig）
  - 入场门槛 = raw_score ≥ score_threshold 且一致性 ≥ min_confidence，
    与 RuleDecider 相同

高周期（默认 1h/1d）由输入 K 线 resample 合成，信号在高周期 K 线
收盘时刻触发，无前视。止损/止盈/移动止损/超时退出由回测引擎的
intrabar 模型按 cfg.strategy.* 参数统一处理（与其他策略一致）。

可选 1d 趋势过滤（params["trend_filter"]="ema50"）：仅当上一根
已收盘日线站在日线 EMA(span) 之上时才允许开多、跌破才允许开空
（卖出/平空退出不受限），用于切掉逆势接刀。ema 周期用
params["trend_filter_span"] 调整，默认 50。

可选做空（params["allow_short"]=True）：空仓时 score ≤ -threshold
且一致性达标 → 开空；持空时 score ≥ +threshold → 平空。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from agents.confidence_scorer import score_signals
from agents.config import AgentSystemConfig
from strategies.base import BaseStrategy, StrategyResult, Signal, PositionInfo

logger = logging.getLogger("strategy.macd_agent")

_TF_SECONDS = {"3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_KDJ_CROSS_MIN_TF_SECONDS = 900  # KDJ 穿越信号仅限 >=15m


def _tf_events(df: pd.DataFrame, tf: str, cooldowns: dict[str, float]) -> pd.DataFrame:
    """计算单个周期的逐 bar 信号事件（向量化指标 + 逐 bar 冷却）。

    返回 DataFrame: index=触发时间（该 bar 收盘时刻），
    columns=[signal, timeframe, confidence]。
    信号定义与 agents/change_detector.py 保持一致。
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # ── MACD ──
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line

    macd_bull = (macd_line.shift(1) <= signal_line.shift(1)) & (macd_line > signal_line)
    macd_bear = (macd_line.shift(1) >= signal_line.shift(1)) & (macd_line < signal_line)
    hist_pos = (hist.shift(1) < 0) & (hist >= 0)
    hist_neg = (hist.shift(1) > 0) & (hist <= 0)

    # ── KDJ ──
    low_n = low.rolling(9).min()
    high_n = high.rolling(9).max()
    rsv = (close - low_n) / (high_n - low_n + 1e-10) * 100
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()
    j = 3 * k - 2 * d
    kdj_bull = (k.shift(1) <= d.shift(1)) & (k > d)
    kdj_bear = (k.shift(1) >= d.shift(1)) & (k < d)
    zone = pd.Series("normal", index=df.index)
    zone[j >= 100] = "overbought"
    zone[j <= 0] = "oversold"
    zone_prev = zone.shift(1).fillna("normal")
    zone_ob = (zone == "overbought") & (zone_prev != "overbought")
    zone_os = (zone == "oversold") & (zone_prev != "oversold")

    # ── BOLL ──
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = mid + 2.0 * std
    lower = mid - 2.0 * std
    bw = (upper - lower) / mid
    squeeze = bw < bw.rolling(20).median() * 0.7
    squeeze = squeeze.fillna(False)
    break_up = (close >= upper) & ~(close >= upper).shift(1, fill_value=False)
    break_dn = (close <= lower) & ~(close <= lower).shift(1, fill_value=False)

    # ── 逐 bar 生成事件（冷却语义同 ChangeDetector）──
    tf_secs = _TF_SECONDS[tf]
    kdj_cross_allowed = tf_secs >= _KDJ_CROSS_MIN_TF_SECONDS
    events = []
    last_fired: dict[str, float] = {}

    def _fire(ts, name, conf):
        cd = cooldowns.get(name, 60.0)
        if ts - last_fired.get(name, -1e18) < cd:
            return
        last_fired[name] = ts
        events.append({"ts": ts, "signal": name, "timeframe": tf, "confidence": conf})

    idx = df.index
    for i in range(len(df)):
        if i < 26:  # MACD 预热
            continue
        ts = idx[i].timestamp() + tf_secs  # 收盘时刻触发
        is_squeeze = bool(squeeze.iloc[i])

        if macd_bull.iloc[i]:
            _fire(ts, "macd_bullish_cross", 0.85)
        elif macd_bear.iloc[i]:
            _fire(ts, "macd_bearish_cross", 0.85)
        if hist_pos.iloc[i]:
            _fire(ts, "macd_hist_positive", 0.7)
        elif hist_neg.iloc[i]:
            _fire(ts, "macd_hist_negative", 0.7)

        if not is_squeeze:
            if kdj_cross_allowed and kdj_bull.iloc[i]:
                _fire(ts, "kdj_bullish_cross", 0.65)
            elif kdj_cross_allowed and kdj_bear.iloc[i]:
                _fire(ts, "kdj_bearish_cross", 0.65)
            if zone_ob.iloc[i]:
                _fire(ts, "kdj_overbought", 0.6)
            elif zone_os.iloc[i]:
                _fire(ts, "kdj_oversold", 0.6)

        if break_up.iloc[i]:
            _fire(ts, "boll_break_upper", 0.75)
        elif break_dn.iloc[i]:
            _fire(ts, "boll_break_lower", 0.75)

    return pd.DataFrame(events)


def _daily_regime(df: pd.DataFrame, span: int) -> np.ndarray:
    """1d 趋势 regime（无前视）：+1=多头, -1=空头, 0=预热/未知

    规则：上一根「已收盘」日线收盘 > 日线 EMA(span) → +1，< → -1。
    当前正在形成的日线不参与（shift(1)）。返回与 df 行对齐的 int8 数组。
    """
    daily_close = df["close"].resample("1D", label="left", closed="left").last().dropna()
    ema = daily_close.ewm(span=span, adjust=False).mean()
    ok = (daily_close > ema).shift(1)  # 只用已收盘日线；首日为 NaN
    regime = pd.Series(0, index=daily_close.index, dtype="int8")
    regime[ok == True] = 1   # NaN 两个比较都为 False，保持 0
    regime[ok == False] = -1
    return regime.reindex(df.index.normalize()).fillna(0).to_numpy(dtype="int8")


def _daily_regime_mask(df: pd.DataFrame, span: int) -> np.ndarray:
    """多头 regime 掩码（_daily_regime == +1）"""
    return _daily_regime(df, span) == 1


class MACDAgentStrategy(BaseStrategy):
    """MACD 多周期共振策略 — 实盘 RuleDecider 的回测同源版"""

    def __init__(self, name: str, params: dict):
        super().__init__(name, params)
        defaults = AgentSystemConfig()
        self._directions = params.get("directions", dict(defaults.confidence_signal_directions))
        self._tf_weights = params.get("tf_weights", dict(defaults.confidence_timeframe_weights))
        self._score_threshold = params.get("score_threshold", defaults.agent3_rule_score_threshold)
        self._min_confidence = params.get("min_confidence", defaults.agent3_rule_min_confidence)
        self._higher_tfs = params.get("higher_tfs", ["1h", "1d"])
        # 1d 趋势过滤（实验）：None=关闭，"ema50"=上一根收盘日线 > 日线 EMA 才允许多
        self._trend_filter = params.get("trend_filter")
        self._trend_filter_span = int(params.get("trend_filter_span", 50))
        # 做空开关（回测）：空仓遇强空头共振时开空
        self._allow_short = bool(params.get("allow_short", False))
        self._position_side: str | None = None  # None=空仓, "long"=多, "short"=空
        # 冷却：与 change_detector 的模块常量一致（秒）
        self._cooldowns = {
            "macd_bullish_cross": 60, "macd_bearish_cross": 60,
            "macd_hist_positive": 60, "macd_hist_negative": 60,
            "kdj_bullish_cross": 120, "kdj_bearish_cross": 120,
            "kdj_overbought": 120, "kdj_oversold": 120,
            "boll_break_upper": 60, "boll_break_lower": 60,
        }
        self._min_bars = 30
        # 增量模式状态：缓冲区上限与上一根已处理 bar 的收盘时刻
        self._buffer_limit = 3000  # 15m × 3000 ≈ 31 天，够 1d 高周期预热
        self._last_close_ts: float | None = None

    @property
    def description(self) -> str:
        return (
            f"MACD 多周期共振（实盘 Agent 同源）— "
            f"评分阈值 ±{self._score_threshold}, 一致性 ≥{self._min_confidence}"
        )

    @staticmethod
    def _infer_base_tf(df: pd.DataFrame) -> str:
        secs = df.index.to_series().diff().dropna().dt.total_seconds().median()
        for tf, s in _TF_SECONDS.items():
            if abs(secs - s) < s * 0.2:
                return tf
        raise ValueError(f"无法识别 K 线周期（间距 {secs}s），支持 {list(_TF_SECONDS)}")

    def _build_events(self, df: pd.DataFrame) -> pd.DataFrame:
        """基础周期 + 高周期合成，合并全部信号事件"""
        base_tf = self._infer_base_tf(df)
        base_secs = _TF_SECONDS[base_tf]
        frames = [_tf_events(df, base_tf, self._cooldowns)]

        # 高周期 resample（仅当高于基础周期）
        rule_map = {"3m": "3min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}
        for tf in self._higher_tfs:
            if _TF_SECONDS[tf] <= base_secs:
                continue
            ohlc = df.resample(rule_map[tf], label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min",
                 "close": "last", "volume": "sum"}
            ).dropna()
            if len(ohlc) >= 30:
                frames.append(_tf_events(ohlc, tf, self._cooldowns))

        events = pd.concat(frames, ignore_index=True)
        if events.empty:
            # 增量模式缓冲区刚满 min_bars 时常无任何事件，
            # 此时 events 没有 ts 列，sort_values 会 KeyError
            return pd.DataFrame(columns=["ts", "signal", "timeframe", "confidence"])
        return events.sort_values("ts").reset_index(drop=True)

    def generate_signals(self, df: pd.DataFrame) -> StrategyResult:
        if len(df) < self._min_bars:
            raise ValueError(f"数据不足（{len(df)} < {self._min_bars} 根）")

        events = self._build_events(df)
        base_secs = _TF_SECONDS[self._infer_base_tf(df)]
        regime = _daily_regime(df, self._trend_filter_span) if self._trend_filter else None

        signals = []
        reasons = []
        pos_state: str | None = None  # None=空仓, "long"=多, "short"=空
        ev_ptr = 0
        n_events = len(events)
        ev_ts = events["ts"].to_numpy() if n_events else np.array([])

        for i, (idx, row) in enumerate(df.iterrows()):
            bar_close_ts = idx.timestamp() + base_secs
            # 收集截至本 bar 收盘触发的所有事件
            fired = []
            while ev_ptr < n_events and ev_ts[ev_ptr] <= bar_close_ts:
                fired.append(events.iloc[ev_ptr])
                ev_ptr += 1

            sig = Signal.HOLD
            reason = ""
            if fired:
                comp = score_signals(
                    [dict(e) for e in fired], self._directions, self._tf_weights
                )
                score = comp["raw_score"]
                conf = comp["composite_confidence"]
                bull = score >= self._score_threshold and conf >= self._min_confidence
                bear = score <= -self._score_threshold and conf >= self._min_confidence
                long_ok = regime is None or regime[i] == 1
                short_ok = regime is None or regime[i] == -1
                if pos_state is None and bull and long_ok:
                    sig = Signal.BUY
                    pos_state = "long"
                    reason = f"多周期共振 score={score:+.2f} conf={conf:.2f}"
                elif pos_state is None and bear and self._allow_short and short_ok:
                    sig = Signal.SHORT
                    pos_state = "short"
                    reason = f"共振做空 score={score:+.2f} conf={conf:.2f}"
                elif pos_state == "long" and bear:
                    sig = Signal.SELL
                    pos_state = None
                    reason = f"共振反转 score={score:+.2f} conf={conf:.2f}"
                elif pos_state == "short" and bull:
                    sig = Signal.COVER
                    pos_state = None
                    reason = f"空头反转 score={score:+.2f} conf={conf:.2f}"

            signals.append(sig)
            reasons.append(reason)

        out = df.copy()
        out["signal"] = signals
        out["reason"] = reasons
        return StrategyResult(
            signals=out,
            metadata={
                "strategy": self.name,
                "score_threshold": self._score_threshold,
                "min_confidence": self._min_confidence,
                "events_total": n_events,
            },
        )

    # ── 增量模式（模拟盘用） ──

    def _regime_ok(self, side: str = "long") -> bool:
        """on_bar 的 1d 趋势判断；缓冲区不足 EMA 预热时过滤不生效（放行）"""
        if not self._trend_filter:
            return True
        need = (self._trend_filter_span + 1) * 24
        if self._bar_buffer is None or len(self._bar_buffer) < need:
            return True
        r = _daily_regime(self._bar_buffer, self._trend_filter_span)[-1]
        return bool(r == 1) if side == "long" else bool(r == -1)

    def on_bar(self, bar: pd.Series) -> Signal:
        """逐根 K 线处理：缓冲区重算事件流，仅消费本 bar 新触发的事件

        事件流由 _build_events 在缓冲区上整体重算（冷却语义确定），
        用 ts 水位线过滤出本 bar 新触发的事件 —— 重复喂入同一根 K 线
        不会重复触发（自动去重）。
        """
        new_df = bar.to_frame().T.infer_objects(copy=False)
        if self._bar_buffer is None:
            self._bar_buffer = new_df
        else:
            self._bar_buffer = pd.concat([self._bar_buffer, new_df])
        if len(self._bar_buffer) > self._buffer_limit:
            self._bar_buffer = self._bar_buffer.iloc[-self._buffer_limit:]

        if len(self._bar_buffer) < self._min_bars:
            return Signal.HOLD

        base_secs = _TF_SECONDS[self._infer_base_tf(self._bar_buffer)]
        bar_close_ts = self._bar_buffer.index[-1].timestamp() + base_secs
        prev_close_ts = self._last_close_ts if self._last_close_ts is not None else -1.0

        events = self._build_events(self._bar_buffer)
        fired = events[(events["ts"] > prev_close_ts) & (events["ts"] <= bar_close_ts)]
        self._last_close_ts = bar_close_ts

        sig = Signal.HOLD
        if not fired.empty:
            comp = score_signals(
                fired.to_dict("records"), self._directions, self._tf_weights
            )
            score = comp["raw_score"]
            conf = comp["composite_confidence"]
            close_price = float(bar["close"])
            bull = score >= self._score_threshold and conf >= self._min_confidence
            bear = score <= -self._score_threshold and conf >= self._min_confidence
            if self.position is None and bull and self._regime_ok("long"):
                sig = Signal.BUY
                self.position = PositionInfo(
                    entry_price=close_price,
                    entry_time=self._bar_buffer.index[-1],
                    size=1.0,
                    highest_price=close_price,
                )
                self._position_side = "long"
            elif (
                self.position is None
                and bear
                and self._allow_short
                and self._regime_ok("short")
            ):
                sig = Signal.SHORT
                self.position = PositionInfo(
                    entry_price=close_price,
                    entry_time=self._bar_buffer.index[-1],
                    size=1.0,
                    highest_price=close_price,
                )
                self._position_side = "short"
            elif self._position_side == "long" and bear:
                sig = Signal.SELL
                self.position = None
                self._position_side = None
            elif self._position_side == "short" and bull:
                sig = Signal.COVER
                self.position = None
                self._position_side = None

        return sig
