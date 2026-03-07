---
description: Search, store, or list shared LLM memories (cross-LLM local RAG)
allowed-tools: Bash
---

Manage shared memories that persist across LLM sessions. Parse the user's intent:

**Search** (default if a query is given):
```bash
curl -s 'http://localhost:8080/api/memory/search?q=QUERY&limit=10' | python3 -c "
import sys, json
results = json.load(sys.stdin)
if not results:
    print('No memories found.')
else:
    for r in results:
        print(f'[{r[\"id\"]}] {r[\"title\"]} ({r[\"project\"]}/{r.get(\"category\",\"\")})')
        print(f'  {r[\"content\"][:200]}')
        print()
"
```

**Store** a new memory:
```bash
curl -s -X POST http://localhost:8080/api/memory \
  -H 'Content-Type: application/json' \
  -d '{"title": "TITLE", "content": "CONTENT", "project": "PROJECT", "category": "CATEGORY", "source_llm": "claude"}'
```

**List** recent memories:
```bash
curl -s 'http://localhost:8080/api/memory?limit=20' | python3 -c "
import sys, json
for r in json.load(sys.stdin):
    print(f'[{r[\"id\"]}] {r[\"title\"]} ({r[\"project\"]}/{r.get(\"category\",\"\")}) by {r.get(\"source_llm\",\"?\")}')"
```

**Delete** a memory by ID:
```bash
curl -s -X DELETE http://localhost:8080/api/memory/MEMORY_ID
```

Categories: decision, finding, context, todo, general
