#!/usr/bin/env python3
"""
OKX 虚拟币量化交易系统 — 主入口

使用:
  python main.py --mode backtest          # 回测
  python main.py --mode paper             # 模拟盘
  python main.py --mode demo              # OKX 模拟盘
  python main.py --mode live              # 实盘（谨慎！）
  python main.py --walk-forward           # Walk-forward 验证
  python main.py --param-sweep            # 参数扫描
  python main.py --list-strategies        # 列出策略
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config import Config, DEFAULT_CONFIG, CONFIG_PATH


def setup_logging(cfg: Config):
    log_dir = Path(cfg.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "quant.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def run_backtest(cfg: Config):
    """运行回测"""
    from data.storage import DataStore
    from data.collector import DataCollector
    from backtest.engine import BacktestEngine
    from backtest.analyzer import WalkForwardAnalyzer
    from agent.report_analyzer import ReportAnalyzer

    logger = logging.getLogger("backtest")

    # 1. 加载数据
    store = DataStore(cfg)
    collector = DataCollector(cfg)
    if store.count_klines(cfg.trading.symbol, cfg.trading.primary_timeframe) < 200:
        logger.info("数据不足，从 OKX 下载…")
        collector.download_klines(cfg.trading.symbol, cfg.trading.primary_timeframe, limit=300)

    df = store.load_klines(cfg.trading.symbol, cfg.trading.primary_timeframe)
    logger.info(f"加载了 {len(df)} 根 K 线 ({df.index[0]} ~ {df.index[-1]})")

    # 2. 回测
    engine = BacktestEngine(cfg)
    result = engine.run(df)

    # 3. 报告
    engine.report(result)

    # 4. Agent 分析
    if cfg.agent.enabled:
        analyzer = ReportAnalyzer(cfg)
        analyzer.analyze_backtest(result, cfg.trading.symbol)

    return result


def run_walk_forward(cfg: Config):
    """Walk-forward 验证"""
    from data.storage import DataStore
    from data.collector import DataCollector
    from backtest.analyzer import WalkForwardAnalyzer
    from agent.report_analyzer import ReportAnalyzer

    logger = logging.getLogger("walk_forward")

    store = DataStore(cfg)
    collector = DataCollector(cfg)
    if store.count_klines(cfg.trading.symbol, cfg.trading.primary_timeframe) < 500:
        logger.info("数据不足，从 OKX 下载…")
        collector.download_klines(cfg.trading.symbol, cfg.trading.primary_timeframe, limit=500)

    df = store.load_klines(cfg.trading.symbol, cfg.trading.primary_timeframe)

    wf = WalkForwardAnalyzer(cfg)
    results = wf.run(df, n_windows=4)

    if cfg.agent.enabled:
        agent = ReportAnalyzer(cfg)
        agent.analyze_overfitting(results)

    return results


def run_paper_trading(cfg: Config):
    """本地模拟盘"""
    from execution.paper import PaperEngine
    engine = PaperEngine(cfg)
    engine.run()


def list_strategies(cfg: Config):
    """列出所有可用策略"""
    from strategies.base import get_available_strategies
    print("\n📊 可用策略池:")
    print("=" * 60)
    for name, info in get_available_strategies().items():
        print(f"  {name:25s} | {info['description']}")
        print(f"  {'':25s}   默认参数: {info['params']}")
        print()


def param_sweep(cfg: Config):
    """参数扫描（蒙特卡洛）"""
    from backtest.analyzer import WalkForwardAnalyzer
    from data.storage import DataStore
    from data.collector import DataCollector

    store = DataStore(cfg)
    collector = DataCollector(cfg)
    if store.count_klines(cfg.trading.symbol, cfg.trading.primary_timeframe) < 300:
        collector.download_klines(cfg.trading.symbol, cfg.trading.primary_timeframe, limit=300)

    df = store.load_klines(cfg.trading.symbol, cfg.trading.primary_timeframe)

    wf = WalkForwardAnalyzer(cfg)
    results = wf.parameter_sweep(df, n_iterations=200)
    return results


def main():
    parser = argparse.ArgumentParser(description="OKX 量化交易系统")
    parser.add_argument("--mode", choices=["backtest", "paper", "demo", "live"], default="backtest")
    parser.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--walk-forward", action="store_true", help="Walk-forward 验证")
    parser.add_argument("--list-strategies", action="store_true", help="列出策略")
    parser.add_argument("--param-sweep", action="store_true", help="参数扫描")
    args = parser.parse_args()

    # 加载配置
    cfg = DEFAULT_CONFIG
    cfg.mode = args.mode

    setup_logging(cfg)

    if args.list_strategies:
        list_strategies(cfg)
        return

    if args.walk_forward:
        run_walk_forward(cfg)
        return

    if args.param_sweep:
        param_sweep(cfg)
        return

    if args.mode == "backtest":
        run_backtest(cfg)
    elif args.mode == "paper":
        run_paper_trading(cfg)
    else:
        print(f"⚠️  '{args.mode}' 模式需要 API Key 配置，请先设置配置文件")
        print(f"   设置 OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE 环境变量")


if __name__ == "__main__":
    main()
