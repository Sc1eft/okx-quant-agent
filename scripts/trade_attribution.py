"""交易归因：按「入场信号」拆盈亏 —— 钱究竟赚/亏在哪类触发上（HAA 借鉴清单②）

引擎的 Trade 只存出场 reason；入场 reason 从 signals_df 回查
（引擎 next-bar 执行：信号 bar 的下一根 bar 即成交 bar）。
归一化规则：
  - 去掉 score/conf 数值尾巴（macd_agent 的「多周期共振 score=+0.85 ...」）
  - 保留触发名括号（daily_trend 的「日线触发入场（boll/kdj）」—— 括号即因子本体）

用法: python scripts/trade_attribution.py [strategy ...]   默认: daily_trend macd_agent
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from backtest.engine import BacktestEngine
from config import Config
from strategies.base import create_strategy

DEFAULT_STRATEGIES = ["daily_trend", "macd_agent"]


def _norm(reason: str) -> str:
    r = re.sub(r"\s*score=.*$", "", reason or "").strip()
    return r or "(无记录)"


def attribute(res, own_signals: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回（按入场原因聚合, 按出场原因聚合）两张表

    own_signals：策略自己的 generate_signals 输出 —— 引擎 _combine_signals
    会把 reason 重写成「策略名:信号」，原始触发原因只在策略自己的帧里。
    """
    sig = own_signals
    rows = []
    for t in res.trades:
        entry_reason = "(无记录)"
        try:
            pos = sig.index.get_loc(t.entry_time)
            if isinstance(pos, slice):
                pos = pos.start
            if pos and pos > 0:
                entry_reason = sig["reason"].iloc[pos - 1]
        except KeyError:
            pass
        rows.append({
            "入场原因": _norm(entry_reason),
            "出场原因": _norm(t.reason),
            "pnl": t.pnl,
            "fee": t.fee,
            "持仓天": (t.exit_time - t.entry_time).total_seconds() / 86400,
        })
    if not rows:
        return pd.DataFrame(), pd.DataFrame()
    df = pd.DataFrame(rows)

    def agg(col):
        g = df.groupby(col)
        out = g.agg(笔数=("pnl", "size"), 总盈亏=("pnl", "sum"),
                    平均盈亏=("pnl", "mean"), 总手续费=("fee", "sum"),
                    平均持仓天=("持仓天", "mean"))
        out["胜率%"] = g["pnl"].apply(lambda s: (s > 0).mean() * 100)
        out = out.round({"总盈亏": 1, "平均盈亏": 1, "总手续费": 1, "平均持仓天": 1, "胜率%": 0})
        return out.sort_values("总盈亏", ascending=False)

    return agg("入场原因"), agg("出场原因")


def main():
    strategies = sys.argv[1:] or DEFAULT_STRATEGIES
    cfg_path = PROJECT_ROOT / "configs" / "default.json"
    cfg = Config.load(str(cfg_path)) if cfg_path.exists() else Config()
    cfg.risk.max_single_order_pct = 1.0  # 全额本金口径，与基线/回测报告一致

    cache = PROJECT_ROOT / "data" / "cache" / f"{cfg.trading.symbol}_1h.csv"
    df = pd.read_csv(cache, index_col="timestamp")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Shanghai")
    print(f"数据: {len(df)} 根 1h  {df.index[0]} ~ {df.index[-1]}")

    engine = BacktestEngine(cfg)
    out_dir = PROJECT_ROOT / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now().strftime("%Y%m%d")
    pd.set_option("display.width", 200)

    for name in strategies:
        res = engine.run(df, strategy_name=name)
        m = res.metrics
        own_signals = create_strategy(name).generate_signals(df).signals
        by_entry, by_exit = attribute(res, own_signals)
        print(f"\n══ {name}（总收益 {m['total_return_pct']:+.1f}%，{len(res.trades)} 笔）══")
        print("\n-- 按入场原因 --")
        print(by_entry.to_string() if not by_entry.empty else "(无交易)")
        print("\n-- 按出场原因 --")
        print(by_exit.to_string() if not by_exit.empty else "(无交易)")
        by_entry.to_csv(out_dir / f"trade_attribution_{name}_entry_{stamp}.csv")
        print(f"已存: data/reports/trade_attribution_{name}_entry_{stamp}.csv")


if __name__ == "__main__":
    main()
