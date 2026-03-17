---
description: Open the MultiLLM dashboard showing sessions, token usage, costs, and backend status. Use when the user asks about LLM usage, costs, dashboard, or wants to see model statistics.
allowed-tools: Bash
---

Open the MultiLLM real-time dashboard and show a quick summary of current state.

First, check if the gateway is running:
```bash
curl -s http://localhost:8080/health | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Gateway: {d.get(\"status\", \"unknown\")}')
backends = d.get('backends', {})
online = sum(1 for b in backends.values() if b.get('healthy'))
print(f'Backends: {online}/{len(backends)} online')
for name, info in sorted(backends.items()):
    status = 'OK' if info.get('healthy') else 'DOWN'
    print(f'  {name:15s} {status}')
"
```

Then open the dashboard in the browser:
```bash
open http://localhost:8080/dashboard 2>/dev/null || echo "Dashboard URL: http://localhost:8080/dashboard"
```

Also show a quick usage snapshot:
```bash
curl -s 'http://localhost:8080/api/dashboard?hours=24' | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d.get('totals', {})
tok = (t.get('total_input',0) or 0) + (t.get('total_output',0) or 0)
cost = t.get('total_cost',0) or 0
reqs = t.get('total_requests',0) or 0
sessions = d.get('session_count',0) or 0
print(f'Last 24h: {sessions} sessions, {reqs} requests, {tok:,} tokens, \${cost:.4f}')
# Show active sessions if any
active = d.get('active_sessions', 0) or 0
if active:
    print(f'Active sessions: {active}')
"
```

Tell the user the dashboard is at http://localhost:8080/dashboard
