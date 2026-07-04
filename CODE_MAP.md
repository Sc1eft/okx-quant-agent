# 项目代码映射
本文件由 `scripts/generate_code_map.py` 自动生成。
用途：快速定位代码位置，避免读取整个源文件。
> 生成时间: 2026-07-05 02:29
---
## 📁 ./
### 📄 agent\__init__.py (4行)
  - 导入: `agent.report_analyzer`,`agent.strategy_interpreter`

### 📄 agent\audit.py (56行) — Agent 审计模块 — 确保 Agent 不越界
  - 导入: `__future__`
  - ⚡ `audit_agent_action()` L42 — 🔓公开 审计 Agent 操作是否在允许范围内

### 📄 agent\report_analyzer.py (170行) — 🔧 P3: DeepSeek Agent — 回测报告分析
  - 导入: `__future__`,`config`,`json`,`logging`,`openai`,`pandas`
  - 🏛️ `ReportAnalyzer` L29 — DeepSeek Agent — 回测分析
    - └ `__init__()` L35 — 🔒内部
    - └ `_init_client()` L39 — 🔐内部 延迟初始化 API 客户端
    - └ `analyze_backtest()` L53 — 🔓公开 分析回测结果
    - └ `analyze_overfitting()` L97 — 🔓公开 分析 Walk-forward 结果中的过拟合风险
    - └ `_local_analysis()` L115 — 🔐内部 本地离线分析（不依赖 DeepSeek API）
    - └ `_local_overfitting_analysis()` L154 — 🔐内部

### 📄 agent\signal_bridge.py (67行) — SignalBridge — 将 AI 多空分析结果转换为交易执行器规则
  - ⚡ `ai_signal_to_rules()` L15 — 🔓公开 将 AI 分析结果转换为 executor rules

### 📄 agent\strategy_interpreter.py (1157行) — 自然语言 → 结构化交易规则
  - 导入: `__future__`,`config`,`json`,`logging`,`openai`,`re`
  - 🏛️ `StrategyInterpreter` L1075 — 自然语言 → 交易规则 JSON
    - └ `__init__()` L1078 — 🔒内部
    - └ `_init_client()` L1082 — 🔐内部
    - └ `interpret()` L1095 — 🔓公开
    - └ `_call_api()` L1114 — 🔐内部
  - ⚡ `_extract_timeframe()` L27 — 🔐内部 从描述中提取建议的 K 线周期
  - ⚡ `_extract_risk_params()` L57 — 🔐内部 从文本中提取止损/止盈/仓位/移动止损等参数
  - ⚡ `_int_groups()` L186 — 🔐内部 从匹配组中提取所有非空的整数值
  - ⚡ `_build_ma_golden()` L191 — 🔐内部 MA金叉买入
  - ⚡ `_build_ma_death()` L205 — 🔐内部 MA死叉卖出
  - ⚡ `_build_ma_trend()` L219 — 🔐内部 双均线趋势跟踪
  - ⚡ `_build_price_up_ma()` L240 — 🔐内部 价格上穿均线买入
  - ⚡ `_build_price_dn_ma()` L253 — 🔐内部 价格下穿均线卖出
  - ⚡ `_build_ema_golden()` L266 — 🔐内部 EMA金叉买入
  - ⚡ `_build_ema_death()` L278 — 🔐内部 EMA死叉卖出
  - ⚡ `_build_ma_rsi_filter()` L290 — 🔐内部 MA金叉 + RSI>XX 组合
  - ⚡ `_build_volatility_contrarian()` L310 — 🔐内部 波动率触发反向策略
  - ⚡ `_parse_locally()` L939 — 🔐内部 关键词匹配降级解析 — 遍历 _STRATEGY_PATTERNS 返回第一个匹配
  - ⚡ `_try_generic_extract()` L972 — 🔐内部 兜底：从文字中提取任何可识别的交易条件

### 📄 agents\__init__.py (1行) — Three-Agent AI Trading System

### 📄 agents\agent1_technical.py (262行) — Agent 1 — 实时技术分析师
  - 导入: `__future__`,`agents.change_detector`,`agents.config`,`agents.event_bus`,`agents.kline_builder`,`agents.okx_ws`,`asyncio`,`collections`
  - 🏛️ `Agent1` L41 — Agent 1 — 技术分析师
    - └ `__init__()` L44 — 🔒内部
    - └async `run()` L78 — 🔓公开 启动 Agent 1 主循环
    - └async `stop()` L87 — 🔓公开 停止 Agent 1
    - └ `_on_tick()` L96 — 🔐内部 处理 WebSocket ticker 消息
    - └ `_on_bar()` L111 — 🔐内部 处理新完成的 K 线
    - └ `_indicator_summary()` L192 — 🔐内部 生成一行指标摘要（供 current_activity 使用）
    - └ `get_status()` L217 — 🔓公开 返回当前状态（供监控用）
    - └ `get_recent_signal_stats()` L233 — 🔓公开 返回近期信号统计数据（供 Agent 4 复盘使用）

### 📄 agents\agent2_news.py (218行) — Agent 2 — 信息收集员（新闻 + 链上数据）
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`agents.onchain_collector`,`asyncio`,`datetime`,`frontend.utils.eth_news`,`logging`
  - 🏛️ `Agent2` L82 — Agent 2 — 信息收集员（新闻 + 链上数据）
    - └ `__init__()` L85 — 🔒内部
    - └async `run()` L112 — 🔓公开 启动 Agent 2 主循环 — 新闻 + 链上数据并发运行
    - └async `stop()` L126 — 🔓公开 停止 Agent 2
    - └async `_news_loop()` L133 — 🔐内部 新闻抓取主循环
    - └async `_fetch_and_score()` L151 — 🔐内部 抓取新闻 → 评分 → 推送
    - └ `get_status()` L200 — 🔓公开 读取
    - └ `get_recent_news()` L216 — 🔓公开 返回最近 N 条新闻（供 Agent 4 复盘使用）
  - ⚡ `_score_news_item()` L64 — 🔐内部 对一条新闻进行影响权重评分，返回 0~1 的分数

### 📄 agents\agent3_trader.py (633行) — Agent 3 — 资深交易员
  - 导入: `__future__`,`agents.confidence_scorer`,`agents.config`,`agents.deepseek_caller`,`agents.event_bus`,`agents.risk_layer`,`agents.signal_aligner`,`agents.trade_executor`
  - 🏛️ `Agent3` L49 — Agent 3 — 交易决策与执行
    - └ `__init__()` L52 — 🔒内部
    - └async `run()` L120 — 🔓公开 启动 Agent 3 主循环
    - └async `stop()` L140 — 🔓公开 停止
    - └async `_consume_a()` L144 — 🔐内部 消费 Queue A（技术面事件）
    - └async `_consume_b()` L172 — 🔐内部 消费 Queue B（新闻/基本面事件）
    - └async `_on_event()` L193 — 🔐内部 收到新事件后的处理
    - └async `_maybe_debounce()` L208 — 🔐内部 检查是否需要触发决策（攒批/超时）
    - └async `_make_decision()` L225 — 🔐内部 执行一次完整的交易决策周期
    - └ `_build_context()` L376 — 🔐内部 从事件列表构建 DeepSeek 上下文
    - └ `_calc_pnl_pct()` L472 — 🔐内部 根据当前仓位计算浮盈/浮亏百分比
    - └ `_suggested_size()` L484 — 🔐内部 根据上下文和风控建议仓位大小 (Phase 4: 信号对齐调节)
    - └async `_refresh_current_price()` L503 — 🔐内部 后台循环：定期从 OKX API 获取最新价格并缓存
    - └async `_review_scheduler()` L520 — 🔐内部 定时检查并生成复盘报告 + 推送微信
    - └ `_push_report_if_needed()` L564 — 🔐内部 如果配置了推送且未推送，推送报告到微信
    - └ `_rewrite_report_file()` L580 — 🔐内部 更新报告文件的 pushed 标记
    - └ `update_position()` L593 — 🔓公开 更新当前持仓（供外部或 main.py 调用）
    - └ `_on_position_closed()` L601 — 🔐内部 仓位监控器平仓后的回调 — 重置 Agent 3 的仓位状态
    - └ `get_status()` L613 — 🔓公开 读取
  - ⚡ `_safe_float()` L39 — 🔐内部 安全地将值转换为 float，不可转换时返回 default

### 📄 agents\agent4_reviewer.py (494行) — Agent 4 — 复盘改进 Agent
  - 导入: `__future__`,`agents.agent1_technical`,`agents.agent2_news`,`agents.config`,`agents.deepseek_caller`,`agents.kline_builder`,`asyncio`,`datetime`
  - 🏛️ `Agent4Reviewer` L71 — 复盘改进 Agent
    - └ `__init__()` L77 — 🔒内部
    - └async `notify_trade()` L116 — 🔓公开 Agent 3 完成一笔交易后调用，触发计数检查
    - └async `run()` L134 — 🔓公开 主循环（空循环，保持与 asyncio 任务体系兼容）
    - └async `stop()` L147 — 🔓公开 停止 Agent 4
    - └async `_run_review()` L153 — 🔐内部 执行一次完整复盘
    - └ `_load_recent_trades()` L246 — 🔐内部 从 SQLite 加载最近 N 笔已完成交易
    - └ `_collect_market_context()` L261 — 🔐内部 采集 KlineBuilder 行情数据（3m/5m/15m/1h/1d 最新 K 线）
    - └ `_collect_signal_stats()` L276 — 🔐内部 采集 Agent 1 信号统计
    - └ `_collect_recent_news()` L285 — 🔐内部 采集最近新闻
    - └ `_collect_onchain_snapshot()` L294 — 🔐内部 采集链上数据快照
    - └ `_build_review_prompt()` L312 — 🔐内部 构建完整的 DeepSeek 复盘 Prompt
    - └ `_validate_adjustment()` L405 — 🔐内部 边界校验 + 防抖
    - └ `_apply_adjustment()` L466 — 🔐内部 写入共享 config（锁保护）
    - └ `get_status()` L481 — 🔓公开 读取

### 📄 agents\change_detector.py (200行) — 信号变化检测器
  - 导入: `__future__`,`logging`
  - 🏛️ `ChangeDetector` L21 — 变化检测器
    - └ `__init__()` L36 — 🔒内部
    - └ `set_cooldown()` L44 — 🔓公开 设置某类型信号的冷却时间
    - └ `check()` L48 — 🔓公开 检查指标变化，返回信号列表
    - └ `_check_macd()` L77 — 🔐内部 检查
    - └ `_check_kdj()` L112 — 🔐内部 检查
    - └ `_check_boll()` L145 — 🔐内部 检查
    - └ `_save_state()` L174 — 🔐内部
    - └ `_can_push()` L181 — 🔐内部 检查某信号的冷却时间是否已过
    - └ `_signal()` L191 — 🔐内部

### 📄 agents\confidence_scorer.py (115行) — 多周期信心分 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`logging`
  - 🏛️ `ConfidenceScorer` L22 — 多周期信心分计算器
    - └ `__init__()` L30 — 🔒内部
    - └ `compute()` L34 — 🔓公开 计算综合信心分

### 📄 agents\config.py (145行) — Agent 系统配置 — 三 Agent 的独立参数
  - 导入: `__future__`
  - 🏛️ `AgentSystemConfig` L12 — 三 Agent 系统配置（与根 config.py 互补）

### 📄 agents\deepseek_caller.py (390行) — DeepSeek 交易决策调用器
  - 导入: `__future__`,`json`,`logging`,`openai`,`re`
  - 🏛️ `DeepSeekTrader` L65 — DeepSeek 交易决策器
    - └ `__init__()` L73 — 🔒内部
    - └ `analyze()` L99 — 🔓公开 调用 DeepSeek 分析，返回交易决策
    - └ `analyze_trade_report()` L216 — 🔓公开 分析一段周期内的交易盈亏模式，识别盈利规律和亏损原因。
    - └ `analyze_review()` L290 — 🔓公开 用 DeepSeek 分析复盘数据（Agent 4 专用）
    - └ `_parse_json_response()` L318 — 🔐内部 从 DeepSeek 响应中提取 JSON（通用方法）
    - └ `_parse_response()` L334 — 🔐内部 解析 DeepSeek 返回的 JSON
    - └ `_fallback_decision()` L370 — 🔐内部 API 失败时的降级决策——不做任何交易
    - └ `get_stats()` L385 — 🔓公开 读取

### 📄 agents\event_bus.py (91行) — 事件总线 — asyncio.Queue 封装 + 标准化事件格式
  - 导入: `__future__`,`asyncio`,`datetime`
  - 🏛️ `AgentEventType` L17 — Agent 事件类型
  - 🏛️ `AgentEvent` L27 — 标准化 Agent 事件
    - └ `to_dict()` L44 — 🔓公开
  - 🏛️ `EventBus` L55 — 事件总线 — 管理两条 asyncio.Queue
    - └ `__init__()` L58 — 🔒内部
    - └async `publish_a()` L62 — 🔓公开 向 Queue A (技术面) 发布事件
    - └async `publish_b()` L71 — 🔓公开 向 Queue B (基本面) 发布事件
    - └async `consume_a()` L79 — 🔓公开 消费 Queue A (阻塞)
    - └async `consume_b()` L83 — 🔓公开 消费 Queue B (阻塞)
    - └ `qsize_a()` L87 — 🔓公开
    - └ `qsize_b()` L90 — 🔓公开

### 📄 agents\kline_builder.py (202行) — K 线构建器 — WebSocket tick → 1秒 K线 → 聚合到标准周期
  - 导入: `__future__`,`collections`,`datetime`,`logging`
  - 🏛️ `KlineBuilder` L19 — K 线构建器
    - └ `__init__()` L41 — 🔒内部
    - └ `add_tick()` L60 — 🔓公开 添加一个 tick 数据（每秒最多一个）
    - └ `_aggregate_sec_candle()` L98 — 🔐内部 将刚完成的 1s K 线聚合到各标准周期
    - └ `_check_new_sec_boundary()` L144 — 🔐内部 检查新秒级 K 线是否跨过标准周期边界
    - └ `_add_to_history()` L181 — 🔐内部 将完成的 K 线加入历史
    - └ `get_current_candle()` L187 — 🔓公开 获取当前进行中的 K 线
    - └ `get_history()` L191 — 🔓公开 获取最近 N 根已完成 K 线
    - └ `get_all_history()` L196 — 🔓公开 获取所有周期的历史
    - └ `has_history()` L200 — 🔓公开 是否有足够的历史数据

### 📄 agents\notifier.py (120行) — ServerChan 推送封装 — 通过 ServerChan 将交易报告推送到微信
  - 导入: `__future__`,`json`,`logging`,`urllib.error`,`urllib.parse`,`urllib.request`
  - 🏛️ `ServerChanNotifier` L20 — ServerChan 微信推送
    - └ `__init__()` L25 — 🔒内部
    - └ `push_report()` L28 — 🔓公开 推送交易报告到微信
    - └ `push_text()` L94 — 🔓公开 发送纯文本消息
    - └ `_send()` L98 — 🔐内部 调用 ServerChan API

### 📄 agents\okx_ws.py (177行) — OKX WebSocket 客户端 — 异步，自动重连
  - 导入: `__future__`,`asyncio`,`base64`,`datetime`,`hashlib`,`hmac`,`json`,`logging`
  - 🏛️ `OKXWebSocketClient` L23 — OKX WebSocket 客户端 — 支持自动重连与订阅管理
    - └ `__init__()` L28 — 🔒内部
    - └ `set_callbacks()` L50 — 🔓公开 设置消息和错误回调
    - └async `connect()` L59 — 🔓公开 建立 WebSocket 连接（自动重连循环）
    - └async `disconnect()` L99 — 🔓公开 断开 WebSocket 连接
    - └async `subscribe()` L107 — 🔓公开 订阅频道
    - └async `_subscribe_all()` L121 — 🔐内部 订阅所有已注册的频道
    - └async `_handle_message()` L128 — 🔐内部 处理收到的 WebSocket 消息
    - └async `__aenter__()` L142 — 🔒内部
    - └async `__aexit__()` L146 — 🔒内部
    - └ `_sign()` L150 — 🔐内部 OKX WebSocket 登录签名
    - └async `login()` L160 — 🔓公开 WebSocket 私有频道登录（Phase 2+ 需要）

### 📄 agents\onchain_collector.py (460行) — 链上数据收集器 — Phase 3 的 Agent 2 扩展模块
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`asyncio`,`datetime`,`httpx`,`json`,`logging`
  - 🏛️ `OnchainCollector` L95 — 链上数据收集器 — 作为 Agent 2 的子协程运行
    - └ `__init__()` L106 — 🔒内部
    - └async `run()` L140 — 🔓公开 启动所有启用的监控协程
    - └async `stop()` L161 — 🔓公开 停止
    - └async `_gas_monitor_loop()` L168 — 🔐内部 定时获取 ETH Gas 费
    - └async `_fetch_and_push_gas()` L180 — 🔐内部 获取 Gas 费并推送事件
    - └async `_whale_monitor_loop()` L235 — 🔐内部 定时获取巨鲸转账
    - └async `_fetch_and_push_whale()` L247 — 🔐内部 获取 Whale Alert 数据并推送
    - └async `_taker_volume_loop()` L319 — 🔐内部 定时获取吃单买卖比
    - └async `_fetch_and_push_taker()` L331 — 🔐内部 获取 OKX 吃单量数据并推送
    - └async `_funding_rate_loop()` L388 — 🔐内部 定时获取资金费率
    - └async `_fetch_and_push_funding()` L400 — 🔐内部 获取 OKX 资金费率数据并推送
    - └ `get_status()` L456 — 🔓公开 读取
  - ⚡ `_cst_now_iso()` L31 — 🔐内部 返回北京时间 ISO 字符串
  - ⚡ `_parse_gas_from_api_response()` L36 — 🔐内部 解析 Etherscan Gas Tracker API 响应
  - ⚡ `_categorize_gas()` L59 — 🔐内部 将 Gas 费归类
  - ⚡ `_parse_whale_from_response()` L70 — 🔐内部 解析 Whale Alert API 响应

### 📄 agents\param_adapter.py (177行) — 参数自适应 — Phase 4
  - 导入: `__future__`,`agents.config`,`datetime`,`logging`,`sqlite3`
  - 🏛️ `ParamAdapter` L23 — 参数自适应调整器
    - └ `__init__()` L26 — 🔒内部
    - └ `should_adjust()` L32 — 🔓公开 检查是否到达调整间隔
    - └ `adjust()` L39 — 🔓公开 评估近期表现并调整参数
    - └ `_get_recent_win_rate()` L84 — 🔐内部 计算最近 N 笔已平仓交易的胜率
    - └ `_get_recent_pnl()` L107 — 🔐内部 计算最近 N 笔已平仓交易的总盈亏
    - └ `_get_consecutive_losses()` L116 — 🔐内部 从数据库推断连续亏损次数
    - └ `_adjust_on_high_win_rate()` L129 — 🔐内部 胜率偏高 → 适当激進
    - └ `_adjust_on_low_win_rate()` L147 — 🔐内部 胜率偏低 → 保守
    - └ `_adjust_on_consecutive_losses()` L165 — 🔐内部 连续亏损 → 延长交易间隔
    - └ `get_adjustment_log()` L175 — 🔓公开 返回最近的调整记录

### 📄 agents\position_monitor.py (301行) — 持仓监控器 — 止盈 / 止损 / 移动止损
  - 导入: `__future__`,`agents.config`,`asyncio`,`datetime`,`logging`
  - 🏛️ `PositionMonitor` L24 — 持仓监控器 — 止盈/止损/移动止损
    - └ `__init__()` L27 — 🔒内部
    - └ `update_position()` L69 — 🔓公开 更新持仓信息（由 Agent 3 在新开仓后调用）
    - └ `clear_position()` L100 — 🔓公开 清空持仓（外部调用，如手动平仓后）
    - └async `run()` L104 — 🔓公开 启动持仓监控主循环
    - └async `stop()` L118 — 🔓公开 停止监控
    - └async `_check_once()` L125 — 🔐内部 执行一次持仓检查
    - └async `_check_long()` L154 — 🔐内部 检查多头持仓
    - └async `_check_short()` L196 — 🔐内部 检查空头持仓
    - └async `_close_position()` L236 — 🔐内部 平仓（按市价卖出/买入）
    - └ `get_status()` L289 — 🔓公开 读取

### 📄 agents\review_generator.py (516行) — 复盘报告生成 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.deepseek_caller`,`datetime`,`json`,`logging`,`os`,`pathlib`
  - 🏛️ `ReviewGenerator` L30 — 复盘报告生成器
    - └ `__init__()` L33 — 🔒内部
    - └ `compute_monthly_stats()` L41 — 🔓公开 计算本月至今的统计
    - └ `compute_daily_stats()` L53 — 🔓公开 计算指定日期的统计
    - └ `compute_weekly_stats()` L62 — 🔓公开 计算过去 7 天的统计
    - └ `generate_daily_report()` L73 — 🔓公开 生成每日复盘报告并写入 JSON
    - └ `generate_weekly_report()` L110 — 🔓公开 生成每周复盘报告并写入 JSON
    - └ `_get_conn()` L150 — 🔐内部
    - └ `_compute_range_stats()` L167 — 🔐内部 计算一个时间范围内的交易统计
    - └ `extract_wins_and_losses()` L264 — 🔓公开 从 SQLite Row 列表中提取盈利和亏损交易详情
    - └ `_analyze_trades_with_deepseek()` L300 — 🔐内部 调用 DeepSeek 分析盈亏模式
    - └ `generate_monthly_report()` L336 — 🔓公开 生成月度复盘报告并写入 JSON
    - └ `_fallback_to_pnl()` L389 — 🔐内部 当 pnl_close 全部为 0 时使用 pnl 字段
    - └ `_compute_max_drawdown()` L437 — 🔐内部 计算最大回撤 (以百分比计)
    - └ `_build_report()` L469 — 🔐内部 构建完整的报告字典
    - └ `_generate_summary_text()` L479 — 🔐内部 生成可读的中文总结
    - └ `_write_report()` L499 — 🔐内部 写入 JSON 文件到 data/reports/{type}/

### 📄 agents\risk_layer.py (411行) — 三层风控系统（阶段一基础版）
  - 导入: `__future__`,`agents.config`,`asyncio`,`datetime`,`logging`,`os`,`sqlite3`
  - 🏛️ `RiskManager` L33 — 风控管理器 — 三层风控
    - └ `__init__()` L36 — 🔒内部
    - └ `check_layer1()` L64 — 🔓公开 交易前全项检查，返回 (通过?, 原因)
    - └ `check_layer2()` L114 — 🔓公开 检查滑点是否可接受
    - └ `report_api_error()` L125 — 🔓公开 报告 API 错误（用于熔断）
    - └ `reset_api_errors()` L133 — 🔓公开 重置 API 错误计数
    - └ `record_trade()` L140 — 🔓公开 记录一笔交易（写入内存 + SQLite）
    - └ `_update_pnl_close()` L178 — 🔐内部 平仓时更新对应开仓记录的 pnl_close
    - └ `_record_loss()` L192 — 🔐内部 记录亏损
    - └ `record_loss()` L197 — 🔓公开 公开的亏损记录接口，代理 _record_loss
    - └ `get_position_size_multiplier()` L201 — 🔓公开 返回仓位乘数（连亏后减半）
    - └ `_utc_to_cst_date()` L208 — 🔐内部 UTC 时间转北京时间（CST, UTC+8）的日期
    - └ `_check_date_reset()` L213 — 🔐内部 每日重置（北京时间午夜 00:00 CST = UTC 16:00）
    - └ `get_status()` L227 — 🔓公开 返回风控状态摘要
    - └async `check_btc_volatility_async()` L243 — 🔓公开 检查 BTC 15m 波动率，超阈值则拒绝交易
    - └async `check_market_depth_async()` L292 — 🔓公开 检查市场深度是否足够
    - └ `_init_db()` L352 — 🔐内部 初始化 SQLite 数据库和表（含 Phase 4 迁移）
    - └ `_log_trade_sync()` L386 — 🔐内部 同步写入交易到 SQLite（含 Phase 4 P&L 列）

### 📄 agents\signal_aligner.py (224行) — 三方信号对齐 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`logging`,`re`
  - 🏛️ `SignalAligner` L33 — 三方信号对齐器
    - └ `__init__()` L36 — 🔒内部
    - └ `align()` L39 — 🔓公开 计算三方信号对齐度
    - └ `_score_technical()` L92 — 🔐内部 从 ConfidenceScorer 输出或事件中提取技术面方向
    - └ `_score_news()` L112 — 🔐内部 从新闻事件中判断方向
    - └ `_score_onchain()` L141 — 🔐内部 从链上事件中判断方向
    - └ `_build_summary()` L200 — 🔐内部 生成中文摘要

### 📄 agents\status_writer.py (69行) — Agent 状态写入器 — 供 main.py 定期将 Agent 运行状态写入 JSON 文件
  - 导入: `__future__`,`datetime`,`json`,`logging`,`os`,`pathlib`
  - ⚡ `write_agent_status()` L19 — 🔓公开 将各 Agent 状态写入 JSON 文件（供 Streamlit 面板读取）
  - ⚡ `read_agent_status()` L58 — 🔓公开 读取 Agent 状态 JSON 文件（供 Streamlit 面板使用）
  - ⚡ `get_status_file_path()` L67 — 🔓公开 返回状态文件路径（供外部判断使用）

### 📄 agents\trade_executor.py (396行) — 交易执行器 — OKX 实盘下单封装
  - 导入: `__future__`,`agents.config`,`asyncio`,`datetime`,`logging`,`random`,`uuid`
  - 🏛️ `TradeExecutor` L23 — 交易执行器
    - └ `__init__()` L30 — 🔒内部 Args:
    - └ `_normalize_result()` L53 — 🔐内部 将 OKX 下单返回结果规范化为 dict
    - └async `execute_market()` L65 — 🔓公开 市价单执行
    - └async `execute_limit()` L118 — 🔓公开 限价单完整生命周期
    - └async `cancel_and_check()` L309 — 🔓公开 撤销订单并查询最终状态
    - └async `execute_safe()` L321 — 🔓公开 安全执行入口（自动处理size格式、限价→市价降级、滑点保护）
    - └ `_extract_fill_price()` L377 — 🔐内部 从 OKX 下单返回值中提取成交价
    - └ `get_stats()` L391 — 🔓公开 读取

### 📄 backtest\__init__.py (0行)

### 📄 backtest\analyzer.py (363行) — 🔧 P0: Walk-forward 验证 + 蒙特卡洛参数测试
  - 导入: `__future__`,`backtest.engine`,`config`,`logging`,`numpy`,`pandas`,`random`
  - 🏛️ `WFWindow` L32 — 单个 Walk-forward 窗口结果
  - 🏛️ `WFResult` L49 — Walk-forward 总体结果
  - 🏛️ `ParamSweepResult` L66 — 参数扫描结果
  - 🏛️ `WalkForwardAnalyzer` L80 — Walk-forward 分析器
    - └ `__init__()` L86 — 🔒内部
    - └ `run()` L89 — 🔓公开 Walk-forward 验证
    - └ `parameter_sweep()` L198 — 🔓公开 蒙特卡洛参数扫描
    - └ `out_of_sample_test()` L295 — 🔓公开 留出法测试：最后 oos_ratio 数据不参与任何调参
    - └ `_print_report()` L338 — 🔐内部 打印 Walk-forward 报告
    - └ `_print_sweep_report()` L355 — 🔐内部

### 📄 backtest\engine.py (232行) — 回测引擎
  - 导入: `__future__`,`backtest.metrics`,`config`,`execution.order`,`logging`,`numpy`,`pandas`,`strategies.base`
  - 🏛️ `Trade` L23 — 单笔交易记录
  - 🏛️ `BacktestResult` L38 — 回测结果
  - 🏛️ `BacktestEngine` L50 — 回测引擎
    - └ `__init__()` L58 — 🔒内部
    - └ `run()` L62 — 🔓公开 运行回测
    - └ `run_all_strategies()` L163 — 🔓公开 运行所有策略
    - └ `run_order_type_comparison()` L179 — 🔓公开 🔧 P2: 比较 Market vs Limit 订单的差异
    - └ `report()` L212 — 🔓公开 打印回测报告

### 📄 backtest\metrics.py (96行) — 回测指标计算
  - 导入: `__future__`,`numpy`,`pandas`,`scipy`
  - ⚡ `compute_metrics()` L12 — 🔓公开 计算回测绩效指标

### 📄 config.py (247行) — OKX 量化交易系统 — 配置管理
  - 导入: `__future__`,`json`,`os`,`pathlib`
  - 🏛️ `ExchangeConfig` L19 — OKX 交易所连接配置
    - └ `is_readonly()` L32 — 🔓公开
  - 🏛️ `TradingConfig` L41 — 交易参数
  - 🏛️ `StrategyConfig` L59 — 策略池配置（多个策略并行）
  - 🏛️ `RiskConfig` L93 — 风控参数
  - 🏛️ `DataConfig` L114 — 数据存储配置
  - 🏛️ `AgentConfig` L130 — DeepSeek Agent 配置
    - └ `__post_init__()` L143 — 🔒内部
  - 🏛️ `NotificationConfig` L152 — 通知配置（P2 优化）
  - 🏛️ `Config` L176 — 系统总配置
    - └ `__post_init__()` L188 — 🔒内部
    - └ `save()` L193 — 🔓公开 保存配置到 JSON
    - └ `load()` L200 — 🔓公开 从 JSON 文件加载配置
    - └ `db_path()` L230 — 🔓公开
    - └ `is_live()` L234 — 🔓公开

### 📄 configs\__init__.py (0行)

### 📄 execution\__init__.py (4行)
  - 导入: `execution.ai_executor`,`execution.paper`

### 📄 execution\ai_executor.py (873行) — AI 交易执行引擎
  - 导入: `__future__`,`config`,`datetime`,`execution.paper`,`logging`,`pandas`,`risk.rules`
  - 🏛️ `AIStrategyExecutor` L331 — AI 交易执行器 — 加载规则 JSON，逐根 K 线执行
    - └ `__init__()` L334 — 🔒内部
    - └ `on_bar()` L391 — 🔓公开 处理一根新 K 线，返回状态 dict
    - └ `get_state()` L503 — 🔓公开 返回当前完整状态（供前端渲染）
    - └ `reset()` L549 — 🔓公开 重置执行器状态
    - └ `_append_bar()` L577 — 🔐内部 追加 K 线到滚动缓冲区
    - └ `_get_indicators()` L590 — 🔐内部 从缓冲区计算指标
    - └ `_position_pnl_pct()` L598 — 🔐内部 计算当前盈亏百分比（正=盈利，负=亏损），方向感知
    - └ `_check_hard_stops()` L608 — 🔐内部 检查硬性止盈止损（方向感知）
    - └ `_check_volatility_contrarian()` L643 — 🔐内部 检查波动率触发条件，返回开仓方向 ("long"/"short") 或 None
    - └ `_check_cooldown()` L674 — 🔐内部 检查同方向冷却是否满足，返回 (ok, reason)
    - └ `_get_position_size()` L692 — 🔐内部 风险预算仓位计算
    - └ `_check_multi_trailing_stop()` L735 — 🔐内部 多级移动止盈检查
    - └ `_execute_partial_close()` L782 — 🔐内部 部分平仓 x% 的当前仓位
    - └ `_execute_entry()` L800 — 🔐内部 执行入场（支持多空）
    - └ `_execute_exit()` L842 — 🔐内部 执行出场（根据当前仓位方向）
  - ⚡ `_calc_rsi()` L25 — 🔐内部 RSI 指标
  - ⚡ `_calc_sma()` L37 — 🔐内部
  - ⚡ `_calc_ema()` L41 — 🔐内部
  - ⚡ `_calc_macd()` L45 — 🔐内部
  - ⚡ `_calc_bollinger()` L54 — 🔐内部
  - ⚡ `_calc_price_change()` L64 — 🔐内部
  - ⚡ `_evaluate_condition()` L71 — 🔐内部 评估单个条件是否成立
  - ⚡ `_calc_indicators()` L217 — 🔐内部 对 DataFrame 计算所有常见指标，返回 {indicator_name: pd.Series}
  - ⚡ `_resolve_indicator_series()` L275 — 🔐内部 将条件中的 indicator 名映射到实际计算出的 Series
  - ⚡ `_check_conditions()` L302 — 🔐内部 检查一组条件，返回触发的条件列表

### 📄 execution\order.py (205行) — 🔧 P2: 订单类型分析模块
  - 导入: `__future__`,`logging`,`numpy`,`pandas`,`strategies.base`
  - 🏛️ `OrderSimulation` L22 — 订单模拟结果
  - 🏛️ `OrderTypeComparison` L33 — 两种订单类型的完整对比
  - ⚡ `simulate_market_order()` L49 — 🔓公开 模拟市价单
  - ⚡ `simulate_limit_order()` L74 — 🔓公开 模拟限价单
  - ⚡ `simulate_limit_orders()` L127 — 🔓公开 用限价单模拟替代市价单
  - ⚡ `compare_order_types()` L161 — 🔓公开 对比两种订单类型

### 📄 execution\paper.py (348行) — 本地模拟盘引擎
  - 导入: `__future__`,`config`,`datetime`,`json`,`logging`,`pandas`,`pathlib`,`risk.rules`
  - 🏛️ `PaperAccount` L23 — 模拟账户 — 支持多空双向
    - └ `__init__()` L26 — 🔒内部
    - └ `is_flat()` L41 — 🔓公开 是否空仓（多空均为空）
    - └ `equity()` L46 — 🔓公开 当前总权益 = 现金 + 多头市值 + 空头未实现盈亏
    - └ `_short_unrealized_pnl()` L50 — 🔐内部 空头未实现盈亏
    - └ `unrealized_pnl()` L58 — 🔓公开 总未实现盈亏 (USD)
    - └ `unrealized_pnl_pct()` L65 — 🔓公开 未实现盈亏 (%) — 根据当前持仓类型计算
    - └ `update_price()` L75 — 🔓公开 更新最新价格，记录权益历史
    - └ `execute_buy()` L87 — 🔓公开 执行买入，返回 trade dict
    - └ `execute_sell()` L113 — 🔓公开 执行卖出，返回 trade dict
    - └ `execute_short()` L146 — 🔓公开 执行开空（卖空），返回 trade dict
    - └ `execute_cover()` L176 — 🔓公开 执行平空（买入平仓），支持部分平仓
    - └ `to_dict()` L208 — 🔓公开 序列化为 JSON 友好 dict（给前端用）
    - └ `save_state()` L227 — 🔓公开
    - └ `load_state()` L241 — 🔓公开
    - └ `report()` L255 — 🔓公开
  - 🏛️ `PaperEngine` L270 — 模拟盘引擎 — 逐根 K 线驱动
    - └ `__init__()` L273 — 🔒内部
    - └ `run_bar()` L279 — 🔓公开 处理一根新 K 线，执行完整模拟盘循环。
    - └ `run()` L341 — 🔓公开 CLI 模式占位 — 前端驱动时用 run_bar

### 📄 main.py (293行) — OKX Quant Agent — 三 Agent 异步事件驱动交易系统
  - 导入: `__future__`,`agents.agent1_technical`,`agents.agent2_news`,`agents.agent3_trader`,`agents.agent4_reviewer`,`agents.config`,`agents.deepseek_caller`,`agents.event_bus`
  - ⚡ `setup_logging()` L39 — 🔓公开 配置日志
  - ⚡ `_install_signal_handlers()` L55 — 🔐内部 Install signal handlers — works on both Unix and Windows.
  - ⚡ async `main()` L67 — 🔓公开
  - ⚡ async `_status_reporter()` L240 — 🔐内部 定期报告系统状态并写入 JSON（每 5s，保证前端实时更新）

### 📄 notification\__init__.py (0行)

### 📄 notification\notifier.py (152行) — 🔧 P2: 通知系统
  - 导入: `__future__`,`config`,`datetime`,`email.message`,`httpx`,`json`,`logging`,`pathlib`
  - 🏛️ `Notifier` L34 — 通知发送器
    - └ `__init__()` L37 — 🔒内部
    - └ `http_client()` L42 — 🔓公开
    - └ `send()` L47 — 🔓公开 发送通知（按配置路由到不同渠道）
    - └ `notify_signal()` L79 — 🔓公开 新交易信号通知
    - └ `notify_trade()` L85 — 🔓公开 成交通知
    - └ `notify_error()` L96 — 🔓公开 异常通知
    - └ `notify_daily_report()` L100 — 🔓公开 每日报告
    - └ `_send_email()` L108 — 🔐内部 通过 SMTP 发送邮件
    - └ `_send_webhook()` L124 — 🔐内部 通过 Webhook 发送（钉钉/企微/Slack 兼容格式）
    - └ `_log_to_file()` L141 — 🔐内部 写入本地日志文件（最低成本通知）
    - └ `close()` L150 — 🔓公开

### 📄 okx_client.py (341行) — OKX REST API 客户端
  - 导入: `__future__`,`base64`,`config`,`datetime`,`hashlib`,`hmac`,`httpx`,`logging`
  - 🏛️ `OKXClient` L26 — OKX API 客户端 — 第一版只接公开行情 REST
    - └ `__init__()` L29 — 🔒内部
    - └ `_request()` L39 — 🔐内部 统一 HTTP 请求入口，自动重试 transient 网络错误。
    - └ `_check_api_response()` L82 — 🔐内部 检查 OKX API 返回码
    - └ `get_klines()` L89 — 🔓公开 获取 K 线数据
    - └ `get_ticker()` L116 — 🔓公开 获取最新 ticker
    - └ `get_balance()` L125 — 🔓公开 查询账户余额（仅 Read 权限）
    - └ `get_positions()` L135 — 🔓公开 查询持仓
    - └ `place_order()` L148 — 🔓公开 下单（需要 Trade 权限）
    - └ `cancel_order()` L175 — 🔓公开 撤销订单
    - └ `get_order()` L191 — 🔓公开 查询订单状态
    - └ `get_order_book()` L207 — 🔓公开 获取订单簿深度
    - └ `get_taker_volume()` L227 — 🔓公开 获取吃单量（买卖比）
    - └ `get_funding_rate()` L249 — 🔓公开 获取永续合约资金费率
    - └ `_normalize_order_data()` L269 — 🔐内部 标准化订单 API 返回值
    - └ `_sign()` L279 — 🔐内部 OKX 签名
    - └ `_timestamp()` L296 — 🔐内部
    - └ `_tf_to_bar()` L300 — 🔐内部
    - └ `_parse_klines()` L309 — 🔐内部 OKX 原始 K 线转 dict
    - └ `_parse_ticker()` L325 — 🔐内部
    - └ `close()` L340 — 🔓公开

### 📄 risk\__init__.py (0行)

### 📄 risk\recovery.py (157行) — 🔧 P3: 风控暂停后的恢复策略
  - 导入: `__future__`,`config`,`datetime`,`logging`
  - 🏛️ `RecoveryManager` L21 — 暂停恢复管理器
    - └ `__init__()` L27 — 🔒内部
    - └ `evaluate_recovery()` L32 — 🔓公开 评估恢复方案
    - └ `_select_alternative_strategy()` L117 — 🔐内部 选择备用策略（不用当前失败的）
    - └ `_elapsed_bars()` L131 — 🔐内部
    - └ `get_recovery_guide()` L138 — 🔓公开 恢复策略配置指南

### 📄 risk\rules.py (126行) — 风控规则引擎
  - 导入: `__future__`,`config`,`datetime`,`logging`
  - 🏛️ `RiskState` L16 — 风控状态
    - └ `__init__()` L19 — 🔒内部
    - └ `reset_daily()` L32 — 🔓公开 每日重置
  - 🏛️ `RiskEngine` L41 — 风控引擎
    - └ `__init__()` L47 — 🔒内部
    - └ `check_signal()` L51 — 🔓公开 检查信号是否允许执行
    - └ `record_trade_result()` L85 — 🔓公开 记录交易结果，更新风控状态
    - └ `_pause()` L106 — 🔐内部 暂停交易
    - └ `check_signal_expiry()` L114 — 🔓公开 检查信号是否过期
    - └ `_estimate_elapsed_bars()` L123 — 🔐内部 估算经过的 K 线数（按 1h）

### 📄 risk\stop_loss.py (99行) — 🔧 P0: 止盈 / 止损 / 移动止损 计算模块
  - 导入: `__future__`
  - 🏛️ `StopLossLevels` L12 — 当前持仓的止损水平
  - ⚡ `compute_stop_levels()` L21 — 🔓公开 计算所有止损水平
  - ⚡ `should_exit()` L77 — 🔓公开 检查是否应该退出

### 📄 scripts\__init__.py (1行)

### 📄 scripts\generate_code_map.py (260行) — 生成项目代码映射索引 CODE_MAP.md
  - 导入: `__future__`,`ast`,`os`,`pathlib`,`sys`
  - ⚡ `get_module_docstring()` L37 — 🔓公开 提取模块/类/函数的文档字符串第一行
  - ⚡ `describe_function()` L47 — 🔓公开 从函数名和文档推断用途标签
  - ⚡ `describe_class()` L86 — 🔓公开
  - ⚡ `extract_file_info()` L93 — 🔓公开 提取单个 Python 文件的代码结构
  - ⚡ `build_markdown()` L163 — 🔓公开 组装 CODE_MAP.md
  - ⚡ `main()` L234 — 🔓公开

### 📄 strategies\__init__.py (0行)

### 📄 strategies\base.py (134行) — 策略基类
  - 导入: `__future__`,`pandas`,`strategies.breakout`,`strategies.ma_cross`,`strategies.rsi_mean_reversion`
  - 🏛️ `Signal` L16 — 交易信号
  - 🏛️ `PositionInfo` L25 — 当前持仓信息（策略内部状态用）
  - 🏛️ `StrategyResult` L37 — 策略输出结果
    - └ `trade_count()` L43 — 🔓公开
  - 🏛️ `BaseStrategy` L49 — 策略基类
    - └ `__init__()` L52 — 🔒内部
    - └ `generate_signals()` L62 — 🔓公开 生成交易信号（批处理模式 — 用于回测）
    - └ `on_bar()` L70 — 🔓公开 增量模式：逐根 K 线处理，返回单根 K 线的信号。
    - └ `get_bar_buffer()` L82 — 🔓公开 获取当前 K 线缓冲区（用于指标计算）
    - └ `reset_buffer()` L88 — 🔓公开 重置 K 线缓冲区（切换策略或重新开始时调用）
    - └ `reset_position()` L92 — 🔓公开 重置持仓状态
    - └ `description()` L97 — 🔓公开
  - ⚡ `get_available_strategies()` L101 — 🔓公开 获取所有可用策略
  - ⚡ `create_strategy()` L126 — 🔓公开 工厂方法：创建策略实例

### 📄 strategies\breakout.py (190行) — 策略 3: 突破策略
  - 导入: `__future__`,`logging`,`numpy`,`pandas`,`strategies.base`
  - 🏛️ `BreakoutStrategy` L21 — 突破策略
    - └ `__init__()` L28 — 🔒内部
    - └ `description()` L34 — 🔓公开
    - └ `_get_params()` L41 — 🔐内部
    - └ `_compute_atr()` L55 — 🔐内部
    - └ `_compute_indicators()` L63 — 🔐内部
    - └ `_check_exit()` L77 — 🔐内部 检查
    - └ `generate_signals()` L106 — 🔓公开
    - └ `on_bar()` L155 — 🔓公开 回调

### 📄 strategies\ma_cross.py (187行) — 策略 1: MA 均线交叉
  - 导入: `__future__`,`logging`,`numpy`,`pandas`,`strategies.base`
  - 🏛️ `MACrossStrategy` L18 — MA 均线交叉策略
    - └ `__init__()` L28 — 🔒内部
    - └ `description()` L33 — 🔓公开
    - └ `_get_params()` L40 — 🔐内部
    - └ `_compute_indicators()` L53 — 🔐内部
    - └ `_check_exit()` L64 — 🔐内部 检查退出条件，返回 (signal, reason)
    - └ `generate_signals()` L93 — 🔓公开
    - └ `on_bar()` L148 — 🔓公开 回调

### 📄 strategies\rsi_mean_reversion.py (169行) — 策略 2: RSI 均值回归
  - 导入: `__future__`,`logging`,`numpy`,`pandas`,`strategies.base`
  - 🏛️ `RSIMeanReversionStrategy` L20 — RSI 均值回归策略
    - └ `__init__()` L23 — 🔒内部
    - └ `description()` L28 — 🔓公开
    - └ `_get_params()` L36 — 🔐内部
    - └ `_compute_rsi()` L49 — 🔐内部
    - └ `_compute_indicators()` L58 — 🔐内部
    - └ `_check_exit()` L67 — 🔐内部 检查
    - └ `generate_signals()` L87 — 🔓公开
    - └ `on_bar()` L135 — 🔓公开 回调

### 📄 tests\__init__.py (0行)

### 📄 tests\test_agent2_scorer.py (27行)
  - 导入: `agents.agent2_news`,`sys`
  - ⚡ `test_scoring()` L5 — 🔓公开

### 📄 tests\test_agent3_phase2.py (447行) — 测试 Agent 3 阶段二集成——风控注入、BTC检查、市场深度
  - 导入: `__future__`,`agents.agent3_trader`,`agents.config`,`agents.event_bus`,`datetime`,`pathlib`,`pytest`,`sys`
  - 🏛️ `TestRiskStatusInjection` L92
    - └ `test_build_context_includes_risk_status()` L93 — 🔓公开 验证 _build_context 注入风控状态
    - └ `test_build_context_with_events()` L112 — 🔓公开 验证上下文包含技术和新闻事件
    - └ `test_build_context_empty()` L127 — 🔓公开 无事件时上下文包含默认值
  - 🏛️ `TestDeepSeekPromptUpdate` L135
    - └ `test_context_passed_to_deepseek()` L136 — 🔓公开 验证上下文正确传递给 DeepSeek
  - 🏛️ `TestBtcDepthChecks` L153
    - └async `test_btc_check_called()` L155 — 🔓公开 有 okx_client 时 BTC 波动检查被调用
    - └async `test_depth_check_called()` L191 — 🔓公开 有 okx_client 时市场深度检查被调用
    - └async `test_btc_check_blocks_trade()` L231 — 🔓公开 BTC 波动检查不通过时交易被阻止
    - └async `test_depth_check_blocks_trade()` L258 — 🔓公开 市场深度不通过时交易被阻止
    - └async `test_no_client_skips_checks()` L285 — 🔓公开 无 okx_client 时跳过 BTC/深度检查
    - └async `test_prefer_limit_passed_to_executor()` L312 — 🔓公开 深度检查返回的 prefer_limit 被传递给执行器
  - 🏛️ `TestPositionMonitorNotify` L344
    - └async `test_position_monitor_notified()` L346 — 🔓公开 交易成功后通知 PositionMonitor
    - └async `test_position_monitor_not_called_on_failure()` L385 — 🔓公开 交易失败时不通知 PositionMonitor
    - └async `test_no_monitor_no_error()` L418 — 🔓公开 没有 PositionMonitor 也不会报错
  - ⚡ `config()` L19 — 🔓公开
  - ⚡ `event_bus()` L24 — 🔓公开
  - ⚡ `mock_deepseek()` L29 — 🔓公开
  - ⚡ `mock_risk_manager()` L45 — 🔓公开
  - ⚡ `mock_executor()` L64 — 🔓公开
  - ⚡ `mock_root_config()` L74 — 🔓公开
  - ⚡ `agent3()` L81 — 🔓公开

### 📄 tests\test_agent4_reviewer.py (235行) — 测试 Agent 4 — 复盘改进 Agent
  - 导入: `__future__`,`agents.agent4_reviewer`,`agents.config`,`agents.deepseek_caller`,`json`,`os`,`pathlib`,`pytest`
  - ⚡ `_make_db()` L21 — 🔐内部 创建临时数据库并写入交易（含 Phase 4 字段）
  - ⚡ `_make_reviewer()` L58 — 🔐内部 创建测试用的 Agent4Reviewer 实例
  - ⚡ `test_init()` L82 — 🔓公开 Agent4Reviewer 初始化后状态正确
  - ⚡ async `test_notify_trade_under_threshold()` L92 — 🔓公开 交易数未达阈值时不会触发复盘
  - ⚡ async `test_notify_trade_triggers_review()` L102 — 🔓公开 交易数达阈值后触发复盘
  - ⚡ async `test_notify_trade_triggers_multiple_reviews()` L112 — 🔓公开 每满 5 笔触发一次复盘，不重置计数
  - ⚡ `test_load_recent_trades()` L130 — 🔓公开 能从 SQLite 加载最近交易
  - ⚡ `test_load_recent_trades_empty_db()` L144 — 🔓公开 空数据库返回空列表
  - ⚡ `test_validate_unknown_param()` L153 — 🔓公开 未知参数名被拒绝
  - ⚡ `test_validate_out_of_bounds()` L161 — 🔓公开 超出安全范围的参数被拒绝
  - ⚡ `test_validate_risk_param_strict()` L172 — 🔓公开 风险参数只能降低不能提高
  - ⚡ `test_validate_debounce()` L185 — 🔓公开 同一参数最小修改间隔
  - ⚡ `test_validate_no_actual_change()` L198 — 🔓公开 值没变化时跳过
  - ⚡ `test_param_bounds_completeness()` L209 — 🔓公开 _PARAM_BOUNDS 表包含所有 config 可调字段，无遗漏
  - ⚡ `test_review_prompt_format()` L217 — 🔓公开 Prompt 模板能正确格式化

### 📄 tests\test_backtest.py (130行) — 回测引擎测试
  - 导入: `__future__`,`backtest.engine`,`config`,`numpy`,`pandas`,`pytest`
  - ⚡ `test_config()` L16 — 🔓公开 简化的测试配置
  - ⚡ `price_data()` L28 — 🔓公开 生成带趋势的模拟 K 线数据
  - ⚡ `test_backtest_engine_initializes()` L49 — 🔓公开
  - ⚡ `test_backtest_returns_result()` L54 — 🔓公开
  - ⚡ `test_backtest_has_metrics()` L64 — 🔓公开
  - ⚡ `test_backtest_metrics_are_reasonable()` L75 — 🔓公开 指标应该在合理范围内
  - ⚡ `test_backtest_trades_have_required_fields()` L89 — 🔓公开
  - ⚡ `test_backtest_includes_benchmark()` L101 — 🔓公开
  - ⚡ `test_multiple_strategies()` L107 — 🔓公开 测试多策略回测
  - ⚡ `test_order_type_comparison()` L116 — 🔓公开 订单类型对比测试
  - ⚡ `test_backtest_reproducible()` L124 — 🔓公开 相同数据应该产生相同结果

### 📄 tests\test_change_detector.py (74行)
  - 导入: `agents.change_detector`,`sys`
  - ⚡ `test_macd_bullish_cross()` L6 — 🔓公开
  - ⚡ `test_cooldown()` L31 — 🔓公开

### 📄 tests\test_confidence_scorer.py (145行) — 测试多周期信心分 — Phase 4
  - 导入: `__future__`,`agents.confidence_scorer`,`agents.config`,`agents.event_bus`,`datetime`,`pathlib`,`pytest`,`sys`
  - 🏛️ `TestConfidenceScorer` L49
    - └ `test_empty_events_returns_neutral()` L51 — 🔓公开 无信号时返回中性
    - └ `test_non_agent1_events_ignored()` L59 — 🔓公开 非 agent1 事件被忽略
    - └ `test_single_bullish_signal()` L70 — 🔓公开 单一偏多信号
    - └ `test_single_bearish_signal()` L80 — 🔓公开 单一偏空信号
    - └ `test_timeframe_weighting()` L88 — 🔓公开 相同信号在不同时间帧上有不同权重
    - └ `test_opposing_signals_cancel()` L103 — 🔓公开 相反信号互相抵消
    - └ `test_composite_confidence_with_agreement()` L116 — 🔓公开 同方向信号越多, 一致性信心越高
    - └ `test_unknown_signal_type_ignored()` L129 — 🔓公开 未知信号类型被跳过
    - └ `test_timeframe_breakdown()` L136 — 🔓公开 时间帧分解包含所有输入的时间帧
  - ⚡ `config()` L18 — 🔓公开
  - ⚡ `_make_agent1_event()` L38 — 🔐内部

### 📄 tests\test_data.py (134行) — 数据层测试
  - 导入: `__future__`,`config`,`data.quality`,`data.storage`,`os`,`pathlib`,`pytest`,`tempfile`
  - ⚡ `temp_db_config()` L18 — 🔓公开 使用临时数据库的配置
  - ⚡ `test_store_creates_database()` L26 — 🔓公开
  - ⚡ `test_store_insert_klines()` L33 — 🔓公开
  - ⚡ `test_store_dedup()` L45 — 🔓公开 按 symbol+timeframe+timestamp 去重
  - ⚡ `test_store_load_klines()` L58 — 🔓公开
  - ⚡ `test_store_load_with_date_filter()` L72 — 🔓公开
  - ⚡ `test_wal_mode_enabled()` L85 — 🔓公开 验证 WAL 模式已启用
  - ⚡ `test_data_quality_continuity_check()` L94 — 🔓公开 数据连续性检测
  - ⚡ `test_data_quality_price_check()` L117 — 🔓公开 异常价格检测

### 📄 tests\test_deepseek_caller.py (52行) — 测试 DeepSeek 调用器
  - 导入: `__future__`,`agents.deepseek_caller`,`pathlib`,`pytest`,`sys`
  - 🏛️ `TestDeepSeekTrader` L13
    - └ `test_analyze_trade_report_returns_expected_keys()` L15 — 🔓公开 trade report 分析返回正确的 key 结构
    - └ `test_analyze_trade_report_fallback_on_error()` L39 — 🔓公开 API 调用失败时回退默认值

### 📄 tests\test_kline_builder.py (67行) — KlineBuilder 单元测试
  - 导入: `agents.kline_builder`,`sys`
  - ⚡ `test_basic_tick_to_15m()` L6 — 🔓公开
  - ⚡ `test_multiple_timeframes()` L40 — 🔓公开

### 📄 tests\test_notifier.py (55行) — 测试 ServerChan 通知
  - 导入: `__future__`,`agents.notifier`,`pathlib`,`pytest`,`sys`
  - 🏛️ `TestServerChanNotifier` L13
    - └ `test_push_report_empty_sendkey_returns_false()` L15 — 🔓公开 空 sendkey 返回 False
    - └ `test_push_text_invalid_key_returns_false()` L26 — 🔓公开 无效 sendkey 返回 False（网络失败）
    - └ `test_push_report_with_wins_and_losses()` L32 — 🔓公开 包含盈亏数据的报告推送

### 📄 tests\test_okx_client_phase2.py (111行) — 测试 OKX API 追加的三个方法（使用 mock 避免真实网络请求）
  - 导入: `__future__`,`config`,`okx_client`,`pathlib`,`pytest`,`sys`,`unittest.mock`
  - 🏛️ `TestCancelOrder` L32
    - └ `test_cancel_order_success()` L33 — 🔓公开 测试成功撤单
    - └ `test_cancel_order_api_error()` L43 — 🔓公开 测试撤单 API 返回错误
  - 🏛️ `TestGetOrder` L52
    - └ `test_get_order_filled()` L53 — 🔓公开 测试查询已成交订单
    - └ `test_get_order_partial_fill()` L65 — 🔓公开 测试查询部分成交订单
    - └ `test_get_order_cancelled()` L76 — 🔓公开 测试查询已取消订单
  - 🏛️ `TestGetOrderBook` L88
    - └ `test_get_order_book()` L89 — 🔓公开 测试获取订单簿
  - ⚡ `client()` L17 — 🔓公开
  - ⚡ `_mock_response()` L25 — 🔐内部 构造 OKX 标准响应格式

### 📄 tests\test_onchain_collector.py (456行) — 测试 Phase 3 链上数据收集器
  - 导入: `__future__`,`agents.agent2_news`,`agents.config`,`agents.event_bus`,`agents.onchain_collector`,`pathlib`,`pytest`,`sys`
  - 🏛️ `TestGasParsing` L83
    - └ `test_parse_gas_normal()` L84 — 🔓公开 正常 Gas API 响应
    - └ `test_parse_gas_empty()` L101 — 🔓公开 空结果
    - └ `test_parse_gas_zero_values()` L106 — 🔓公开 全部为零
    - └ `test_parse_gas_malformed()` L112 — 🔓公开 畸形数据
    - └ `test_categorize_low()` L117 — 🔓公开
    - └ `test_categorize_medium()` L121 — 🔓公开
    - └ `test_categorize_high()` L125 — 🔓公开
    - └ `test_categorize_extreme()` L129 — 🔓公开
  - 🏛️ `TestWhaleParsing` L136
    - └ `test_parse_whale_normal()` L137 — 🔓公开 正常 Whale Alert 数据
    - └ `test_parse_whale_empty()` L156 — 🔓公开 空列表
    - └ `test_parse_whale_missing_fields()` L161 — 🔓公开 缺少字段
    - └ `test_parse_whale_non_dict_items()` L168 — 🔓公开 不是 dict 的项被跳过
  - 🏛️ `TestTakerVolume` L177
    - └async `test_taker_volume_push()` L179 — 🔓公开 吃单比推送事件
    - └async `test_taker_volume_no_duplicate()` L208 — 🔓公开 重复相近值不推送
    - └async `test_taker_volume_api_error()` L232 — 🔓公开 API 异常时优雅跳过
  - 🏛️ `TestFundingRate` L251
    - └async `test_funding_rate_push()` L253 — 🔓公开 资金费率推送事件
    - └async `test_funding_rate_api_error()` L276 — 🔓公开 API 异常时跳过
  - 🏛️ `TestOnchainCollectorIntegration` L292
    - └async `test_run_disabled_modules()` L294 — 🔓公开 全部模块禁用时不启动任何协程
    - └async `test_get_status()` L311 — 🔓公开 状态返回包含所有监控字段
    - └async `test_stop_cleans_up()` L328 — 🔓公开 stop 关闭 HTTP 客户端
    - └async `test_gas_without_api_key_skips()` L343 — 🔓公开 无 API key 时不抓取 Gas
    - └async `test_gas_with_api_key()` L360 — 🔓公开 有 API key 时抓取 Gas 并推送
    - └async `test_taker_bullish_signal()` L389 — 🔓公开 买占比超过阈值触发偏多信号
    - └async `test_whale_requires_api_key()` L414 — 🔓公开 无 Whale Alert API key 时跳过
  - 🏛️ `TestAgent2WithOnchain` L431
    - └ `test_agent2_accepts_okx_client()` L432 — 🔓公开 Agent2 接受 okx_client 后创建 OnchainCollector
    - └ `test_agent2_without_okx_client()` L441 — 🔓公开 无 okx_client 时不创建 OnchainCollector
    - └ `test_agent2_onchain_disabled()` L449 — 🔓公开 onchain 禁用时不创建 OnchainCollector
  - ⚡ `config()` L34 — 🔓公开
  - ⚡ `_mock_okx_client()` L51 — 🔐内部 构造模拟 OKXClient
  - ⚡ `_mock_http_client()` L70 — 🔐内部 构造模拟 httpx.AsyncClient

### 📄 tests\test_param_adapter.py (177行) — 测试参数自适应 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.param_adapter`,`datetime`,`os`,`pathlib`,`pytest`,`sqlite3`
  - 🏛️ `TestParamAdapter` L59
    - └ `test_no_adjustment_below_min_trades()` L61 — 🔓公开 数据不足时不调整
    - └ `test_adjust_on_high_win_rate()` L72 — 🔓公开 高胜率 → 增加日交易次数, 缩短间隔
    - └ `test_adjust_on_low_win_rate()` L88 — 🔓公开 低胜率 → 减少日交易次数, 延长间隔
    - └ `test_bounds_enforced()` L104 — 🔓公开 调整不超过安全边界
    - └ `test_should_adjust_timing()` L120 — 🔓公开 调整间隔检查
    - └ `test_get_recent_win_rate_empty()` L143 — 🔓公开 空数据库返回 None
    - └ `test_adjustment_log_append()` L157 — 🔓公开 调整记录被保存到日志
  - ⚡ `_make_db()` L19 — 🔐内部 创建临时数据库并写入交易
  - ⚡ `config()` L45 — 🔓公开

### 📄 tests\test_position_monitor.py (248行) — 测试持仓监控器——止盈、止损、移动止损
  - 导入: `__future__`,`agents.config`,`agents.position_monitor`,`datetime`,`pathlib`,`pytest`,`sys`,`unittest.mock`
  - 🏛️ `TestPositionMonitor` L51
    - └async `test_stop_loss_triggered()` L53 — 🔓公开 价格跌破止损 → 触发止损卖出
    - └async `test_take_profit_triggered()` L77 — 🔓公开 价格涨到止盈 → 触发止盈卖出
    - └async `test_trailing_stop_activates()` L97 — 🔓公开 浮盈达到 3% 后激活移动止损，止损位上移
    - └async `test_trailing_stop_triggers()` L122 — 🔓公开 移动止损激活后，价格回落到新止损位 → 触发卖出
    - └async `test_no_position_no_action()` L146 — 🔓公开 无持仓时不做任何操作
    - └async `test_short_position_take_profit_and_stop()` L164 — 🔓公开 空头仓位：止盈（价格跌）和止损（价格涨）方向正确
    - └async `test_short_trailing_stop_activates()` L187 — 🔓公开 空头：价格下跌触发移动止损激活
    - └async `test_short_trailing_stop_triggers()` L207 — 🔓公开 空头移动止损激活后价格回升 → 触发
    - └async `test_status_report()` L230 — 🔓公开 get_status 返回正确统计
  - ⚡ `config()` L17 — 🔓公开
  - ⚡ `mock_risk_manager()` L26 — 🔓公开
  - ⚡ `mock_executor()` L34 — 🔓公开
  - ⚡ `mock_okx_client()` L45 — 🔓公开

### 📄 tests\test_review_generator.py (253行) — 测试复盘报告生成器 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.review_generator`,`datetime`,`gc`,`json`,`os`,`pathlib`
  - 🏛️ `TestReviewGenerator` L81
    - └ `test_empty_db()` L83 — 🔓公开 空数据库返回零值
    - └ `test_win_rate_calculation()` L92 — 🔓公开 胜率计算
    - └ `test_max_drawdown()` L107 — 🔓公开 最大回撤计算
    - └ `test_daily_report_generates_file()` L123 — 🔓公开 每日报告生成 JSON 文件
    - └ `test_weekly_report_generates_file()` L143 — 🔓公开 每周报告生成 JSON 文件
    - └ `test_report_skipped_below_min_trades()` L158 — 🔓公开 低于最小交易次数时生成摘要说明
    - └ `test_fallback_to_pnl()` L166 — 🔓公开 当 pnl_close 全为空时回退到 pnl 字段
    - └ `test_by_side_breakdown()` L179 — 🔓公开 按方向拆分统计
    - └ `test_extract_wins_and_losses()` L195 — 🔓公开 提取盈亏交易
    - └ `test_monthly_report_no_trades()` L214 — 🔓公开 无交易时月度报告返回零值
    - └ `test_monthly_report_with_trades()` L223 — 🔓公开 月度报告包含交易明细
    - └ `test_report_writes_to_new_dir()` L234 — 🔓公开 报告写入新目录结构 data/reports/{type}/
    - └ `test_ai_analysis_not_called_when_no_deepseek()` L245 — 🔓公开 不传 deepseek 时不调用 AI 分析
  - ⚡ `config()` L21 — 🔓公开
  - ⚡ `temp_db()` L30 — 🔓公开 创建临时数据库, 自动清理
  - ⚡ `_populate_trades()` L44 — 🔐内部 向测试数据库写入交易记录

### 📄 tests\test_risk.py (143行) — 风控模块测试
  - 导入: `__future__`,`datetime`,`pytest`,`risk.recovery`,`risk.rules`,`risk.stop_loss`
  - ⚡ `risk_config()` L16 — 🔓公开
  - ⚡ `test_risk_engine_init()` L27 — 🔓公开
  - ⚡ `test_signal_allowed_within_limits()` L34 — 🔓公开
  - ⚡ `test_signal_rejected_when_max_position()` L41 — 🔓公开
  - ⚡ `test_consecutive_losses_triggers_pause()` L48 — 🔓公开
  - ⚡ `test_daily_loss_limit_triggers_pause()` L56 — 🔓公开
  - ⚡ `test_win_resets_consecutive_losses()` L65 — 🔓公开
  - ⚡ `test_signal_expiry()` L73 — 🔓公开
  - ⚡ `test_fixed_stop_loss()` L82 — 🔓公开
  - ⚡ `test_take_profit()` L95 — 🔓公开
  - ⚡ `test_trailing_stop_activates()` L108 — 🔓公开 浮盈达到阈值后，移动止损生效
  - ⚡ `test_no_exit_when_price_normal()` L123 — 🔓公开
  - ⚡ `test_recovery_starts_without_pause()` L137 — 🔓公开

### 📄 tests\test_risk_layer.py (70行)
  - 导入: `agents.config`,`agents.risk_layer`,`datetime`,`sys`
  - ⚡ `test_layer1_min_interval()` L8 — 🔓公开
  - ⚡ `test_layer1_daily_loss()` L29 — 🔓公开
  - ⚡ `test_consecutive_losses()` L49 — 🔓公开

### 📄 tests\test_risk_layer_phase2.py (188行) — 测试 RiskManager 阶段二功能
  - 导入: `__future__`,`agents.config`,`agents.risk_layer`,`datetime`,`pathlib`,`pytest`,`sys`,`unittest.mock`
  - 🏛️ `TestBtcVolatility` L53
    - └async `test_btc_normal_volatility()` L55 — 🔓公开 BTC 正常波动 → 通过
    - └async `test_btc_high_volatility()` L63 — 🔓公开 BTC 高波动 → 拒绝
    - └async `test_btc_insufficient_data()` L76 — 🔓公开 BTC 数据不足 → 通过（不阻塞交易）
    - └async `test_btc_delay_cooldown()` L83 — 🔓公开 BTC 波动延迟期内再次检查 → 仍拒绝
  - 🏛️ `TestMarketDepth` L99
    - └async `test_depth_sufficient()` L101 — 🔓公开 市场深度充足 → 通过
    - └async `test_depth_wide_spread()` L116 — 🔓公开 买卖价差过大 → 强制限价单
    - └async `test_depth_insufficient_liquidity()` L132 — 🔓公开 深度不足以完成交易 → 拒绝
  - 🏛️ `TestBeijingSettlement` L146
    - └ `test_daily_reset_at_cst_midnight()` L147 — 🔓公开 北京时间（UTC+8）午夜重置
    - └ `test_no_reset_within_same_day()` L164 — 🔓公开 同一天内不重复重置
    - └ `test_reset_accounts_for_cst_date_change()` L177 — 🔓公开 UTC 16:00 后应该用新的日期标识
  - ⚡ `config()` L19 — 🔓公开
  - ⚡ `manager()` L29 — 🔓公开
  - ⚡ `make_mock_client()` L33 — 🔓公开 构造模拟 OKXClient

### 📄 tests\test_signal_aligner.py (166行) — 测试三方信号对齐 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`agents.signal_aligner`,`datetime`,`pathlib`,`pytest`,`sys`
  - 🏛️ `TestSignalAligner` L46
    - └ `test_empty_events_returns_neutral()` L48 — 🔓公开 无事件时全部中性
    - └ `test_all_sources_agree_consensus()` L58 — 🔓公开 三方看多 → 共识
    - └ `test_technical_news_conflict()` L73 — 🔓公开 技术看多, 新闻看空 → 冲突
    - └ `test_whale_to_exchange_is_bearish()` L85 — 🔓公开 巨鲸转交易所 → 偏空
    - └ `test_whale_out_of_exchange_is_bullish()` L98 — 🔓公开 巨鲸出交易所 → 偏多
    - └ `test_funding_rate_high_positive_is_bearish()` L108 — 🔓公开 资金费率高正 → 偏空
    - └ `test_taker_bullish_is_bullish()` L118 — 🔓公开 吃单比偏多 → 偏多
    - └ `test_gas_extreme_is_bearish()` L127 — 🔓公开 Gas 极高 → 轻微偏空
    - └ `test_weak_signals_no_alignment()` L136 — 🔓公开 弱信号 → 中性对齐
    - └ `test_summary_line_format()` L147 — 🔓公开 摘要格式包含三类信号方向
    - └ `test_confidence_scores_override_technical()` L160 — 🔓公开 传入 confidence_scores 时优先使用其技术面分数
  - ⚡ `config()` L18 — 🔓公开
  - ⚡ `_agent1()` L22 — 🔐内部
  - ⚡ `_agent2()` L30 — 🔐内部
  - ⚡ `_onchain()` L38 — 🔐内部

### 📄 tests\test_strategy.py (121行) — 策略单元测试
  - 导入: `__future__`,`numpy`,`pandas`,`pytest`,`strategies.base`
  - ⚡ `sample_klines()` L15 — 🔓公开 生成 200 根模拟 K 线
  - ⚡ `test_all_strategies_can_be_created()` L32 — 🔓公开 测试所有策略可以成功实例化
  - ⚡ `test_ma_cross_generates_signals()` L42 — 🔓公开 MA 交叉策略生成信号
  - ⚡ `test_rsi_generates_signals()` L58 — 🔓公开 RSI 策略生成信号
  - ⚡ `test_breakout_generates_signals()` L69 — 🔓公开 突破策略生成信号
  - ⚡ `test_strategy_signal_types()` L79 — 🔓公开 信号类型必须是 Signal 枚举
  - ⚡ `test_strategy_metadata_has_name()` L88 — 🔓公开 策略元数据包含名称
  - ⚡ `test_stop_loss_triggered()` L95 — 🔓公开 止损应该触发 EXIT 信号
  - ⚡ `test_signal_consistency()` L109 — 🔓公开 BUY 后不能马上 BUY（有持仓时）

### 📄 tests\test_trade_executor_phase2.py (109行) — 测试 TradeExecutor 阶段二升级（限价单完整生命周期、滑点保护、部分成交）
  - 导入: `__future__`,`agents.config`,`agents.trade_executor`,`pathlib`,`pytest`,`sys`,`unittest.mock`
  - 🏛️ `TestExecuteLimit` L40
    - └async `test_limit_order_fills_normally()` L42 — 🔓公开 测试限价单正常成交流程
    - └async `test_limit_order_unfilled_cancel()` L57 — 🔓公开 测试限价单未成交→撤单→市价单兜底
    - └async `test_limit_order_partial_fill_cancel_remainder()` L73 — 🔓公开 测试限价单部分成交→撤销剩余→报告实际成交
    - └async `test_limit_order_slippage_too_high()` L87 — 🔓公开 测试限价单滑点超过上限→交易拒绝
    - └async `test_limit_order_place_order_fails()` L103 — 🔓公开 测试限价单下单失败→转市价单
  - ⚡ `config()` L17 — 🔓公开
  - ⚡ `okx_mock()` L22 — 🔓公开 模拟 OKXClient
  - ⚡ `executor()` L36 — 🔓公开

---
**统计**: 73 文件 | 14941 行代码 | 80 类 | 580 函数/方法
