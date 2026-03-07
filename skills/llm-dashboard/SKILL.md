---
name: LLM Dashboard
description: Open the MultiLLM dashboard showing sessions, token usage, costs, and backend status. Use when the user asks about LLM usage, costs, dashboard, or wants to see model statistics.
version: 0.5.0
---

# LLM Dashboard

Open the MultiLLM dashboard and provide a usage summary.

## Steps

1. Fetch token usage summary (last 24h):
```bash
curl -s 'http://localhost:8080/api/dashboard?hours=24' | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d.get('totals', {})
total = (t.get('total_input',0) or 0) + (t.get('total_output',0) or 0)
print(f'Requests: {t.get(\"total_requests\",0):,}  |  Tokens: {total:,} ({t.get(\"total_input\",0):,} in / {t.get(\"total_output\",0):,} out)  |  Cost: \${t.get(\"total_cost\",0):.4f}')
for m in d.get('by_model', [])[:10]:
    tok = (m.get('input_tokens',0) or 0) + (m.get('output_tokens',0) or 0)
    print(f'  {m[\"model_alias\"]:25s} {m[\"requests\"]:4d} reqs  {tok:>10,} tok  \${m.get(\"cost_usd\",0):.4f}')
"
```

2. Fetch recent sessions:
```bash
curl -s 'http://localhost:8080/api/sessions?hours=168&limit=10' | python3 -c "
import sys, json
from datetime import datetime
for s in json.load(sys.stdin):
    started = datetime.fromtimestamp(s['started_at']).strftime('%b %d %H:%M')
    models = ', '.join(s.get('models_used', []))
    tok = (s.get('total_input_tokens',0) or 0) + (s.get('total_output_tokens',0) or 0)
    print(f'  {started} [{s[\"project\"]}] {s.get(\"total_requests\",0)} reqs, {tok:,} tok, \${s.get(\"total_cost_usd\",0):.4f} -- {models}')
"
```

3. Present a formatted summary of:
   - Total requests, tokens, and estimated costs per model
   - Active backends and available models
   - Recent sessions with duration and models used
4. Direct the user to the full dashboard: http://localhost:8080/dashboard
5. If the gateway is not running, inform them to start it: `python -m multillm.gateway`
