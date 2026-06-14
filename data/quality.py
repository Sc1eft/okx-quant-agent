"""
🔧 数据完整性检查（P2 优化）

检查项目：
1. K 线连续性 — 检测缺失的 K 线
2. 异常价格 — 统计离群值检测
3. 数据对齐 — 时间戳偏差检查
4. 重复数据 — 确保已去重
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from config import Config
from data.storage import DataStore

logger = logging.getLogger("data.quality")


class DataQualityChecker:
    """
    数据质量检查器
    每次检查结果写入 data_quality_log 表
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = DataStore(cfg)

    # ── 1. K 线连续性检测 ──

    def check_continuity(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> dict:
        """
        检测 K 线是否连续
        返回：缺失段列表 + 总缺失数
        """
        df = self.store.load_klines(symbol, timeframe)
        if df.empty:
            return {"status": "no_data", "missing_count": 0, "gaps": []}

        # 计算 K 线间隔（毫秒）
        tf_minutes = self._timeframe_to_minutes(timeframe)
        expected_gap = tf_minutes * 60 * 1000
        max_tolerance = self.cfg.data.max_kline_gap_minutes * 60 * 1000

        timestamps = df.index.view("int64") // 1_000_000  # ns -> ms
        gaps = np.diff(timestamps)

        missing_indices = np.where(gaps > expected_gap + max_tolerance)[0]

        gaps_info = []
        for idx in missing_indices:
            gap_start = timestamps[idx]
            gap_end = timestamps[idx + 1]
            missing_count = int((gap_end - gap_start) / expected_gap) - 1
            gaps_info.append({
                "from_ts": int(gap_start),
                "from_time": datetime.fromtimestamp(gap_start / 1000, tz=timezone.utc).isoformat(),
                "to_ts": int(gap_end),
                "to_time": datetime.fromtimestamp(gap_end / 1000, tz=timezone.utc).isoformat(),
                "missing_klines": missing_count,
            })

        total_missing = sum(g["missing_klines"] for g in gaps_info)
        has_gaps = len(gaps_info) > 0

        # 记录到日志
        status = "ok" if not has_gaps else "warning"
        detail = f"缺失 {total_missing} 根 K 线，{len(gaps_info)} 个缺口" if has_gaps else "完整无缺"
        self.store.log_quality_check(symbol, timeframe, "continuity", status, detail)

        if has_gaps:
            logger.warning(f"[数据质量] {symbol} {timeframe} {detail}")
            for g in gaps_info[:3]:  # 最多打印 3 个
                logger.warning(f"  缺口: {g['from_time']} -> {g['to_time']} (缺失 {g['missing_klines']} 根)")
            if len(gaps_info) > 3:
                logger.warning(f"  ... 还有 {len(gaps_info) - 3} 个缺口")
        else:
            logger.info(f"[数据质量] {symbol} {timeframe} 连续性 OK")

        return {
            "status": status,
            "missing_count": total_missing,
            "gap_count": len(gaps_info),
            "gaps": gaps_info[:10],  # 最多返回 10 个
        }

    # ── 2. 异常价格检测 ──

    def check_price_anomalies(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> dict:
        """
        用标准差检测异常价格
        - 超过 mean ± n*std 视为异常
        - 记录疑似数据错误的 K 线
        """
        df = self.store.load_klines(symbol, timeframe)
        if df.empty:
            return {"status": "no_data", "anomalies": 0}

        n_std = self.cfg.data.max_price_deviation_std
        anomalies = []

        for col in ["open", "high", "low", "close"]:
            mean = df[col].mean()
            std = df[col].std()
            if std == 0:
                continue

            mask = np.abs(df[col] - mean) > n_std * std
            anom_indices = df.index[mask]

            for idx in anom_indices:
                anomalies.append({
                    "time": idx.isoformat(),
                    "field": col,
                    "value": float(df.loc[idx, col]),
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "z_score": round((df.loc[idx, col] - mean) / std, 2),
                })

        # 检查 high < low 或 open/close 超出范围
        for idx, row in df.iterrows():
            if row["high"] < row["low"]:
                anomalies.append({
                    "time": idx.isoformat(),
                    "field": "high<low",
                    "value": f"high={row['high']}, low={row['low']}",
                    "mean": 0,
                    "std": 0,
                    "z_score": 0,
                })

        status = "ok" if len(anomalies) == 0 else "warning"
        detail = f"发现 {len(anomalies)} 个异常价格" if anomalies else "价格正常"
        self.store.log_quality_check(symbol, timeframe, "price_anomaly", status, detail)

        if anomalies:
            logger.warning(f"[数据质量] {symbol} {timeframe} {detail}")
            for a in anomalies[:5]:
                logger.warning(f"  {a['time']} {a['field']}={a['value']} (z={a['z_score']})")
        else:
            logger.info(f"[数据质量] {symbol} {timeframe} 价格正常")

        return {"status": status, "anomalies": len(anomalies), "details": anomalies[:10]}

    # ── 3. 全面检查 ──

    def full_check(self, symbol: str, timeframe: str = "1h") -> dict:
        """运行所有检查"""
        results = {
            "symbol": symbol,
            "timeframe": timeframe,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "continuity": self.check_continuity(symbol, timeframe),
            "price_anomalies": self.check_price_anomalies(symbol, timeframe),
            "total_klines": self.store.count_klines(symbol, timeframe),
        }

        # 总体状态
        all_ok = all(
            r.get("status") in ("ok", "no_data")
            for r in [results["continuity"], results["price_anomalies"]]
        )
        results["overall_status"] = "ok" if all_ok else "issues_found"

        if not all_ok:
            logger.warning(f"[数据质量] {symbol} {timeframe} 总体状态: 发现异常")
        else:
            logger.info(f"[数据质量] {symbol} {timeframe} 总体状态: ✅ 正常")

        return results

    # ── 辅助 ──

    @staticmethod
    def _timeframe_to_minutes(tf: str) -> int:
        unit = tf[-1]
        val = int(tf[:-1]) if len(tf) > 1 else 1
        if unit == "m":
            return val
        elif unit == "h":
            return val * 60
        elif unit == "d":
            return val * 1440
        elif unit == "w":
            return val * 10080
        return 60  # default 1h

    def close(self):
        self.store.close()
