---
description: Run the adaptive council and return a verified synthesis with trace and cost
allowed-tools: Bash
---

Run the shared adaptive council with the user's prompt. Parse the input for the
prompt and optionally a constrained candidate model set.

The dashboard and this command use `mode: "synthesized"`: cheap draft,
deterministic and independent verification, progressive diverse specialists,
structured comparison, then one final answer. Use `mode: "raw"` only when the
caller explicitly wants the legacy side-by-side parallel opinions.

Default candidates are controlled by gateway configuration. An explicit
`models` array constrains, rather than expands, the permitted candidates.

```bash
curl -s http://localhost:8080/api/council \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"PROMPT","mode":"synthesized","preset":"balanced","models":["ollama/qwen3-30b","codex/gpt-5-4","gemini/flash"],"max_tokens":2048,"temperature":0.2}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"### Final answer (confidence {d.get('confidence', 0):.2f})\")
print(d.get('finalAnswer') or '[no answer]')
print('\n### Stage trace')
for s in d.get('stages', []):
    print(f\"  {s.get('stage','?'):22} {s.get('model','-'):30} {s.get('effort','none'):7} \\${s.get('actual_cost_usd',0):.4f}\")
print('\n### Individual responses')
for r in d.get('responses', []):
    print(f\"## {r.get('alias','?')}\")
    print(r.get('error') or r.get('text') or '[empty]')
t = d.get('totals', {})
print(f\"--- actual \\${t.get('actualCostUSD',0):.4f}; estimated \\${t.get('estimatedCostUSD',0):.4f} ---\")
"
```

Present the verified final answer first. Include the stage trace or individual
responses only when they materially explain uncertainty or disagreement.
