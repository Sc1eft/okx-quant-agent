"""滚动分位数追踪器 — 事件触发层（信号池）的「极端」判定器

思路（借鉴 Hyper-Alpha-Arena 的因子管道）：资金费率 / 吃单失衡 / OI 变动
这类指标没有普适的绝对阈值，「是否处于自身近期历史分布的极端分位」
才是更稳的异常定义。窗口样本数 < min_samples 时不做判断（冷启动不误报）。

用法:
    t = RollingPercentile(window=2016, min_samples=200)
    t.update(0.53)
    t.extreme(0.61, upper=0.95, lower=0.05)  # -> "high" / "low" / None
"""

from __future__ import annotations

from collections import deque


class RollingPercentile:
    """固定窗口滚动分位追踪：rank ∈ [0,1] = 新观测在历史样本中的分位"""

    def __init__(self, window: int = 2016, min_samples: int = 200):
        if window < 10:
            raise ValueError("window 过小，分位数无意义")
        if not 0 < min_samples <= window:
            raise ValueError("min_samples 必须在 (0, window] 内")
        self.window = int(window)
        self.min_samples = int(min_samples)
        self._samples: deque[float] = deque(maxlen=self.window)

    def update(self, value: float) -> None:
        self._samples.append(float(value))

    @property
    def n(self) -> int:
        return len(self._samples)

    def rank(self, value: float) -> float | None:
        """value 在历史样本中的分位（0~1）；样本不足返回 None"""
        if len(self._samples) < self.min_samples:
            return None
        v = float(value)
        return sum(1 for s in self._samples if s <= v) / len(self._samples)

    def extreme(self, value: float, upper: float = 0.95, lower: float = 0.05) -> str | None:
        """极端判定：rank ≥ upper → "high"；rank ≤ lower → "low"；否则 None"""
        r = self.rank(value)
        if r is None:
            return None
        if r >= upper:
            return "high"
        if r <= lower:
            return "low"
        return None

    # ── 持久化（JSON 可序列化）──

    def to_dict(self) -> dict:
        return {
            "window": self.window,
            "min_samples": self.min_samples,
            "samples": list(self._samples),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RollingPercentile":
        t = cls(window=d.get("window", 2016), min_samples=d.get("min_samples", 200))
        for v in d.get("samples", [])[-t.window:]:
            t.update(v)
        return t
