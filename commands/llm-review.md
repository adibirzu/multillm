---
description: Get a code/plan review from another LLM (second opinion)
allowed-tools: Bash, Read
---

Ask another LLM to review code or a plan. Parse the user's input for:
- The reviewer model (default: oca/gpt5)
- The artifact to review (code, plan, or text — read from file if a path is given)
- Review focus (default: correctness, security, and clarity)

Send the review request (replace MODEL, ARTIFACT, and FOCUS):
```bash
curl -s http://localhost:8080/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "MODEL",
    "system": "You are a rigorous technical reviewer. Focus on: FOCUS. Structure: VERDICT: PASS|WARN|FAIL, ISSUES: (list), SUGGESTIONS: (list), SUMMARY: (2-3 sentences)",
    "messages": [{"role": "user", "content": "Review this artifact:\n\nARTIFACT"}],
    "max_tokens": 4096,
    "temperature": 0.3
  }' | python3 -c "
import sys, json
data = json.load(sys.stdin)
for block in data.get('content', []):
    if block.get('type') == 'text':
        print(block['text'])
"
```

Present the response with a header: `## Second Opinion from [model]`
