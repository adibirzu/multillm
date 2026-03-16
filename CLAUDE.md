# MultiLLM Gateway — Claude Code Instructions

## Overview

MultiLLM is a unified LLM gateway that proxies requests to 16+ backends through a single Anthropic-compatible API. It provides token tracking, cost estimation, shared cross-LLM memory, circuit breakers, health probes, and a real-time dashboard.

**Gateway URL**: `http://localhost:8080`
**Dashboard**: `http://localhost:8080/dashboard`
**Data directory**: `MULTILLM_HOME` or `~/.multillm/` (SQLite DBs, PID file, logs)

## Architecture

```
Claude Code → HTTP requests → FastAPI Gateway (port 8080) → Backend adapters → LLM APIs
                                    ↓
                              SQLite tracking + FTS5 memory + OpenTelemetry
```

- **`multillm/gateway.py`** — Main FastAPI app with all routing logic (inline functions, not adapter registry)
- **`multillm/adapters/`** — Adapter classes per backend (kept in sync with gateway inline functions)
- **`multillm/resilience.py`** — Retry with exponential backoff + per-backend circuit breakers
- **`multillm/health.py`** — Background health probes every 120s, readiness-aware routing
- **`multillm/tracking.py`** — SQLite usage/session tracking + OCI APM via OpenTelemetry
- **`multillm/memory.py`** — SQLite + FTS5 shared memory store (cross-LLM RAG)
- **`multillm/streaming.py`** — SSE streaming from all backends → Anthropic SSE format
- **`multillm/config.py`** — Route loading, env-based config, project detection
- **`multillm/converters.py`** — Anthropic <-> OpenAI format conversion

## Available Backends (16)

| Type | Backends |
|------|----------|
| Local | Ollama, LM Studio, Codex CLI, Gemini CLI |
| Cloud | OpenAI, Anthropic, Gemini, OpenRouter, Groq, DeepSeek, Mistral, Together, xAI, Fireworks, Azure OpenAI, AWS Bedrock |
| Enterprise | OCA (Oracle Code Assist) |

## Plugin Commands (Slash Commands)

| Command | Purpose |
|---------|---------|
| `/llm-ask <model> <prompt>` | Send a prompt to any backend model |
| `/llm-usage` | Token usage, costs, sessions across all backends |
| `/llm-discover` | Discover available models from all backends |
| `/llm-council` | Query multiple LLMs in parallel for diverse perspectives |
| `/llm-review` | Get a second opinion from another LLM |
| `/llm-memory` | Search, store, list, delete shared memories |
| `/llm-settings` | View or update gateway settings |
| `/llm-dashboard` | Open the real-time dashboard |

## Shared Memory (Cross-LLM RAG)

The gateway provides a shared memory store that persists across all LLM sessions. Use it for:
- Storing findings, decisions, and context that other LLM agents should know
- Searching across previously stored knowledge
- Building institutional memory across projects

## Automatic Help

Use the built-in orchestration agents when you want the system to decide when other models should help:

- `work-orchestrator` for council, second-opinion, and context-sharing decisions
- `arch-council` for architectural decisions
- `security-reviewer` for security-sensitive changes

Default orchestration behavior is controlled by gateway settings:

- `auto_orchestration_enabled`
- `auto_second_opinion_model`
- `auto_council_models`
- `auto_share_context`

### Memory API

```bash
# Store a memory
curl -X POST http://localhost:8080/api/memory \
  -H 'Content-Type: application/json' \
  -d '{"title": "...", "content": "...", "project": "...", "category": "decision|finding|context|todo|general", "source_llm": "claude"}'

# Search memories (FTS5)
curl 'http://localhost:8080/api/memory/search?q=keyword&limit=10'

# List recent memories
curl 'http://localhost:8080/api/memory?limit=20'

# Delete a memory
curl -X DELETE http://localhost:8080/api/memory/{id}
```

## Gateway API Reference

### Core Proxy
- `POST /v1/messages` — Route LLM requests (Anthropic format)

### Routes & Discovery
- `GET /routes` — List all model routes
- `POST /api/routes` — Add routes dynamically
- `DELETE /api/routes` — Remove routes
- `GET /api/backends?refresh=true` — Discover models from all backends
- `POST /api/discover` — Force re-discovery

### Usage & Sessions
- `GET /api/dashboard?hours=168&project=name` — Aggregated stats with derived metrics and optional project filter
- `GET /api/sessions?hours=168&limit=50` — Session list
- `GET /api/sessions/{id}` — Session detail with per-request breakdown
- `GET /api/active-sessions` — Currently active sessions
- `GET /api/claude-stats` — Claude Code token usage from ~/.claude/
- `GET /usage` — Usage summary

### Health & Resilience
- `GET /health` — Basic health check
- `GET /api/health` — Active health results + circuit breaker state
- `POST /api/health/check` — Force immediate health check
- `GET /api/auth` — Auth status for all backends with login instructions

### Memory & Context
- `GET/POST /api/memory` — List/store shared memories
- `GET /api/memory/search?q=...` — FTS5 search
- `GET/DELETE /api/memory/{id}` — Get/delete memory
- `POST /api/context` — Share cross-LLM context
- `GET /api/context/{session_id}` — Get shared context

### Configuration
- `GET/PUT /settings` — Gateway settings
- `GET /api/cache` — Cache stats
- `GET /api/otel` — OTel/OCI APM status

## Sandbox Modes (CLI Backends)

Codex CLI and Gemini CLI support configurable sandbox modes:

### Codex CLI
- **Env var**: `CODEX_SANDBOX` (default: `read-only`)
- **Values**: `read-only`, `workspace-write`, `danger-full-access`
- **Per-request**: Set `metadata.sandbox_mode` in request body
- **Profile**: `CODEX_DEFAULT_PROFILE` (default: `gpt-5-4`)

### Gemini CLI
- **Env var**: `GEMINI_APPROVAL_MODE` (default: `yolo`)
- **Values**: `yolo`, `default`, `auto_edit`, `plan`
- **Per-request**: Set `metadata.sandbox_mode` in request body

## Example Model Aliases

```
ollama/qwen3-30b, ollama/llama3.3
oca/gpt5, oca/gpt-4o
openai/gpt-4o, openai/o1
gemini/flash, gemini/pro
groq/llama-3.3-70b
deepseek/chat, deepseek/reasoner
codex/cli, codex/gpt-5-4
gemini-cli/default, gemini-cli/flash
```

## Testing

Run the test suite:
```bash
python -m pytest tests/ -v
```

Tests cover converters, gateway, memory, streaming, tracking, sessions, discovery, caching, http_pool, auth, resilience, health, rate_limit.

## Development Notes

- Gateway uses **inline routing functions** in `gateway.py`, not the adapter registry — both must be kept in sync
- Cost tracking for all 16 backends is in `COST_TABLE` in `tracking.py`
- Local backends (ollama, lmstudio, codex_cli, gemini_cli, oca) are $0 cost
- Circuit breaker: 5 failures → open, 60s recovery → half-open probe
- `CancelledError` is NOT counted as a backend failure (important for half-open probes)
