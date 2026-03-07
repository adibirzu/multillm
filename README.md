# MultiLLM Gateway

A cross-LLM gateway that routes AI requests to **16 backends** through a single Anthropic-compatible API. Built for Claude Code, Cline, Codex CLI, and any tool that speaks the Anthropic Messages API.

```
┌──────────────────────────────────────────┐
│  Claude Code / Cline / Codex CLI / Any   │
│    ANTHROPIC_BASE_URL=localhost:8080      │
└───────────────────┬──────────────────────┘
                    │ /v1/messages (Anthropic format)
                    ▼
┌──────────────────────────────────────────┐
│          MultiLLM Gateway :8080          │
│  Routing · Streaming · Caching · Memory  │
│  Tracking · Auth · Fallback · Discovery  │
└─┬────┬────┬────┬────┬────┬────┬────┬────┘
  │    │    │    │    │    │    │    │
  ▼    ▼    ▼    ▼    ▼    ▼    ▼    ▼
 Local Backends          Cloud Backends
 ─────────────          ───────────────
 Ollama                 OpenAI    Anthropic
 LM Studio              Gemini    OpenRouter
 Codex CLI               Groq     DeepSeek
                        Mistral   Together
                          xAI     Fireworks
                        Azure     Bedrock
                          OCA
```

## Features

| Feature | Description |
|---------|-------------|
| **16 Backends** | Ollama, LM Studio, OpenAI, Anthropic, Gemini, OpenRouter, Groq, DeepSeek, Mistral, Together, xAI, Fireworks, Azure OpenAI, AWS Bedrock, OCA, Codex CLI |
| **SSE Streaming** | Full streaming with tool_use/tool_result passthrough |
| **Auto-Discovery** | Finds available models from all backends automatically |
| **Fallback Chain** | Cloud → local model chain when backends fail |
| **Semantic Cache** | Optional Redis-based cache for repeated queries |
| **Usage Tracking** | Per-project token/cost tracking with SQLite |
| **Shared Memory** | Cross-LLM memory with FTS5 full-text search |
| **Dashboard** | Real-time web dashboard with provider status |
| **MCP Server** | 20 tools for memory, routing, usage, settings |
| **API Key Auth** | Optional authentication for all proxy endpoints |
| **HTTP/2 Pooling** | Persistent connection pools per backend |
| **OpenTelemetry** | Optional distributed tracing |

## Quick Start

### 1. Install

```bash
git clone https://github.com/youruser/multillm.git
cd multillm
pip install -e ".[http2]"

# Optional: semantic cache support
pip install -e ".[cache]"

# Optional: AWS Bedrock support
pip install boto3
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — only set backends you have access to
```

**Minimum setup** (free, local only):
```bash
# Just have Ollama running — no API keys needed
ollama serve
```

**Full setup** (add any combination):
```bash
# Cloud providers (set only what you have)
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=...
export GROQ_API_KEY=gsk_...
export DEEPSEEK_API_KEY=sk-...
# ... see .env.example for all options

# Optional: protect the gateway with an API key
export MULTILLM_API_KEY=your-secret-key
```

### 3. Start

```bash
python -m multillm.gateway
# Gateway running on http://localhost:8080
# Dashboard at http://localhost:8080/dashboard
```

### 4. Connect Your AI Tool

**Claude Code:**
```bash
export ANTHROPIC_BASE_URL=http://localhost:8080
claude
```

**Cline (VS Code):**
1. Open Cline settings
2. Set API Provider to "Anthropic"
3. Set Base URL to `http://localhost:8080`
4. Set any string as API key (or your `MULTILLM_API_KEY` if auth is enabled)

**Codex CLI:**
```bash
# Add to ~/.codex/config.toml
[mcp_servers.multillm]
command = "python"
args = ["-m", "multillm.mcp_server"]
```

## Supported Backends

### Local (Free)

| Backend | Models | Setup |
|---------|--------|-------|
| **Ollama** | Llama 3, Mistral, CodeLlama, Qwen, Gemma, etc. | `ollama serve` |
| **LM Studio** | Any GGUF model | Enable Local Server in LM Studio |
| **Codex CLI** | OpenAI Codex | `npm i -g @openai/codex` |

### Cloud (API Key Required)

| Backend | Key Env Var | Models | Pricing |
|---------|------------|--------|---------|
| **OpenAI** | `OPENAI_API_KEY` | GPT-4o, GPT-4o-mini, o1-mini | Pay-per-token |
| **Anthropic** | `ANTHROPIC_REAL_KEY` | Claude Sonnet, Claude Haiku | Pay-per-token |
| **Google Gemini** | `GEMINI_API_KEY` | Gemini Flash, Pro | Free tier + pay |
| **OpenRouter** | `OPENROUTER_API_KEY` | 200+ models | Pay-per-token |
| **Groq** | `GROQ_API_KEY` | Llama 3.3, Mixtral, Gemma | Free tier (rate limited) |
| **DeepSeek** | `DEEPSEEK_API_KEY` | DeepSeek Chat, Reasoner | Very low cost |
| **Mistral** | `MISTRAL_API_KEY` | Mistral Large, Small, Codestral | Pay-per-token |
| **Together AI** | `TOGETHER_API_KEY` | Llama 3.3, Qwen 2.5, DeepSeek V3 | Pay-per-token |
| **xAI** | `XAI_API_KEY` | Grok 3, Grok 3 Fast/Mini | Pay-per-token |
| **Fireworks AI** | `FIREWORKS_API_KEY` | Llama 3.3, Qwen 2.5 | Pay-per-token |
| **Azure OpenAI** | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` | GPT-4o, GPT-4o-mini | Enterprise |
| **AWS Bedrock** | AWS credentials | Claude, Llama, Mistral | Enterprise |
| **OCA** | OAuth (auto) | GPT-5.x, Grok, Llama 4 | Oracle Cloud |

## Dashboard

The real-time dashboard at `http://localhost:8080/dashboard` shows:

- **Provider Status Strip** — all 16 backends with online/offline/unconfigured status and live request counts
- **Usage Limits** — progress bars showing % of daily token limits per provider
- **Active Sessions** — live sessions with green pulse indicator
- **Daily Activity** — request volume chart over time
- **Backend/Model Breakdown** — token usage by backend and model
- **Claude Code Stats** — model usage, costs, session history from `~/.claude/`
- **Session Explorer** — drill into any session to see individual requests

Auto-refreshes every 5 seconds.

## MCP Server

The MCP server exposes 20 tools for use from Claude Code, Cline, or any MCP client:

```bash
# Register globally for Claude Code
# Add to ~/.claude/.mcp.json:
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

### Available Tools

| Tool | Description |
|------|-------------|
| `llm_ask_model` | Send a prompt to any model |
| `llm_second_opinion` | Get code/plan review from another model |
| `llm_council` | Query 2-3 models in parallel |
| `llm_summarize_cheap` | Summarize text using a free local model |
| `llm_list_models` | List all available models |
| `llm_memory_store` | Store persistent cross-LLM memory |
| `llm_memory_search` | Full-text search shared memory |
| `llm_memory_list` | List memories by project/category |
| `llm_memory_delete` | Delete a memory entry |
| `llm_share_context` | Share context to another LLM session |
| `llm_get_context` | Retrieve shared context |
| `llm_usage` | View token usage and costs |
| `llm_settings_get` | Read gateway settings |
| `llm_settings_set` | Update gateway settings |

## Security

### API Key Authentication

Protect the gateway with an API key:

```bash
export MULTILLM_API_KEY=your-secret-key-here
```

When set, all `/v1/*` endpoints require the key via:
- Header: `X-API-Key: your-key`
- Header: `Authorization: Bearer your-key`

Public endpoints (dashboard, health, API) remain accessible without auth.

### Credential Safety

- **No secrets are hardcoded** — all API keys and endpoints come from environment variables
- **`.env` is gitignored** — only `.env.example` (with placeholder values) is committed
- **Token caches** (`.oca/`, `.aws/`) are gitignored
- **Logs** (`*.log`) are gitignored
- **SQLite databases** (`*.db`) are gitignored

### For Public Deployment

If deploying beyond localhost:

1. **Always set `MULTILLM_API_KEY`** — prevents unauthorized proxy access
2. **Use HTTPS** — put behind a reverse proxy (nginx, Caddy) with TLS
3. **Restrict CORS** — edit `allow_origins` in `gateway.py` to your domain
4. **Review route list** — disable backends you don't use
5. **Set resource limits** — configure max request size in your reverse proxy

## Architecture

```
multillm/
├── gateway.py        # FastAPI proxy, routing, fallback, streaming
├── config.py         # Env-based configuration (no hardcoded secrets)
├── converters.py     # Anthropic <-> OpenAI format conversion
├── streaming.py      # SSE streaming for all backends
├── tracking.py       # SQLite usage tracking, per-project sessions
├── memory.py         # SQLite + FTS5 shared memory and settings
├── discovery.py      # Auto-discover models from all backends
├── caching.py        # Semantic cache with Redis/LangCache
├── mcp_server.py     # FastMCP server with 20 tools
├── http_pool.py      # Shared httpx client pools (HTTP/2)
├── auth.py           # API key authentication middleware
├── oca_auth.py       # Oracle Code Assist OAuth PKCE
├── claude_stats.py   # Claude Code stats integration
└── static/
    └── dashboard.html  # Real-time web dashboard
```

### Data Storage

All data is stored locally in `~/.multillm/`:
- `usage.db` — request/session tracking (SQLite + WAL)
- `memory.db` — shared memory with FTS5 full-text search
- `gateway.pid` / `gateway.log` — process management

## Configuration

### Custom Routes

Add custom model aliases via `~/.multillm/routes.json`:

```json
{
  "my-fast-model": {
    "backend": "groq",
    "model": "llama-3.3-70b-versatile"
  },
  "my-cheap-model": {
    "backend": "together",
    "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"
  }
}
```

### Fallback Chain

When a cloud backend fails (timeout, auth error, 5xx), requests automatically fall back:

```
Cloud backend → ollama/qwen3-30b → ollama/llama3 → ollama/mistral
```

Configure via the settings MCP tool or `~/.multillm/routes.json`.

## Testing

```bash
pip install -e ".[test]"
pytest tests/ -q
# 154 tests passing
```

Tests cover: converters (29), gateway (16), memory (18), streaming (17), tracking (6), sessions (15), discovery (9), caching (17), http_pool (6), auth (11).

## API Reference

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/messages` | Anthropic Messages API (main proxy endpoint) |
| GET | `/health` | Health check |
| GET | `/dashboard` | Web dashboard |
| GET | `/api/dashboard` | Dashboard stats JSON |
| GET | `/api/sessions` | List sessions |
| GET | `/api/sessions/{id}` | Session detail |
| GET | `/api/backends` | Backend discovery status |
| GET | `/api/routes` | Current routing table |
| POST | `/api/routes` | Add a route |
| DELETE | `/api/routes/{alias}` | Remove a route |
| GET | `/api/cache` | Cache stats |
| GET | `/api/claude-stats` | Claude Code usage stats |
| GET | `/api/active-sessions` | Currently active sessions |

## License

MIT
