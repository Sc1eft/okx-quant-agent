"""
🔧 P0: Walk-forward 验证 + 蒙特卡洛参数测试

核心目的：
  1. Walk-forward: 滚动训练/测试，检测策略是否过拟合
  2. 蒙特卡洛: 随机参数测试，看策略对参数敏感度
  3. Out-of-sample: 留 30% 数据不碰，验证真实表现

反过拟合三板斧：
  - Walk-forward：多窗口验证，每窗口用前面数据训练、后面测试
  - 参数稳定性：随机采样参数组合，看收益分布是否集中
  - Out-of-sample：最后 30% 数据全程不碰，最后验证
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np

from config import Config
from backtest.engine import BacktestEngine

logger = logging.getLogger("backtest.analyzer")


@dataclass
class WFWindow:
    """单个 Walk-forward 窗口结果"""
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_return: float
    test_return: float
    train_sharpe: float
    test_sharpe: float
    train_max_dd: float
    test_max_dd: float
    n_trades_train: int
    n_trades_test: int


@dataclass
class WFResult:
    """Walk-forward 总体结果"""
    strategy_name: str
    windows: list[WFWindow]
    avg_train_return: float
    avg_test_return: float
    avg_train_sharpe: float
    avg_test_sharpe: float
    # 过拟合指标
    sharpe_drop_pct: float  # Sharpe 从训练到测试的下降幅度
    return_consistency: float  # 各窗口收益的一致性（std/mean）
    stable_window_ratio: float  # 收益为正的窗口占比
    verdict: str  # PASS / WARNING / FAIL
    details: str


@dataclass
class ParamSweepResult:
    """参数扫描结果"""
    strategy_name: str
    n_iterations: int
    best_return: float
    worst_return: float
    median_return: float
    std_return: float
    param_stability: float  # 变异系数 CV
    top_10pct_params: list[dict]
    verdict: str
    details: str
    n_valid: int = 0            # 交易次数达标的组合数（参与排名）
    n_skipped_low_trades: int = 0  # 因交易次数不足被排除的组合数


class WalkForwardAnalyzer:
    """
    Walk-forward 分析器
    P0 优化：每个策略在上实盘前必须先过此关
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def run(
        self,
        df: pd.DataFrame,
        strategy_name: Optional[str] = None,
        n_windows: int = 4,
        train_ratio: float = 0.7,
    ) -> WFResult:
        """
        Walk-forward 验证
        将数据分成 n_windows 个时间窗口，每个窗口前 train_ratio 训练、后 (1-train_ratio) 测试
        """
        engine = BacktestEngine(self.cfg)
        name = strategy_name or self.cfg.strategy.enabled_strategies[0]

        df_sorted = df.sort_index()
        total_len = len(df_sorted)
        window_size = total_len // n_windows
        test_size = int(window_size * (1 - train_ratio))

        windows = []

        for i in range(n_windows):
            train_end_idx = (i + 1) * window_size - test_size
            if i == 0:
                train_start_idx = 0
            else:
                train_start_idx = i * window_size

            if train_end_idx <= train_start_idx or train_end_idx >= total_len:
                continue

            test_start_idx = train_end_idx
            test_end_idx = min(test_start_idx + test_size, total_len)

            train_df = df_sorted.iloc[train_start_idx:train_end_idx]
            test_df = df_sorted.iloc[test_start_idx:test_end_idx]

            if len(train_df) < 50 or len(test_df) < 20:
                continue

            # 训练集回测
            train_result = engine.run(train_df, strategy_name=name)
            # 测试集回测
            test_result = engine.run(test_df, strategy_name=name)

            window = WFWindow(
                train_start=train_df.index[0],
                train_end=train_df.index[-1],
                test_start=test_df.index[0],
                test_end=test_df.index[-1],
                train_return=train_result.metrics.get("total_return_pct", 0),
                test_return=test_result.metrics.get("total_return_pct", 0),
                train_sharpe=train_result.metrics.get("sharpe", 0),
                test_sharpe=test_result.metrics.get("sharpe", 0),
                train_max_dd=train_result.metrics.get("max_drawdown_pct", 0),
                test_max_dd=test_result.metrics.get("max_drawdown_pct", 0),
                n_trades_train=train_result.metrics.get("total_trades", 0),
                n_trades_test=test_result.metrics.get("total_trades", 0),
            )
            windows.append(window)

        if not windows:
            return WFResult(
                strategy_name=name, windows=[], avg_train_return=0, avg_test_return=0,
                avg_train_sharpe=0, avg_test_sharpe=0, sharpe_drop_pct=0,
                return_consistency=0, stable_window_ratio=0, verdict="FAIL",
                details="数据不足，无法完成 walk-forward 验证",
            )

        # 汇总
        avg_train_return = float(np.mean([w.train_return for w in windows]))
        avg_test_return = float(np.mean([w.test_return for w in windows]))
        avg_train_sharpe = float(np.mean([w.train_sharpe for w in windows]))
        avg_test_sharpe = float(np.mean([w.test_sharpe for w in windows]))

        # 过拟合指标
        sharpe_drop = (avg_train_sharpe - avg_test_sharpe) / abs(avg_train_sharpe) * 100 if avg_train_sharpe != 0 else 0
        test_returns = [w.test_return for w in windows]
        return_consistency = float(np.std(test_returns) / abs(np.mean(test_returns))) if np.mean(test_returns) != 0 else float("inf")
        stable_ratio = sum(1 for r in test_returns if r > 0) / len(test_returns)

        # 判定
        if sharpe_drop < 30 and stable_ratio >= 0.5 and avg_test_return > 0:
            verdict = "PASS"
            details = "策略在多个窗口表现一致，过拟合风险低"
        elif sharpe_drop < 60 and stable_ratio >= 0.3:
            verdict = "WARNING"
            details = f"策略有一定过拟合风险，Sharpe 下降 {sharpe_drop:.0f}%"
        else:
            verdict = "FAIL"
            details = f"策略严重过拟合！训练集 ({avg_train_return:.1f}%) 远好于测试集 ({avg_test_return:.1f}%)"

        result = WFResult(
            strategy_name=name,
            windows=windows,
            avg_train_return=round(avg_train_return, 2),
            avg_test_return=round(avg_test_return, 2),
            avg_train_sharpe=round(avg_train_sharpe, 2),
            avg_test_sharpe=round(avg_test_sharpe, 2),
            sharpe_drop_pct=round(sharpe_drop, 1),
            return_consistency=round(return_consistency, 2),
            stable_window_ratio=round(stable_ratio, 2),
            verdict=verdict,
            details=details,
        )

        try:
            self._print_report(result)
        except UnicodeEncodeError:
            pass  # Windows GBK 控制台无法打印 emoji，结果已在返回值中
        return result

    def parameter_sweep(
        self,
        df: pd.DataFrame,
        strategy_name: Optional[str] = None,
        n_iterations: int = 200,
        min_trades: int = 5,
    ) -> ParamSweepResult:
        """
        蒙特卡洛参数扫描（带过拟合护栏）
        随机采样参数组合，评估策略的参数敏感度
        参数越敏感 → 越容易过拟合

        护栏：
          - 交易次数 < min_trades 的组合不参与排名（0~2 笔交易的"高收益"是噪音）
          - Top 5 组合自动在最后 30% 留出段复验（样本外保留率）
          - 判定附带多重检验提示（N 次试验的最优存在选择偏差）
        """
        engine = BacktestEngine(self.cfg)
        name = strategy_name or self.cfg.strategy.enabled_strategies[0]

        param_spaces = {
            "ma_cross": lambda: {
                "short_window": random.randint(3, 50),
                "long_window": random.randint(10, 100),
                "stop_loss_pct": round(random.uniform(1, 5), 1),
                "take_profit_pct": round(random.uniform(3, 15), 1),
            },
            "rsi_mean_reversion": lambda: {
                "rsi_period": random.randint(5, 30),
                "oversold": random.randint(20, 40),
                "overbought": random.randint(60, 80),
                "stop_loss_pct": round(random.uniform(1, 4), 1),
            },
            "breakout": lambda: {
                "period": random.randint(5, 50),
                "atr_multiplier": round(random.uniform(1.0, 3.5), 1),
                "stop_loss_pct": round(random.uniform(1, 4), 1),
            },
            "macd_agent": lambda: {
                "score_threshold": round(random.uniform(0.1, 0.8), 2),
                "min_confidence": round(random.uniform(0.5, 0.95), 2),
            },
            "daily_trend": lambda: {
                "trend_span": random.randint(30, 80),
                "trigger_lookback_days": random.randint(2, 5),
            },
        }

        param_gen = param_spaces.get(name, param_spaces["ma_cross"])

        # ── IS/OOS 切分：扫描只碰前 70%，top 组合在最后 30% 复验 ──
        split_idx = int(len(df) * 0.7)
        is_df = df.iloc[:split_idx]
        oos_df = df.iloc[split_idx:]

        results = []
        for i in range(n_iterations):
            try:
                params = param_gen()
                result = engine.run(is_df, strategy_name=name, params=params)
                ret = result.metrics.get("total_return_pct", -999)
                results.append({
                    "params": params,
                    "return": ret,
                    "sharpe": result.metrics.get("sharpe", 0),
                    "max_dd": result.metrics.get("max_drawdown_pct", 0),
                    "trades": result.metrics.get("total_trades", 0),
                })
            except Exception:
                continue

        if not results:
            return ParamSweepResult(
                strategy_name=name, n_iterations=0, best_return=0, worst_return=0,
                median_return=0, std_return=0, param_stability=0,
                top_10pct_params=[], verdict="FAIL", details="参数扫描失败",
            )

        # ── 护栏 1：交易次数过少的组合是噪音，不参与排名 ──
        valid = [r for r in results if r["trades"] >= min_trades]
        n_skipped = len(results) - len(valid)
        ranked = valid if valid else results

        returns = np.array([r["return"] for r in ranked])
        best_idx = int(np.argmax(returns))
        worst_idx = int(np.argmin(returns))

        # 变异系数 CV = std/mean（越小越稳定）
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns))
        cv = std_ret / abs(mean_ret) if mean_ret != 0 else float("inf")

        # 前 10% 参数（仅从交易次数达标的组合中选）
        sorted_results = sorted(ranked, key=lambda r: r["return"], reverse=True)
        top_10pct = sorted_results[:max(1, n_iterations // 10)]

        # ── 护栏 2：top 组合在留出段复验（样本外保留率）──
        if valid and len(oos_df) >= 30:
            for entry in top_10pct[:5]:
                try:
                    oos_result = engine.run(oos_df, strategy_name=name, params=entry["params"])
                    entry["oos_return"] = oos_result.metrics.get("total_return_pct", 0)
                    entry["oos_sharpe"] = oos_result.metrics.get("sharpe", 0)
                    is_ret = entry["return"]
                    entry["oos_retention"] = (
                        round(entry["oos_return"] / is_ret * 100, 1) if is_ret else 0
                    )
                except Exception:
                    continue

        # ── 护栏 3：多重检验提示（N 次随机试验中的最优存在选择偏差）──
        mt_note = (
            f"（{len(ranked)} 组有效参数参与排名，最优点为多次试验中的极值，"
            f"样本外表现预期退化，请以留出段复验为准）"
        )

        # 判定
        if not valid:
            verdict = "FAIL"
            details = f"所有组合交易次数均不足 {min_trades} 笔，无法评估参数稳定性"
        elif cv < 1.0 and mean_ret > 0:
            verdict = "PASS"
            details = f"参数稳定性好 (CV={cv:.2f})，策略对参数不敏感{mt_note}"
        elif cv < 2.0:
            verdict = "WARNING"
            details = f"参数有一定敏感性 (CV={cv:.2f})，建议优中选优{mt_note}"
        else:
            verdict = "FAIL"
            details = f"参数极度敏感 (CV={cv:.2f})！微调参数导致收益剧烈波动{mt_note}"
        if n_skipped:
            details += f"；{n_skipped} 组因交易次数 <{min_trades} 被排除"

        try:
            self._print_sweep_report(name, ranked, verdict, details)
        except UnicodeEncodeError:
            pass  # Windows GBK 控制台无法打印 emoji，结果已在返回值中
        return ParamSweepResult(
            strategy_name=name,
            n_iterations=len(results),
            best_return=round(float(returns[best_idx]), 2),
            worst_return=round(float(returns[worst_idx]), 2),
            median_return=round(float(np.median(returns)), 2),
            std_return=round(std_ret, 2),
            param_stability=round(cv, 2),
            top_10pct_params=top_10pct[:5],
            verdict=verdict,
            details=details,
            n_valid=len(valid),
            n_skipped_low_trades=n_skipped,
        )

    def out_of_sample_test(
        self,
        df: pd.DataFrame,
        strategy_name: Optional[str] = None,
        oos_ratio: float = 0.3,
    ) -> dict:
        """
        留出法测试：最后 oos_ratio 数据不参与任何调参
        只有在完整的 in-sample 优化后，最后验证一次
        """
        split_idx = int(len(df) * (1 - oos_ratio))
        is_df = df.iloc[:split_idx]
        oos_df = df.iloc[split_idx:]

        engine = BacktestEngine(self.cfg)
        name = strategy_name or self.cfg.strategy.enabled_strategies[0]

        is_result = engine.run(is_df, strategy_name=name)
        oos_result = engine.run(oos_df, strategy_name=name)

        is_ret = is_result.metrics.get("total_return_pct", 0)
        oos_ret = oos_result.metrics.get("total_return_pct", 0)
        oos_sharpe = oos_result.metrics.get("sharpe", 0)

        report = {
            "strategy": name,
            "in_sample_return": is_ret,
            "out_of_sample_return": oos_ret,
            "out_of_sample_sharpe": oos_sharpe,
            "oos_trades": oos_result.metrics.get("total_trades", 0),
            "retention_ratio": round(oos_ret / is_ret * 100, 1) if is_ret != 0 else 0,
        }
        retention = report["retention_ratio"]
        # 判定纳入收益保留率：OOS 保留 <50% 说明样本内优化未能泛化
        if oos_ret > 0 and oos_sharpe > 0.5 and retention >= 50:
            verdict = "PASS"
        elif oos_ret > 0 and retention >= 25:
            verdict = "WARNING"
        else:
            verdict = "FAIL"
        report["verdict"] = verdict

        logger.info(f"📊 Out-of-Sample 测试 ({name}):")
        logger.info(f"  In-sample: {is_ret:+.2f}%")
        logger.info(f"  Out-of-sample: {oos_ret:+.2f}%")
        logger.info(f"  OOS Sharpe: {oos_sharpe:.2f}")
        logger.info(f"  收益保留率: {report['retention_ratio']:.1f}%")
        logger.info(f"  判定: {report['verdict']}")

        return report

    def _print_report(self, result: WFResult):
        """打印 Walk-forward 报告"""
        print("\n" + "=" * 60)
        print(f"  Walk-Forward 验证: {result.strategy_name}")
        print("=" * 60)
        print(f"  窗口数:     {len(result.windows)}")
        print(f"  训练 Sharpe: {result.avg_train_sharpe:.2f} → 测试 Sharpe: {result.avg_test_sharpe:.2f}")
        print(f"  Sharpe 下降: {result.sharpe_drop_pct:.1f}%")
        print(f"  训练收益:   {result.avg_train_return:+.2f}% → 测试收益: {result.avg_test_return:+.2f}%")
        print(f"  窗口稳定性: {result.stable_window_ratio:.0%} 窗口盈利")
        print(f"  ⚖️  判定: {result.verdict} — {result.details}")
        print("-" * 60)
        for i, w in enumerate(result.windows):
            print(f"  W{i+1}: 训练 {w.train_return:+.1f}% → 测试 {w.test_return:+.1f}% | "
                  f"Sharpe {w.train_sharpe:.1f}→{w.test_sharpe:.1f}")
        print("=" * 60 + "\n")

    def _print_sweep_report(self, name: str, results: list, verdict: str, details: str):
        returns = [r["return"] for r in results]
        print("\n" + "=" * 60)
        print(f"  参数扫描: {name} ({len(results)} 次)")
        print("=" * 60)
        print(f"  最佳: {max(returns):+.2f}% | 最差: {min(returns):+.2f}% | 中位: {np.median(returns):+.2f}%")
        print(f"  Std: {np.std(returns):.2f}%")
        print(f"  ⚖️  判定: {verdict} — {details}")
        print("=" * 60 + "\n")
