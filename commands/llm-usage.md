---
description: Show LLM token usage, costs, and session history across all backends
allowed-tools: Bash
---

Fetch usage stats and sessions from the MultiLLM gateway API and present a clear summary.

Parse an optional window from the user's input. Supported examples: `1h`, `6h`, `12h`, `24h`, `72h`, `7d`, `30d`, `90d`, `1y`, `2y`, `5y`.
Convert days/years to hours before calling the API. Default to `168` hours if nothing is specified.

Get bundled dashboard stats. This endpoint calculates gateway, Claude Code, Codex CLI, Gemini CLI, and unified costs in one server pass and returns timing metadata:
```bash
curl -s 'http://localhost:8080/api/dashboard-bundle?hours=HOURS&session_limit=15&direct_session_limit=25' | python3 -c "
import sys, json
d = json.load(sys.stdin)
stats = d.get('stats', {})
t = stats.get('totals', {})
u = d.get('unified', {})
perf = d.get('performance', {})
total_tok = (t.get('total_input',0) or 0) + (t.get('total_output',0) or 0)
reqs = t.get('total_requests',0) or 0
sessions = stats.get('session_count',0) or 0
cost = t.get('total_cost',0) or 0
derived = stats.get('derived', {})
print(f'=== MultiLLM Usage (last {stats.get(\"hours\", \"HOURS\")}h) ===')
print(f'Computed: {perf.get(\"elapsedMs\", \"?\")}ms via {perf.get(\"strategy\", \"bundle\")}; cache TTL {perf.get(\"cacheTtlSeconds\", \"?\")}s')
print(f'Gateway sessions: {sessions:,} | Gateway requests: {reqs:,}')
print(f'Gateway tokens:   {total_tok:,} ({t.get(\"total_input\",0):,} in / {t.get(\"total_output\",0):,} out)')
print(f'Gateway cost:     \${cost:.4f}')
print(f'All LLM tokens:   {u.get(\"grandTotalTokens\",0):,}')
print(f'All LLM cost:     \${u.get(\"grandTotalCostUSD\",0):.4f} actual / \${u.get(\"grandTotalListPriceUSD\",0):.4f} list')
print()
print('--- Derived Metrics ---')
print(f'  Avg requests/session: {derived.get(\"avg_requests_per_session\",0):.2f}')
print(f'  Avg tokens/request:   {derived.get(\"avg_tokens_per_request\",0):.1f}')
print(f'  Avg cost/request:     \${derived.get(\"avg_cost_per_request\",0):.6f}')
print(f'  Avg cost/1K tokens:   \${derived.get(\"avg_cost_per_1k_tokens\",0):.6f}')
print()
print('--- By Backend ---')
for b in stats.get('by_backend', []):
    tok = (b.get('input_tokens',0) or 0) + (b.get('output_tokens',0) or 0)
    breqs = b.get('requests',0) or 0
    print(f'  {b[\"backend\"]:15s} {breqs:4d} reqs  {tok:>10,} tokens  avg {(tok / breqs) if breqs else 0:>8.1f} tok/req  \${b.get(\"cost_usd\",0):.4f}')
print()
print('--- Top Models Across All Sources ---')
for m in u.get('byModel', [])[:15]:
    print(f'  {m.get(\"model\", \"unknown\")[:28]:28s} {m.get(\"source\", \"gateway\")[:12]:12s} {m.get(\"tokens\",0):>12,} tokens  \${m.get(\"actualCostUSD\",0):.4f}')
"
```

Get recent gateway sessions:
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
