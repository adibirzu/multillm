---
description: Route work through the local MultiLLM gateway and decide when to ask other LLMs or helper agents for support. Use when Codex should leverage Claude, OCA, GPT, local models, or MultiLLM specialist agents for second opinions, architecture review, security review, context handoff, dashboard checks, or multi-device session consolidation.
allowed-tools: Bash, Read, Glob, Grep
---

Unified entry point for MultiLLM orchestration. Parse the user's input and route to the right action.

## Routing Logic

Analyze the user's request and determine the best action:

1. **If the request is about architecture, design, or tradeoffs:**
   - Check health first: `curl -s http://localhost:8080/health | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('backends',{}), indent=2))"`
   - Send to council (3-4 models in parallel) using the `/llm-council` pattern
   - Synthesize and present the result

2. **If the request is about reviewing code or a plan:**
   - Read the relevant files
   - Send to a reviewer model using the `/llm-review` pattern
   - Present structured PASS/WARN/FAIL verdict

3. **If the request is a direct question for another model:**
   - Route using the `/llm-ask` pattern
   - Present the response

4. **If the request is about sharing context or handing off work:**
   - Summarize current context
   - Store to shared memory: `curl -s -X POST http://localhost:8080/api/memory -H 'Content-Type: application/json' -d '{"title": "Context handoff", "content": "CONTEXT_HERE", "category": "context", "source_llm": "claude", "project": "auto"}'`
   - Share context: `curl -s -X POST http://localhost:8080/api/context -H 'Content-Type: application/json' -d '{"context": "CONTEXT_HERE", "source": "claude", "target": "TARGET"}'`

5. **If the request is about usage or costs:**
   - Redirect to `/llm-usage` or `/llm-usage-hourly`

6. **If unsure, check shared memory first:**
   - Search: `curl -s 'http://localhost:8080/api/memory/search?q=KEYWORDS&limit=5' | python3 -c "import sys,json; [print(f'- [{m[\"category\"]}] {m[\"title\"]}: {m[\"content\"][:100]}...') for m in json.load(sys.stdin).get('memories',[])]"`

## After Every Action

Store significant findings to shared memory:
```bash
curl -s -X POST http://localhost:8080/api/memory \
  -H 'Content-Type: application/json' \
  -d '{"title": "TITLE", "content": "CONTENT", "category": "CATEGORY", "source_llm": "claude", "project": "PROJECT"}'
```

Present results clearly with headers indicating which model(s) contributed.
