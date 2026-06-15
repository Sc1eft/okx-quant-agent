"""
AI 交易执行引擎

加载 StrategyInterpreter 解析出的结构化规则 JSON，
在实时 K 线数据流上逐根执行，通过 PaperAccount 模拟交易。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from config import Config
from execution.paper import PaperAccount, PaperEngine
from risk.rules import RiskEngine

logger = logging.getLogger("execution.ai")

# ── 指标计算（纯 pandas） ──


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI 指标"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=1).mean()


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
    ema_fast = _calc_ema(series, fast)
    ema_slow = _calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def _calc_bollinger(series: pd.Series, period: int = 20, std: float = 2.0) -> Dict[str, pd.Series]:
    sma = _calc_sma(series, period)
    std_dev = series.rolling(window=period, min_periods=1).std()
    return {
        "middle": sma,
        "upper": sma + std * std_dev,
        "lower": sma - std * std_dev,
    }


def _calc_price_change(series: pd.Series, period: int = 1) -> pd.Series:
    return series.pct_change(period) * 100


# ── 条件引擎 ──


def _evaluate_condition(cond: dict, indicators: dict) -> bool:
    """评估单个条件是否成立

    支持多种比较方式，包括 fixed-value 和 cross_with 两种模式。
    """
    indicator = cond.get("indicator", "")
    comparison = cond.get("comparison", "greater_than")
    value = cond.get("value")
    cross_with = cond.get("cross_with", "")

    # 获取主指标值
    series = indicators.get(indicator)
    if series is None:
        logger.warning(f"指标 {indicator} 未计算")
        return False

    current = series.iloc[-1] if not series.empty else None
    if current is None or pd.isna(current):
        return False

    # ── greater_than / 大于 ──
    if comparison in ("greater_than", ">"):
        if value is not None:
            return current > value
        elif cross_with:
            other_series = indicators.get(cross_with)
            if other_series is None or other_series.empty:
                return False
            other_val = other_series.iloc[-1]
            if pd.isna(other_val):
                return False
            return current > other_val
        return False

    # ── greater_or_equal / 大于等于 ──
    if comparison in ("greater_or_equal", ">=", "≥"):
        if value is not None:
            return current >= value
        elif cross_with:
            other_series = indicators.get(cross_with)
            if other_series is None or other_series.empty:
                return False
            other_val = other_series.iloc[-1]
            if pd.isna(other_val):
                return False
            return current >= other_val
        return False

    # ── less_than / 小于 ──
    if comparison in ("less_than", "<"):
        if value is not None:
            return current < value
        elif cross_with:
            other_series = indicators.get(cross_with)
            if other_series is None or other_series.empty:
                return False
            other_val = other_series.iloc[-1]
            if pd.isna(other_val):
                return False
            return current < other_val
        return False

    # ── less_or_equal / 小于等于 ──
    if comparison in ("less_or_equal", "<=", "≤"):
        if value is not None:
            return current <= value
        elif cross_with:
            other_series = indicators.get(cross_with)
            if other_series is None or other_series.empty:
                return False
            other_val = other_series.iloc[-1]
            if pd.isna(other_val):
                return False
            return current <= other_val
        return False

    # ── crosses_above / 上穿 ──
    if comparison == "crosses_above":
        if len(series) < 2:
            return False
        if value is not None:
            # 交叉固定值
            return series.iloc[-2] <= value and current > value
        else:
            # 交叉另一个指标
            other = indicators.get(cross_with)
            if other is None or len(other) < 2:
                return False
            prev_other = other.iloc[-2]
            cur_other = other.iloc[-1]
            return series.iloc[-2] <= prev_other and current > cur_other

    # ── crosses_below / 下穿 ──
    if comparison == "crosses_below":
        if len(series) < 2:
            return False
        if value is not None:
            return series.iloc[-2] >= value and current < value
        else:
            other = indicators.get(cross_with)
            if other is None or len(other) < 2:
                return False
            prev_other = other.iloc[-2]
            cur_other = other.iloc[-1]
            return series.iloc[-2] >= prev_other and current < cur_other

    # ── consecutive_gain / 连续N根上涨 ──
    if comparison == "consecutive_gain":
        n = int(value) if value else 3
        if len(series) < n + 1:
            return False
        for i in range(-n, 0):
            # 每根 close 都比前一根高
            if series.iloc[i - 1] >= series.iloc[i]:
                return False
        return True

    # ── consecutive_loss / 连续N根下跌 ──
    if comparison == "consecutive_loss":
        n = int(value) if value else 3
        if len(series) < n + 1:
            return False
        for i in range(-n, 0):
            if series.iloc[i - 1] <= series.iloc[i]:
                return False
        return True

    # ── touches / 触及（近似等于，误差范围内） ──
    if comparison == "touches":
        if value is not None:
            eps = max(abs(value) * 0.001, 0.01)
            return abs(current - value) <= eps
        elif cross_with:
            other_series = indicators.get(cross_with)
            if other_series is None or other_series.empty:
                return False
            other_val = other_series.iloc[-1]
            if pd.isna(other_val):
                return False
            eps = max(abs(other_val) * 0.001, 0.01)
            return abs(current - other_val) <= eps
        return False

    return False


def _calc_indicators(df: pd.DataFrame) -> dict:
    """对 DataFrame 计算所有常见指标，返回 {indicator_name: pd.Series}"""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    ind: Dict[str, pd.Series] = {
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
    }

    # RSI 多个周期
    for p in [6, 14, 20]:
        if len(close) >= p:
            ind[f"rsi_{p}"] = _calc_rsi(close, p)

    # SMA 多个周期
    for p in [5, 10, 20, 50, 200]:
        if len(close) >= p:
            ind[f"sma_{p}"] = _calc_sma(close, p)
            ind[f"ema_{p}"] = _calc_ema(close, p)

    # MACD
    if len(close) >= 26:
        macd = _calc_macd(close)
        ind["macd"] = macd["macd"]
        ind["macd_signal"] = macd["signal"]
        ind["macd_histogram"] = macd["histogram"]

    # 布林带
    for p in [20]:
        if len(close) >= p:
            bb = _calc_bollinger(close, p)
            ind["bb_middle"] = bb["middle"]
            ind["bb_upper"] = bb["upper"]
            ind["bb_lower"] = bb["lower"]

    # 价格变动百分比
    ind["price_change_pct"] = _calc_price_change(close, 1)
    ind["price_change_5"] = _calc_price_change(close, 5)

    # ── K线实体波动率（用于波动率触发策略） ──
    open_series = df["open"]
    body_size = (close - open_series).abs()
    ind["body_size"] = body_size
    ind["body_sum_2"] = body_size.rolling(window=2, min_periods=1).sum()
    # body_direction: 1=阴线(close<open, 做多), -1=阳线(close>open, 做空), 0=平盘
    direction = pd.Series(0, index=close.index)
    direction[close < open_series] = 1
    direction[close > open_series] = -1
    ind["body_direction"] = direction

    return ind


def _resolve_indicator_series(indicator_name: str, indicators: dict) -> Optional[pd.Series]:
    """将条件中的 indicator 名映射到实际计算出的 Series"""
    # 直接匹配（优先）
    if indicator_name in indicators:
        return indicators[indicator_name]

    # 带默认参数的简写
    if indicator_name == "rsi":
        return indicators.get("rsi_14")
    elif indicator_name == "sma":
        return indicators.get("sma_20")
    elif indicator_name == "ema":
        return indicators.get("ema_20")
    elif indicator_name == "bb_upper":
        return indicators.get("bb_upper")
    elif indicator_name == "bb_lower":
        return indicators.get("bb_lower")
    elif indicator_name == "bb_middle":
        return indicators.get("bb_middle")
    elif indicator_name == "macd_histogram":
        return indicators.get("macd_histogram")
    elif indicator_name == "macd_signal":
        return indicators.get("macd_signal")

    return None


def _check_conditions(conditions: List[dict], indicators: dict) -> List[dict]:
    """检查一组条件，返回触发的条件列表"""
    triggered = []
    for cond in conditions:
        ind_name = cond.get("indicator", "")
        series = _resolve_indicator_series(ind_name, indicators)
        if series is None:
            logger.warning(f"无法解析指标: {ind_name}")
            continue
        ind_map = {}
        for k, v in indicators.items():
            ind_map[k] = v
        # 注册 cross_with 所需的指标
        cross_with = cond.get("cross_with", "")
        if cross_with:
            cw_series = _resolve_indicator_series(cross_with, indicators)
            if cw_series is not None:
                ind_map[cross_with] = cw_series
        ind_map[ind_name] = series

        if _evaluate_condition(cond, ind_map):
            triggered.append(cond)

    return triggered


# ── 主执行器 ──


class AIStrategyExecutor:
    """AI 交易执行器 — 加载规则 JSON，逐根 K 线执行"""

    def __init__(
        self,
        rules: dict,
        cfg: Config,
        initial_balance: float = 10000.0,
        mode: str = "paper",
    ):
        self.rules = rules
        self.cfg = cfg
        self.mode = mode  # "paper" or "live"

        # 模拟账户
        self.account = PaperAccount(initial_balance=initial_balance)

        # 风控
        self.risk_engine = RiskEngine(cfg.risk)

        # 滚动 K 线缓冲区
        self.bar_buffer: Optional[pd.DataFrame] = None
        self.MAX_BUFFER = 300

        # ── 持仓状态 ──
        self.in_position = False
        self.position_side = ""  # "long" or "short"
        self.entry_price = 0.0
        self.entry_time: Optional[str] = None
        self.highest_since_entry = 0.0  # 多头最高价
        self.lowest_since_entry = 0.0   # 空头最低价
        self.bars_since_entry = 0

        # ── 多级移动止盈状态 ──
        self.multi_tp_level = 0        # 0=未激活, 1=保本, 2=+1.25%, 3=平50%+2.5%
        self.dynamic_stop_price = 0.0  # 当前动态止损价
        self.partial_close_done = False

        # ── 冷却追踪 ──
        self.current_bar_index = 0           # 单调递增K线计数器
        self.last_long_bar = -999            # 上次开多头时的 bar_index
        self.last_short_bar = -999           # 上次开空头时的 bar_index
        self._prev_trade_loss = False        # 上一笔是否亏损

        # 运行时统计
        self.total_signals = 0
        self.total_trades = 0
        self.start_time = datetime.now(timezone.utc).isoformat()

        # 最近状态（给前端用）
        self.last_signal = "hold"
        self.last_signal_reason = ""
        self.last_check_time: Optional[str] = None

        # ── AI 信号策略状态 ──
        self.ai_signal_consumed = False      # 信号是否已执行（一次信号一次交易）
        self.ai_signal_skip_entry = False    # 预热阶段跳过入场

    # ── 公开接口 ──

    def on_bar(self, bar: pd.Series) -> dict:
        """处理一根新 K 线，返回状态 dict"""
        self._append_bar(bar)
        indicators = self._get_indicators()

        signal = "hold"
        signal_reason = ""
        strategy_type = self.rules.get("_strategy_type", "")

        # 检查退出条件（持仓时优先）
        if self.in_position:
            # 硬性止盈/止损/超时
            exit_reason = self._check_hard_stops(float(bar["close"]))
            if not exit_reason and strategy_type == "volatility_contrarian":
                # 多级移动止盈
                exit_reason = self._check_multi_trailing_stop(float(bar["close"]))

            if exit_reason:
                self._execute_exit(float(bar["close"]), reason=exit_reason)
                signal = "sell"
                signal_reason = exit_reason
            else:
                # 条件退出（通用条件引擎，非波动率策略使用）
                exit_conditions = self.rules.get("exit_conditions", [])
                if exit_conditions and strategy_type != "volatility_contrarian":
                    triggered = _check_conditions(exit_conditions, indicators)
                    if triggered:
                        condition_logic = self.rules.get("_condition_logic", "any")
                        conditions_met = (
                            len(triggered) == len(exit_conditions)
                            if condition_logic == "all"
                            else len(triggered) > 0
                        )
                        if conditions_met:
                            reason = " + ".join(
                                t.get("indicator", "") for t in triggered
                            ) + " 条件触发"
                            self._execute_exit(float(bar["close"]), reason=reason)
                            signal = "sell"
                            signal_reason = reason

        # 检查入场条件（空仓时）
        if not self.in_position:
            if strategy_type == "volatility_contrarian":
                # ═══ 波动率反向策略专用逻辑 ═══
                direction = self._check_volatility_contrarian(bar)
                if direction:
                    cooldown_ok, cd_reason = self._check_cooldown(direction)
                    if cooldown_ok:
                        risk_ok, risk_reason = self.risk_engine.check_signal(
                            "buy",
                            current_equity=self.account.equity,
                            current_position_pct=0.0,
                        )
                        if risk_ok:
                            dir_label = "多头" if direction == "long" else "空头"
                            reason = f"波动率触发反向开{dir_label}"
                            self._execute_entry(float(bar["close"]), direction=direction, reason=reason)
                            signal = "buy" if direction == "long" else "short"
                            signal_reason = reason
                        else:
                            signal = "blocked"
                            signal_reason = f"风控拒绝: {risk_reason}"
                    else:
                        signal = "blocked"
                        signal_reason = cd_reason
            elif strategy_type == "ai_signal" and not self.ai_signal_consumed and not self.ai_signal_skip_entry:
                # ═══ AI 信号策略：一次信号只做一次交易 ═══
                ai_signal = self.rules.get("ai_signal", {})
                direction = ai_signal.get("original_direction", "")
                if direction in ("long", "short"):
                    dir_label = "多头" if direction == "long" else "空头"
                    reason = f"AI信号开{dir_label}"
                    self._execute_entry(float(bar["close"]), direction=direction, reason=reason)
                    self.ai_signal_consumed = True
                    signal = "buy" if direction == "long" else "short"
                    signal_reason = reason
            else:
                # ═══ 通用条件引擎入场 ═══
                entry_conditions = self.rules.get("entry_conditions", [])
                if entry_conditions:
                    triggered = _check_conditions(entry_conditions, indicators)
                    if triggered:
                        condition_logic = self.rules.get("_condition_logic", "any")
                        conditions_met = (
                            len(triggered) == len(entry_conditions)
                            if condition_logic == "all"
                            else len(triggered) > 0
                        )
                        if conditions_met:
                            risk_ok, risk_reason = self.risk_engine.check_signal(
                                "buy",
                                current_equity=self.account.equity,
                                current_position_pct=0.0,
                            )
                            if risk_ok:
                                reason = " + ".join(
                                    t.get("indicator", "") for t in triggered
                                ) + " 条件触发"
                                self._execute_entry(float(bar["close"]), reason=reason)
                                signal = "buy"
                                signal_reason = reason
                            else:
                                signal = "blocked"
                                signal_reason = f"风控拒绝: {risk_reason}"

        self.last_signal = signal
        self.last_signal_reason = signal_reason
        self.last_check_time = datetime.now(timezone.utc).isoformat()

        return self.get_state()

    def get_state(self) -> dict:
        """返回当前完整状态（供前端渲染）"""
        account_dict = self.account.to_dict()
        account_dict["in_position"] = self.in_position
        account_dict["position_side"] = self.position_side
        account_dict["entry_price"] = self.entry_price
        account_dict["entry_time"] = self.entry_time or ""
        account_dict["bars_since_entry"] = self.bars_since_entry
        account_dict["multi_tp_level"] = self.multi_tp_level
        account_dict["dynamic_stop_price"] = self.dynamic_stop_price
        account_dict["partial_close_done"] = self.partial_close_done

        # 冷却信息
        remaining = 0
        if self.position_side == "long" and self.last_long_bar > 0:
            remaining = max(0, 8 - (self.current_bar_index - self.last_long_bar))
        elif self.position_side == "short" and self.last_short_bar > 0:
            remaining = max(0, 8 - (self.current_bar_index - self.last_short_bar))
        account_dict["cooldown_remaining"] = remaining
        account_dict["prev_trade_loss"] = self._prev_trade_loss

        return {
            "running": True,
            "mode": self.mode,
            "signal": self.last_signal,
            "signal_reason": self.last_signal_reason,
            "last_check": self.last_check_time or "",
            "in_position": self.in_position,
            "position_side": self.position_side,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time or "",
            "bars_since_entry": self.bars_since_entry,
            "multi_tp_level": self.multi_tp_level,
            "dynamic_stop_price": self.dynamic_stop_price,
            "partial_close_done": self.partial_close_done,
            "cooldown_remaining": remaining,
            "prev_trade_loss": self._prev_trade_loss,
            "account": account_dict,
            "total_signals": self.total_signals,
            "total_trades": len(self.account.trades),
            "rules": self.rules,
            "buffer_size": len(self.bar_buffer) if self.bar_buffer is not None else 0,
            "strategy_name": self.rules.get("strategy_name", "AI策略"),
            "ai_signal_consumed": self.ai_signal_consumed,
        }

    def reset(self, initial_balance: Optional[float] = None):
        """重置执行器状态"""
        bal = initial_balance if initial_balance is not None else self.account.initial_balance
        self.account = PaperAccount(initial_balance=bal)
        self.bar_buffer = None
        self.in_position = False
        self.position_side = ""
        self.entry_price = 0.0
        self.entry_time = None
        self.highest_since_entry = 0.0
        self.lowest_since_entry = 0.0
        self.bars_since_entry = 0
        self.multi_tp_level = 0
        self.dynamic_stop_price = 0.0
        self.partial_close_done = False
        self.current_bar_index = 0
        self.last_long_bar = -999
        self.last_short_bar = -999
        self._prev_trade_loss = False
        self.total_signals = 0
        self.last_signal = "hold"
        self.last_signal_reason = ""
        self.last_check_time = None
        self.ai_signal_consumed = False
        self.ai_signal_skip_entry = False

    # ── 内部：K线管理 ──

    def _append_bar(self, bar: pd.Series):
        """追加 K 线到滚动缓冲区"""
        if self.bar_buffer is None:
            self.bar_buffer = bar.to_frame().T
        else:
            self.bar_buffer = pd.concat([self.bar_buffer, bar.to_frame().T])
            self.bar_buffer = self.bar_buffer.tail(self.MAX_BUFFER)

        self.current_bar_index += 1

        if self.in_position:
            self.bars_since_entry += 1

    def _get_indicators(self) -> dict:
        """从缓冲区计算指标"""
        if self.bar_buffer is None or self.bar_buffer.empty:
            return {}
        return _calc_indicators(self.bar_buffer)

    # ── 内部：方向感知盈亏 ──

    def _position_pnl_pct(self, current_price: float) -> float:
        """计算当前盈亏百分比（正=盈利，负=亏损），方向感知"""
        if self.position_side == "long" and self.entry_price > 0:
            return (current_price - self.entry_price) / self.entry_price * 100
        elif self.position_side == "short" and self.entry_price > 0:
            return (self.entry_price - current_price) / self.entry_price * 100
        return 0.0

    # ── 内部：硬性止损止盈（方向感知） ──

    def _check_hard_stops(self, current_price: float) -> Optional[str]:
        """检查硬性止盈止损（方向感知）"""
        if not self.in_position or self.entry_price <= 0:
            return None

        risk_params = self.rules.get("risk_params", {})

        # 更新极值（移动止盈用）
        self.highest_since_entry = max(self.highest_since_entry, current_price)
        if self.position_side == "short":
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)

        pnl_pct = self._position_pnl_pct(current_price)

        # 止损
        sl_pct = risk_params.get("stop_loss_pct")
        if sl_pct and sl_pct > 0:
            if pnl_pct <= -sl_pct:
                return f"止损触发 (-{sl_pct}%)"

        # 止盈（简单固定止盈，不使用多级时）
        tp_pct = risk_params.get("take_profit_pct")
        if tp_pct and tp_pct > 0:
            if pnl_pct >= tp_pct:
                return f"止盈触发 (+{tp_pct}%)"

        # 超时退出（方向无关）
        timeout_bars = risk_params.get("position_timeout_bars", 0)
        if timeout_bars and timeout_bars > 0 and self.bars_since_entry >= timeout_bars:
            return f"持仓超时 ({timeout_bars} 根K线)"

        return None

    # ── 内部：波动率触发检测 ──

    def _check_volatility_contrarian(self, bar: pd.Series) -> Optional[str]:
        """检查波动率触发条件，返回开仓方向 ("long"/"short") 或 None"""
        risk_params = self.rules.get("risk_params", {})
        body_threshold = risk_params.get("volatility_body_threshold", 15.0)
        sum_threshold = risk_params.get("volatility_sum_threshold", 20.0)

        close_price = float(bar["close"])
        open_price = float(bar["open"])
        body = abs(close_price - open_price)
        two_bar_sum = body

        # 有上一根K线时计算2根之和
        if self.bar_buffer is not None and len(self.bar_buffer) >= 2:
            prev_bar = self.bar_buffer.iloc[-2]
            prev_body = abs(float(prev_bar["close"]) - float(prev_bar["open"]))
            two_bar_sum = body + prev_body

        # 触发判定：单根 > $15 OR 2根之和 > $20
        triggered = body > body_threshold or two_bar_sum > sum_threshold
        if not triggered:
            return None

        # 方向判定：反向
        if close_price < open_price:
            return "long"   # 阴线→做多
        elif close_price > open_price:
            return "short"  # 阳线→做空
        return None  # 平盘不操作

    # ── 内部：冷却检查 ──

    def _check_cooldown(self, direction: str) -> tuple:
        """检查同方向冷却是否满足，返回 (ok, reason)"""
        risk_params = self.rules.get("risk_params", {})
        cooldown_bars = risk_params.get("cooldown_bars", 8)

        if direction == "long":
            bars_since = self.current_bar_index - self.last_long_bar
            if bars_since < cooldown_bars:
                return False, f"多头冷却中 ({bars_since}/{cooldown_bars}根)"
        elif direction == "short":
            bars_since = self.current_bar_index - self.last_short_bar
            if bars_since < cooldown_bars:
                return False, f"空头冷却中 ({bars_since}/{cooldown_bars}根)"

        return True, ""

    # ── 内部：风险预算仓位计算 ──

    def _get_position_size(self, price: float, direction: str = "long") -> float:
        """风险预算仓位计算

        公式: 名义仓位 = (总资金 × max_loss_pct) / stop_pct
        每笔最大亏损 = 总资金 × max_loss_pct
        其中 max_loss_pct 默认 3%, stop_pct 默认 1.25%
        """
        risk_params = self.rules.get("risk_params", {})
        capital = self.account.equity

        max_loss_pct = risk_params.get("max_loss_pct", 3.0) / 100.0
        stop_pct = risk_params.get("stop_loss_pct", 1.25) / 100.0

        # 核心公式
        max_loss_amount = capital * max_loss_pct
        if stop_pct <= 0:
            position_value = max_loss_amount * 10  # fallback
        else:
            position_value = max_loss_amount / stop_pct

        # 确保不超过余额能承担的范围
        max_affordable = self.account.balance * 10
        position_value = min(position_value, max_affordable)

        if position_value <= 0:
            return 0.0

        size = position_value / price

        # 连亏减半
        if self._prev_trade_loss:
            size *= 0.5
            logger.info(f"前笔亏损，仓位减半: {size:.6f}")

        # 最小交易量检查
        min_size = 0.001
        if size < min_size:
            return 0.0

        return size

    # ── 内部：多级移动止盈 ──

    def _check_multi_trailing_stop(self, current_price: float) -> Optional[str]:
        """多级移动止盈检查

        Level 1: profit≥1.25% → 移动止损到保本价
        Level 2: profit≥2.5%  → 移动止损到+1.25%
        Level 3: profit≥5%    → 平50%仓位，剩余止损到+2.5%
        """
        if not self.in_position or self.entry_price <= 0:
            return None

        profit_pct = self._position_pnl_pct(current_price)

        # 检查动态止损是否被触发
        if self.dynamic_stop_price > 0:
            if self.position_side == "long" and current_price <= self.dynamic_stop_price:
                return f"多级移动止盈触发 (级别{self.multi_tp_level})"
            elif self.position_side == "short" and current_price >= self.dynamic_stop_price:
                return f"多级移动止盈触发 (级别{self.multi_tp_level})"

        # Level 1: profit ≥ 1.25% → 移动止损到保本
        if profit_pct >= 1.25 and self.multi_tp_level < 1:
            self.dynamic_stop_price = self.entry_price
            self.multi_tp_level = 1
            logger.info(f"多级止盈级别1: 移动止损到保本价 {self.dynamic_stop_price:.2f}")

        # Level 2: profit ≥ 2.5% → 移动止损到 +1.25%
        if profit_pct >= 2.5 and self.multi_tp_level < 2:
            if self.position_side == "long":
                self.dynamic_stop_price = self.entry_price * 1.0125
            else:
                self.dynamic_stop_price = self.entry_price * 0.9875
            self.multi_tp_level = 2
            logger.info(f"多级止盈级别2: 止损移动+1.25% ({self.dynamic_stop_price:.2f})")

        # Level 3: profit ≥ 5% → 平50% + 止损移动到+2.5%
        if profit_pct >= 5.0 and self.multi_tp_level < 3 and not self.partial_close_done:
            self._execute_partial_close(current_price)
            self.partial_close_done = True
            if self.position_side == "long":
                self.dynamic_stop_price = self.entry_price * 1.025
            else:
                self.dynamic_stop_price = self.entry_price * 0.975
            self.multi_tp_level = 3
            logger.info(f"多级止盈级别3: 平50%仓位, 止损移动+2.5% ({self.dynamic_stop_price:.2f})")

        return None

    def _execute_partial_close(self, price: float):
        """部分平仓 x% 的当前仓位"""
        fee_rate = self.cfg.trading.taker_fee / 100
        side_label = ""
        if self.position_side == "long" and self.account.position > 0.001:
            size = self.account.position * 0.5
            trade = self.account.execute_sell(price, size=size, fee_rate=fee_rate)
            side_label = f"多头平50% {size:.6f}"
        elif self.position_side == "short" and self.account.short_position > 0.001:
            size = self.account.short_position * 0.5
            trade = self.account.execute_cover(price, size=size, fee_rate=fee_rate)
            side_label = f"空头平50% {size:.6f}"
        else:
            return
        logger.info(f"多级止盈部分平仓: ${price:.2f} {side_label}")

    # ── 内部：入场 / 出场 ──

    def _execute_entry(self, price: float, direction: str = "long", reason: str = ""):
        """执行入场（支持多空）"""
        size = self._get_position_size(price, direction)
        if size <= 0:
            logger.warning(f"仓位计算为0，跳过入场: {reason}")
            return

        fee_rate = self.cfg.trading.taker_fee / 100

        if direction == "long":
            trade = self.account.execute_buy(price, size, fee_rate=fee_rate)
        elif direction == "short":
            trade = self.account.execute_short(price, size, fee_rate=fee_rate)
        else:
            return

        self.in_position = True
        self.position_side = direction
        self.entry_price = price
        self.entry_time = trade.get("time", datetime.now(timezone.utc).isoformat())
        self.highest_since_entry = price
        self.lowest_since_entry = price
        self.bars_since_entry = 0

        # 重置多级止盈状态
        self.multi_tp_level = 0
        self.dynamic_stop_price = 0.0
        self.partial_close_done = False

        # 更新冷却
        if direction == "long":
            self.last_long_bar = self.current_bar_index
        elif direction == "short":
            self.last_short_bar = self.current_bar_index

        self.total_signals += 1
        self.total_trades += 1
        self.risk_engine.record_trade_result(0)

        dir_label = "做多" if direction == "long" else "做空"
        logger.info(f"🤖 AI {dir_label}: ${price:,.2f} x {size:.6f} | {reason}")

    def _execute_exit(self, price: float, reason: str = ""):
        """执行出场（根据当前仓位方向）"""
        fee_rate = self.cfg.trading.taker_fee / 100

        if self.position_side == "long":
            if self.account.position <= 0.001:
                self.in_position = False
                return
            trade = self.account.execute_sell(price, fee_rate=fee_rate)
        elif self.position_side == "short":
            if self.account.short_position <= 0.001:
                self.in_position = False
                return
            trade = self.account.execute_cover(price, fee_rate=fee_rate)
        else:
            self.in_position = False
            return

        self.in_position = False
        self.total_signals += 1
        self.total_trades += 1

        # 风控记录
        if trade and "pnl" in trade:
            pnl = trade.get("pnl", 0)
            self._prev_trade_loss = pnl < 0
            # 用 entry_price 估算 pnl%
            cost_basis = self.entry_price * trade.get("size", 1)
            pnl_pct = pnl / max(cost_basis, 1) * 100
            self.risk_engine.record_trade_result(pnl_pct)

        logger.info(f"🤖 AI 平仓: ${price:,.2f} | PnL: ${trade.get('pnl', 0):,.2f} | {reason}")
