---
description: Show MultiLLM gateway health, auth mode, local tool readiness, and direct-client visibility
allowed-tools: Bash
---

Check the MultiLLM runtime status and present the result as a compact operational summary.

```bash
curl -s http://localhost:8080/api/status | python3 -c "
import json, sys

try:
    data = json.load(sys.stdin)
except Exception:
    print('MultiLLM gateway is not responding at http://localhost:8080')
    raise SystemExit(0)

gateway = data.get('gateway', {})
runtime = data.get('runtime', {})
health = data.get('health', {})
clients = data.get('direct_clients', {})
tools = data.get('tools', {})

print('=== MultiLLM Status ===')
print(f\"Version:       {data.get('version', '?')}\")
print(f\"Project:       {data.get('project', '?')}\")
print(f\"Gateway:       {gateway.get('host', '?')}:{gateway.get('port', '?')}\")
print(f\"Dashboard:     {gateway.get('dashboard_url', 'http://localhost:8080/dashboard')}\")
print(f\"Auth:          {'enabled' if gateway.get('auth_enabled') else 'disabled'}\")
print(f\"Routes:        {runtime.get('routes', 0)} routes / {runtime.get('adapters', 0)} adapters\")
print(f\"Backend health:{health.get('healthy_backends', 0)}/{health.get('total_backends', 0)} healthy\")
print(f\"Codex CLI:     {'installed' if tools.get('codex_cli') else 'not found'}\")
print(f\"Gemini CLI:    {'installed' if tools.get('gemini_cli') else 'not found'}\")

print('\\n--- Direct Clients ---')
for name, info in sorted(clients.items()):
    status = 'available' if info.get('available') else f\"unavailable ({info.get('error') or 'no data'})\"
    print(f\"  {name:14s} {status}\")

if gateway.get('unsafe_open_mode'):
    print('\\nSECURITY: gateway is unauthenticated and bound to a non-localhost interface.')
"
```

If the command says the gateway is not responding, run:

```bash
python -m multillm.gateway
```
