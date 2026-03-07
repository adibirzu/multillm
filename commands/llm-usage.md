---
description: Show LLM token usage, costs, and session history across all backends
allowed-tools: Bash
---

Fetch usage stats and sessions from the MultiLLM gateway API and present a clear summary.

Get dashboard stats (last 7 days):
```bash
curl -s 'http://localhost:8080/api/dashboard?hours=168' | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d.get('totals', {})
total_tok = (t.get('total_input',0) or 0) + (t.get('total_output',0) or 0)
print(f'=== MultiLLM Usage (last 7 days) ===')
print(f'Sessions: {d.get(\"session_count\",0)}')
print(f'Requests: {t.get(\"total_requests\",0):,}')
print(f'Tokens:   {total_tok:,} ({t.get(\"total_input\",0):,} in / {t.get(\"total_output\",0):,} out)')
print(f'Cost:     \${t.get(\"total_cost\",0):.4f}')
print()
print('--- By Backend ---')
for b in d.get('by_backend', []):
    tok = (b.get('input_tokens',0) or 0) + (b.get('output_tokens',0) or 0)
    print(f'  {b[\"backend\"]:15s} {b[\"requests\"]:4d} reqs  {tok:>10,} tokens  \${b.get(\"cost_usd\",0):.4f}')
print()
print('--- By Model ---')
for m in d.get('by_model', []):
    tok = (m.get('input_tokens',0) or 0) + (m.get('output_tokens',0) or 0)
    print(f'  {m[\"model_alias\"]:25s} {m[\"requests\"]:4d} reqs  {tok:>10,} tokens  avg {m.get(\"avg_latency_ms\",0):.0f}ms  \${m.get(\"cost_usd\",0):.4f}')
"
```

Get recent sessions:
```bash
curl -s 'http://localhost:8080/api/sessions?hours=168&limit=15' | python3 -c "
import sys, json
from datetime import datetime
sessions = json.load(sys.stdin)
print(f'\n--- Recent Sessions ({len(sessions)}) ---')
for s in sessions:
    started = datetime.fromtimestamp(s['started_at']).strftime('%b %d %H:%M')
    dur_s = int(s['last_active_at'] - s['started_at'])
    dur = f'{dur_s}s' if dur_s < 60 else f'{dur_s//60}m'
    models = ', '.join(s.get('models_used', []))
    tok = (s.get('total_input_tokens',0) or 0) + (s.get('total_output_tokens',0) or 0)
    print(f'  {started} ({dur:>4s}) [{s[\"project\"]}] {s.get(\"total_requests\",0)} reqs, {tok:,} tok, \${s.get(\"total_cost_usd\",0):.4f} -- {models}')
"
```

Dashboard: http://localhost:8080/dashboard
