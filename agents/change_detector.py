"""
信号变化检测器

将最新指标值与上次值对比，检测有意义的变化并生成事件。
只推送实质性的交易信号，避免每秒重复推送。

检测范围:
  - MACD: 金叉/死叉、柱线零轴穿越（去掉柱线动量方向——太噪）
  - KDJ: 仅 >=15m 才检测 K/D 穿越，去掉 K 值中轴穿越（纯噪音）
  - BOLL: 价格突破上/下轨、布林收口/扩张、带宽趋势
  - 布林 squeeze 期间抑制 KDJ 信号（盘整期 KDJ 不可靠）
"""
from __future__ import annotations

import logging
from typing import Optional

from agents.helpers import tf_minutes

logger = logging.getLogger("change_detector")

# ── KDJ 信号最低时间周期 ──
# < 15m 的周期只保留 zone 变化（超买/超卖），不生成穿越信号
_KDJ_CROSS_MIN_TF = 15  # 低于此值的 timeframe 不生成 K/D 穿越信号
# 各时间周期 KDJ 冷却时间（秒），短周期冷却更长
_KDJ_COOLDOWN: dict[str, float] = {
    "3m": 300.0, "5m": 180.0, "15m": 120.0, "1h": 60.0, "1d": 30.0,
}
# MACD 冷却时间（秒）
_MACD_CROSS_COOLDOWN: dict[str, float] = {
    "3m": 180.0, "5m": 120.0, "15m": 90.0, "1h": 60.0, "1d": 30.0,
}
# 布林信号冷却
_BOLL_BREAK_COOLDOWN: dict[str, float] = {
    "3m": 180.0, "5m": 120.0, "15m": 90.0, "1h": 60.0, "1d": 30.0,
}


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

    def __init__(self, default_cooldown: float = 60.0, cooldown_config: dict | None = None):
        # 存储上次各周期各指标的值 {timeframe: {indicator_key: value}}
        self._prev: dict[str, dict] = {}
        # 冷却计时 {timeframe_signal_type: last_push_timestamp_s}
        self._cooldown: dict[str, float] = {}
        # 默认冷却时间（秒）
        self._default_cooldown = default_cooldown
        # 外部配置冷却覆盖（如 agent1_signal_cooldowns），_can_push 中优先于模块常量
        # 格式: { "kdj_*": {"3m": 300, ...}, "macd_*": {...}, "boll_*": {...} }
        self._cooldown_config = cooldown_config or {}

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
        signals.extend(self._check_kdj(timeframe, kdj, prev.get("kdj"), boll, price, current_ts))
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

        # 金叉/死叉（最强信号）
        if cur.get("crossover") == "bullish" and prev.get("crossover") != "bullish":
            if self._can_push(tf, "macd_bullish_cross", ts, _MACD_CROSS_COOLDOWN):
                signals.append(self._signal("macd_bullish_cross", tf, "high", 0.85,
                                             f"MACD {tf} 金叉↑", price))
        elif cur.get("crossover") == "bearish" and prev.get("crossover") != "bearish":
            if self._can_push(tf, "macd_bearish_cross", ts, _MACD_CROSS_COOLDOWN):
                signals.append(self._signal("macd_bearish_cross", tf, "high", 0.85,
                                             f"MACD {tf} 死叉↓", price))

        # 柱线零轴穿越（正↔负反转，中等强度）
        prev_hist = prev.get("histogram")
        cur_hist = cur.get("histogram")
        if prev_hist is not None and cur_hist is not None:
            if prev_hist < 0 and cur_hist >= 0:
                if self._can_push(tf, "macd_hist_positive", ts, _MACD_CROSS_COOLDOWN):
                    signals.append(self._signal("macd_hist_positive", tf, "high", 0.7,
                                                 f"MACD {tf} 柱线转正", price))
            elif prev_hist > 0 and cur_hist <= 0:
                if self._can_push(tf, "macd_hist_negative", ts, _MACD_CROSS_COOLDOWN):
                    signals.append(self._signal("macd_hist_negative", tf, "high", 0.7,
                                                 f"MACD {tf} 柱线转负", price))

        # 移除了 hist_direction momentum 信号（方向值±0.2 太噪，无实际价值）

        return signals

    # ── KDJ 检测 ──

    def _check_kdj(
        self, tf: str, cur: Optional[dict], prev: Optional[dict],
        boll: Optional[dict], price: float, ts: float,
    ) -> list[dict]:
        """KDJ 信号检测

        规则:
          1. K/D 穿越信号仅限 >=15m 周期
          2. zone 变化（超买/超卖）所有周期都检测，但有冷却
          3. 跳过 K 值中轴穿越（没有预测价值）
          4. 布林 squeeze 期间抑制所有 KDJ 信号（盘整期 KDJ 不可靠）
        """
        signals = []
        if not cur or not prev:
            return signals

        # 布林 squeeze 期间抑制 KDJ 信号
        is_squeeze = boll and boll.get("squeeze", False)
        tf_min = tf_minutes(tf)

        # K 穿越 D — 仅限 >=15m
        if tf_min >= _KDJ_CROSS_MIN_TF and not is_squeeze:
            if cur.get("k_cross_d") == "bullish" and prev.get("k_cross_d") != "bullish":
                if self._can_push(tf, "kdj_bullish_cross", ts, _KDJ_COOLDOWN):
                    signals.append(self._signal("kdj_bullish_cross", tf, "medium", 0.65,
                                                 f"KDJ {tf} K↑D 金叉", price))
            elif cur.get("k_cross_d") == "bearish" and prev.get("k_cross_d") != "bearish":
                if self._can_push(tf, "kdj_bearish_cross", ts, _KDJ_COOLDOWN):
                    signals.append(self._signal("kdj_bearish_cross", tf, "medium", 0.65,
                                                 f"KDJ {tf} K↓D 死叉", price))

        # 超买/超卖区进出（不含 squeeze，加了也不可靠）
        if not is_squeeze:
            if cur.get("zone") != prev.get("zone"):
                if cur["zone"] == "overbought":
                    if self._can_push(tf, "kdj_overbought", ts, _KDJ_COOLDOWN):
                        signals.append(self._signal("kdj_overbought", tf, "medium", 0.6,
                                                     f"KDJ {tf} 超买 ⚠️", price))
                elif cur["zone"] == "oversold":
                    if self._can_push(tf, "kdj_oversold", ts, _KDJ_COOLDOWN):
                        signals.append(self._signal("kdj_oversold", tf, "medium", 0.6,
                                                     f"KDJ {tf} 超卖 🔻", price))
        else:
            # squeeze 中，超买超卖信号大幅降权 + 冷却极长
            if cur.get("zone") != prev.get("zone"):
                if cur["zone"] == "overbought":
                    if self._can_push(tf, "kdj_overbought", ts, {"*": 600}):
                        signals.append(self._signal("kdj_overbought", tf, "low", 0.2,
                                                     f"KDJ {tf} 超买(盘整中)", price))
                elif cur["zone"] == "oversold":
                    if self._can_push(tf, "kdj_oversold", ts, {"*": 600}):
                        signals.append(self._signal("kdj_oversold", tf, "low", 0.2,
                                                     f"KDJ {tf} 超卖(盘整中)", price))

        # 移除了 k value 穿越 50 中轴信号（纯噪音）

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
            if self._can_push(tf, "boll_break_upper", ts, _BOLL_BREAK_COOLDOWN):
                signals.append(self._signal("boll_break_upper", tf, "high", 0.75,
                                             f"价格突破布林上轨 {tf} ⬆", price))
        # 价格突破下轨
        elif cur.get("position_label") == "touch_lower" and prev.get("position_label") != "touch_lower":
            if self._can_push(tf, "boll_break_lower", ts, _BOLL_BREAK_COOLDOWN):
                signals.append(self._signal("boll_break_lower", tf, "high", 0.75,
                                             f"价格突破布林下轨 {tf} ⬇", price))

        # 布林收口（squeeze 开始）
        if not prev.get("squeeze") and cur.get("squeeze"):
            if self._can_push(tf, "boll_squeeze", ts, {"*": 600}):  # squeeze 10 分钟才发一次
                signals.append(self._signal("boll_squeeze", tf, "medium", 0.6,
                                             f"布林收口 {tf} 🌀", price))

        # 布林开口（squeeze 结束 → 可能变盘）
        if prev.get("squeeze") and not cur.get("squeeze"):
            if self._can_push(tf, "boll_expansion", ts, {"*": 600}):
                # squeeze 后开口方向由 position_label 决定
                direction = "向上" if cur.get("position_pct", 50) > 65 else "向下" if cur.get("position_pct", 50) < 35 else "方向不明"
                signals.append(self._signal("boll_expansion", tf, "high", 0.7,
                                             f"布林开口 {tf} {direction} 💥", price))

        # 带宽持续扩张（盘整→趋势的信号）
        prev_bw = prev.get("bandwidth")
        cur_bw = cur.get("bandwidth")
        if prev_bw is not None and cur_bw is not None and prev_bw > 0:
            bw_change_pct = (cur_bw - prev_bw) / prev_bw
            if bw_change_pct > 0.20 and cur_bw > 0.05:  # 带宽增大 20% 以上
                if self._can_push(tf, "boll_bandwidth_expanding", ts, {"*": 600}):
                    signals.append(self._signal("boll_bandwidth_expanding", tf, "low", 0.5,
                                                 f"布林带宽扩张 {tf} +{bw_change_pct:.0%}", price))

        # 移除了 position_pct 穿越 75/25 预警（太噪，布林突破本身已有信号）

        return signals

    # ── 内部 ──

    def _save_state(self, tf: str, macd, kdj, boll):
        self._prev[tf] = {
            "macd": dict(macd) if macd else None,
            "kdj": dict(kdj) if kdj else None,
            "boll": dict(boll) if boll else None,
        }

    def _can_push(
        self, tf: str, signal_type: str, ts: float,
        cooldown_override: dict | float | None = None,
    ) -> bool:
        """检查某信号的冷却时间是否已过

        Args:
            cooldown_override: 可选特定冷却字典（按 timeframe）或纯数字
        """
        key = f"{tf}:{signal_type}"

        # 1. 外部配置覆盖优先（Agent 4 可动态调整）
        cd = self._get_config_cooldown(signal_type, tf)
        if cd is None:
            # 2. 无配置覆盖 → 使用调用方传入的模块常量
            if cooldown_override is not None:
                if isinstance(cooldown_override, dict):
                    cd = cooldown_override.get(tf, cooldown_override.get("*", self._default_cooldown))
                else:
                    cd = cooldown_override
            else:
                cd = self._default_cooldown

        last = self._cooldown.get(f"last:{key}", 0)
        if ts - last < cd:
            return False
        self._cooldown[f"last:{key}"] = ts
        return True

    def _get_config_cooldown(self, signal_type: str, tf: str) -> float | None:
        """从 _cooldown_config 中查找匹配的冷却时间

        模式匹配: "macd_*" → 匹配 "macd_bullish_cross", "macd_bearish_cross" 等
        """
        if not self._cooldown_config:
            return None
        for pattern, cd_map in self._cooldown_config.items():
            prefix = pattern.rstrip("*")
            if signal_type.startswith(prefix):
                return cd_map.get(tf, cd_map.get("*", None))
        return None

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
