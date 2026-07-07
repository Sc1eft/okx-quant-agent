#!/usr/bin/env python3
"""Update DeepSeek system prompt"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('agents/deepseek_caller.py', 'rb') as f:
    raw = f.read()

start_marker = b'_SYSTEM_PROMPT = '
idx = raw.find(start_marker)
triple = b'"""'
start_triple = raw.find(triple, idx)
end_triple = raw.find(triple, start_triple + 3)

old_prompt_bytes = raw[start_triple:end_triple + 3]
old_prompt = old_prompt_bytes.decode('utf-8')

# --- 1. Add max_position info to risk status section ---
# The section between 【风控状态】 and 【技术面摘要】
old_risk = "【风控状态】\n- 今日交易次数: {daily_trade_count} / {max_daily_trades}\n- 今日亏损: {daily_loss} USDT / {max_daily_loss} USDT\n- 连续亏损次数: {consecutive_losses} / {max_consecutive_losses}\n- 当前仓位乘数: {position_size_multiplier}x"

new_risk = "【风控状态】\n- 今日交易次数: {daily_trade_count} / {max_daily_trades}\n- 今日亏损: {daily_loss} USDT / {max_daily_loss} USDT\n- 连续亏损次数: {consecutive_losses} / {max_consecutive_losses}\n- 当前仓位乘数: {position_size_multiplier}x\n- 单笔最大仓位: {max_position_eth} ETH\n- 最大开仓价值: ${max_position_value_usdt} USD"

assert old_risk in old_prompt, f"old_risk not found in prompt!\nSearching for: {repr(old_risk[:80])}"
new_prompt = old_prompt.replace(old_risk, new_risk, 1)

# --- 2. Update position_size_pct description ---
old_pos_desc = '"position_size_pct": "建议仓位占总资金百分比",'
new_pos_desc = '"position_size_pct": "仓位比例(0-100)，0=最低 100=打满，体现把握程度",'
assert old_pos_desc in new_prompt, "position_size_pct old desc not found"
new_prompt = new_prompt.replace(old_pos_desc, new_pos_desc, 1)

# --- 3. Update the note about empty fields ---
old_note = "注意：如果当前无仓位且 action 为 hold，则其他字段可为空字符串。"
new_note = "注意：\n- action=buy/sell 时，stop_loss、take_profit、position_size_pct 为必填\n- action=hold 时，position_size_pct 设为0，stop_loss/take_profit 可设为0\n- position_size_pct 体现把握：高把握 70-100，中等 30-70，低把握 5-30\n- stop_loss 和 take_profit 结合波动率设置，不要设太紧"

if old_note in new_prompt:
    new_prompt = new_prompt.replace(old_note, new_note, 1)
else:
    print(f"WARNING: old_note not found! Looking for: {repr(old_note[:40])}")
    # try to find the note by searching around the JSON block
    import re
    m = re.search(r'注意[^"]*', new_prompt)
    if m:
        print(f"Found note-like text: {repr(m.group())}")

# Write back
new_prompt_bytes = new_prompt.encode('utf-8')
new_raw = raw[:start_triple] + new_prompt_bytes + raw[end_triple + 3:]

with open('agents/deepseek_caller.py', 'wb') as f:
    f.write(new_raw)

print("Done")
print(f"Prompt before: {len(old_prompt)} chars")
print(f"Prompt after: {len(new_prompt)} chars")
