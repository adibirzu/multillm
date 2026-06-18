---
description: Query multiple LLMs in parallel for diverse perspectives (cost-aware)
allowed-tools: Bash
---

Query 2-5 LLMs simultaneously with the user's prompt. Parse the user's input for the prompt and optionally specific models.

The gateway's `/api/council` endpoint does this in one call: it returns a
**pre-flight cost estimate** for the chosen models, queries them in parallel, and
reports each model's response with its **actual token cost** plus combined totals.
One model failing never sinks the rest.

Default model set (omit `models` to use gateway settings `auto_council_models`):
`ollama/qwen3-30b`, `codex/gpt-5-4`, `gemini/flash`.

Send the council request (replace PROMPT; optionally set models/max_tokens):
```bash
curl -s http://localhost:8080/api/council \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "PROMPT", "models": ["ollama/qwen3-30b", "codex/gpt-5-4", "gemini/flash"], "max_tokens": 2048, "temperature": 0.7}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
est = d['preflightEstimate']
print('### Pre-flight cost estimate')
for e in est['estimates']:
    tag = 'FREE' if e['isFree'] else f\"\${e['estimatedCostUSD']:.4f}\"
    print(f\"  {e['alias']:30} {tag}\")
print()
for r in d['responses']:
    print(f\"## {r['alias']}\")
    if r['error']:
        print(f\"  ⚠ error: {r['error']}\")
    else:
        print(r['text'])
        print(f\"\n[in {r['inputTokens']} / out {r['outputTokens']} tok · actual \${r['actualCostUSD']:.4f} · {r['latencyMs']:.0f}ms]\")
    print()
t = d['totals']
print(f\"--- Total actual spend: \${t['actualCostUSD']:.4f} across {t['modelsSucceeded']}/{t['modelsQueried']} models ---\")
"
```

After presenting each model's response, provide a brief synthesis of where the
models agree and disagree, and note the cheapest model that gave a strong answer.
