---
description: Show an hourly MultiLLM usage summary and short-window calculations
allowed-tools: Bash
---

Use this command when the user wants an hourly check instead of only daily or weekly summaries.

Parse an optional hour window from the user's input. Default to `1`. Recommended values: `1`, `3`, `6`, `12`, `24`.

Get the short-window dashboard summary:
```bash
curl -s 'http://localhost:8080/api/dashboard?hours=HOURS' | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d.get('totals', {})
hours = d.get('hours', 'HOURS')
derived = d.get('derived', {})
inp = t.get('total_input',0) or 0
out = t.get('total_output',0) or 0
tok = inp + out
cost = t.get('total_cost',0) or 0
print(f'=== MultiLLM Hourly Usage ({hours}h window) ===')
print(f'Requests/hour: {derived.get(\"requests_per_hour\", 0):.2f}')
print(f'Tokens/hour:   {derived.get(\"tokens_per_hour\", 0):.1f}')
print(f'Cost/hour:     \${derived.get(\"cost_per_hour\", 0):.6f}')
print(f'Total tokens:  {tok:,} ({inp:,} in / {out:,} out)')
print()
print('--- Top Models ---')
for m in d.get('by_model', [])[:10]:
    mtok = (m.get('input_tokens',0) or 0) + (m.get('output_tokens',0) or 0)
    print(f'  {m[\"model_alias\"]:25s} {m.get(\"requests\",0):4d} reqs  {mtok:>10,} tok  avg {m.get(\"avg_latency_ms\",0):.0f}ms')
"
```

Get short-window sessions:
```bash
curl -s 'http://localhost:8080/api/sessions?hours=HOURS&limit=20' | python3 -c "
import sys, json
from datetime import datetime
sessions = json.load(sys.stdin)
print(f'\\n--- Sessions In Window ({len(sessions)}) ---')
for s in sessions:
    started = datetime.fromtimestamp(s['started_at']).strftime('%b %d %H:%M')
    dur_s = max(0, int(s['last_active_at'] - s['started_at']))
    tok = (s.get('total_input_tokens',0) or 0) + (s.get('total_output_tokens',0) or 0)
    print(f'  {started}  {dur_s:>4d}s  {s.get(\"total_requests\",0):>3d} reqs  {tok:>8,} tok  [{s[\"project\"]}]')
"
```

Direct the user to the dashboard if they want the visual panel: http://localhost:8080/dashboard
