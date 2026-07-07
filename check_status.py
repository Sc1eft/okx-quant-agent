#!/usr/bin/env python3
"""Get detailed status - writes to file to avoid terminal encoding issues"""
import json

with open('data/agent_status.json', encoding='utf-8') as f:
    d = json.load(f)

lines = []

a1 = d.get('agent1', {})
lines.append('ETH Price by timeframe:')
for tf in ['3m','5m','15m','1h']:
    ind = a1.get('latest_indicators',{}).get(tf, None)
    if ind:
        lines.append('  %s: $%.2f' % (tf, ind.get('close',0)))

a3 = d.get('agent3', {})
lines.append('')
lines.append('Agent 3: calls=%d executed=%d skipped=%d' % (
    a3.get('deepseek_calls',0),
    a3.get('trades_executed',0),
    a3.get('trades_skipped',0),
))

lines.append('')
lines.append('Agent 1 recent signals:')
for sig in a1.get('signal_history', [])[-8:]:
    desc = sig.get('description','').encode('ascii', errors='replace').decode()
    desc = desc.replace('?', '')
    lines.append('  [%s] %s (urgency=%s, $%.2f)' % (
        sig.get('timeframe',''),
        desc.strip(),
        sig.get('urgency',''),
        sig.get('price',0),
    ))

# News
a2 = d.get('agent2', {})
lines.append('')
lines.append('Agent 2: fetch=%s news_pushed=%s onchain=%s' % (
    a2.get('fetch_count',0), a2.get('news_pushed',0), a2.get('onchain_events_pushed',0)
))

with open('status_detail.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('done')
