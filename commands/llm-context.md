---
description: Share or retrieve working context between Claude and Codex sessions
allowed-tools: Bash
---

Use this command to move active task context between Claude, Codex, or another MCP client.

If the user wants to share context:
```bash
curl -s -X POST http://localhost:8080/api/context \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "SESSION_ID",
    "source_llm": "SOURCE",
    "target_llm": "TARGET",
    "context_type": "info",
    "content": "CONTEXT",
    "ttl_seconds": 3600
  }'
```

If the user wants to retrieve context:
```bash
curl -s 'http://localhost:8080/api/context/SESSION_ID?target_llm=TARGET' | python3 -c "
import sys, json
results = json.load(sys.stdin)
if not results:
    print('No shared context found.')
else:
    for r in results:
        print(f'[{r[\"source_llm\"]} -> {r[\"target_llm\"]}] {r[\"context_type\"]}')
        print(r['content'])
        print()
"
```
