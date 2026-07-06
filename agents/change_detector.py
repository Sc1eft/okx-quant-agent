"""
信号变化检测器

将最新指标值与上次值对比，检测有意义的变化并生成事件。
只推送实质性的交易信号，避免每秒重复推送。

检测范围:
  - MACD: 金叉/死叉、柱线方向反转、零轴穿越
  - KDJ:  K 穿越 D、超买/超卖区进出
  - BOLL: 价格突破上/下轨、布林收口扩张
  - 多周期信心分变化
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("change_detector")


class ChangeDetector:
    """变化检测器

    每次调用 check() 时，传入当前各指标的最新值，返回检测到的变更列表。
    每个变更格式:
    {
        "signal": "macd_bullish_cross",
        "timeframe": "15m",
        "urgency": "high",
        "confidence": 0.85,
        "description": "MACD 15m 金叉出现",
        "price": 3000.0  # 触发时的价格
    }
    """

    def __init__(self, default_cooldown: float = 60.0):
        # 存储上次各周期各指标的值 {timeframe: {indicator_key: value}}
        self._prev: dict[str, dict] = {}
        # 冷却计时 {timeframe_signal_type: last_push_timestamp_s}
        self._cooldown: dict[str, float] = {}
        # 默认冷却时间（秒）
        self._default_cooldown = default_cooldown

    def set_cooldown(self, signal_key: str, seconds: float):
        """设置某类型信号的冷却时间"""
        self._cooldown[signal_key] = seconds

    def check(
        self,
        timeframe: str,
        macd: Optional[dict],
        kdj: Optional[dict],
        boll: Optional[dict],
        price: float,
        current_ts: float,
    ) -> list[dict]:
        """检查指标变化，返回信号列表"""
        signals: list[dict] = []

        if timeframe not in self._prev:
            self._prev[timeframe] = {}
            # 首次调用，只保存不检测
            self._save_state(timeframe, macd, kdj, boll)
            return signals

        prev = self._prev[timeframe]
        signals.extend(self._check_macd(timeframe, macd, prev.get("macd"), price, current_ts))
        signals.extend(self._check_kdj(timeframe, kdj, prev.get("kdj"), price, current_ts))
        signals.extend(self._check_boll(timeframe, boll, prev.get("boll"), price, current_ts))

        # 保存本次状态
        self._save_state(timeframe, macd, kdj, boll)
        return signals

    # ── MACD 检测 ──

    def _check_macd(
        self, tf: str, cur: Optional[dict], prev: Optional[dict],
        price: float, ts: float,
    ) -> list[dict]:
        signals = []
        if not cur or not prev:
            return signals

        # 金叉/死叉
        if cur.get("crossover") == "bullish" and prev.get("crossover") != "bullish":
            if self._can_push(tf, "macd_bullish_cross", ts):
                signals.append(self._signal("macd_bullish_cross", tf, "high", 0.85,
                                             f"MACD {tf} 金叉↑", price))
        elif cur.get("crossover") == "bearish" and prev.get("crossover") != "bearish":
            if self._can_push(tf, "macd_bearish_cross", ts):
                signals.append(self._signal("macd_bearish_cross", tf, "high", 0.85,
                                             f"MACD {tf} 死叉↓", price))

        # 柱线方向反转（正→负 或 负→正）
        prev_hist = prev.get("histogram")
        cur_hist = cur.get("histogram")
        if prev_hist is not None and cur_hist is not None:
            if prev_hist < 0 and cur_hist >= 0:
                if self._can_push(tf, "macd_hist_positive", ts):
                    signals.append(self._signal("macd_hist_positive", tf, "high", 0.7,
                                                 f"MACD {tf} 柱线转正", price))
            elif prev_hist > 0 and cur_hist <= 0:
                if self._can_push(tf, "macd_hist_negative", ts):
                    signals.append(self._signal("macd_hist_negative", tf, "high", 0.7,
                                                 f"MACD {tf} 柱线转负", price))

        # 柱线方向变化（rising↔falling）— 弱信号
        prev_dir = prev.get("hist_direction")
        cur_dir = cur.get("hist_direction")
        if prev_dir is not None and cur_dir is not None and prev_dir != cur_dir:
            if cur_dir == "rising":
                if self._can_push(tf, "macd_hist_momentum_up", ts):
                    signals.append(self._signal("macd_hist_momentum_up", tf, "low", 0.4,
                                                 f"MACD {tf} 柱线动量转强 ↑", price))
            elif cur_dir == "falling":
                if self._can_push(tf, "macd_hist_momentum_down", ts):
                    signals.append(self._signal("macd_hist_momentum_down", tf, "low", 0.4,
                                                 f"MACD {tf} 柱线动量转弱 ↓", price))

        return signals

    # ── KDJ 检测 ──

    def _check_kdj(
        self, tf: str, cur: Optional[dict], prev: Optional[dict],
        price: float, ts: float,
    ) -> list[dict]:
        signals = []
        if not cur or not prev:
            return signals

        # K 穿越 D
        if cur.get("k_cross_d") == "bullish" and prev.get("k_cross_d") != "bullish":
            if self._can_push(tf, "kdj_bullish_cross", ts):
                signals.append(self._signal("kdj_bullish_cross", tf, "medium", 0.7,
                                             f"KDJ {tf} K↑D 金叉", price))
        elif cur.get("k_cross_d") == "bearish" and prev.get("k_cross_d") != "bearish":
            if self._can_push(tf, "kdj_bearish_cross", ts):
                signals.append(self._signal("kdj_bearish_cross", tf, "medium", 0.7,
                                             f"KDJ {tf} K↓D 死叉", price))

        # 超买/超卖区进出
        if cur.get("zone") != prev.get("zone"):
            if cur["zone"] == "overbought":
                if self._can_push(tf, "kdj_overbought", ts):
                    signals.append(self._signal("kdj_overbought", tf, "medium", 0.6,
                                                 f"KDJ {tf} 进入超买区 ⚠️", price))
            elif cur["zone"] == "oversold":
                if self._can_push(tf, "kdj_oversold", ts):
                    signals.append(self._signal("kdj_oversold", tf, "medium", 0.6,
                                                 f"KDJ {tf} 进入超卖区 🔻", price))

        # K 值穿越 50 中轴线 — 弱信号
        prev_k = prev.get("k")
        cur_k = cur.get("k")
        if prev_k is not None and cur_k is not None:
            if prev_k <= 50 < cur_k:
                if self._can_push(tf, "kdj_k_above_50", ts):
                    signals.append(self._signal("kdj_k_above_50", tf, "low", 0.4,
                                                 f"KDJ {tf} K值上穿50中轴 ↗", price))
            elif prev_k >= 50 > cur_k:
                if self._can_push(tf, "kdj_k_below_50", ts):
                    signals.append(self._signal("kdj_k_below_50", tf, "low", 0.4,
                                                 f"KDJ {tf} K值下穿50中轴 ↘", price))

        return signals

    # ── 布林带检测 ──

    def _check_boll(
        self, tf: str, cur: Optional[dict], prev: Optional[dict],
        price: float, ts: float,
    ) -> list[dict]:
        signals = []
        if not cur or not prev:
            return signals

        # 价格突破上轨
        if cur.get("position_label") == "touch_upper" and prev.get("position_label") != "touch_upper":
            if self._can_push(tf, "boll_break_upper", ts):
                signals.append(self._signal("boll_break_upper", tf, "high", 0.75,
                                             f"价格突破布林上轨 {tf}", price))
        # 价格突破下轨
        elif cur.get("position_label") == "touch_lower" and prev.get("position_label") != "touch_lower":
            if self._can_push(tf, "boll_break_lower", ts):
                signals.append(self._signal("boll_break_lower", tf, "high", 0.75,
                                             f"价格突破布林下轨 {tf}", price))

        # 布林开始收口（带宽挤压收缩）
        if not prev.get("squeeze") and cur.get("squeeze"):
            if self._can_push(tf, "boll_squeeze", ts):
                signals.append(self._signal("boll_squeeze", tf, "medium", 0.65,
                                             f"布林收口 {tf} 🌀", price))

        # position_pct 穿越阈值 — 弱信号（接近上轨/下轨的预警）
        prev_pct = prev.get("position_pct")
        cur_pct = cur.get("position_pct")
        if prev_pct is not None and cur_pct is not None:
            if prev_pct <= 75 < cur_pct:
                if self._can_push(tf, "boll_upper_approach", ts):
                    signals.append(self._signal("boll_upper_approach", tf, "low", 0.35,
                                                 f"布林 {tf} 价格偏向上轨 (pos={cur_pct:.0f}%)", price))
            elif prev_pct >= 25 > cur_pct:
                if self._can_push(tf, "boll_lower_approach", ts):
                    signals.append(self._signal("boll_lower_approach", tf, "low", 0.35,
                                                 f"布林 {tf} 价格偏向下轨 (pos={cur_pct:.0f}%)", price))

        return signals

    # ── 内部 ──

    def _save_state(self, tf: str, macd, kdj, boll):
        self._prev[tf] = {
            "macd": dict(macd) if macd else None,
            "kdj": dict(kdj) if kdj else None,
            "boll": dict(boll) if boll else None,
        }

    def _can_push(self, tf: str, signal_type: str, ts: float) -> bool:
        """检查某信号的冷却时间是否已过"""
        key = f"{tf}:{signal_type}"
        cd = self._cooldown.get(key, self._default_cooldown)
        last = self._cooldown.get(f"last:{key}", 0)
        if ts - last < cd:
            return False
        self._cooldown[f"last:{key}"] = ts
        return True

    def _signal(self, sig: str, tf: str, urgency: str, confidence: float,
                 description: str, price: float) -> dict:
        return {
            "signal": sig,
            "timeframe": tf,
            "urgency": urgency,
            "confidence": confidence,
            "description": description,
            "price": price,
        }
