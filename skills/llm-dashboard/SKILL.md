---
name: LLM Dashboard
description: Open the MultiLLM dashboard showing sessions, token usage, costs, and backend status. Use when the user asks about LLM usage, costs, dashboard, or wants to see model statistics.
version: 0.6.0
---

# LLM Dashboard

Open the MultiLLM dashboard and provide a usage summary.

If the user asks for an hourly check, use `hours=1` unless they specify another short window.

## Steps

1. Fetch token usage summary:
```bash
curl -s 'http://localhost:8080/api/dashboard?hours=HOURS' | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d.get('totals', {})
total = (t.get('total_input',0) or 0) + (t.get('total_output',0) or 0)
reqs = t.get('total_requests',0) or 0
sessions = d.get('session_count',0) or 0
cost = t.get('total_cost',0) or 0
derived = d.get('derived', {})
print(f'Window:   {d.get(\"hours\", \"HOURS\")}h')
print(f'Requests: {reqs:,}  |  Tokens: {total:,} ({t.get(\"total_input\",0):,} in / {t.get(\"total_output\",0):,} out)  |  Cost: \${cost:.4f}')
print(f'Rates:    {derived.get(\"avg_requests_per_session\",0):.2f} req/session  |  {derived.get(\"avg_tokens_per_request\",0):.1f} tok/req  |  \${derived.get(\"avg_cost_per_request\",0):.6f}/req')
for m in d.get('by_model', [])[:10]:
    tok = (m.get('input_tokens',0) or 0) + (m.get('output_tokens',0) or 0)
    reqs = m.get('requests',0) or 0
    print(f'  {m[\"model_alias\"]:25s} {reqs:4d} reqs  {tok:>10,} tok  {(tok / reqs) if reqs else 0:>8.1f} tok/req  \${m.get(\"cost_usd\",0):.4f}')
"
```

2. Fetch recent sessions:
```bash
curl -s 'http://localhost:8080/api/sessions?hours=HOURS&limit=10' | python3 -c "
import sys, json
from datetime import datetime
for s in json.load(sys.stdin):
    started = datetime.fromtimestamp(s['started_at']).strftime('%b %d %H:%M')
    models = ', '.join(s.get('models_used', []))
    tok = (s.get('total_input_tokens',0) or 0) + (s.get('total_output_tokens',0) or 0)
    reqs = s.get('total_requests',0) or 0
    print(f'  {started} [{s[\"project\"]}] {reqs} reqs, {tok:,} tok, {(tok / reqs) if reqs else 0:.1f} tok/req, \${s.get(\"total_cost_usd\",0):.4f} -- {models}')
"
```

3. Present a formatted summary of:
   - Total requests, tokens, and estimated costs per model
   - Derived calculations such as request/session, token/request, and cost/request
   - Active backends and available models
   - Recent sessions with duration and models used
   - Hourly rates when the user asks for a short window such as `1h`, `3h`, `6h`, or `12h`
4. Direct the user to the full dashboard: http://localhost:8080/dashboard
5. If the gateway is not running, inform them to start it: `python -m multillm.gateway`
