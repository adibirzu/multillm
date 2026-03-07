---
description: Ask a question to a specific LLM model (e.g., /llm-ask oca/gpt5 explain this code)
allowed-tools: Bash
---

Parse the user's input to extract the model alias and prompt.

If no model is specified, first list available models:
```bash
curl -s http://localhost:8080/routes | python3 -c "
import sys, json
routes = json.load(sys.stdin)
by_backend = {}
for alias, cfg in routes.items():
    by_backend.setdefault(cfg['backend'], []).append(alias)
for backend in sorted(by_backend):
    print(f'\n{backend.upper()}:')
    for a in sorted(by_backend[backend]):
        print(f'  {a}')
"
```

Then send the prompt to the chosen model. Replace MODEL and PROMPT:
```bash
curl -s http://localhost:8080/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{"model": "MODEL", "messages": [{"role": "user", "content": "PROMPT"}], "max_tokens": 4096, "temperature": 0.7}' \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
content = data.get('content', [])
for block in content:
    if block.get('type') == 'text':
        print(block['text'])
usage = data.get('usage', {})
print(f'\n---\nModel: {data.get(\"model\",\"?\")} | Tokens: {usage.get(\"input_tokens\",0)} in / {usage.get(\"output_tokens\",0)} out')
"
```

Example models: ollama/qwen3-30b, oca/gpt5, gemini/flash, openai/gpt-4o, groq/llama-3.3-70b, deepseek/chat
