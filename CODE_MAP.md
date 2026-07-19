# 项目代码映射
本文件由 `scripts/generate_code_map.py` 自动生成。
用途：快速定位代码位置，避免读取整个源文件。
> 生成时间: 2026-07-17 18:57
---
## 📁 ./
### 📄 agents\__init__.py (1行) — Three-Agent AI Trading System

### 📄 agents\agent1_technical.py (436行) — Agent 1 — 实时技术分析师
  - 导入: `__future__`,`agents.change_detector`,`agents.config`,`agents.event_bus`,`agents.kline_builder`,`agents.market_state`,`agents.okx_ws`,`asyncio`
  - 🏛️ `Agent1` L46 — Agent 1 — 技术分析师
    - └ `__init__()` L49 — 🔒内部
    - └async `run()` L90 — 🔓公开 启动 Agent 1 主循环
    - └async `stop()` L104 — 🔓公开 停止 Agent 1
    - └async `_on_reconnect()` L113 — 🔐内部 WebSocket 重连后触发：回填缺失 K 线数据
    - └ `_on_tick()` L124 — 🔐内部 处理 WebSocket ticker 消息
    - └ `_on_bar()` L141 — 🔐内部 处理新完成的 K 线
    - └async `_warmup()` L222 — 🔐内部 启动预热：本地 SQLite 缓存优先 → OKX REST API 保底 → 持久化
    - └ `get_indicators_table()` L350 — 🔓公开 返回多周期指标格式化表格（供 Agent 3 注入 DeepSeek prompt）
    - └ `get_market_state()` L358 — 🔓公开 返回当前市场状态分类（供 Agent 3 注入 DeepSeek prompt）
    - └ `_indicator_summary()` L366 — 🔐内部 生成一行指标摘要（供 current_activity 使用）
    - └ `get_status()` L391 — 🔓公开 返回当前状态（供监控用）
    - └ `get_recent_signal_stats()` L407 — 🔓公开 返回近期信号统计数据（供 Agent 4 复盘使用）

### 📄 agents\agent2_news.py (217行) — Agent 2 — 信息收集员（新闻 + 链上数据）
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`agents.onchain_collector`,`asyncio`,`datetime`,`frontend.utils.eth_news`,`logging`
  - 🏛️ `Agent2` L82 — Agent 2 — 信息收集员（新闻 + 链上数据）
    - └ `__init__()` L85 — 🔒内部
    - └async `run()` L112 — 🔓公开 启动 Agent 2 主循环 — 新闻 + 链上数据并发运行
    - └async `stop()` L126 — 🔓公开 停止 Agent 2
    - └async `_news_loop()` L133 — 🔐内部 新闻抓取主循环
    - └async `_fetch_and_score()` L151 — 🔐内部 抓取新闻 → 评分 → 推送
    - └ `get_status()` L200 — 🔓公开 读取
    - └ `get_recent_news()` L215 — 🔓公开 返回最近 N 条新闻（供 Agent 4 复盘使用）
  - ⚡ `_score_news_item()` L64 — 🔐内部 对一条新闻进行影响权重评分，返回 0~1 的分数

### 📄 agents\agent3_trader.py (1259行) — Agent 3 — 资深交易员
  - 导入: `__future__`,`agents.confidence_scorer`,`agents.config`,`agents.deepseek_caller`,`agents.event_bus`,`agents.risk_layer`,`agents.rule_decider`,`agents.rule_engine.base`
  - 🏛️ `TradingContext` L53 — Agent 3 构建的 DeepSeek 交易决策上下文
  - 🏛️ `Agent3` L93 — Agent 3 — 交易决策与执行
    - └ `__init__()` L96 — 🔒内部
    - └async `run()` L194 — 🔓公开 启动 Agent 3 主循环
    - └async `stop()` L216 — 🔓公开 停止
    - └async `_consume_a()` L220 — 🔐内部 消费 Queue A（技术面事件）
    - └async `_consume_b()` L248 — 🔐内部 消费 Queue B（新闻/基本面事件）
    - └async `_on_event()` L269 — 🔐内部 收到新事件后的处理
    - └async `_maybe_debounce()` L285 — 🔐内部 检查是否需要触发决策（攒批/超时）
    - └async `_idle_decision_loop()` L302 — 🔐内部 空闲定时决策循环 — 长时间无事件时强制触发规则评估
    - └async `_make_decision()` L347 — 🔐内部 执行一次完整的交易决策周期
    - └ `_build_context()` L686 — 🔐内部 从事件列表构建决策上下文（返回类型为 TradingContext TypedDict）
    - └ `_build_rule_engine_context()` L801 — 🔐内部 从 RiskManager 状态构建 RuleEngine 上下文 dict
    - └ `_summarize_agent1()` L848 — 🔐内部 智能构建技术面摘要 — 区分空闲触发、新闻驱动等场景
    - └ `_load_recent_trades_summary()` L865 — 🔐内部 Step 5: 加载最近 N 笔已平仓交易摘要（供决策上下文注入）
    - └ `_pos_state()` L894 — 🔐内部 持仓权威状态: (side, size, entry_price)
    - └ `_calc_pnl_pct()` L910 — 🔐内部 根据当前仓位计算浮盈/浮亏百分比（合约模式含杠杆放大）
    - └ `_suggested_size()` L926 — 🔐内部 根据决策信心（position_size_pct）+ 风控建议仓位大小
    - └ `_suggested_add_size()` L974 — 🔐内部 计算补仓追加量（基于剩余仓位空间）
    - └ `_should_add_to_position()` L1008 — 🔐内部 判断是否应该补仓
    - └async `_refresh_current_price()` L1037 — 🔐内部 后台循环：定期从 OKX API 获取最新价格并缓存
    - └async `_review_scheduler()` L1054 — 🔐内部 定时检查并生成复盘报告 + 推送微信
    - └ `_push_report_if_needed()` L1098 — 🔐内部 如果配置了推送且未推送，推送报告到微信
    - └ `_rewrite_report_file()` L1114 — 🔐内部 更新报告文件的 pushed 标记
    - └ `update_position()` L1127 — 🔓公开 更新当前持仓（供外部或 main.py 调用）
    - └ `_on_position_closed()` L1144 — 🔐内部 仓位监控器平仓后的回调 — 重置 Agent 3 的仓位状态
    - └ `_update_position_pnl()` L1155 — 🔐内部 用当前价格实时计算浮动盈亏，写入 _current_position
    - └ `get_status()` L1188 — 🔓公开 读取
  - ⚡ `_safe_float()` L41 — 🔐内部 安全地将值转换为 float，不可转换时返回 default

### 📄 agents\agent4_reviewer.py (588行) — Agent 4 — 复盘改进 Agent
  - 导入: `__future__`,`agents.agent1_technical`,`agents.agent2_news`,`agents.config`,`agents.deepseek_caller`,`agents.kline_builder`,`asyncio`,`data.db_manager`
  - 🏛️ `Agent4Reviewer` L91 — 复盘改进 Agent
    - └ `__init__()` L97 — 🔒内部
    - └async `notify_trade()` L141 — 🔓公开 Agent 3 完成一笔交易后调用，触发计数检查
    - └async `run()` L163 — 🔓公开 主循环（空循环，保持与 asyncio 任务体系兼容）
    - └async `stop()` L176 — 🔓公开 停止 Agent 4
    - └async `_run_review()` L182 — 🔐内部 执行一次完整复盘
    - └ `get_advisory()` L288 — 🔓公开 返回最新交易建议字符串
    - └ `_init_persistent_state()` L294 — 🔐内部 从 SQLite 恢复 trade_count / last_review_count（使用共享连接）
    - └ `_save_persistent_state()` L319 — 🔐内部 保存 trade_count / last_review_count 到 SQLite（使用共享连接）
    - └ `_load_recent_trades()` L338 — 🔐内部 从 SQLite 加载最近 N 笔已完成交易（使用共享连接）
    - └ `_collect_market_context()` L352 — 🔐内部 采集 KlineBuilder 行情数据（3m/5m/15m/1h/1d 最新 K 线）
    - └ `_collect_signal_stats()` L367 — 🔐内部 采集 Agent 1 信号统计
    - └ `_collect_recent_news()` L376 — 🔐内部 采集最近新闻
    - └ `_collect_onchain_snapshot()` L385 — 🔐内部 采集链上数据快照
    - └ `_build_review_prompt()` L403 — 🔐内部 构建完整的 DeepSeek 复盘 Prompt
    - └ `_validate_adjustment()` L499 — 🔐内部 边界校验 + 防抖
    - └ `_apply_adjustment()` L560 — 🔐内部 写入共享 config（锁保护）
    - └ `get_status()` L575 — 🔓公开 读取

### 📄 agents\change_detector.py (302行) — 信号变化检测器
  - 导入: `__future__`,`agents.helpers`,`logging`
  - 🏛️ `ChangeDetector` L39 — 变化检测器
    - └ `__init__()` L54 — 🔒内部
    - └ `set_cooldown()` L65 — 🔓公开 设置某类型信号的冷却时间
    - └ `check()` L69 — 🔓公开 检查指标变化，返回信号列表
    - └ `_check_macd()` L98 — 🔐内部 检查
    - └ `_check_kdj()` L135 — 🔐内部 KDJ 信号检测
    - └ `_check_boll()` L195 — 🔐内部 检查
    - └ `_save_state()` L244 — 🔐内部
    - └ `_can_push()` L251 — 🔐内部 检查某信号的冷却时间是否已过
    - └ `_get_config_cooldown()` L280 — 🔐内部 从 _cooldown_config 中查找匹配的冷却时间
    - └ `_signal()` L293 — 🔐内部

### 📄 agents\confidence_scorer.py (148行) — 多周期信心分 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`logging`
  - 🏛️ `ConfidenceScorer` L113 — 多周期信心分计算器
    - └ `__init__()` L121 — 🔒内部
    - └ `compute()` L125 — 🔓公开 计算综合信心分
  - ⚡ `score_signals()` L22 — 🔓公开 纯函数：将一组信号字典聚合为综合方向性评分。

### 📄 agents\config.py (235行) — Agent 系统配置 — 三 Agent 的独立参数
  - 导入: `__future__`,`config`
  - 🏛️ `AgentSystemConfig` L15 — 三 Agent 系统配置（与根 config.py 互补）
    - └ `from_root_config()` L209 — 🔓公开 从根 Config 创建 AgentSystemConfig，自动填充共享字段。

### 📄 agents\deepseek_caller.py (530行) — DeepSeek 交易决策调用器
  - 导入: `__future__`,`json`,`logging`,`openai`,`re`,`threading`
  - 🏛️ `DeepSeekTrader` L99 — DeepSeek 交易决策器
    - └ `__init__()` L107 — 🔒内部
    - └ `analyze()` L134 — 🔓公开 调用 DeepSeek 分析，返回交易决策
    - └ `analyze_trade_report()` L264 — 🔓公开 分析一段周期内的交易盈亏模式，识别盈利规律和亏损原因。
    - └ `analyze_review()` L340 — 🔓公开 用 DeepSeek 分析复盘数据（Agent 4 专用）
    - └ `_repair_json()` L370 — 🔐内部 尝试修复被截断/不完整的 JSON
    - └ `_parse_json_response()` L436 — 🔐内部 Extract JSON from DeepSeek response (general method)
    - └ `_parse_response()` L464 — 🔐内部 Parse DeepSeek response JSON
    - └ `_fallback_decision()` L508 — 🔐内部 Fallback when API fails - return hold
    - └ `get_stats()` L524 — 🔓公开 读取

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

### 📄 agents\helpers.py (22行) — Agent 系统共享工具函数
  - 导入: `__future__`
  - ⚡ `tf_minutes()` L7 — 🔓公开 将 timeframe 字符串转为分钟数

### 📄 agents\kline_builder.py (271行) — K 线构建器 — WebSocket tick → 1秒 K线 → 聚合到标准周期
  - 导入: `__future__`,`collections`,`datetime`,`logging`
  - 🏛️ `KlineBuilder` L19 — K 线构建器
    - └ `__init__()` L41 — 🔒内部
    - └ `add_tick()` L64 — 🔓公开 添加一个 tick 数据（每秒最多一个）
    - └ `_aggregate_sec_candle()` L113 — 🔐内部 将刚完成的 1s K 线聚合到各标准周期
    - └ `_check_new_sec_boundary()` L159 — 🔐内部 检查新秒级 K 线是否跨过标准周期边界
    - └ `_add_to_history()` L196 — 🔐内部 将完成的 K 线加入历史
    - └ `add_history_batch()` L202 — 🔓公开 批量注入已完成的历史 K 线（用于启动预热）
    - └ `get_current_candle()` L256 — 🔓公开 获取当前进行中的 K 线
    - └ `get_history()` L260 — 🔓公开 获取最近 N 根已完成 K 线
    - └ `get_all_history()` L265 — 🔓公开 获取所有周期的历史
    - └ `has_history()` L269 — 🔓公开 是否有足够的历史数据

### 📄 agents\market_state.py (379行) — Market State Classifier
  - 导入: `__future__`,`logging`
  - ⚡ `classify_market()` L31 — 🔓公开 多周期指标 → 市场状态分类
  - ⚡ `format_indicators_table()` L233 — 🔓公开 将多周期指标格式化为结构化表格文本（供 DeepSeek prompt 注入）

### 📄 agents\notifier.py (120行) — ServerChan 推送封装 — 通过 ServerChan 将交易报告推送到微信
  - 导入: `__future__`,`json`,`logging`,`urllib.error`,`urllib.parse`,`urllib.request`
  - 🏛️ `ServerChanNotifier` L20 — ServerChan 微信推送
    - └ `__init__()` L25 — 🔒内部
    - └ `push_report()` L28 — 🔓公开 推送交易报告到微信
    - └ `push_text()` L94 — 🔓公开 发送纯文本消息
    - └ `_send()` L98 — 🔐内部 调用 ServerChan API

### 📄 agents\okx_ws.py (190行) — OKX WebSocket 客户端 — 异步，自动重连
  - 导入: `__future__`,`asyncio`,`base64`,`datetime`,`hashlib`,`hmac`,`json`,`logging`
  - 🏛️ `OKXWebSocketClient` L23 — OKX WebSocket 客户端 — 支持自动重连与订阅管理
    - └ `__init__()` L28 — 🔒内部
    - └ `set_callbacks()` L51 — 🔓公开 设置消息和错误回调
    - └async `connect()` L65 — 🔓公开 建立 WebSocket 连接（自动重连循环）
    - └async `disconnect()` L112 — 🔓公开 断开 WebSocket 连接
    - └async `subscribe()` L120 — 🔓公开 订阅频道
    - └async `_subscribe_all()` L134 — 🔐内部 订阅所有已注册的频道
    - └async `_handle_message()` L141 — 🔐内部 处理收到的 WebSocket 消息
    - └async `__aenter__()` L155 — 🔒内部
    - └async `__aexit__()` L159 — 🔒内部
    - └ `_sign()` L163 — 🔐内部 OKX WebSocket 登录签名
    - └async `login()` L173 — 🔓公开 WebSocket 私有频道登录（Phase 2+ 需要）

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

### 📄 agents\position_monitor.py (612行) — 持仓监控器 — 止盈 / 止损 / 移动止损
  - 导入: `__future__`,`agents.config`,`asyncio`,`datetime`,`logging`
  - 🏛️ `PositionMonitor` L24 — 持仓监控器 — 止盈/止损/移动止损
    - └ `__init__()` L27 — 🔒内部
    - └ `update_position()` L86 — 🔓公开 更新持仓信息（由 Agent 3 在新开仓后调用）
    - └ `_record_close_pnl()` L197 — 🔐内部 记录现有持仓的平仓盈亏（反转或清仓时调用）
    - └ `clear_position()` L249 — 🔓公开 清空持仓（外部调用，如手动平仓后）
    - └ `restore_from_db()` L254 — 🔓公开 启动时从 SQLite 回放交易记录，重建持仓状态
    - └async `run()` L347 — 🔓公开 启动持仓监控主循环
    - └async `stop()` L361 — 🔓公开 停止监控
    - └async `_check_once()` L368 — 🔐内部 执行一次持仓检查
    - └async `_check_long()` L397 — 🔐内部 检查多头持仓
    - └async `_check_short()` L439 — 🔐内部 检查空头持仓
    - └ `_maker_fee_rate()` L480 — 🔐内部 Maker 费率（限价单吃深度）
    - └ `_taker_fee_rate()` L488 — 🔐内部 Taker 费率（市价单立即成交）
    - └async `_close_position()` L495 — 🔐内部 平仓（按市价卖出/买入）
    - └ `get_status()` L597 — 🔓公开 读取

### 📄 agents\review_generator.py (571行) — 复盘报告生成 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.deepseek_caller`,`data.db_manager`,`datetime`,`json`,`logging`,`os`
  - 🏛️ `ReviewGenerator` L31 — 复盘报告生成器
    - └ `__init__()` L34 — 🔒内部
    - └ `compute_monthly_stats()` L43 — 🔓公开 计算本月至今的统计
    - └ `compute_daily_stats()` L52 — 🔓公开 计算指定日期的统计
    - └ `compute_weekly_stats()` L58 — 🔓公开 计算过去 7 天的统计
    - └ `generate_daily_report()` L66 — 🔓公开 生成每日复盘报告并写入 JSON
    - └ `generate_weekly_report()` L100 — 🔓公开 生成每周复盘报告并写入 JSON
    - └ `get_recent_trades_summary()` L135 — 🔓公开 获取最近 N 笔已平仓交易的格式化摘要（供 DeepSeek 上下文注入）
    - └ `_get_conn()` L168 — 🔐内部 返回共享连接（由 DatabaseManager 缓存，不要 close）
    - └ `_compute_range_stats()` L176 — 🔐内部 计算一个时间范围内的交易统计
    - └ `extract_wins_and_losses()` L276 — 🔓公开 从 SQLite Row 列表中提取盈利和亏损交易详情
    - └ `_analyze_trades_with_deepseek()` L350 — 🔐内部 调用 DeepSeek 分析盈亏模式
    - └ `generate_monthly_report()` L387 — 🔓公开 生成月度复盘报告并写入 JSON
    - └ `_fallback_to_pnl()` L440 — 🔐内部 当 pnl_close 全部为 0 时使用 pnl 字段
    - └ `_compute_max_drawdown()` L489 — 🔐内部 计算最大回撤 (以百分比计)
    - └ `_build_report()` L521 — 🔐内部 构建完整的报告字典
    - └ `_generate_summary_text()` L531 — 🔐内部 生成可读的中文总结
    - └ `_write_report()` L554 — 🔐内部 写入 JSON 文件到 data/reports/{type}/

### 📄 agents\risk_layer.py (307行) — 风控管理器（RiskManager）— 交易后状态与记录
  - 导入: `__future__`,`agents.config`,`data.db_manager`,`datetime`,`json`,`logging`,`os`,`sqlite3`
  - 🏛️ `RiskManager` L26 — 风控管理器 — 三层风控
    - └ `__init__()` L29 — 🔒内部
    - └ `get_position()` L61 — 🔓公开 当前持仓 (side, size_eth)，side 为 None 表示无持仓
    - └ `report_api_error()` L78 — 🔓公开 报告 API 错误（用于熔断）
    - └ `reset_api_errors()` L86 — 🔓公开 重置 API 错误计数
    - └ `record_trade()` L93 — 🔓公开 记录一笔交易（写入内存 + SQLite）
    - └ `_update_pnl_close()` L131 — 🔐内部 平仓时更新对应开仓记录的 pnl_close
    - └ `_record_loss()` L146 — 🔐内部 记录亏损
    - └ `record_loss()` L151 — 🔓公开 公开的亏损记录接口，代理 _record_loss
    - └ `get_position_size_multiplier()` L155 — 🔓公开 返回仓位乘数（连亏后减半）
    - └ `_utc_to_cst_date()` L162 — 🔐内部 UTC 时间转北京时间（CST, UTC+8）的日期
    - └ `_check_date_reset()` L167 — 🔐内部 每日重置（北京时间午夜 00:00 CST = UTC 16:00）
    - └ `is_daily_limit_reached()` L182 — 🔓公开 已达每日交易上限？（含跨日自动重置）
    - └ `get_status()` L187 — 🔓公开 返回风控状态摘要
    - └ `_init_db()` L206 — 🔐内部 初始化 SQLite 数据库和表（使用 DatabaseManager 共享连接）
    - └ `_restore_daily_state()` L221 — 🔐内部 启动时从 DB 回放当日交易，重建内存风控状态
    - └ `_log_trade_sync()` L275 — 🔐内部 同步写入交易到 SQLite（含 Phase 4 P&L 列 + 手续费 + 信心度）

### 📄 agents\rule_decider.py (136行) — 规则决策器 — 替代 DeepSeek 的实时交易决策
  - 导入: `__future__`,`agents.confidence_scorer`,`agents.config`,`agents.event_bus`,`logging`
  - 🏛️ `RuleDecider` L36 — 基于多周期信号综合评分的规则交易决策器
    - └ `__init__()` L39 — 🔒内部
    - └ `decide()` L50 — 🔓公开 从事件缓冲区做出交易决策，返回与 deepseek.analyze 兼容的 dict
    - └ `get_stats()` L135 — 🔓公开 读取

### 📄 agents\rule_engine\__init__.py (52行)
  - 导入: `agents.rule_engine.base`,`agents.rule_engine.engine`,`agents.rule_engine.rules.execution`,`agents.rule_engine.rules.pre_trade`

### 📄 agents\rule_engine\base.py (215行) — RuleEngine 基础类型 — Rule, RuleResult, RuleCategory
  - 导入: `__future__`,`logging`
  - 🏛️ `RuleCategory` L20 — 规则分类 — 决定规则在交易周期中的执行阶段
  - 🏛️ `RuleResult` L35 — 单条规则的执行结果
    - └ `to_dict()` L51 — 🔓公开
    - └ `__bool__()` L59 — 🔒内部 RuleResult 可直接用于布尔判断
  - 🏛️ `Rule` L64 — 规则基类
    - └ `__init__()` L89 — 🔒内部
    - └ `enabled()` L100 — 🔓公开
    - └ `enabled()` L104 — 🔓公开
    - └ `is_async()` L108 — 🔓公开 是否需要异步执行（如调用外部 API 的规则）
    - └ `stats()` L113 — 🔓公开
    - └ `check()` L118 — 🔓公开 同步检查 — 子类重写此方法实现检查逻辑
    - └async `check_async()` L146 — 🔓公开 异步检查 — 调用外部 API 的规则重写此方法
    - └ `_ok()` L153 — 🔐内部 快捷方法：返回通过结果
    - └ `_reject()` L167 — 🔐内部 快捷方法：返回拒绝结果
    - └ `reset_stats()` L182 — 🔓公开 重置统计计数
  - 🏛️ `Ctx` L189 — context dict 键名常量

### 📄 agents\rule_engine\engine.py (282行) — RuleEngine — 可插拔规则引擎主模块
  - 导入: `__future__`,`agents.rule_engine.base`,`agents.rule_engine.rules.execution`,`agents.rule_engine.rules.pre_trade`,`datetime`,`logging`
  - 🏛️ `RuleEngine` L44 — 规则引擎 — 规则注册、调度、结果聚合
    - └ `__init__()` L51 — 🔒内部
    - └ `register()` L57 — 🔓公开 注册一条规则
    - └ `register_many()` L68 — 🔓公开 批量注册规则
    - └ `unregister()` L75 — 🔓公开 注销一条规则
    - └ `get_rule()` L93 — 🔓公开 按名称查找规则
    - └ `get_rules()` L97 — 🔓公开 获取规则列表，可按分类过滤
    - └ `get_enabled_rules()` L103 — 🔓公开 获取已启用的规则
    - └ `rule_count()` L109 — 🔓公开
    - └ `load_defaults()` L112 — 🔓公开 从工厂函数加载默认规则集
    - └async `check_pre_trade()` L126 — 🔓公开 执行所有交易前检查规则
    - └async `check_execution()` L140 — 🔓公开 执行所有执行中保护规则
    - └async `check_all()` L147 — 🔓公开 按顺序执行所有阶段的规则
    - └async `_run_category()` L158 — 🔐内部 运行一个分类下的所有规则
    - └ `all_pass()` L210 — 🔓公开 所有规则都通过？
    - └ `blocked_by()` L215 — 🔓公开 如果被阻断，返回第一个阻断规则的名称
    - └ `get_warnings()` L223 — 🔓公开 获取所有警告（passed=True 但 severity=warning）
    - └ `get_errors()` L228 — 🔓公开 获取所有错误（passed=False）
    - └ `enable_rule()` L234 — 🔓公开 启用一条规则
    - └ `disable_rule()` L242 — 🔓公开 禁用一条规则
    - └ `set_rule_enabled()` L250 — 🔓公开 设置规则启用状态
    - └ `get_status()` L258 — 🔓公开 返回引擎状态摘要
    - └ `reset_all_stats()` L275 — 🔓公开 重置所有规则的统计计数

### 📄 agents\rule_engine\rules\__init__.py (6行)

### 📄 agents\rule_engine\rules\execution.py (193行) — 执行中保护规则（Execution Rules）
  - 导入: `__future__`,`agents.rule_engine.base`,`asyncio`,`logging`
  - 🏛️ `MarketDepthRule` L17 — 市场深度检查（Phase 2）
    - └ `is_async()` L30 — 🔓公开
    - └async `check_async()` L33 — 🔓公开 检查
  - 🏛️ `SlippageRule` L126 — 滑点保护规则
    - └ `check()` L140 — 🔓公开
  - ⚡ `create_execution_rules()` L186 — 🔓公开 创建所有执行中保护规则实例

### 📄 agents\rule_engine\rules\pre_trade.py (359行) — 交易前检查规则（Pre-Trade Rules）
  - 导入: `__future__`,`agents.rule_engine.base`,`asyncio`,`datetime`,`logging`
  - 🏛️ `APIBreakerRule` L25 — API 熔断检查
    - └ `check()` L37 — 🔓公开
  - 🏛️ `TradeIntervalRule` L52 — 最小交易间隔检查
    - └ `check()` L64 — 🔓公开
  - 🏛️ `DailyTradeLimitRule` L82 — 每日交易次数上限检查
    - └ `check()` L94 — 🔓公开
  - 🏛️ `DailyLossLimitRule` L106 — 每日亏损上限检查
    - └ `check()` L118 — 🔓公开
  - 🏛️ `ConsecutiveLossRule` L130 — 连续亏损检查
    - └ `check()` L142 — 🔓公开
  - 🏛️ `HFTProtectionRule` L156 — HFT 防护 — 每小时交易频率上限
    - └ `check()` L169 — 🔓公开
  - 🏛️ `PositionSizeRule` L203 — 单笔上限检查
    - └ `check()` L215 — 🔓公开
  - 🏛️ `DirectionConflictRule` L227 — 方向冲突检查
    - └ `check()` L239 — 🔓公开
  - 🏛️ `VolatilityCheckRule` L263 — 价格波动检查（Phase 2）
    - └ `is_async()` L278 — 🔓公开
    - └async `check_async()` L281 — 🔓公开 检查
  - ⚡ `create_pre_trade_rules()` L338 — 🔓公开 创建所有交易前检查规则实例

### 📄 agents\signal_aligner.py (230行) — 三方信号对齐 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`logging`,`re`
  - 🏛️ `SignalAligner` L33 — 三方信号对齐器
    - └ `__init__()` L36 — 🔒内部
    - └ `align()` L39 — 🔓公开 计算三方信号对齐度
    - └ `_score_technical()` L91 — 🔐内部 从 ConfidenceScorer 输出或事件中提取技术面方向
    - └ `_score_news()` L118 — 🔐内部 从新闻事件中判断方向
    - └ `_score_onchain()` L147 — 🔐内部 从链上事件中判断方向
    - └ `_build_summary()` L206 — 🔐内部 生成中文摘要

### 📄 agents\status_writer.py (69行) — Agent 状态写入器 — 供 main.py 定期将 Agent 运行状态写入 JSON 文件
  - 导入: `__future__`,`datetime`,`json`,`logging`,`os`,`pathlib`
  - ⚡ `write_agent_status()` L19 — 🔓公开 将各 Agent 状态写入 JSON 文件（供 Streamlit 面板读取）
  - ⚡ `read_agent_status()` L58 — 🔓公开 读取 Agent 状态 JSON 文件（供 Streamlit 面板使用）
  - ⚡ `get_status_file_path()` L67 — 🔓公开 返回状态文件路径（供外部判断使用）

### 📄 agents\trade_executor.py (609行) — 交易执行器 — OKX 实盘下单封装
  - 导入: `__future__`,`agents.config`,`asyncio`,`datetime`,`logging`,`random`,`uuid`
  - 🏛️ `TradeExecutor` L25 — 交易执行器
    - └ `__init__()` L32 — 🔒内部 Args:
    - └ `_normalize_result()` L64 — 🔐内部 将 OKX 下单返回结果规范化为 dict
    - └async `execute_market()` L76 — 🔓公开 市价单执行
    - └async `_query_by_clord()` L161 — 🔐内部 按 clOrdId 查询订单（用于异常后的幂等恢复），查不到返回 None
    - └async `_fetch_fill()` L172 — 🔐内部 查询订单的真实成交价/量（市价单成交快，最多等 ~1.5s）
    - └async `execute_limit()` L209 — 🔓公开 限价单完整生命周期
    - └async `cancel_and_check()` L433 — 🔓公开 撤销订单并查询最终状态
    - └async `execute_safe()` L445 — 🔓公开 安全执行入口（自动处理size格式、限价→市价降级、滑点保护）
    - └ `get_stats()` L604 — 🔓公开 读取

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

### 📄 backtest\engine.py (234行) — Backtest engine with next-bar execution and conservative OHLC exits.
  - 导入: `__future__`,`backtest.metrics`,`config`,`logging`,`pandas`,`strategies.base`
  - 🏛️ `Trade` L19
  - 🏛️ `BacktestResult` L33
  - 🏛️ `BacktestEngine` L44 — Long-only spot backtester using no-lookahead signal execution.
    - └ `__init__()` L47 — 🔒内部
    - └ `run()` L51 — 🔓公开 主循环
    - └ `_strategy_params()` L124 — 🔐内部
    - └ `_combine_signals()` L133 — 🔐内部
    - └ `_entry_fill_price()` L156 — 🔐内部
    - └ `_exit_fill_price()` L164 — 🔐内部
    - └ `_intrabar_exit_price()` L171 — 🔐内部 Conservative OHLC exit model: stop loss wins if stop and target share a bar.
    - └ `_close_position()` L195 — 🔐内部
    - └ `run_all_strategies()` L206 — 🔓公开 主循环
    - └ `run_order_type_comparison()` L215 — 🔓公开 主循环
    - └ `report()` L223 — 🔓公开

### 📄 backtest\metrics.py (99行) — 回测指标计算
  - 导入: `__future__`,`numpy`,`pandas`,`scipy`
  - ⚡ `compute_metrics()` L12 — 🔓公开 计算回测绩效指标

### 📄 config.py (279行) — OKX 量化交易系统 — 配置管理
  - 导入: `__future__`,`json`,`os`,`pathlib`
  - 🏛️ `FuturesConfig` L19 — 合约（交割/永续）参数
    - └ `__post_init__()` L25 — 🔒内部
  - 🏛️ `ExchangeConfig` L37 — OKX 交易所连接配置
    - └ `is_readonly()` L50 — 🔓公开
  - 🏛️ `TradingConfig` L59 — 交易参数
  - 🏛️ `StrategyConfig` L77 — 策略池配置（多个策略并行）
  - 🏛️ `RiskConfig` L111 — 风控参数
  - 🏛️ `DataConfig` L132 — 数据存储配置
  - 🏛️ `AgentConfig` L148 — DeepSeek Agent 配置
    - └ `__post_init__()` L161 — 🔒内部
  - 🏛️ `NotificationConfig` L170 — 通知配置（P2 优化）
  - 🏛️ `Config` L194 — 系统总配置
    - └ `__post_init__()` L207 — 🔒内部
    - └ `save()` L212 — 🔓公开 保存配置到 JSON
    - └ `load()` L219 — 🔓公开 从 JSON 文件加载配置
    - └ `db_path()` L251 — 🔓公开
    - └ `is_live()` L255 — 🔓公开
  - ⚡ `_test_futures_config()` L269 — 🔐内部 快速验证合约配置的 __post_init__ 校验

### 📄 configs\__init__.py (0行)

### 📄 execution\__init__.py (11行)
  - 导入: `execution.ai_executor`,`execution.futures_paper`,`execution.paper`,`execution.trade_result`

### 📄 execution\ai_executor.py (755行) — AI 交易执行引擎
  - 导入: `__future__`,`config`,`datetime`,`execution.paper`,`indicators`,`logging`,`pandas`,`risk.rules`
  - 🏛️ `AIStrategyExecutor` L213 — AI 交易执行器 — 加载规则 JSON，逐根 K 线执行
    - └ `__init__()` L216 — 🔒内部
    - └ `on_bar()` L273 — 🔓公开 处理一根新 K 线，返回状态 dict
    - └ `get_state()` L385 — 🔓公开 返回当前完整状态（供前端渲染）
    - └ `reset()` L431 — 🔓公开 重置执行器状态
    - └ `_append_bar()` L459 — 🔐内部 追加 K 线到滚动缓冲区
    - └ `_get_indicators()` L472 — 🔐内部 从缓冲区计算指标
    - └ `_position_pnl_pct()` L480 — 🔐内部 计算当前盈亏百分比（正=盈利，负=亏损），方向感知
    - └ `_check_hard_stops()` L490 — 🔐内部 检查硬性止盈止损（方向感知）
    - └ `_check_volatility_contrarian()` L525 — 🔐内部 检查波动率触发条件，返回开仓方向 ("long"/"short") 或 None
    - └ `_check_cooldown()` L556 — 🔐内部 检查同方向冷却是否满足，返回 (ok, reason)
    - └ `_get_position_size()` L574 — 🔐内部 风险预算仓位计算
    - └ `_check_multi_trailing_stop()` L617 — 🔐内部 多级移动止盈检查
    - └ `_execute_partial_close()` L664 — 🔐内部 部分平仓 x% 的当前仓位
    - └ `_execute_entry()` L682 — 🔐内部 执行入场（支持多空）
    - └ `_execute_exit()` L724 — 🔐内部 执行出场（根据当前仓位方向）
  - ⚡ `_evaluate_condition()` L27 — 🔐内部 评估单个条件是否成立
  - ⚡ `_calc_indicators()` L173 — 🔐内部 对 DataFrame 计算所有常见指标，返回 {indicator_name: pd.Series}
  - ⚡ `_resolve_indicator_series()` L179 — 🔐内部 将条件中的 indicator 名映射到实际计算出的 Series
  - ⚡ `_check_conditions()` L184 — 🔐内部 检查一组条件，返回触发的条件列表

### 📄 execution\futures_paper.py (623行) — 合约模拟盘引擎
  - 导入: `__future__`,`config`,`datetime`,`execution.trade_result`,`json`,`logging`,`math`,`pandas`
  - 🏛️ `FuturesPosition` L79 — 合约持仓
    - └ `__post_init__()` L95 — 🔒内部
    - └ `liquidation_price()` L102 — 🔓公开
    - └ `is_active()` L108 — 🔓公开
    - └ `unrealized_pnl()` L113 — 🔓公开
    - └ `unrealized_pnl_pct()` L120 — 🔓公开
    - └ `roi_pct()` L125 — 🔓公开 总投资回报率 (含杠杆效果)
    - └ `margin_rate()` L131 — 🔓公开 当前保证金率 (%)，低于维持保证金率即触发强平
    - └ `is_liquidated()` L139 — 🔓公开 是否触发强平
    - └ `to_dict()` L147 — 🔓公开
  - 🏛️ `FuturesAccount` L167 — 合约账户 — 管理钱包余额、持仓、保证金
    - └ `__init__()` L170 — 🔒内部
    - └ `__repr__()` L181 — 🔒内部
    - └ `_fee_rate()` L188 — 🔐内部 限价单挂单成交走 maker 费率，否则走 taker 费率
    - └ `used_margin()` L195 — 🔓公开
    - └ `available_balance()` L199 — 🔓公开
    - └ `total_equity()` L203 — 🔓公开 总权益 = 钱包余额 + 未实现盈亏
    - └ `total_realized_pnl()` L209 — 🔓公开
    - └ `is_flat()` L214 — 🔓公开
    - └ `position_side()` L218 — 🔓公开
    - └ `update_price()` L225 — 🔓公开 更新
    - └ `open_long()` L237 — 🔓公开 开多 / 加多
    - └ `open_short()` L287 — 🔓公开 开空 / 加空
    - └ `close_position()` L338 — 🔓公开 平仓 (多仓卖出 / 空仓买入)
    - └ `close_all()` L390 — 🔓公开 全额平仓
    - └ `liquidate()` L398 — 🔓公开 强平 — 剩余资产归零 / 按破产价处理
    - └ `to_dict()` L428 — 🔓公开
    - └ `report()` L449 — 🔓公开 打印账户摘要 (CLI)
  - 🏛️ `FuturesPaperEngine` L470 — 合约模拟盘引擎 — 逐根 K 线驱动
    - └ `__init__()` L473 — 🔒内部
    - └ `run_bar()` L486 — 🔓公开 处理一根新 K 线。
    - └ `_execute_signal()` L564 — 🔐内部 将策略信号映射为合约操作
    - └ `run()` L616 — 🔓公开 CLI 占位
  - ⚡ `_mmr_for_leverage()` L41 — 🔐内部 根据杠杆倍数返回 OKX ETH-USDT 维持保证金率
  - ⚡ `calc_liquidation_price()` L54 — 🔓公开 计算逐仓强平价 (USDT 本位永续)

### 📄 execution\order.py (213行) — 🔧 P2: 订单类型分析模块
  - 导入: `__future__`,`logging`,`pandas`,`random`,`strategies.base`
  - 🏛️ `OrderSimulation` L23 — 订单模拟结果
  - 🏛️ `OrderTypeComparison` L34 — 两种订单类型的完整对比
  - ⚡ `simulate_market_order()` L50 — 🔓公开 模拟市价单
  - ⚡ `simulate_limit_order()` L75 — 🔓公开 模拟限价单
  - ⚡ `simulate_limit_orders()` L128 — 🔓公开 用限价单模拟替代市价单
  - ⚡ `compare_order_types()` L169 — 🔓公开 对比两种订单类型

### 📄 execution\paper.py (337行) — 本地模拟盘引擎
  - 导入: `__future__`,`config`,`datetime`,`execution.trade_result`,`json`,`logging`,`pandas`,`pathlib`
  - 🏛️ `PaperAccount` L24 — 模拟账户 — 支持多空双向
    - └ `__init__()` L27 — 🔒内部
    - └ `is_flat()` L42 — 🔓公开 是否空仓（多空均为空）
    - └ `equity()` L47 — 🔓公开 当前总权益 = 现金 + 多头市值 + 空头未实现盈亏
    - └ `_short_unrealized_pnl()` L51 — 🔐内部 空头未实现盈亏
    - └ `unrealized_pnl()` L59 — 🔓公开 总未实现盈亏 (USD)
    - └ `total_realized_pnl()` L66 — 🔓公开 所有已平仓交易的总盈亏
    - └ `unrealized_pnl_pct()` L71 — 🔓公开 未实现盈亏 (%) — 根据当前持仓类型计算
    - └ `update_price()` L81 — 🔓公开 更新最新价格，记录权益历史
    - └ `execute_buy()` L93 — 🔓公开 执行买入，返回 trade dict
    - └ `execute_sell()` L115 — 🔓公开 执行卖出，返回 trade dict
    - └ `execute_short()` L143 — 🔓公开 执行开空（卖空），返回 trade dict
    - └ `execute_cover()` L169 — 🔓公开 执行平空（买入平仓），支持部分平仓
    - └ `to_dict()` L196 — 🔓公开 序列化为 JSON 友好 dict（给前端用）
    - └ `save_state()` L216 — 🔓公开
    - └ `load_state()` L230 — 🔓公开
    - └ `report()` L244 — 🔓公开
  - 🏛️ `PaperEngine` L259 — 模拟盘引擎 — 逐根 K 线驱动
    - └ `__init__()` L262 — 🔒内部
    - └ `run_bar()` L268 — 🔓公开 处理一根新 K 线，执行完整模拟盘循环。
    - └ `run()` L330 — 🔓公开 CLI 模式占位 — 前端驱动时用 run_bar

### 📄 execution\trade_result.py (74行) — 统一交易结果（trade dict）定义 — 所有模拟执行层的返回形状
  - 导入: `__future__`,`datetime`
  - ⚡ `make_trade()` L37 — 🔓公开 构造成交 trade dict（core 字段 + 账户层附加字段）
  - ⚡ `reject_trade()` L60 — 🔓公开 构造拒绝 trade dict（未成交，note 为原因）
  - ⚡ `is_rejected()` L72 — 🔓公开 模拟层 trade dict 是否为拒绝（未成交）

### 📄 indicators.py (316行) — 共享技术指标计算 — 纯 pandas 函数，无外部依赖
  - 导入: `__future__`,`pandas`
  - ⚡ `calc_rsi()` L14 — 🔓公开 RSI 指标
  - ⚡ `calc_sma()` L25 — 🔓公开 简单移动平均
  - ⚡ `calc_ema()` L30 — 🔓公开 指数移动平均
  - ⚡ `calc_macd()` L35 — 🔓公开 MACD 指标，返回 {macd, signal, histogram}
  - ⚡ `calc_bollinger()` L47 — 🔓公开 布林带，返回 {middle, upper, lower}
  - ⚡ `calc_price_change()` L60 — 🔓公开 价格变动百分比
  - ⚡ `calc_indicators()` L65 — 🔓公开 对 DataFrame 计算所有常见指标
  - ⚡ `resolve_indicator()` L125 — 🔓公开 将条件中的 indicator 名映射到实际计算出的 Series
  - ⚡ `calc_macd_summary()` L153 — 🔓公开 计算 MACD 指标，返回最新值摘要。
  - ⚡ `calc_kdj_summary()` L201 — 🔓公开 计算 KDJ 随机指标，返回最新值摘要。
  - ⚡ `calc_boll_summary()` L257 — 🔓公开 计算布林带（Bollinger Bands）指标，返回最新值摘要。

### 📄 main.py (564行) — OKX Quant Agent — 三 Agent 异步事件驱动交易系统
  - 导入: `__future__`,`agents.agent1_technical`,`agents.agent2_news`,`agents.agent3_trader`,`agents.agent4_reviewer`,`agents.config`,`agents.deepseek_caller`,`agents.event_bus`
  - ⚡ `_pid_belongs_to_agent()` L36 — 🔐内部 确认 PID 对应的进程是否是本 agent（main.py），防止 PID 复用误杀。
  - ⚡ `_acquire_pid_lock()` L62 — 🔐内部 用原子性文件创建 (O_EXCL) 实现 PID 锁，消除 TOCTOU 竞争条件。
  - ⚡ `_release_pid_lock()` L114 — 🔐内部 退出时清理 PID 文件（仅当是自己写的才删）。
  - ⚡ `setup_logging()` L135 — 🔓公开 配置日志
  - ⚡ `_install_signal_handlers()` L156 — 🔐内部 Install signal handlers — works on both Unix and Windows.
  - ⚡ async `_preflight_check()` L168 — 🔐内部 启动前检查：把"第一笔真单才炸"提前到启动即报错
  - ⚡ async `main()` L206 — 🔓公开
  - ⚡ async `_status_reporter()` L443 — 🔐内部 定期报告系统状态并写入 JSON（每 5s，保证前端实时更新）

### 📄 okx_client.py (356行) — OKX REST API 客户端
  - 导入: `__future__`,`base64`,`config`,`datetime`,`hashlib`,`hmac`,`httpx`,`json`
  - 🏛️ `OKXClient` L27 — OKX API 客户端 — 第一版只接公开行情 REST
    - └ `__init__()` L30 — 🔒内部
    - └ `_request()` L40 — 🔐内部 统一 HTTP 请求入口，自动重试 transient 网络错误。
    - └ `_check_api_response()` L83 — 🔐内部 检查 OKX API 返回码
    - └ `get_klines()` L90 — 🔓公开 获取 K 线数据
    - └ `get_ticker()` L117 — 🔓公开 获取最新 ticker
    - └ `get_balance()` L126 — 🔓公开 查询账户余额（仅 Read 权限）
    - └ `get_positions()` L136 — 🔓公开 查询持仓
    - └ `place_order()` L149 — 🔓公开 下单（需要 Trade 权限）
    - └ `cancel_order()` L184 — 🔓公开 撤销订单
    - └ `get_order()` L200 — 🔓公开 查询订单状态
    - └ `get_order_book()` L221 — 🔓公开 获取订单簿深度
    - └ `get_taker_volume()` L241 — 🔓公开 获取吃单量（买卖比）
    - └ `get_funding_rate()` L263 — 🔓公开 获取永续合约资金费率
    - └ `_normalize_order_data()` L283 — 🔐内部 标准化订单 API 返回值
    - └ `_sign()` L293 — 🔐内部 OKX 签名
    - └ `_timestamp()` L310 — 🔐内部
    - └ `_tf_to_bar()` L315 — 🔐内部
    - └ `_parse_klines()` L324 — 🔐内部 OKX 原始 K 线转 dict
    - └ `_parse_ticker()` L340 — 🔐内部
    - └ `close()` L355 — 🔓公开

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

### 📄 scripts\check_status.py (46行) — Get detailed status - writes to file to avoid terminal encoding issues
  - 导入: `json`

### 📄 scripts\edit_prompt.py (56行) — Update DeepSeek system prompt
  - 导入: `re`,`sys`

### 📄 scripts\generate_code_map.py (260行) — 生成项目代码映射索引 CODE_MAP.md
  - 导入: `__future__`,`ast`,`os`,`pathlib`,`sys`
  - ⚡ `get_module_docstring()` L37 — 🔓公开 提取模块/类/函数的文档字符串第一行
  - ⚡ `describe_function()` L47 — 🔓公开 从函数名和文档推断用途标签
  - ⚡ `describe_class()` L86 — 🔓公开
  - ⚡ `extract_file_info()` L93 — 🔓公开 提取单个 Python 文件的代码结构
  - ⚡ `build_markdown()` L163 — 🔓公开 组装 CODE_MAP.md
  - ⚡ `main()` L234 — 🔓公开

### 📄 scripts\smoke_pages.py (28行) — 临时冒烟脚本：AppTest 逐页加载，报告异常
  - 导入: `pathlib`,`streamlit.testing.v1`,`sys`

### 📄 strategies\__init__.py (0行)

### 📄 strategies\base.py (140行) — 策略基类
  - 导入: `__future__`,`pandas`,`strategies.breakout`,`strategies.ma_cross`,`strategies.macd_agent`,`strategies.rsi_mean_reversion`
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
  - ⚡ `create_strategy()` L132 — 🔓公开 工厂方法：创建策略实例

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

### 📄 strategies\macd_agent.py (242行) — 策略 4: MACD 多周期共振（与实盘 Agent 决策链路同源）
  - 导入: `__future__`,`agents.confidence_scorer`,`agents.config`,`logging`,`numpy`,`pandas`,`strategies.base`
  - 🏛️ `MACDAgentStrategy` L132 — MACD 多周期共振策略 — 实盘 RuleDecider 的回测同源版
    - └ `__init__()` L135 — 🔒内部
    - └ `description()` L154 — 🔓公开
    - └ `_infer_base_tf()` L161 — 🔐内部
    - └ `_build_events()` L168 — 🔐内部 基础周期 + 高周期合成，合并全部信号事件
    - └ `generate_signals()` L189 — 🔓公开
  - ⚡ `_tf_events()` L35 — 🔐内部 计算单个周期的逐 bar 信号事件（向量化指标 + 逐 bar 冷却）。

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

### 📄 tests\test_agent3_phase2.py (458行) — 测试 Agent 3 阶段二集成——风控注入、RuleEngine 两阶段检查、仓位通知
  - 导入: `__future__`,`agents.agent3_trader`,`agents.config`,`agents.event_bus`,`agents.rule_engine.base`,`datetime`,`pathlib`,`pytest`
  - 🏛️ `TestRiskStatusInjection` L130
    - └ `test_build_context_includes_risk_status()` L131 — 🔓公开 验证 _build_context 注入风控状态
    - └ `test_build_context_with_events()` L150 — 🔓公开 验证上下文包含技术和新闻事件
    - └ `test_build_context_empty()` L165 — 🔓公开 无事件时上下文包含默认值
  - 🏛️ `TestRuleDeciderContext` L173
    - └async `test_decide_receives_current_price()` L175 — 🔓公开 _make_decision 把事件中的当前价格传给 RuleDecider
  - 🏛️ `TestRuleEngineChecks` L190 — 风控检查统一走 RuleEngine（pre-trade / execution 两阶段）
    - └ `_buy_decision()` L194 — 🔐内部
    - └ `_fill_buffer()` L203 — 🔐内部
    - └async `test_pre_trade_check_called()` L211 — 🔓公开 决策前先过 RuleEngine pre-trade 检查
    - └async `test_execution_check_called()` L224 — 🔓公开 决策买入后过 RuleEngine execution 检查（上下文含方向/数量）
    - └async `test_pre_trade_rule_blocks_trade()` L240 — 🔓公开 pre-trade 规则拒绝（如波动过大）→ 不交易、不进入决策
    - └async `test_execution_rule_blocks_trade()` L256 — 🔓公开 execution 规则拒绝（如深度不足）→ 不交易
    - └async `test_prefer_limit_passed_to_executor()` L271 — 🔓公开 market_depth 结果的 prefer_limit 被传递给执行器
  - 🏛️ `TestPositionMonitorNotify` L290
    - └async `test_position_monitor_notified()` L292 — 🔓公开 交易成功后通知 PositionMonitor
    - └async `test_position_monitor_not_called_on_failure()` L331 — 🔓公开 交易失败时不通知 PositionMonitor
    - └async `test_no_monitor_no_error()` L364 — 🔓公开 没有 PositionMonitor 也不会报错
  - 🏛️ `TestSlTpDirectionValidation` L392 — 决策给出方向错误的 SL/TP 时回退默认值（防开仓即触发止损）
    - └async `test_wrong_direction_sltp_falls_back_to_defaults()` L396 — 🔓公开 多头 SL>入场 / TP<入场 → 回退配置默认百分比
    - └async `test_correct_direction_sltp_kept()` L430 — 🔓公开 方向正确的 SL/TP 原样保留
  - ⚡ `config()` L20 — 🔓公开
  - ⚡ `event_bus()` L25 — 🔓公开
  - ⚡ `mock_deepseek()` L30 — 🔓公开
  - ⚡ `mock_risk_manager()` L46 — 🔓公开
  - ⚡ `mock_executor()` L66 — 🔓公开
  - ⚡ `mock_root_config()` L76 — 🔓公开
  - ⚡ `mock_rule_decider()` L83 — 🔓公开
  - ⚡ `mock_rule_engine()` L100 — 🔓公开 RuleEngine mock：默认全部通过，market_depth 返回 prefer_limit=False
  - ⚡ `agent3()` L116 — 🔓公开

### 📄 tests\test_agent4_reviewer.py (245行) — 测试 Agent 4 — 复盘改进 Agent
  - 导入: `__future__`,`agents.agent4_reviewer`,`agents.config`,`agents.deepseek_caller`,`json`,`os`,`pathlib`,`pytest`
  - ⚡ `_make_db()` L21 — 🔐内部 创建临时数据库并写入交易（含 Phase 4 字段）
  - ⚡ `_make_reviewer()` L58 — 🔐内部 创建测试用的 Agent4Reviewer 实例
  - ⚡ `test_init()` L89 — 🔓公开 Agent4Reviewer 初始化后状态正确
  - ⚡ async `test_notify_trade_under_threshold()` L99 — 🔓公开 交易数未达阈值时不会触发复盘
  - ⚡ async `test_notify_trade_triggers_review()` L109 — 🔓公开 交易数达阈值后触发复盘
  - ⚡ async `test_notify_trade_triggers_multiple_reviews()` L119 — 🔓公开 每满 5 笔触发一次复盘，不重置计数
  - ⚡ `test_load_recent_trades()` L137 — 🔓公开 能从 SQLite 加载最近交易
  - ⚡ `test_load_recent_trades_empty_db()` L153 — 🔓公开 空数据库返回空列表
  - ⚡ `test_validate_unknown_param()` L162 — 🔓公开 未知参数名被拒绝
  - ⚡ `test_validate_out_of_bounds()` L170 — 🔓公开 超出安全范围的参数被拒绝
  - ⚡ `test_validate_risk_param_strict()` L181 — 🔓公开 风险参数只能降低不能提高
  - ⚡ `test_validate_debounce()` L194 — 🔓公开 同一参数最小修改间隔
  - ⚡ `test_validate_no_actual_change()` L207 — 🔓公开 值没变化时跳过
  - ⚡ `test_param_bounds_completeness()` L219 — 🔓公开 _PARAM_BOUNDS 表包含所有 config 可调字段，无遗漏
  - ⚡ `test_review_prompt_format()` L227 — 🔓公开 Prompt 模板能正确格式化

### 📄 tests\test_backtest.py (252行) — 回测引擎测试
  - 导入: `__future__`,`backtest.engine`,`backtest.metrics`,`config`,`numpy`,`pandas`,`pytest`,`strategies.base`
  - 🏛️ `ScriptedStrategy` L135
    - └ `__init__()` L136 — 🔒内部
    - └ `generate_signals()` L140 — 🔓公开
  - ⚡ `test_config()` L18 — 🔓公开 简化的测试配置
  - ⚡ `price_data()` L30 — 🔓公开 生成带趋势的模拟 K 线数据
  - ⚡ `test_backtest_engine_initializes()` L51 — 🔓公开
  - ⚡ `test_backtest_returns_result()` L56 — 🔓公开
  - ⚡ `test_backtest_has_metrics()` L66 — 🔓公开
  - ⚡ `test_backtest_metrics_are_reasonable()` L77 — 🔓公开 指标应该在合理范围内
  - ⚡ `test_backtest_trades_have_required_fields()` L91 — 🔓公开
  - ⚡ `test_backtest_includes_benchmark()` L103 — 🔓公开
  - ⚡ `test_multiple_strategies()` L109 — 🔓公开 测试多策略回测
  - ⚡ `test_order_type_comparison()` L118 — 🔓公开 订单类型对比测试
  - ⚡ `test_backtest_reproducible()` L126 — 🔓公开 相同数据应该产生相同结果
  - ⚡ `_scripted_engine()` L147 — 🔐内部
  - ⚡ `_ohlc_frame()` L162 — 🔐内部
  - ⚡ `test_signal_executes_at_next_bar_open()` L167 — 🔓公开
  - ⚡ `test_open_position_is_liquidated_at_end_of_data()` L184 — 🔓公开
  - ⚡ `test_intrabar_stop_loss_uses_kline_low()` L199 — 🔓公开
  - ⚡ `test_unfilled_limit_order_does_not_become_market_order()` L214 — 🔓公开
  - ⚡ `test_multiple_strategies_use_weighted_signal_votes()` L226 — 🔓公开
  - ⚡ `test_sharpe_uses_equity_curve_frequency()` L243 — 🔓公开

### 📄 tests\test_change_detector.py (75行)
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

### 📄 tests\test_futures_paper.py (490行) — 合约模拟盘引擎测试
  - 导入: `__future__`,`config`,`datetime`,`execution.futures_paper`,`math`,`pandas`,`pytest`,`strategies.base`
  - 🏛️ `TestLiquidationPrice` L28
    - └ `test_long_10x()` L29 — 🔓公开 10x 多仓强平价
    - └ `test_short_10x()` L35 — 🔓公开 10x 空仓强平价
    - └ `test_long_125x()` L41 — 🔓公开 125x 多仓强平价 (mmr = 5%)
    - └ `test_short_custom_mmr()` L48 — 🔓公开 自定义维持保证金率
  - 🏛️ `TestMMR` L60
    - └ `test_low_leverage()` L61 — 🔓公开
    - └ `test_medium_leverage()` L65 — 🔓公开
    - └ `test_high_leverage()` L68 — 🔓公开
    - └ `test_max_leverage()` L72 — 🔓公开
  - 🏛️ `TestFuturesPosition` L81
    - └ `long_pos()` L83 — 🔓公开
    - └ `short_pos()` L95 — 🔓公开
    - └ `test_long_unrealized_pnl()` L106 — 🔓公开 多仓未实现盈亏
    - └ `test_short_unrealized_pnl()` L111 — 🔓公开 空仓未实现盈亏
    - └ `test_long_pnl_pct()` L116 — 🔓公开 多仓盈亏百分比 (杠杆放大)
    - └ `test_short_pnl_pct()` L121 — 🔓公开 空仓盈亏百分比
    - └ `test_margin_rate_normal()` L126 — 🔓公开 正常保证金率
    - └ `test_margin_rate_loss()` L132 — 🔓公开 亏损时保证金率下降
    - └ `test_is_liquidated_long()` L138 — 🔓公开 多仓强平检查
    - └ `test_is_liquidated_short()` L144 — 🔓公开 空仓强平检查
    - └ `test_is_active()` L151 — 🔓公开 持仓是否有效
  - 🏛️ `TestFuturesAccount` L164
    - └ `account()` L166 — 🔓公开
    - └ `test_init()` L169 — 🔓公开
    - └ `test_open_long()` L177 — 🔓公开 开多 — 占用保证金，钱包扣除手续费
    - └ `test_open_short()` L201 — 🔓公开 开空
    - └ `test_open_long_then_close()` L215 — 🔓公开 开多 → 平多 → 计算盈亏
    - └ `test_open_short_then_close()` L227 — 🔓公开 开空 → 平空 → 计算盈亏
    - └ `test_liquidation_long()` L237 — 🔓公开 多仓强平 — 损失全部保证金
    - └ `test_liquidation_short()` L247 — 🔓公开 空仓强平
    - └ `test_close_all_no_position()` L256 — 🔓公开 无仓位时平仓返回 None
    - └ `test_multiple_trades()` L260 — 🔓公开 多次交易记录
    - └ `test_to_dict()` L270 — 🔓公开 序列化 — 含所有关键字段
    - └ `test_repr()` L283 — 🔓公开 字符串表示
  - 🏛️ `MockStrategy` L296 — 模拟策略 — 返回预设信号
    - └ `__init__()` L299 — 🔒内部
    - └ `on_bar()` L307 — 🔓公开 回调
    - └ `reset_buffer()` L312 — 🔓公开
    - └ `get_bar_buffer()` L315 — 🔓公开 读取
  - 🏛️ `TestFuturesPaperEngine` L319
    - └ `cfg()` L321 — 🔓公开
    - └ `bar()` L330 — 🔓公开
    - └ `test_engine_init()` L336 — 🔓公开
    - └ `test_run_bar_buy_signal()` L342 — 🔓公开 BUY 信号 → 开多
    - └ `test_run_bar_sell_signal()` L353 — 🔓公开 SELL 信号 → 开空
    - └ `test_run_bar_hold_signal()` L362 — 🔓公开 HOLD 信号 → 无交易
    - └ `test_run_bar_exit_signal()` L371 — 🔓公开 开多后 EXIT 信号 → 平多
    - └ `test_buy_when_short_flips()` L388 — 🔓公开 BUY 信号 + 空仓 → 先平空再开多
    - └ `test_sell_when_long_flips()` L405 — 🔓公开 SELL 信号 + 多仓 → 先平多再开空
    - └ `test_liquidation_in_run_bar()` L422 — 🔓公开 run_bar 中触发强平
    - └ `test_state_dict_keys()` L440 — 🔓公开 run_bar 返回的状态 dict 包含所有必要字段
  - 🏛️ `TestFuturesConfig` L472
    - └ `test_default_config()` L473 — 🔓公开
    - └ `test_invalid_leverage()` L479 — 🔓公开
    - └ `test_invalid_margin_mode()` L483 — 🔓公开
    - └ `test_integrated_in_main_config()` L487 — 🔓公开

### 📄 tests\test_kline_builder.py (74行) — KlineBuilder 单元测试
  - 导入: `agents.kline_builder`,`sys`
  - ⚡ `test_basic_tick_to_15m()` L6 — 🔓公开
  - ⚡ `test_multiple_timeframes()` L47 — 🔓公开

### 📄 tests\test_main_pid.py (105行) — 测试 main.py 的 PID 锁进程身份核实（防 PID 复用误杀）
  - 导入: `__future__`,`main`,`pathlib`,`pytest`,`subprocess`,`sys`,`unittest.mock`
  - 🏛️ `TestPidBelongsToAgent` L24
    - └ `test_agent_process_detected()` L25 — 🔓公开 命令行含 main.py → 认定为本 agent
    - └ `test_unrelated_process_rejected()` L31 — 🔓公开 命令行不含 main.py → 不是本 agent（PID 被复用）
    - └ `test_nonexistent_pid()` L37 — 🔓公开 进程不存在（命令行为空）→ False
    - └ `test_query_failure_returns_none()` L42 — 🔓公开 查询失败 → None（调用方应保守不杀）
    - └ `test_timeout_returns_none()` L47 — 🔓公开 查询超时 → None
  - 🏛️ `TestPreflightCheck` L54 — live 模式启动前检查
    - └ `_root_config()` L57 — 🔐内部
    - └async `test_non_live_mode_skips()` L68 — 🔓公开 非 live 模式直接通过
    - └async `test_missing_credentials_rejected()` L76 — 🔓公开 live 缺凭证 → 拒绝启动
    - └async `test_read_permission_rejected()` L83 — 🔓公开 live 但权限 read（会静默模拟成交）→ 拒绝启动
    - └async `test_balance_check_failure_rejected()` L90 — 🔓公开 账户查询失败（凭证无效/网络断）→ 拒绝启动
    - └async `test_all_good_passes()` L99 — 🔓公开 凭证齐全 + 连通正常 → 通过
  - ⚡ `_mock_run()` L16 — 🔐内部 构造 subprocess.run 的假返回

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

### 📄 tests\test_position_monitor.py (574行) — 测试持仓监控器——止盈、止损、移动止损
  - 导入: `__future__`,`agents.config`,`agents.position_monitor`,`agents.risk_layer`,`data.db_manager`,`datetime`,`json`,`pathlib`
  - 🏛️ `TestPositionMonitor` L62
    - └async `test_stop_loss_triggered()` L64 — 🔓公开 价格跌破止损 → 触发止损卖出
    - └async `test_take_profit_triggered()` L88 — 🔓公开 价格涨到止盈 → 触发止盈卖出
    - └async `test_trailing_stop_activates()` L108 — 🔓公开 浮盈达到 3% 后激活移动止损，止损位上移
    - └async `test_trailing_stop_triggers()` L133 — 🔓公开 移动止损激活后，价格回落到新止损位 → 触发卖出
    - └async `test_no_position_no_action()` L157 — 🔓公开 无持仓时不做任何操作
    - └async `test_short_position_take_profit_and_stop()` L175 — 🔓公开 空头仓位：止盈（价格跌）和止损（价格涨）方向正确
    - └async `test_short_trailing_stop_activates()` L198 — 🔓公开 空头：价格下跌触发移动止损激活
    - └async `test_short_trailing_stop_triggers()` L218 — 🔓公开 空头移动止损激活后价格回升 → 触发
    - └async `test_status_report()` L241 — 🔓公开 get_status 返回正确统计
    - └async `test_accumulate_position()` L262 — 🔓公开 同方向补仓：累加 size + 加权均价
    - └async `test_accumulate_reverse_direction()` L294 — 🔓公开 补仓时反方向不应累加，应触发反转
    - └async `test_accumulate_then_close_pnl()` L319 — 🔓公开 补仓后平仓：PnL 计算中累计开仓费用正确
    - └async `test_accumulate_maker_taker_fees()` L359 — 🔓公开 混合 maker/taker 费率累计正确
    - └async `test_accumulate_non_existent_position_falls_back()` L393 — 🔓公开 无持仓时 accumulate=True 应退化为新开仓行为
  - 🏛️ `TestCloseRetryAndRestore` L413 — 平仓失败保留状态重试 / 启动状态恢复（P0 修复的回归锁定）
    - └async `test_close_failure_keeps_position_and_retries()` L417 — 🔓公开 平仓 3 次全失败 → 保留持仓、不记账、计数失败；下次成功才清算
    - └async `test_close_uses_close_only_no_reversal()` L458 — 🔓公开 平仓必须走 close_only 通道，防止止损变反向开仓
    - └ `test_restore_from_db_rebuilds_position()` L477 — 🔓公开 重启后从 trades 表回放恢复未平仓持仓
    - └ `test_restore_preserves_persisted_sltp()` L539 — 🔓公开 开仓时入库的 SL/TP 在重启恢复后原样还原（不回退默认值）
  - ⚡ `config()` L17 — 🔓公开
  - ⚡ `mock_risk_manager()` L26 — 🔓公开
  - ⚡ `mock_executor()` L34 — 🔓公开
  - ⚡ `mock_okx_client()` L56 — 🔓公开

### 📄 tests\test_review_generator.py (268行) — 测试复盘报告生成器 — Phase 4
  - 导入: `__future__`,`agents.config`,`agents.review_generator`,`datetime`,`gc`,`json`,`os`,`pathlib`
  - 🏛️ `TestReviewGenerator` L81
    - └ `test_empty_db()` L83 — 🔓公开 空数据库返回零值
    - └ `test_win_rate_calculation()` L92 — 🔓公开 胜率计算
    - └ `test_max_drawdown()` L107 — 🔓公开 最大回撤计算
    - └ `test_daily_report_generates_file()` L123 — 🔓公开 每日报告生成 JSON 文件
    - └ `test_weekly_report_generates_file()` L143 — 🔓公开 每周报告生成 JSON 文件
    - └ `test_report_skipped_below_min_trades()` L158 — 🔓公开 低于最小交易次数时生成摘要说明
    - └ `test_fallback_to_pnl()` L166 — 🔓公开 当 pnl_close 全为空时回退到 pnl 字段
    - └ `test_by_side_breakdown()` L179 — 🔓公开 按持仓方向拆分统计（close 的 side 是平仓单方向：sell=平多, buy=平空）
    - └ `test_extract_wins_and_losses()` L196 — 🔓公开 提取盈亏交易（含持仓方向与开/平仓价还原）
    - └ `test_monthly_report_no_trades()` L229 — 🔓公开 无交易时月度报告返回零值
    - └ `test_monthly_report_with_trades()` L238 — 🔓公开 月度报告包含交易明细
    - └ `test_report_writes_to_new_dir()` L249 — 🔓公开 报告写入新目录结构 data/reports/{type}/
    - └ `test_ai_analysis_not_called_when_no_deepseek()` L260 — 🔓公开 不传 deepseek 时不调用 AI 分析
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

### 📄 tests\test_risk_layer_phase2.py (71行) — 测试 RiskManager 北京时间日结重置（波动/深度检查已迁移至 rule_engine）
  - 导入: `__future__`,`agents.config`,`agents.risk_layer`,`datetime`,`pathlib`,`pytest`,`sys`
  - 🏛️ `TestBeijingSettlement` L27
    - └ `test_daily_reset_at_cst_midnight()` L28 — 🔓公开 北京时间（UTC+8）午夜重置
    - └ `test_no_reset_within_same_day()` L45 — 🔓公开 同一天内不重复重置
    - └ `test_reset_accounts_for_cst_date_change()` L60 — 🔓公开 UTC 16:00 后应该用新的日期标识
  - ⚡ `config()` L18 — 🔓公开
  - ⚡ `manager()` L23 — 🔓公开

### 📄 tests\test_rule_decider.py (120行) — 测试 RuleDecider — 规则决策器（替代 DeepSeek 实时决策）
  - 导入: `__future__`,`agents.config`,`agents.event_bus`,`agents.rule_decider`,`datetime`,`pathlib`,`pytest`,`sys`
  - 🏛️ `TestHoldCases` L32
    - └ `test_no_events_holds()` L33 — 🔓公开
    - └ `test_single_weak_signal_holds()` L38 — 🔓公开 单条短周期弱信号强度不足，不应交易
    - └ `test_single_strong_signal_holds()` L44 — 🔓公开 单条 1h 金叉强度仍低于阈值（需要多周期共振）
    - └ `test_conflicting_signals_hold()` L49 — 🔓公开 多空矛盾 → 一致性不足 → hold
    - └ `test_non_agent1_events_ignored()` L59 — 🔓公开 agent2 新闻事件不参与评分
  - 🏛️ `TestTradeCases` L70
    - └ `test_multi_tf_bullish_alignment_buys()` L71 — 🔓公开
    - └ `test_bearish_alignment_sells()` L84 — 🔓公开
    - └ `test_output_shape_compatible_with_deepseek()` L95 — 🔓公开 输出 dict 必须与 deepseek.analyze 的下游消费字段兼容
    - └ `test_zero_price_gives_empty_sltp()` L107 — 🔓公开
    - └ `test_stats_count_calls()` L117 — 🔓公开
  - ⚡ `decider()` L18 — 🔓公开
  - ⚡ `_ev()` L22 — 🔐内部

### 📄 tests\test_rule_engine.py (706行) — RuleEngine 单元测试
  - 导入: `__future__`,`agents.rule_engine.base`,`agents.rule_engine.engine`,`agents.rule_engine.rules.execution`,`agents.rule_engine.rules.pre_trade`,`asyncio`,`datetime`,`sys`
  - ⚡ `test_rule_category_values()` L48 — 🔓公开 RuleCategory 应包含所有预期分类
  - ⚡ `test_rule_result_bool()` L56 — 🔓公开 RuleResult 应支持布尔判断
  - ⚡ `test_rule_result_to_dict()` L62 — 🔓公开 RuleResult.to_dict 应返回标准格式
  - ⚡ `test_rule_base_class()` L72 — 🔓公开 Rule 基类应提供 _ok / _reject 快捷方法
  - ⚡ `test_rule_enabled_property()` L102 — 🔓公开 Rule 应支持启用/禁用
  - ⚡ `test_api_breaker_pass()` L116 — 🔓公开
  - ⚡ `test_api_breaker_block()` L129 — 🔓公开
  - ⚡ `test_trade_interval_pass()` L139 — 🔓公开
  - ⚡ `test_trade_interval_block()` L152 — 🔓公开
  - ⚡ `test_daily_trade_limit_pass()` L163 — 🔓公开
  - ⚡ `test_daily_trade_limit_block()` L169 — 🔓公开
  - ⚡ `test_daily_loss_limit_pass()` L177 — 🔓公开
  - ⚡ `test_daily_loss_limit_block()` L183 — 🔓公开
  - ⚡ `test_consecutive_loss_pass()` L191 — 🔓公开
  - ⚡ `test_consecutive_loss_block()` L197 — 🔓公开
  - ⚡ `test_position_size_pass()` L205 — 🔓公开
  - ⚡ `test_position_size_block()` L211 — 🔓公开
  - ⚡ `test_direction_conflict_pass()` L219 — 🔓公开 同方向和反方向都通过
  - ⚡ `test_direction_conflict_block()` L239 — 🔓公开 同方向累加超限
  - ⚡ `test_slippage_no_price()` L256 — 🔓公开
  - ⚡ `test_slippage_within_range()` L262 — 🔓公开
  - ⚡ `test_slippage_exceeded()` L273 — 🔓公开
  - ⚡ `test_engine_register()` L290 — 🔓公开
  - ⚡ `test_engine_unregister()` L301 — 🔓公开
  - ⚡ `test_engine_register_many()` L314 — 🔓公开
  - ⚡ `test_engine_load_defaults()` L320 — 🔓公开
  - ⚡ `test_engine_enable_disable()` L331 — 🔓公开
  - ⚡ `test_engine_all_pass()` L344 — 🔓公开
  - ⚡ `test_engine_blocked_by()` L356 — 🔓公开
  - ⚡ `test_engine_get_warnings()` L368 — 🔓公开
  - ⚡ `test_engine_get_errors()` L379 — 🔓公开
  - ⚡ `test_scenario_normal_trade()` L394 — 🔓公开 正常交易应通过所有规则
  - ⚡ `test_scenario_daily_limit_reached()` L429 — 🔓公开 每日交易达上限应阻断
  - ⚡ `test_scenario_consecutive_losses_then_profit()` L445 — 🔓公开 连亏后应阻断，盈利后应恢复
  - ⚡ `test_scenario_partial_block()` L468 — 🔓公开 单个规则阻断后后续规则不应继续执行
  - ⚡ `test_engine_get_status()` L495 — 🔓公开
  - ⚡ `test_reset_all_stats()` L508 — 🔓公开 reset_all_stats 应将所有规则统计归零
  - ⚡ `test_rule_priority_order()` L525 — 🔓公开 规则应按 priority 升序执行
  - ⚡ `test_volatility_skips_without_client()` L551 — 🔓公开 无 OKX 客户端时 VolatilityCheckRule 应放行
  - ⚡ `test_volatility_symbol_default()` L560 — 🔓公开 VolatilityCheckRule 默认使用 ETH-USDT
  - ⚡ `test_slippage_with_signal_price()` L569 — 🔓公开
  - ⚡ `test_hft_rule_no_db()` L596 — 🔓公开 无 DB 连接时 HFT 规则应放行

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

### 📄 tests\test_trade_executor_phase2.py (127行) — 测试 TradeExecutor 阶段二升级（限价单完整生命周期、滑点保护、部分成交）
  - 导入: `__future__`,`agents.config`,`agents.trade_executor`,`pathlib`,`pytest`,`sys`,`unittest.mock`
  - 🏛️ `TestExecuteLimit` L40
    - └async `test_limit_order_fills_normally()` L42 — 🔓公开 测试限价单正常成交流程
    - └async `test_limit_order_unfilled_cancel()` L57 — 🔓公开 测试限价单未成交→撤单→市价单兜底
    - └async `test_limit_order_unfilled_cancel_not_confirmed()` L78 — 🔓公开 撤单后订单仍为 live → 拒绝市价兜底（防双仓）
    - └async `test_limit_order_partial_fill_cancel_remainder()` L91 — 🔓公开 测试限价单部分成交→撤销剩余→报告实际成交
    - └async `test_limit_order_slippage_recorded_not_rejected()` L105 — 🔓公开 限价单已成交后滑点只记录不拒绝（报失败会导致仓位失控）
    - └async `test_limit_order_place_order_fails()` L121 — 🔓公开 测试限价单下单失败→转市价单
  - ⚡ `config()` L17 — 🔓公开
  - ⚡ `okx_mock()` L22 — 🔓公开 模拟 OKXClient
  - ⚡ `executor()` L36 — 🔓公开

### 📄 tests\test_trade_result.py (89行) — 统一 trade dict（execution/trade_result.py）schema 测试
  - 导入: `__future__`,`execution.futures_paper`,`execution.paper`,`execution.trade_result`,`pathlib`,`sys`
  - 🏛️ `TestMakeTrade` L15
    - └ `test_core_fields_present()` L16 — 🔓公开
    - └ `test_pnl_only_when_given()` L26 — 🔓公开
    - └ `test_extra_fields_passthrough()` L30 — 🔓公开
  - 🏛️ `TestRejectTrade` L38
    - └ `test_shape()` L39 — 🔓公开
    - └ `test_is_rejected()` L47 — 🔓公开
  - 🏛️ `TestAccountAlignment` L54 — 两个账户层的返回统一走 trade_result schema
    - └ `test_spot_buy_schema()` L57 — 🔓公开
    - └ `test_spot_sell_reject_schema()` L64 — 🔓公开
    - └ `test_futures_open_reject_schema()` L70 — 🔓公开
    - └ `test_futures_close_has_pnl()` L77 — 🔓公开
    - └ `test_futures_close_reject_no_position()` L85 — 🔓公开

---
**统计**: 84 文件 | 19879 行代码 | 113 类 | 803 函数/方法
