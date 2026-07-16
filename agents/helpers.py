"""
Agent 系统共享工具函数
"""
from __future__ import annotations


def tf_minutes(tf: str) -> int:
    """将 timeframe 字符串转为分钟数

    Args:
        tf: "3m", "5m", "15m", "1h", "1d" 等

    Returns:
        对应的分钟数，未知格式返回 999
    """
    if tf.endswith("m"):
        return int(tf[:-1])
    elif tf.endswith("h"):
        return int(tf[:-1]) * 60
    elif tf.endswith("d"):
        return int(tf[:-1]) * 1440
    return 999
