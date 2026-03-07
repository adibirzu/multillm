---
description: Query multiple LLMs in parallel for diverse perspectives
allowed-tools: Bash
---

Query 2-5 LLMs simultaneously with the user's prompt. Parse the user's input for the prompt and optionally specific models.

Default model set (use these unless user specifies others): ollama/qwen3-30b, oca/gpt5, gemini/flash

First check which models are available:
```bash
curl -s http://localhost:8080/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('backends',{}), indent=2))"
```

Then send parallel requests. For each model, run this (replace MODEL and PROMPT):
```bash
curl -s http://localhost:8080/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{"model": "MODEL", "messages": [{"role": "user", "content": "PROMPT"}], "max_tokens": 2048, "temperature": 0.7}' \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
content = data.get('content', [])
text = next((b['text'] for b in content if b.get('type') == 'text'), '')
usage = data.get('usage', {})
print(text)
print(f'\n[Tokens: {usage.get(\"input_tokens\",0)} in / {usage.get(\"output_tokens\",0)} out]')
"
```

Run the requests in parallel (multiple Bash calls at once) and present each model's response clearly labeled with a header like `## [model-name]`. After all responses, provide a brief synthesis of where the models agree and disagree.
