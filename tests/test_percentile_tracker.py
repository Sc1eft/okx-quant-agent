"""RollingPercentile 测试：分位数学、极端判定、窗口与持久化"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.percentile_tracker import RollingPercentile


def _uniform_tracker(n=200, lo=0.0, hi=1.0, **kw):
    """[lo, hi] 均匀分布样本"""
    args = {"window": 1000, "min_samples": 100}
    args.update(kw)
    t = RollingPercentile(**args)
    step = (hi - lo) / (n - 1)
    for i in range(n):
        t.update(lo + i * step)
    return t


class TestRank:
    def test_rank_none_below_min_samples(self):
        t = RollingPercentile(window=100, min_samples=50)
        for i in range(49):
            t.update(i)
        assert t.rank(999) is None
        assert t.extreme(999) is None

    def test_rank_math(self):
        t = _uniform_tracker()  # 0.0~1.0 均匀 200 样本
        assert t.rank(1.5) == 1.0       # 高于全部样本
        assert t.rank(-0.5) == 0.0      # 低于全部样本
        assert t.rank(0.5) == pytest.approx(0.51, abs=0.02)

    def test_rank_at_min_samples_boundary(self):
        t = RollingPercentile(window=100, min_samples=10)
        for i in range(10):
            t.update(i)
        assert t.rank(5) == pytest.approx(0.6)  # 0..5 共 6 个 ≤ 5


class TestExtreme:
    def test_high(self):
        t = _uniform_tracker()
        assert t.extreme(0.97) == "high"

    def test_low(self):
        t = _uniform_tracker()
        assert t.extreme(0.03) == "low"

    def test_normal_is_none(self):
        t = _uniform_tracker()
        assert t.extreme(0.5) is None

    def test_custom_thresholds(self):
        t = _uniform_tracker()
        assert t.extreme(0.85, upper=0.8) == "high"
        assert t.extreme(0.85, upper=0.99) is None


class TestWindow:
    def test_maxlen_respected(self):
        t = RollingPercentile(window=50, min_samples=10)
        for i in range(200):
            t.update(i)
        assert t.n == 50
        # 旧样本被淘汰：分布只剩 150~199，小值应判 low
        assert t.extreme(100) == "low"

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            RollingPercentile(window=5)
        with pytest.raises(ValueError):
            RollingPercentile(window=100, min_samples=0)
        with pytest.raises(ValueError):
            RollingPercentile(window=100, min_samples=101)


class TestPersistence:
    def test_roundtrip(self):
        t = _uniform_tracker()
        t2 = RollingPercentile.from_dict(t.to_dict())
        assert t2.n == t.n
        assert t2.rank(0.5) == t.rank(0.5)
        assert t2.extreme(0.97) == "high"

    def test_from_dict_truncates_to_window(self):
        d = {"window": 50, "min_samples": 10,
             "samples": list(range(200))}
        t = RollingPercentile.from_dict(d)
        assert t.n == 50
        assert t.extreme(100) == "low"  # 只保留最后 50 个（150~199）

    def test_from_partial_dict(self):
        t = RollingPercentile.from_dict({})
        assert t.n == 0
        assert t.extreme(1.0) is None
