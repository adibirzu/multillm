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
- **`multillm/discovery.py`** — Dynamic model discovery + installed-aware local routing (`resolve_local_target`)
- **`multillm/service.py`** — OS-start service installer (launchd plist / systemd user unit)

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

The orchestration system auto-detects when to invoke agents based on the task phase. **You don't need to ask for help explicitly** — the agents should be triggered proactively.

### Agent Roster

| Agent | Phase | Auto-Trigger When |
|-------|-------|-------------------|
| `work-orchestrator` | Any | Detects phase and routes to specialists; high-risk changes; uncertainty |
| `task-planner` | Planning | Complex tasks needing decomposition; multi-step work; ambiguous goals |
| `arch-council` | Planning | Architecture decisions; tradeoff analysis; competing designs |
| `code-reviewer` | QA | Code just written/modified; PR review; implementation quality check |
| `security-reviewer` | QA | Auth, crypto, secrets, IAM, compliance changes |
| `local-summarizer` | Any | Large files (>200 lines); log analysis; token-saving exploration |

### Phase-Based Routing

**Planning:** "how should we...", "design", "plan", "compare" → `task-planner` or `arch-council`
**Execution:** Code changes touching auth/security → `security-reviewer`; high-risk refactors → `work-orchestrator`
**QA:** "review", "check", "validate" → `code-reviewer`; security areas → `security-reviewer`

### Checkpoint Discipline

After every orchestration action, agents store findings to shared memory automatically. This ensures:
- Other LLM sessions (Codex, Gemini CLI) can find prior decisions
- Repeated questions get answered from memory, not re-analyzed
- Cross-device work has continuity

### Orchestration Commands

| Command | Purpose |
|---------|---------|
| `/llm-orchestrator` | Unified entry point — auto-routes to the right agent/tool |
| `/llm-council` | Query 2-5 models in parallel |
| `/llm-review` | Get a second opinion from another LLM |
| `/llm-ask` | Direct question to a specific model |

### Settings

Default orchestration behavior is controlled by gateway settings:

- `auto_orchestration_enabled`
- `auto_second_opinion_model`
- `auto_council_models`
- `auto_share_context`

Use `skills/llm-orchestrator` as the reusable skill entry point for routing work through MultiLLM, pulling in other models, and consolidating context across devices.

### Session Lifecycle

Use `session-manager` agent for context continuity:

- **Session start:** Searches shared memory for prior checkpoints and presents a recovery summary
- **Session end:** Stores a structured checkpoint (completed, pending, decisions, open questions)
- **Context large:** Auto-checkpoints before context gets compacted

Hook scripts available at `skills/llm-orchestrator/hooks/`:
- `auto-checkpoint.sh [project] [summary]` — Store checkpoint to shared memory
- `session-recover.sh [project]` — Retrieve last checkpoint

### Model Profiles

Defined in `skills/llm-orchestrator/agents/openai.yaml`:

| Profile | Models | Use Case |
|---------|--------|----------|
| **planning** | qwen3-30b, gpt4o-mini, deepseek, gemini/flash | Architecture, design decisions |
| **execution** | claude-sonnet + gpt4o reviewer | Implementation with review |
| **qa** | gpt4o reviewer + gpt4o-mini/gemini council | Thorough code review |
| **budget** | All local/free models | Cost-sensitive work |

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
- `GET /api/dashboard-bundle?hours=168&refresh=true` — Single SWR-cached payload (gateway SQL + Claude/Codex/Gemini scans). Served instantly from a disk-persisted cache and revalidated in the background; `refresh=true` forces a fresh compute. Response `performance.cacheState` is `fresh|stale-refreshing|cold|forced`.
- `GET /api/sessions?hours=168&limit=50` — Session list
- `GET /api/sessions/{id}` — Session detail with per-request breakdown
- `GET /api/active-sessions` — Currently active sessions
- `GET /api/claude-stats` — Claude Code token usage from ~/.claude/
- `GET /usage` — Usage summary

### Cost Prediction & Budgets
- `GET /api/cost/forecast?hours=168&project=name` — Burn-rate (gateway live + per-source window avg), projected spend per day/week/month, and quota-exhaustion ETA per usage limit. Reuses the cached bundle (no extra scan).
- `POST /api/cost/estimate` — Pre-flight prompt pricing across candidate model aliases. Body: `{"prompt":"...","models":["openai/gpt-4o",...],"expected_output_tokens":500}`. Returns estimates sorted cheapest-first (tiktoken `cl100k_base`), flags free local models.
- `POST /api/council` — Query several models in parallel, cost-aware. Body: `{"prompt":"...","models":[...],"max_tokens":2048,"temperature":0.7}`. Returns a pre-flight cheapest-first cost estimate, each model's response with its **actual** token cost, and combined totals. One model failing does not sink the rest. Backs the `/llm-council` command.
- `POST /api/fusion` — Thought-level **fusion**: panel → judge → synthesis. Body: `{"prompt":"...","fusion_panel":[...],"fusion_judge":"...","max_tokens":1024}`. Dispatches the panel in parallel, then one judge call produces a structured analysis (consensus / contradictions / partial coverage / unique insights / blind spots) and a grounded final answer. Returns the full result (panel + analysis + answer + cost). Degrades gracefully: 1 panel success → returns it; judge failure → best panel answer.
- `GET /api/budgets` — Budget status: caps, gateway-metered spend (rolling 24h/30d), %used, alert states (`ok|warn|exceeded`), enforcement flag.
- `PUT /api/budgets` — Set budget config. Body: `{"enabled":true,"daily_usd":10,"monthly_usd":200,"alert_thresholds":[0.8,1.0],"per_project":{"name":{"daily_usd":5}}}`. When `enabled`, an exceeded global/project cap blocks new **cloud** requests with HTTP 402 (local backends are free, never blocked).

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

## Installed-Aware Local Routing

The gateway routes to the LLM the user actually has installed locally:

- **Discovery** probes Ollama (`/api/tags`) and LM Studio (`/v1/models`) at startup (`discovery.py`); only reachable backends populate the cache.
- **`resolve_local_target()`** picks the most capable installed + reachable local model (ranked by parameter size).
- **Fallback** (`_get_fallback_model`) prefers the configured `fallback_chain`, then `resolve_local_target()` — so it never targets a model the user hasn't pulled.
- **`local_first` setting** (default `true`): an unknown/unavailable model alias degrades to the best installed local model instead of returning 400.
- **On-demand startup** (`local_launch.py`, `local_autostart` setting default `true`): when fallback needs a local model but the daemon is stopped, the gateway starts the installed backend (`ollama serve` / `lms server start`), waits for readiness, marks it healthy, re-discovers, then routes. Only localhost URLs are auto-started; spawning is per-backend locked. Manual control: `POST /api/local/start {"backend":"ollama"}` and `GET /api/local/status`.

## Quota-Aware Failover (`failover.py`)

"Continue working when out of tokens." When a cloud backend returns a quota /
credit / rate-limit error (HTTP 429/402 or an `insufficient_quota`-style body),
the gateway does not surface the error — it walks the configured `fallback_chain`
(cloud or local), trying each provider until one succeeds, then appends the best
installed local model as a last resort. The response carries a `[Failover: ...]`
notice. Plain 4xx client errors (400, etc.) are NOT failed over.

- `is_quota_error()` detects 429/402 + quota markers; `build_failover_candidates()`
  builds the ordered, de-duplicated provider list (skipping the failed backend).
- Set `fallback_chain` to your preferred provider order for cross-cloud failover,
  e.g. `["anthropic/claude-sonnet-4-6","openai/gpt-4o","deepseek/chat","ollama/<model>"]`.

## Model Fusion & Auto-Routing (`fusion.py`, `complexity.py`)

Thought-level fusion (OpenRouter-Fusion / FusionFactory style): combine a panel of
models into one answer that beats any single model.

- **`fusion` model slug** on `/v1/messages` (`{"model":"fusion"}`): runs the
  panel→judge→synthesis pipeline and returns a single Anthropic response (JSON or
  SSE), so any client treats it like one model. Recursion is blocked (a panel/judge
  member cannot be `fusion`/`auto`).
- **`auto` model slug**: `complexity.estimate_complexity()` scores the prompt; if
  it clears `fusion_auto_threshold` (default 0.6) the request escalates to fusion,
  otherwise it routes to a single capable model (`fusion_judge`). This is the
  "selective invocation" — don't pay 2–3× latency for simple prompts.
- **Pipeline**: panel dispatched in parallel (reuses the council query path, each
  sub-call recorded for accurate cost), then ONE judge call yields the structured
  analysis + a `===FINAL ANSWER===` section. Cost = Σ panel + judge.
- **Settings**: `fusion_panel` (list), `fusion_judge` (alias), `fusion_auto_threshold`.
  Default panel is free/authenticated backends; unavailable members degrade
  gracefully.

## Telemetry (Langfuse + OCI APM)

Every LLM call is recorded as a Langfuse generation (`trace_llm_generation`) and
an OCI APM span (`trace_llm_call`) with model, tokens, cost, latency, project.

- **Langfuse**: set `LANGFUSE_ENABLED=true`, `LANGFUSE_HOST`, and keys (`.env`).
- **OCI APM**: set `OCI_APM_DOMAIN_ID`, `OCI_APM_DATA_KEY`, and
  **`OCI_APM_DATA_UPLOAD_ENDPOINT`** (the domain-specific data upload host — the
  generic `apm-trace.<region>` host 404s; see KB-001). OTLP paths are built by
  `tracking._oci_apm_signal_endpoint()`: `/opentelemetry/{private|public}/v1/traces`
  and `/opentelemetry/v1/metrics`. `OCI_APM_METRICS_ENABLED` defaults `false`
  (many domains accept traces but not OTLP metrics); traces always flow.

## OS-Start Service

Run the gateway as a boot service (replaces the SessionStart-hook-only startup):

```bash
multillm service install     # launchd (macOS) or systemd --user (Linux); RunAtLoad + KeepAlive
multillm service status      # installed / loaded state
multillm service uninstall   # stop + remove
```

The launchd plist sets `PATH` explicitly so subprocess CLI backends (`codex`, `gemini`, `ollama`) resolve under launchd's minimal environment.

## Dashboard — Routing & Reliability Panel

`get_dashboard_stats()` now returns `by_status`, `reliability` (error_rate, fallback_rate, counts), and `recent_errors`. The dashboard's **Routing & Reliability** panel renders these plus live `/api/routing/scores` (adaptive scores) and `/api/health` (circuit-breaker state).

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
