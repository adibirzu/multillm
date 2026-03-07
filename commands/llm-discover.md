---
description: Discover available models from all LLM backends
allowed-tools: Bash
---

Trigger model discovery and display results from all backends (Ollama, LM Studio, OpenAI, OpenRouter, OCA, Gemini, Groq, DeepSeek, Mistral, Together, xAI, Fireworks).

```bash
curl -s 'http://localhost:8080/api/backends?refresh=true' | python3 -c "
import sys, json
data = json.load(sys.stdin)
backends = data.get('backends', {})
print(f'=== Model Discovery (total routes: {data.get(\"total_routes\",\"?\")}) ===\n')
for name in sorted(backends):
    info = backends[name]
    status = 'ONLINE' if info['available'] else 'offline'
    count = info['model_count']
    print(f'{name.upper()} ({status}, {count} models)')
    for m in info.get('models', []):
        print(f'  - {m[\"id\"]:40s} {m.get(\"name\", m[\"model\"])}')
    if not info.get('models'):
        print('  (no models found)')
    print()
"
```
