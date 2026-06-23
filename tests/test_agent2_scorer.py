import sys; sys.path.insert(0, ".")
from agents.agent2_news import _score_news_item


def test_scoring():
    # High impact: 监管
    s = _score_news_item("SEC new regulations on crypto", "CoinDesk")
    assert s >= 0.7, f"Expected >=0.7, got {s}"

    # Medium impact: 机构
    s = _score_news_item("Institutional adoption growing", "CoinTelegraph")
    assert s >= 0.4, f"Expected >=0.4, got {s}"

    # Low impact: 普通
    s = _score_news_item("Daily market update", "Decrypt")
    assert s <= 0.3, f"Expected <=0.3, got {s}"

    # 中文高影响: ETF
    s = _score_news_item("以太坊 ETF 获批", "PANews")
    assert s >= 0.7, f"Expected >=0.7, got {s}"

    print("test_scoring PASSED")


if __name__ == "__main__":
    test_scoring()
    print("ALL PASSED")
