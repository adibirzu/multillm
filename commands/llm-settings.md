---
description: View or update MultiLLM gateway settings
allowed-tools: Bash
---

View or update gateway settings (persisted to `MULTILLM_HOME/memory.db` or `~/.multillm/memory.db`).

**View all settings:**
```bash
curl -s http://localhost:8080/settings | python3 -c "
import sys, json
settings = json.load(sys.stdin)
print('=== MultiLLM Settings ===')
for k, v in sorted(settings.items()):
    print(f'  {k}: {json.dumps(v)}')"
```

**Update settings** (replace KEY and VALUE):
```bash
curl -s -X PUT http://localhost:8080/settings \
  -H 'Content-Type: application/json' \
  -d '{"KEY": VALUE}'
```

Available settings:
- `default_model`: Default model alias (e.g., "ollama/llama3")
- `default_temperature`: Default temperature (0.0-1.0)
- `max_tokens_default`: Default max tokens
- `streaming_enabled`: Enable/disable streaming
- `fallback_chain`: Ordered list of fallback models
- `otel_enabled`: Enable/disable OpenTelemetry
- `auto_orchestration_enabled`: Allow cross-LLM delegation by default
- `auto_second_opinion_model`: Default model for second opinions
- `auto_council_models`: Default model set for council/orchestration
- `auto_share_context`: Share working context across clients/sessions by default
