"""Agent Analysis page - DeepSeek analysis display and interaction."""

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.utils.session_state import get_config
from frontend.utils.backtest_runner import run_backtest
from strategies.base import get_available_strategies


st.title("🤖 Agent 分析")
st.markdown("DeepSeek 驱动的回测分析和交易复盘")


cfg = get_config()
strategies = get_available_strategies()
strategy_names = list(strategies.keys())

# ============ Run Analysis Section ============
st.subheader("📊 回测分析")

ana_cols = st.columns([2, 1, 1])
with ana_cols[0]:
    if strategy_names:
        analysis_strategy = st.selectbox("选择策略", strategy_names, key="agent_strategy")
    else:
        st.warning("无可用的策略")
        st.stop()

with ana_cols[1]:
    run_analysis_btn = st.button("▶ 运行 DeepSeek 分析", type="primary", use_container_width=True)

with ana_cols[2]:
    use_local = st.checkbox("使用本地分析 (不调用 API)", value=True, key="agent_local")


# Run analysis
if run_analysis_btn:
    with st.spinner(f"正在运行 {analysis_strategy} 回测并生成分析..."):
        # Run backtest first
        result = run_backtest(analysis_strategy, cfg)
        if result:
            metrics = result.get("metrics", {})
            trades = result.get("trades", [])

            # Store analysis data
            st.session_state.agent_analysis[analysis_strategy] = {
                "metrics": metrics,
                "trades": trades,
                "strategy": analysis_strategy,
            }

            # Generate analysis text
            analysis_lines = []
            analysis_lines.append(f"## {analysis_strategy} 回测分析报告")
            analysis_lines.append("")
            analysis_lines.append("### 核心指标")
            analysis_lines.append(f"- 总收益率: {metrics.get('total_return_pct', 0):+.2f}%")
            analysis_lines.append(f"- 年化收益率: {metrics.get('annual_return_pct', 0):+.2f}%")
            analysis_lines.append(f"- 最大回撤: {metrics.get('max_drawdown_pct', 0):.2f}%")
            analysis_lines.append(f"- Sharpe 比率: {metrics.get('sharpe', 0):.2f}")
            analysis_lines.append(f"- 胜率: {metrics.get('win_rate', 0):.1f}%")
            analysis_lines.append(f"- 盈亏比: {metrics.get('profit_factor', 0):.2f}")
            analysis_lines.append(f"- 总交易次数: {metrics.get('total_trades', 0)}")
            analysis_lines.append(f"- 跑赢基准: {'是' if metrics.get('outperform_benchmark') else '否'}")
            analysis_lines.append("")

            # Local heuristic analysis
            analysis_lines.append("### 本地分析判断")
            warnings = []

            sharpe = metrics.get('sharpe', 0)
            if sharpe > 3:
                warnings.append("⚠️ **Sharpe > 3**: 可能存在过拟合，建议用 Walk-Forward 验证")
            elif sharpe > 2:
                warnings.append("✅ Sharpe > 2: 表现优秀")
            elif sharpe > 1:
                warnings.append("👍 Sharpe > 1: 表现良好")
            else:
                warnings.append("⚠️ Sharpe < 1: 策略风险调整后收益偏低")

            win_rate = metrics.get('win_rate', 0)
            if win_rate > 80:
                warnings.append("⚠️ **胜率 > 80%**: 对趋势跟踪策略来说偏高，需验证样本外表现")
            elif win_rate < 40:
                warnings.append("📉 胜率偏低，但若盈亏比较高则可以接受")

            total_trades = metrics.get('total_trades', 0)
            if total_trades < 20:
                warnings.append("⚠️ **交易次数 < 20**: 统计样本不足，结论置信度低")
            elif total_trades < 50:
                warnings.append("📊 交易次数适中，建议增加更多数据")

            max_dd = metrics.get('max_drawdown_pct', 0)
            if max_dd < 2:
                warnings.append("⚠️ **最大回撤 < 2%**: 异常偏低，请检查回测逻辑")
            elif max_dd > 20:
                warnings.append(f"🔴 **最大回撤 {max_dd:.1f}%**: 风险较高，建议缩小仓位")

            total_return = metrics.get('total_return_pct', 0)
            if total_return > 500:
                warnings.append("⚠️ **收益 > 500%**: 极度异常，请检查是否有计算错误")
            elif total_return > 100:
                warnings.append("🔥 收益较高，但需确认是否包含幸存者偏差")

            if not warnings:
                warnings.append("✅ 所有基础检查通过")

            for w in warnings:
                analysis_lines.append(f"- {w}")

            analysis_lines.append("")
            analysis_lines.append("### 改进建议")
            analysis_lines.append("1. **参数优化**: 运行参数扫描 (Walk-Forward 页面)")
            analysis_lines.append("2. **多策略组合**: 在 Dashboard 页面对比多策略结果")
            analysis_lines.append("3. **样本外验证**: 在 Walk-Forward 页面运行 OOS 测试")
            analysis_lines.append("4. **风控检查**: 确认止损/止盈设置是否合理 (Risk 页面)")

            st.session_state.agent_analysis[f"{analysis_strategy}_text"] = "\n".join(analysis_lines)
            st.success(f"{analysis_strategy} 分析完成!")
        else:
            st.error("回测运行失败，无法生成分析")


# ============ Display Agent Analysis ============
st.subheader("📄 分析报告")

# Show analysis for selected strategy
analysis_text = st.session_state.agent_analysis.get(f"{analysis_strategy}_text", "")
analysis_data = st.session_state.agent_analysis.get(analysis_strategy)

if analysis_text:
    st.markdown(analysis_text)
elif analysis_data:
    # Reconstruct from data
    metrics = analysis_data.get("metrics", {})
    analysis_lines = [f"## {analysis_strategy} 回测分析报告"]
    analysis_lines.append("")
    analysis_lines.append("### 核心指标")
    for k, v in metrics.items():
        analysis_lines.append(f"- {k}: {v}")
    st.markdown("\n".join(analysis_lines))
else:
    st.info("👈 选择一个策略并点击「运行 DeepSeek 分析」")


# ============ Custom Question ============
st.divider()
st.subheader("💬 自定义分析")

question = st.text_area(
    "输入你对策略或回测结果的疑问",
    placeholder="例如：这个策略在震荡市表现如何？建议优化哪些参数？",
    height=100,
)

if st.button("分析", type="primary") and question.strip():
    analysis_data = st.session_state.agent_analysis.get(analysis_strategy)
    if analysis_data:
        metrics = analysis_data.get("metrics", {})
        trades = analysis_data.get("trades", [])

        # Build context from available data
        context = f"策略: {analysis_strategy}\n"
        context += f"总收益: {metrics.get('total_return_pct', 0):+.2f}%\n"
        context += f"Sharpe: {metrics.get('sharpe', 0):.2f}\n"
        context += f"胜率: {metrics.get('win_rate', 0):.1f}%\n"
        context += f"交易次数: {len(trades)}\n"
        context += f"问题: {question}\n"

        st.info("💡 **本地分析模式** (如需 DeepSeek AI 分析，请在配置中设置 API Key)")

        # Generate simple local response
        response_parts = []
        if "震荡" in question:
            if metrics.get('win_rate', 0) > 50:
                response_parts.append("该策略胜率较高，可能对震荡有一定适应性。")
            else:
                response_parts.append("胜率偏低，震荡市中可能会产生较多小额亏损。")
        if "参数" in question.lower() or "优化" in question:
            response_parts.append("建议运行参数扫描 (Walk-Forward 页面) 来找到最佳参数组合。")
        if "风险" in question:
            response_parts.append(f"当前最大回撤 {metrics.get('max_drawdown_pct', 0):.1f}%，"
                                 f"Sharpe {metrics.get('sharpe', 0):.2f}。"
                                 f"可在 Risk 页面调整风控参数。")

        if response_parts:
            st.markdown("### 分析结果")
            for p in response_parts:
                st.markdown(f"- {p}")
        else:
            st.markdown("可在 Backtest 或 Walk-Forward 页面运行详细分析后查看具体建议。")
    else:
        st.warning("请先运行回测分析")


# ============ Audit Rules Display ============
st.divider()
st.subheader("🔒 审计规则")

audit_tabs = st.tabs(["✅ 允许的操作", "❌ 禁止的操作", "⚠️ 需审批的操作"])

with audit_tabs[0]:
    st.markdown("Agent 可以自动执行以下操作:")
    for action in [
        "解释策略逻辑和信号",
        "总结回测报告",
        "检测过拟合迹象",
        "建议参数优化范围",
        "检查风控配置合理性",
        "生成交易日志摘要",
    ]:
        st.markdown(f"- ✅ {action}")

with audit_tabs[1]:
    st.markdown("Agent 在任何情况下都不可以:")
    for action in [
        "直接下单或修改订单",
        "绕过风控检查",
        "修改 API Key 或交易配置",
        "自动增加仓位或杠杆",
        "关闭风控保护",
        "自动部署到实盘",
    ]:
        st.markdown(f"- ❌ {action}")

with audit_tabs[2]:
    st.markdown("以下操作需要人工审批:")
    for action in [
        "修改策略参数",
        "切换交易模式 (如 回测→实盘)",
        "修改风控阈值",
        "启用新的交易对",
    ]:
        st.markdown(f"- ⚠️ {action}")

st.info("信号链路: 策略生成信号 → 风控审核 → 执行器下单 → Agent 分析与审计 → 日志记录")
