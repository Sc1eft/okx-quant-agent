# tests/test_change_detector.py
import sys; sys.path.insert(0, ".")
from agents.change_detector import ChangeDetector


def test_macd_bullish_cross():
    detector = ChangeDetector()
    detector._cooldown = {}  # disable cooldown for test
    ts = 1000.0

    # First call - no signals (initial state)
    signals = detector.check("15m",
        macd={"macd": 0.1, "signal": 0.05, "histogram": 0.05, "hist_direction": "rising", "crossover": None},
        kdj={"k": 50, "d": 50, "j": 50, "k_cross_d": None, "zone": "normal"},
        boll={"upper": 3100, "middle": 3000, "lower": 2900, "bandwidth": 0.05, "position_pct": 50, "position_label": "inside", "squeeze": False},
        price=3000, current_ts=ts)
    assert len(signals) == 0, f"Expected 0, got {len(signals)}"

    # Second call - MACD bullish cross
    signals = detector.check("15m",
        macd={"macd": 0.2, "signal": 0.15, "histogram": 0.05, "hist_direction": "rising", "crossover": "bullish"},
        kdj={"k": 50, "d": 50, "j": 50, "k_cross_d": None, "zone": "normal"},
        boll={"upper": 3100, "middle": 3000, "lower": 2900, "bandwidth": 0.05, "position_pct": 50, "position_label": "inside", "squeeze": False},
        price=3020, current_ts=ts+1)
    assert len(signals) >= 1
    assert signals[0]["signal"] == "macd_bullish_cross"
    assert signals[0]["urgency"] == "high"
    print("test_macd_bullish_cross PASSED")


def test_cooldown():
    detector = ChangeDetector()
    detector._default_cooldown = 60.0  # 60s cooldown
    ts = 1000.0

    # init
    detector.check("15m",
        macd={"crossover": None, "histogram": -0.1},
        kdj={}, boll={}, price=3000, current_ts=ts)

    # first bullish cross
    signals = detector.check("15m",
        macd={"crossover": "bullish", "histogram": 0.05},
        kdj={}, boll={}, price=3020, current_ts=ts+1)
    assert len(signals) >= 1

    # go back to neutral before testing cooldown
    detector.check("15m",
        macd={"crossover": None, "histogram": 0.04},
        kdj={}, boll={}, price=3025, current_ts=ts+28)

    # trigger bullish again within cooldown window
    signals = detector.check("15m",
        macd={"crossover": "bullish", "histogram": 0.06},
        kdj={}, boll={}, price=3030, current_ts=ts+30)
    assert len(signals) == 0, f"Expected cooldown, got {len(signals)}"

    # go back to neutral
    detector.check("15m",
        macd={"crossover": None, "histogram": 0.04},
        kdj={}, boll={}, price=3035, current_ts=ts+65)

    # after cooldown expires, signal should flow again
    signals = detector.check("15m",
        macd={"crossover": "bullish", "histogram": 0.07},
        kdj={}, boll={}, price=3040, current_ts=ts+70)
    assert len(signals) >= 1
    print("test_cooldown PASSED")


if __name__ == "__main__":
    test_macd_bullish_cross()
    test_cooldown()
    print("ALL PASSED")
