# MultiLLM Gateway

> Open-source multi-tenant LLM gateway. Route one API to 19 backends, predict and cap costs, fail over when you run out of tokens, fuse models into one best answer, ship `docker compose up`, own your data.

[![CI](https://github.com/adibirzu/multillm/actions/workflows/ci.yml/badge.svg)](https://github.com/adibirzu/multillm/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/multillm.svg)](https://pypi.org/project/multillm/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Image size](https://img.shields.io/docker/image-size/adibirzu/multillm/latest)](https://github.com/adibirzu/multillm/pkgs/container/multillm)

## Why MultiLLM

- **Self-hostable in one command.** `docker compose up` brings up the whole gateway. No vendor account, no per-seat pricing, no telemetry that leaves your network.
- **Cost-aware by default.** Real-time burn-rate, spend projection (day/week/month), quota-exhaustion ETA, pre-flight per-model estimates, and budget caps with alerts and optional auto-reject.
- **Resilient.** Quota-aware failover continues on the next provider when one runs out of tokens; per-backend circuit breakers and health-aware routing.
- **Adaptive fusion.** `auto` starts with the cheapest capable model, verifies the answer, and escalates across vendors only when risk or quality requires it. Explicit `fusion` keeps the fixed-panel compatibility contract; `fusion/economy`, `fusion/balanced`, `fusion/quality`, and `fusion/critical` provide bounded progressive deliberation.
- **Built for multi-LLM workflows.** Cross-LLM shared memory (FTS5), cost-aware council mode, side-by-side compare, LLM-as-judge — first-class capabilities, not bolt-ons.
- **Multi-tenant from day one.** API-key issuance, per-tenant budgets, and quota tracking are built in (the wizard provisions the first admin today).

## Quickstart (5 minutes)

The shortest path from `git clone` to a working `/v1/messages` request, using a local Ollama backend.

**Prerequisites:** Docker (with `docker compose`) and Ollama already running locally (`ollama serve` and `ollama pull llama3.2`).

1. **Clone**

   ```bash
   git clone https://github.com/adibirzu/multillm.git
   cd multillm
   ```

2. **Configure**

   ```bash
   cp .env.example .env
   ```

3. **Start the gateway**

   ```bash
   docker compose up -d
   ```

4. **Open the setup wizard**

   ```bash
   open http://localhost:8080/setup
   ```

5. **Walk through the wizard.** Create the admin account. On the backends pane, paste `http://host.docker.internal:11434` as `OLLAMA_URL` and skip the other backends. Finish.

6. **Send your first request**

   ```bash
   curl -X POST http://localhost:8080/v1/messages \
     -H 'Content-Type: application/json' \
     -d '{"model":"ollama/llama3.2","messages":[{"role":"user","content":"Say hi"}]}'
   ```

   You should get back an Anthropic-format response containing the model's reply.

> If you don't have Ollama installed, follow the same flow with any cloud backend by pasting its API key in the `/setup` wizard's backends pane.

## Architecture

```
Claude Code / OpenAI SDK / curl
            │
            ▼
   ┌────────────────────┐
   │  MultiLLM :8080    │  FastAPI + httpx (HTTP/2 pooling)
   │  ─ routing         │
   │  ─ streaming (SSE) │
   │  ─ tracking        │
   │  ─ resilience      │
   │  ─ shared memory   │
   └────────┬───────────┘
            │
   ┌────────┴────────────────────┐
   │  19 backends                │
   │  Ollama / LM Studio         │
   │  Claude / Codex / Gemini / Antigravity (CLI agents) │
   │  OpenAI / Anthropic / Gemini / Groq … │
   │  OCI Generative AI          │
   └─────────────────────────────┘
```

`routing` (`router.py`), legacy fixed `fusion` (`fusion.py`), adaptive orchestration,
the capability/pricing registry, `cost_forecast`, `budgets`, and
`failover` sit on the routing path; a stale-while-revalidate cache keeps the
dashboard instant.

Data lives in `MULTILLM_HOME` (defaults to `~/.multillm/` or the compose-mounted `./.multillm/`): SQLite tracking, FTS5 shared memory, automatic pre-migration backups. For production deployment recipes (Docker Compose, systemd, Kubernetes) see [docs/operations/deployment.md](docs/operations/deployment.md).

Adaptive policy controls, council modes, traces, GPT-5.6 discovery gating, and
rollout guidance are documented in [Adaptive Fusion v2](docs/adaptive-fusion.md).

## Backends

| Backend       | Type       | Auth mode      | Streaming |
| ------------- | ---------- | -------------- | --------- |
| Ollama        | Local      | —              | ✓ (SSE)   |
| LM Studio     | Local      | —              | ✓ (SSE)   |
| Claude Code CLI (`claude`) | Local CLI | Local CLI (rides Claude Code login) | — (JSON) |
| Codex CLI     | Local      | Local CLI      | ✓         |
| Gemini CLI    | Local      | Local CLI      | ✓         |
| Antigravity (`agy`) | Local CLI | Local CLI (Gemini 3.x / Claude 4.6 / GPT-OSS) | — (JSON) |
| OpenAI        | Cloud      | API key        | ✓ (SSE)   |
| Anthropic     | Cloud      | API key        | ✓ (SSE)   |
| Gemini        | Cloud      | API key        | ✓ (SSE)   |
| OpenRouter    | Cloud      | API key        | ✓ (SSE)   |
| Groq          | Cloud      | API key        | ✓ (SSE)   |
| DeepSeek      | Cloud      | API key        | ✓ (SSE)   |
| Mistral       | Cloud      | API key        | ✓ (SSE)   |
| Together      | Cloud      | API key        | ✓ (SSE)   |
| xAI (Grok)    | Cloud      | API key        | ✓ (SSE)   |
| Fireworks     | Cloud      | API key        | ✓ (SSE)   |
| Azure OpenAI  | Cloud      | API key        | ✓ (SSE)   |
| AWS Bedrock   | Cloud      | Cloud IAM      | ✓ (SSE)   |

## Plugin / Slash Commands

| Command                       | What it does                                       |
| ----------------------------- | -------------------------------------------------- |
| `/llm-ask <model> <prompt>`   | Send a prompt to any backend                       |
| `/llm-council <prompt>`       | Query 3+ models in parallel, get synthesis        |
| `/llm-review`                 | Get a second opinion from another LLM             |
| `/llm-status`                 | Gateway health, auth, CLI readiness                |
| `/llm-usage`                  | Token usage, costs, sessions                       |
| `/llm-discover`               | Find available models across all backends          |
| `/llm-doctor`                 | Production-readiness checks                        |
| `/llm-memory <query>`         | Search/store cross-LLM shared memory               |
| `/llm-settings`               | View/update gateway config                         |
| `/llm-dashboard`              | Open the real-time web dashboard                   |

See `commands/*.md` for the full plugin command reference.

## Configuration

The full inventory of environment variables — every `os.getenv()` lookup in the codebase — lives in [`.env.example`](.env.example). CI verifies this stays in sync. Copy it to `.env`, edit, restart.

The wizard at `/setup` covers the common path (admin user, backend keys, observability). For everything else, the .env file is the source of truth.

## Operations

- [Deployment recipes](docs/operations/deployment.md) — Docker Compose, systemd, Kubernetes
- [Backup & restore](docs/operations/backup-restore.md) — SQLite snapshots and recovery
- [Upgrades](docs/operations/upgrade.md) — version migration procedure
- [Troubleshooting](docs/operations/troubleshooting.md) — common failures and fixes
- [Release runbook](docs/operations/release.md) — for maintainers

## Dashboard

Open `http://localhost:8080/dashboard` once setup is complete. Real-time usage, per-backend latency and error rates, cost rollups, active sessions, and a side-by-side compare for evaluating answers across multiple backends.

## API

| Method     | Endpoint                    | Description                              |
| ---------- | --------------------------- | ---------------------------------------- |
| POST       | `/v1/messages`              | Anthropic Messages API proxy (`fusion`/`auto` slugs work here) |
| POST       | `/api/fusion`               | Fixed compatibility panel or adaptive preset with full trace |
| POST       | `/api/council`              | `raw`, `adaptive`, or `synthesized` council mode |
| POST       | `/api/cost/estimate`        | Pre-flight cost per model                |
| GET        | `/api/cost/forecast`        | Burn-rate, spend projection, quota ETA   |
| GET / PUT  | `/api/budgets`              | Budget caps, alerts, enforcement         |
| GET        | `/api/routing/decision`     | Which model the router would pick + why  |
| GET        | `/api/models/capabilities`  | Effective capabilities, reasoning controls, and model prices |
| GET        | `/api/orchestration/{id}`   | Sanitized, prompt-free orchestration trace |
| POST       | `/api/orchestration/{id}/feedback` | Rating feedback for local scorecards |
| GET        | `/health`                   | Liveness check                           |
| GET        | `/api/health`               | Per-backend health + breaker state       |
| GET        | `/api/dashboard`            | Stats JSON                               |
| GET        | `/api/sessions`             | Session list                             |
| GET        | `/api/backends`             | Backend discovery                        |
| GET / POST | `/api/memory`               | Shared memory (cross-LLM RAG)            |
| GET        | `/api/memory/search?q=...`  | FTS5 memory search                       |
| GET / PUT  | `/settings`                 | Gateway settings                         |

## Testing

```bash
pip install -e ".[test]"
pytest tests/ -q
```

Coverage gate is 70% (enforced in CI), plus ruff format/check and secret scans.

## Contributing

Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, code-style expectations, and the pre-commit hooks you'll want installed locally. Security-sensitive reports go through [SECURITY.md](SECURITY.md), not the public issue tracker.

This project is Apache 2.0 licensed — the patent grant is intentional and protects contributors and downstream users.

## Status & roadmap

Phase 1 (open-source readiness) is in progress. Phases 2–10 cover multi-tenant auth, dashboard polish, observability v2, semantic caching, eval harness, docs site, and the plugin SDK. The public roadmap lives on the GitHub Projects board.

## License

Apache 2.0 — see [LICENSE](LICENSE).
