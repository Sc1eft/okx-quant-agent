"""KlineBuilder 单元测试"""
import sys; sys.path.insert(0, ".")
from agents.kline_builder import KlineBuilder


def test_basic_tick_to_15m():
    builder = KlineBuilder()
    completed = []

    def on_bar(tf, bar):
        completed.append((tf, bar))

    builder.on_completed_bar = on_bar

    # 模拟 10 秒 ticks，同一 15m 窗口内
    base_ts = 1700000000  # 某个整数时间
    for i in range(10):
        builder.add_tick(3000.0 + i * 0.1, base_ts + i)

    # 还没有完成的 K 线（都在同一 15m 窗口）
    assert len(completed) == 0
    assert builder.get_current_candle("15m") is not None

    # 跳过一个 15m 窗口（同时也会触发 3m/5m 周期完成）
    next_window = base_ts + 15 * 60 + 1
    builder.add_tick(3010.0, next_window)

    # 应触发多个时间周期的 K 线完成（3m/5m/15m/1h/1d 中跨越边界的）
    assert len(completed) >= 1
    # 第一个完成的 K 线可能是 3m（TIMEFRAMES 中最短周期）
    # 验证 15m 在完成的列表里
    completed_tfs = {tf for tf, _ in completed}
    assert "15m" in completed_tfs, f"15m not in completed: {completed_tfs}"

    # 验证第一个 15m bar 的数据
    fifteen_min_bars = [(tf, bar) for tf, bar in completed if tf == "15m"]
    assert len(fifteen_min_bars) >= 1
    _, bar = fifteen_min_bars[0]
    assert bar["open"] == 3000.0
    assert bar["close"] == 3000.9  # 最后一秒的值
    assert bar["high"] == 3000.9
    assert bar["low"] == 3000.0

    print("test_basic_tick_to_15m PASSED")


def test_multiple_timeframes():
    builder = KlineBuilder()
    completed = []
    builder.on_completed_bar = lambda tf, bar: completed.append((tf, bar))

    base_ts = 1700000000
    # 模拟 2 小时的数据（每分钟一个 tick）
    for minute in range(120):
        ts = base_ts + minute * 60
        builder.add_tick(3000.0 + minute * 0.5, ts)

    # 应该有完整的 15m 和 1h K 线
    tf_counts = {}
    for tf, _ in completed:
        tf_counts[tf] = tf_counts.get(tf, 0) + 1

    print(f"Completed bars: {tf_counts}")
    assert tf_counts.get("15m", 0) >= 7  # 120分钟/15分钟 = 8个窗口
    assert tf_counts.get("1h", 0) >= 1    # 至少 1 根小时线
    assert builder.has_history("15m", 5)
    assert builder.has_history("1h", 1)

    print("test_multiple_timeframes PASSED")


if __name__ == "__main__":
    test_basic_tick_to_15m()
    test_multiple_timeframes()
