"""日线 EMA50 趋势基线：检验「钱在日线趋势里」的假设

规则极简（无前视）：
  - 上一根收盘日线 > 日线 EMA50 → 今日做多；< → 空仓（变体：翻空）
  - 全额本金、按日收盘价调仓，taker 手续费按仓位变化量计
与 1h 信号体系（A~F 组合）共用同一份 3 年缓存数据，作为「趋势本身值多少钱」的对照。

用法: python scripts/daily_trend_baseline.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from config import Config


def main():
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    cfg = Config.load(str(cfg_path)) if cfg_path.exists() else Config()
    fee = cfg.trading.taker_fee

    cache = PROJECT_ROOT / "data" / "cache" / f"{cfg.trading.symbol}_1h.csv"
    df = pd.read_csv(cache, index_col="timestamp")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Shanghai")

    daily = df["close"].resample("1D", label="left", closed="left").last().dropna()
    ema = daily.ewm(span=50, adjust=False).mean()
    ret = daily.pct_change().fillna(0)

    above = (daily > ema).astype(int)
    variants = {
        "多/空仓": above.shift(1).fillna(0),             # 站上持有，跌破空仓
        "多/翻空": above.replace(0, -1).shift(1).fillna(0),  # 站上多，跌破空
    }

    bh_total = (daily.iloc[-1] / daily.iloc[0] - 1) * 100
    print(f"数据: {len(daily)} 根日线  {daily.index[0].date()} ~ {daily.index[-1].date()}  "
          f"费率 {fee*100:.2f}%/边  全额本金")
    print(f"\n{'策略':<10}{'总收益%':>9}{'最大回撤%':>10}{'调仓次数':>9}"
          f"{2023:>9}{2024:>9}{2025:>9}{2026:>9}")

    for name, pos in variants.items():
        gross = pos * ret
        cost = pos.diff().abs().fillna(0) * fee
        eq = (1 + gross - cost).cumprod()
        total = (eq.iloc[-1] - 1) * 100
        dd = abs((eq / eq.cummax() - 1).min()) * 100
        n_changes = int(pos.diff().abs().fillna(0).sum())
        yearly = {}
        for y, g in eq.groupby(eq.index.year):
            seg = g / g.iloc[0]
            yearly[y] = (seg.iloc[-1] - 1) * 100
        print(f"{name:<10}{total:>9.1f}{dd:>10.1f}{n_changes:>9}"
              + "".join(f"{yearly.get(y, 0):>9.1f}" for y in (2023, 2024, 2025, 2026)))

    # 基准
    eq_bh = (1 + ret).cumprod()
    dd_bh = abs((eq_bh / eq_bh.cummax() - 1).min()) * 100
    yearly_bh = {}
    for y, g in eq_bh.groupby(eq_bh.index.year):
        seg = g / g.iloc[0]
        yearly_bh[y] = (seg.iloc[-1] - 1) * 100
    print(f"{'买入持有':<10}{bh_total:>9.1f}{dd_bh:>10.1f}{1:>9}"
          + "".join(f"{yearly_bh.get(y, 0):>9.1f}" for y in (2023, 2024, 2025, 2026)))


if __name__ == "__main__":
    main()
