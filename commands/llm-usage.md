---
description: Show LLM token usage, costs, and session history across all backends
allowed-tools: Bash
---

Fetch usage stats and sessions from the MultiLLM gateway API and present a clear summary.

Parse an optional window from the user's input. Supported examples: `1h`, `6h`, `12h`, `24h`, `72h`, `7d`.
Convert days to hours before calling the API. Default to `168` hours if nothing is specified.

Get dashboard stats:
```bash
curl -s 'http://localhost:8080/api/dashboard?hours=HOURS' | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d.get('totals', {})
total_tok = (t.get('total_input',0) or 0) + (t.get('total_output',0) or 0)
reqs = t.get('total_requests',0) or 0
sessions = d.get('session_count',0) or 0
cost = t.get('total_cost',0) or 0
derived = d.get('derived', {})
print(f'=== MultiLLM Usage (last {d.get(\"hours\", \"HOURS\")}h) ===')
print(f'Sessions: {sessions:,}')
print(f'Requests: {reqs:,}')
print(f'Tokens:   {total_tok:,} ({t.get(\"total_input\",0):,} in / {t.get(\"total_output\",0):,} out)')
print(f'Cost:     \${cost:.4f}')
print()
print('--- Derived Metrics ---')
print(f'  Avg requests/session: {derived.get(\"avg_requests_per_session\",0):.2f}')
print(f'  Avg tokens/request:   {derived.get(\"avg_tokens_per_request\",0):.1f}')
print(f'  Avg cost/request:     \${derived.get(\"avg_cost_per_request\",0):.6f}')
print(f'  Avg cost/1K tokens:   \${derived.get(\"avg_cost_per_1k_tokens\",0):.6f}')
print()
print('--- By Backend ---')
for b in d.get('by_backend', []):
    tok = (b.get('input_tokens',0) or 0) + (b.get('output_tokens',0) or 0)
    breqs = b.get('requests',0) or 0
    print(f'  {b[\"backend\"]:15s} {breqs:4d} reqs  {tok:>10,} tokens  avg {(tok / breqs) if breqs else 0:>8.1f} tok/req  \${b.get(\"cost_usd\",0):.4f}')
print()
print('--- By Model ---')
for m in d.get('by_model', []):
    tok = (m.get('input_tokens',0) or 0) + (m.get('output_tokens',0) or 0)
    mreqs = m.get('requests',0) or 0
    print(f'  {m[\"model_alias\"]:25s} {mreqs:4d} reqs  {tok:>10,} tokens  {((tok / mreqs) if mreqs else 0):>8.1f} tok/req  avg {m.get(\"avg_latency_ms\",0):.0f}ms  \${m.get(\"cost_usd\",0):.4f}')
"
```

Get recent sessions:
```bash
curl -s 'http://localhost:8080/api/sessions?hours=HOURS&limit=15' | python3 -c "
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
    reqs = s.get('total_requests',0) or 0
    print(f'  {started} ({dur:>4s}) [{s[\"project\"]}] {reqs} reqs, {tok:,} tok, {(tok / reqs) if reqs else 0:.1f} tok/req, \${s.get(\"total_cost_usd\",0):.4f} -- {models}')
"
```

Dashboard: http://localhost:8080/dashboard
