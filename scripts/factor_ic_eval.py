"""因子 IC 评估：每个信号事件类型的预测力度量（借鉴 Hyper-Alpha-Arena 的因子智能思路）

事件流直接用 MACDAgentStrategy._build_events —— 与回测/实盘完全同源，
评的就是策略实际交易的那些信号。另评日线 EMA50 regime（策略重写候选因子）。

指标口径：
  - 方向调整收益：事件触发后 h 根 K 线的前向收益 × 信号方向（正 = 信号方向正确）
  - 胜率 / t 值：方向调整收益的统计量（前向收益相互重叠，t 值偏乐观，仅作相对比较）
  - IC：全样本上 信号值(±1/0) 与前向收益的 Pearson 相关
  - ICIR：逐月 IC 的 mean/std（>0.5 通常认为因子可用，>1 优秀）

用法: python scripts/factor_ic_eval.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from config import Config
from strategies.macd_agent import MACDAgentStrategy

HORIZONS = [1, 4, 12, 24, 72, 168]  # 1h K 线根数：1h / 4h / 12h / 1d / 3d / 1w


def load_cache(symbol: str) -> pd.DataFrame:
    cache = PROJECT_ROOT / "data" / "cache" / f"{symbol}_1h.csv"
    if not cache.exists():
        raise SystemExit("缓存不存在，先跑: python scripts/robustness_check.py --years 3")
    df = pd.read_csv(cache, index_col="timestamp")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Shanghai")
    return df


def eval_events(df: pd.DataFrame) -> pd.DataFrame:
    """逐事件类型 × 周期的方向调整收益 / 胜率 / t 值 / IC / ICIR"""
    strategy = MACDAgentStrategy("eval", {})
    events = strategy._build_events(df)
    directions = strategy._directions
    close = df["close"].astype(float).to_numpy()
    n_bars = len(df)
    pos_of_epoch = {int(t.timestamp()): i for i, t in enumerate(df.index)}
    month_of_bar = df.index.to_period("M")

    # 预计算各周期全样本前向收益（IC 用）与无条件均值（基线）
    fwd_all = {}
    baseline = {}
    for h in HORIZONS:
        r = pd.Series(close).shift(-h) / pd.Series(close) - 1
        fwd_all[h] = r.to_numpy()
        baseline[h] = float(r.mean() * 1e4)

    rows = []
    for (sig, tf), g in events.groupby(["signal", "timeframe"]):
        direction = directions.get(sig, 0.0)
        if direction == 0.0:
            continue
        # 事件 ts = 生成 bar 的收盘时刻 → 信号来源 = ts-3600 处的 1h bar 收盘价。
        # 对 1h 事件即生成 bar 本身；对 1d 事件是当日最后一根 1h bar
        # （其收盘 == 日线收盘）。缓存固定为 1h，故统一 -3600。
        src_epochs = (g["ts"] - 3600).astype(int)
        positions = pd.array([pos_of_epoch.get(e, np.nan) for e in src_epochs], dtype="float")
        g = g.assign(pos=positions).dropna(subset=["pos"])
        g["pos"] = g["pos"].astype(int)
        if len(g) < 5:
            continue

        row = {"signal": sig, "tf": tf, "n": len(g)}
        sig_arr = np.zeros(n_bars)
        sig_arr[g["pos"].to_numpy()] = direction

        for h in HORIZONS:
            valid = g["pos"] + h < n_bars
            src = g.loc[valid, "pos"].to_numpy()
            # 方向调整条件收益
            adj = (close[src + h] / close[src] - 1) * direction * 1e4  # bps
            row[f"r{h}(bps)"] = round(float(adj.mean()), 1)
            row[f"w{h}%"] = round(float((adj > 0).mean() * 100), 0)
            row[f"t{h}"] = round(float(adj.mean() / (adj.std(ddof=1) / np.sqrt(len(adj)))), 2) if len(adj) > 1 else np.nan
            # 全样本 IC（信号 ±1/0 vs 前向收益）
            fr = fwd_all[h]
            mask = ~np.isnan(fr)
            row[f"ic{h}"] = round(float(np.corrcoef(sig_arr[mask], fr[mask])[0, 1]), 4)
            # 逐月 IC → ICIR
            monthly = []
            for m in month_of_bar.unique():
                mm = mask & np.asarray(month_of_bar == m)
                if mm.sum() > h * 2 and sig_arr[mm].any():
                    monthly.append(np.corrcoef(sig_arr[mm], fr[mm])[0, 1])
            if len(monthly) >= 6:
                row[f"icir{h}"] = round(float(np.mean(monthly) / (np.std(monthly) + 1e-12)), 2)
            else:
                row[f"icir{h}"] = np.nan
        rows.append(row)

    out = pd.DataFrame(rows)
    out.attrs["baseline_bps"] = baseline
    return out


def eval_daily_regime(df: pd.DataFrame) -> pd.DataFrame:
    """日线 EMA50 regime 的 rank IC（策略重写候选因子）"""
    daily = df["close"].resample("1D", label="left", closed="left").last().dropna()
    ema = daily.ewm(span=50, adjust=False).mean()
    sig = np.sign(daily - ema)  # 当日信号预测未来收益（IC 标准口径，无前视）
    rows = []
    for h in [1, 5, 10, 20]:
        fwd = daily.shift(-h) / daily - 1
        pair = pd.DataFrame({"sig": sig, "fwd": fwd}).dropna()
        ic = pair["sig"].corr(pair["fwd"], method="spearman")
        monthly = []
        for _, gm in pair.groupby(pair.index.to_period("M")):
            if len(gm) > h and gm["sig"].nunique() > 1:
                monthly.append(gm["sig"].corr(gm["fwd"], method="spearman"))
        above = pair.loc[pair["sig"] > 0, "fwd"]
        below = pair.loc[pair["sig"] < 0, "fwd"]
        rows.append({
            "horizon_d": h, "rank_ic": round(float(ic), 4),
            "icir": round(float(np.mean(monthly) / (np.std(monthly) + 1e-12)), 2) if len(monthly) >= 6 else np.nan,
            "站上日均收益bps": round(float(above.mean() * 1e4), 1),
            "跌破日均收益bps": round(float(below.mean() * 1e4), 1),
        })
    return pd.DataFrame(rows)


def main():
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    cfg = Config.load(str(cfg_path)) if cfg_path.exists() else Config()
    df = load_cache(cfg.trading.symbol)
    print(f"数据: {len(df)} 根 1h  {df.index[0]} ~ {df.index[-1]}")

    table = eval_events(df)
    base = table.attrs["baseline_bps"]
    print(f"\n基线（无条件前向收益均值, bps）: "
          + "  ".join(f"{h}h={base[h]:.1f}" for h in HORIZONS))

    # 主表：24h（1 天）周期为核心参考，附 4h 与 168h
    cols = ["signal", "tf", "n",
            "r4(bps)", "w4%", "t4", "ic4", "icir4",
            "r24(bps)", "w24%", "t24", "ic24", "icir24",
            "r168(bps)", "w168%", "t168"]
    show = table[[c for c in cols if c in table.columns]].sort_values("ic24", ascending=False)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    print("\n══ 事件因子评估（按 24h IC 排序）══")
    print(show.to_string(index=False))

    regime = eval_daily_regime(df)
    print("\n══ 日线 EMA50 regime 因子 ══")
    print(regime.to_string(index=False))

    out_dir = PROJECT_ROOT / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now().strftime("%Y%m%d")
    table.to_csv(out_dir / f"factor_ic_events_{stamp}.csv", index=False)
    regime.to_csv(out_dir / f"factor_ic_daily_regime_{stamp}.csv", index=False)
    print(f"\n已存: data/reports/factor_ic_events_{stamp}.csv / factor_ic_daily_regime_{stamp}.csv")


if __name__ == "__main__":
    main()
