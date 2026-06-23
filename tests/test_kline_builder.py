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

    # 跳过一个 15m 窗口
    next_window = base_ts + 15 * 60 + 1
    builder.add_tick(3010.0, next_window)

    # 应该触发了一根 15m K 线完成
    assert len(completed) >= 1
    tf, bar = completed[0]
    assert tf == "15m"
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
