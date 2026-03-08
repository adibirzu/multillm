# MultiLLM Gateway

> Route Claude Code to **16 LLM backends** through a single local gateway.
> Token tracking, cost dashboard, shared memory, slash commands — all running on your machine.

```
Claude Code ──→ MultiLLM Gateway :8080 ──→ Ollama, OpenAI, Gemini, Groq, DeepSeek,
                                           Mistral, OpenRouter, Together, xAI,
                                           Fireworks, Anthropic, Azure, Bedrock,
                                           LM Studio, Codex CLI, OCA
```

## Install (one command)

```bash
curl -sSL https://raw.githubusercontent.com/adibirzu/multillm/main/install.sh | bash
```

Or manually:

```bash
git clone https://github.com/adibirzu/multillm.git
cd multillm
pip install -e .
./install.sh   # registers Claude Code hooks
```

## Start

```bash
python -m multillm.gateway
```

The gateway auto-starts when Claude Code launches (via session hooks). No need to run it manually after install.

## Connect Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8080
claude
```

That's it. Claude Code now routes through MultiLLM. Use any model:

```
> /llm-ask ollama/llama3 explain this function
> /llm-ask gemini/flash summarize this file
> /llm-ask openai/gpt-4o review this PR
```

## Slash Commands

| Command | What it does |
|---------|-------------|
| `/llm-ask <model> <prompt>` | Send a prompt to any backend |
| `/llm-council <prompt>` | Query 3+ models in parallel, get synthesis |
| `/llm-review` | Second opinion from another LLM |
| `/llm-usage` | Token usage, costs, sessions |
| `/llm-discover` | Find available models across all backends |
| `/llm-memory <query>` | Search/store cross-LLM shared memory |
| `/llm-settings` | View/update gateway config |
| `/llm-dashboard` | Open the real-time web dashboard |

## Add API Keys

Only configure the backends you use. Ollama works with zero config.

```bash
# Edit ~/.local/share/multillm/.env (or wherever you cloned it)
# Uncomment and fill in what you have:

OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
GROQ_API_KEY=gsk_...
DEEPSEEK_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
# See .env.example for all 16 backends
```

## Dashboard

Open `http://localhost:8080/dashboard` for real-time stats:

- Provider status (online/offline) with live request counts
- Token usage and costs by backend and model
- Active and historical sessions
- Claude Code stats integration

## Backends

### Local (free, no API key)

| Backend | Setup |
|---------|-------|
| **Ollama** | `ollama serve` — Llama 3, Qwen, Mistral, CodeLlama, etc. |
| **LM Studio** | Enable Local Server — any GGUF model |
| **Codex CLI** | `npm i -g @openai/codex` |

### Cloud (API key required)

| Backend | Env var | Models |
|---------|---------|--------|
| **OpenAI** | `OPENAI_API_KEY` | GPT-4o, o1, GPT-4o-mini |
| **Anthropic** | `ANTHROPIC_REAL_KEY` | Claude Sonnet, Haiku |
| **Gemini** | `GEMINI_API_KEY` | Flash, Pro |
| **OpenRouter** | `OPENROUTER_API_KEY` | 200+ models |
| **Groq** | `GROQ_API_KEY` | Llama 3.3, Mixtral (free tier) |
| **DeepSeek** | `DEEPSEEK_API_KEY` | Chat, Reasoner |
| **Mistral** | `MISTRAL_API_KEY` | Large, Small, Codestral |
| **Together** | `TOGETHER_API_KEY` | Llama 3.3, Qwen 2.5 |
| **xAI** | `XAI_API_KEY` | Grok 3 |
| **Fireworks** | `FIREWORKS_API_KEY` | Llama 3.3, Qwen 2.5 |
| **Azure OpenAI** | `AZURE_OPENAI_API_KEY` | GPT-4o (enterprise) |
| **AWS Bedrock** | AWS credentials | Claude, Llama (enterprise) |
| **OCA** | OAuth (auto) | GPT-5.x, Grok, Llama 4 |

## Model Aliases

```bash
ollama/llama3          ollama/qwen3-30b       ollama/mistral
openai/gpt-4o          openai/o1              openai/gpt-4o-mini
gemini/flash           gemini/pro
groq/llama-3.3-70b     deepseek/chat          deepseek/reasoner
oca/gpt5               codex/cli              openrouter/claude-sonnet
```

Add custom aliases in `~/.multillm/routes.json`:

```json
{
  "fast": { "backend": "groq", "model": "llama-3.3-70b-versatile" },
  "cheap": { "backend": "deepseek", "model": "deepseek-chat" }
}
```

## MCP Server (optional)

For MCP-compatible clients (Cline, Codex CLI), add to `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "multillm": {
      "command": "python",
      "args": ["-m", "multillm.mcp_server"],
      "env": { "LLM_GATEWAY_URL": "http://localhost:8080" }
    }
  }
}
```

Exposes 20 tools: `llm_ask_model`, `llm_council`, `llm_memory_store`, `llm_memory_search`, `llm_usage`, etc.

## Features

| Feature | Details |
|---------|---------|
| **SSE Streaming** | Full streaming with tool_use passthrough to all backends |
| **Auto-Discovery** | Finds models from all configured backends on startup |
| **Fallback Chain** | Cloud fails → auto-fallback to local Ollama models |
| **Shared Memory** | Cross-LLM memory with FTS5 full-text search |
| **Circuit Breaker** | 5 failures → open, 60s recovery → half-open probe |
| **Usage Tracking** | Per-project token/cost tracking in SQLite |
| **HTTP/2 Pooling** | Persistent connection pools per backend |
| **API Key Auth** | Optional `MULTILLM_API_KEY` for all proxy endpoints |
| **OpenTelemetry** | Optional distributed tracing (OCI APM supported) |
| **Semantic Cache** | Optional Redis-based cache for repeated queries |

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/messages` | Anthropic Messages API proxy |
| GET | `/health` | Health check |
| GET | `/dashboard` | Web dashboard |
| GET | `/api/dashboard` | Stats JSON |
| GET | `/api/sessions` | Session list |
| GET | `/api/backends` | Backend discovery |
| GET/POST | `/api/memory` | Shared memory |
| GET | `/api/memory/search?q=...` | FTS5 memory search |
| GET/PUT | `/settings` | Gateway settings |

## Testing

```bash
pip install -e ".[test]"
pytest tests/ -q
# 209 tests
```

## Uninstall

```bash
# Remove hooks from ~/.claude/hooks.json (delete the MultiLLM SessionStart entry)
pip uninstall multillm
rm -rf ~/.multillm                    # usage data
rm -rf ~/.local/share/multillm        # source (if installed via curl)
```

## Architecture

```
multillm/
├── gateway.py      # FastAPI proxy — routing, streaming, fallback
├── adapters/       # 16 backend adapters (Ollama, OpenAI, Gemini, etc.)
├── config.py       # Env-based config, route loading
├── converters.py   # Anthropic ↔ OpenAI format conversion
├── streaming.py    # SSE streaming for all backends
├── tracking.py     # SQLite token/cost tracking + OpenTelemetry
├── memory.py       # SQLite + FTS5 shared memory
├── discovery.py    # Auto-discover models from backends
├── mcp_server.py   # FastMCP server (20 tools)
├── health.py       # Background health probes + circuit breakers
├── resilience.py   # Retry with exponential backoff
└── static/
    └── dashboard.html
```

Data stored in `~/.multillm/` (SQLite DBs, PID file, logs).

## License

MIT
