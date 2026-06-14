"""
🔧 P3: DeepSeek Agent — 回测报告分析

第一版范围：
  - 回测报告解读
  - 过拟合迹象检查
  - 风险点提示
  - 参数优化建议

不做：
  - ❌ 实时行情判断
  - ❌ 直接下单
  - ❌ 自动修改策略参数
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import pandas as pd

from config import Config, AgentConfig

logger = logging.getLogger("agent.analyzer")


class ReportAnalyzer:
    """
    DeepSeek Agent — 回测分析
    第一版：只做回测后分析和报告解读
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client: Optional["OpenAI"] = None

    def _init_client(self):
        """延迟初始化 API 客户端"""
        if self.client is not None:
            return
        try:
            from openai import OpenAI
            self.client = OpenAI(
                api_key=self.cfg.agent.api_key or "sk-placeholder",
                base_url=self.cfg.agent.base_url,
            )
        except ImportError:
            logger.warning("openai 库未安装，Agent 功能不可用")
            self.client = None

    def analyze_backtest(self, result, symbol: str) -> str:
        """
        分析回测结果
        提供自然语言解读和风险提示
        """
        if not self.cfg.agent.enabled:
            return "[Agent disabled]"

        self._init_client()
        if self.client is None:
            return self._local_analysis(result, symbol)

        # 构建回测摘要
        m = result.metrics
        summary = f"""
        回测结果摘要:
        - 策略: {result.strategy_name}
        - 交易对: {symbol}
        - 总收益: {m.get('total_return_pct', 'N/A')}%
        - 年化收益: {m.get('annual_return_pct', 'N/A')}%
        - 最大回撤: {m.get('max_drawdown_pct', 'N/A')}%
        - Sharpe: {m.get('sharpe', 'N/A')}
        - 胜率: {m.get('win_rate', 'N/A')}%
        - 交易次数: {m.get('total_trades', 'N/A')}
        - Benchmark(持有): {m.get('benchmark_return_pct', 'N/A')}%
        """

        try:
            resp = self.client.chat.completions.create(
                model=self.cfg.agent.model,
                messages=[
                    {"role": "system", "content": "你是一个量化交易回测分析师。请指出回测中的问题、过拟合迹象和改进方向。简明扼要，每条分析带证据。"},
                    {"role": "user", "content": f"分析以下回测结果：\n{summary}"},
                ],
                temperature=self.cfg.agent.temperature,
                max_tokens=self.cfg.agent.max_tokens,
            )
            analysis = resp.choices[0].message.content or ""
            logger.info(f"🤖 Agent 回测分析完成:\n{analysis}")
            return analysis
        except Exception as e:
            logger.warning(f"Agent API 调用失败: {e}，使用本地分析")
            return self._local_analysis(result, symbol)

    def analyze_overfitting(self, wf_result) -> str:
        """
        分析 Walk-forward 结果中的过拟合风险
        """
        if not self.cfg.agent.enabled or self.client is None:
            return self._local_overfitting_analysis(wf_result)

        summary = f"""
        Walk-forward 验证结果:
        - 策略: {wf_result.strategy_name}
        - 窗口数: {len(wf_result.windows)}
        - 训练 Sharpe: {wf_result.avg_train_sharpe} → 测试 Sharpe: {wf_result.avg_test_sharpe}
        - 训练收益: {wf_result.avg_train_return}% → 测试收益: {wf_result.avg_test_return}%
        - Sharpe 下降: {wf_result.sharpe_drop_pct}%
        - 判定: {wf_result.verdict}
        """
        return summary

    def _local_analysis(self, result, symbol: str) -> str:
        """本地离线分析（不依赖 DeepSeek API）"""
        m = result.metrics
        warnings = []

        # 过拟合检查
        if m.get("sharpe", 0) > 3:
            warnings.append("⚠️  Sharpe > 3，存在过拟合嫌疑，建议做 Walk-forward 验证")
        if m.get("win_rate", 0) > 80:
            warnings.append("⚠️  胜率 > 80%，在趋势跟踪策略中少见，检查是否过拟合")
        if m.get("total_trades", 0) < 20:
            warnings.append("⚠️  交易次数不足 20 次，统计意义有限")
        if m.get("max_drawdown_pct", 0) < 2:
            warnings.append("⚠️  最大回撤 < 2%，异常偏低，检查回测是否有 bug")
        if m.get("total_return_pct", 0) > 500:
            warnings.append("⚠️  收益率 > 500%，在现货 BTC 中极其异常，检查回测逻辑")

        analysis = f"""
📊 回测分析报告 — {result.strategy_name} @ {symbol}

📈 绩效概览:
  总收益 {m.get('total_return_pct', 0):+.2f}% | 年化 {m.get('annual_return_pct', 0):+.2f}%
  Sharpe {m.get('sharpe', 0):.2f} | 回撤 {m.get('max_drawdown_pct', 0):.2f}%
  胜率 {m.get('win_rate', 0):.1f}% | 盈亏比 {m.get('profit_factor', 0):.2f}
  交易 {m.get('total_trades', 0)} 笔

📊 基准对比:
  {m.get('benchmark_vs_strategy', '')}

{"".join(warnings) if warnings else "✅ 未发现明显异常"}

💡 建议:
  1. 跑 Walk-forward 确认收益可持续性
  2. 参数敏感性分析（蒙特卡洛扫描）
  3. 最后 30% 数据留作 out-of-sample 验证
"""
        print(analysis)
        return analysis

    def _local_overfitting_analysis(self, wf_result) -> str:
        analysis = f"""
📊 过拟合分析 — {wf_result.strategy_name}

Walk-Forward 结果:
  {wf_result.avg_train_return:+.2f}% (训练) → {wf_result.avg_test_return:+.2f}% (测试)
  Sharpe: {wf_result.avg_train_sharpe:.2f} → {wf_result.avg_test_sharpe:.2f}
  Sharpe 下降 {wf_result.sharpe_drop_pct:.1f}%

判定: {wf_result.verdict}
{wf_result.details}

{'建议: 策略可接受，但需持续监控测试集表现' if wf_result.verdict == 'PASS'
 else '建议: 优化参数或换策略，过拟合风险较高'}
"""
        print(analysis)
        return analysis
