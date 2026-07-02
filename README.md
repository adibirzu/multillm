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
- **Mixture of Agents (MoA).** `moa/*`, `POST /api/moa`, and MCP `llm_moa` run parallel proposers, optional refiners, and a structured final aggregator. `auto` remains cheap-first adaptive routing; `fusion/*` remains a compatibility surface. MoA is unrelated to Oracle Fusion.
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
The same-prompt evaluation methodology, live-host preflight, DeepEval workflow,
D3 workspace, audit exports, and Codex steps are documented in
[Model and MoA evaluation](docs/evaluations.md).

## Optional use with OCI Skills

OCI Skills can offer this gateway for model comparisons, cheap-first `auto`
routing, Fusion synthesis, and sanitized cost/latency traces. It is strictly
opt-in: OCI Skills never installs, enables, or sends project content to a
MultiLLM provider unless the user chooses it. Work continues with the primary
agent when the gateway is absent or declined. The separate
[DeepEval comparison suite](evals/deepeval/README.md) exercises live configured
models and runs Fusion last.

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

Claude Fable 5 is available as `claude-cli/fable` through the installed Claude
Code login and as `claude-fable` through the Anthropic API.

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
- [Selective installation](docs/installation.md) — gateway, Codex MCP/skills, and Claude components
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
| POST       | `/api/adaptive`             | Cheap-first adaptive run; full result without forced fusion |
| POST       | `/api/council`              | `raw`, `adaptive`, or `synthesized` council mode |
| POST       | `/api/cost/estimate`        | Pre-flight cost per model                |
| GET        | `/api/cost/forecast`        | Burn-rate, spend projection, quota ETA   |
| GET / PUT  | `/api/budgets`              | Budget caps, alerts, enforcement         |
| GET        | `/api/routing/decision`     | Which model the router would pick + why  |
| GET        | `/api/models/capabilities`  | Effective capabilities, reasoning controls, and model prices |
| GET        | `/api/models/catalog`       | Live-discovery-backed availability, health, pricing, and scorecards |
| GET        | `/api/models/scorecards`    | Filtered local model quality/reliability scorecards |
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

### Optional Codex gateway

The project-local Codex configuration already registers the MultiLLM MCP
server. It is optional: Codex and OCI Skills work normally without it. When a
user chooses it, `llm_adaptive` runs cheap-first selection, `llm_fusion` forces
a bounded synthesis, and the catalog/cost/routing/trace/feedback tools expose
only the required operational evidence. The gateway retains validation, budget,
verification, and prompt-free trace data; it does not make provider selection
or external transmission mandatory.

### Use Fusion from Claude Code or Codex

MultiLLM is optional. Start a normal Claude or Codex session when one model is
enough; choose the gateway for independent answers, cost-aware routing, or one
synthesized answer across locally available agents.

From a checkout, install only the integration you need and start the gateway:

```bash
./install.sh --list-components
./install.sh --component codex-mcp --component codex-skills
multillm-gateway
```

`codex-mcp` explicitly includes its `gateway` dependency. Skills remain
standalone, so this installs only reusable workflows and no gateway or MCP
server:

```bash
./install.sh --component codex-skills
```

Running `./install.sh` without arguments remains the complete installation and
creates both optional launchers:

```bash
claude-multillm
codex-multillm
```

Installed MCP clients invoke the generated `multillm-mcp` executable, which is
bound to MultiLLM's isolated Python 3.11+ runtime. This avoids relying on the
client's working directory or system Python. Start a fresh Codex thread after
installing MCP tools or skills. See [Selective installation](docs/installation.md)
for dry runs, dependencies, upgrades, and removal.

In either session, use `llm_adaptive` for cheap-first work, `llm_moa` for
layered proposer → refiner → aggregator synthesis, or `llm_fusion` for the
compatibility workflow. Start with
`llm_model_catalog` to see confirmed live aliases, then use
`llm_orchestration_trace` for a prompt-free decision/cost/latency record.

### Use Mixture of Agents (MoA) from Codex

MoA is the canonical layered multi-model synthesis capability. Use `llm_moa`
or `POST /api/moa`; the separately named Fusion tools remain compatible for
existing clients. When agents are omitted, the default roster uses Claude
Sonnet, Codex GPT-5.5, and Gemini Flash as proposers, with Claude Opus as the
aggregator. MoA is unrelated to Oracle Fusion.

Langfuse receives model, project, token, cost, latency, and orchestration-stage
metadata for gateway and MoA calls. Set
`MULTILLM_LANGFUSE_CAPTURE_CONTENT=true` to include complete visible prompts
and outputs (bounded by `MULTILLM_LANGFUSE_CONTENT_MAX_CHARS`). Hidden
chain-of-thought is not available to the gateway and is never exported.

1. Start the gateway as a normal host process (not from a sandboxed test
   runner) when using the local Codex CLI backend. Give it a writable data
   directory:

   ```bash
   MULTILLM_HOME=./.multillm multillm serve
   ```

2. In Codex, use `llm_model_catalog` and select only aliases reported as
   available. Local CLI routes commonly include `claude-cli/sonnet`,
   `codex/gpt-5-6-terra`, and configured Antigravity routes.

3. Call `llm_moa` with a prompt, `preset="quality"`, at least two proposer
   aliases, and an independent aggregator. MoA retains only usable responses,
   optionally refines them, and synthesizes last.

4. For the reproducible FinOps evaluation, configure
   `MULTILLM_EVAL_ARTIFACT_KEY` and `MULTILLM_EVAL_ALLOW_LIVE_HOST=true`, then
   use `multillm eval run --all-live --live`. The full command, release gate,
   review queue, and export steps are in
   [Model and MoA evaluation](docs/evaluations.md). The smaller DeepEval smoke
   remains opt-in:

   ```bash
   DEEPEVAL_E2E=1 MULTILLM_GATEWAY_URL=http://127.0.0.1:8080 \
     .venv/bin/pytest evals/deepeval/test_gateway_model_comparison.py -q
   ```

   This test may incur provider usage. It skips safely when live evaluation is
   not explicitly enabled or when no usable alias is discovered.

5. Local CLI controls are inherited from the gateway process. For Codex GPT-5.6
   runs, launch the gateway outside any parent sandbox that blocks Codex's local
   app-server; `CODEX_SANDBOX` is a ceiling that can restrict a request but
   cannot escape the parent process sandbox.

For a direct API client, `auto` lets the gateway decide whether escalation is
needed; `fusion/balanced` explicitly requests synthesis:

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{"model":"fusion/balanced","messages":[{"role":"user","content":"Compare these implementation options."}]}'
```

The gateway uses only live discovered or locally authenticated candidates. An
unavailable provider or gateway falls back to the primary-agent workflow rather
than blocking development.

## Testing

```bash
pip install -e ".[test]"
pytest tests/ -q
```

Coverage gate is 80% for new evaluation/MoA work, plus ruff format/check and
secret scans.

## Contributing

Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, code-style expectations, and the pre-commit hooks you'll want installed locally. Security-sensitive reports go through [SECURITY.md](SECURITY.md), not the public issue tracker.

This project is Apache 2.0 licensed — the patent grant is intentional and protects contributors and downstream users.

## Status & roadmap

Phase 1 (open-source readiness) is in progress. Phases 2–10 cover multi-tenant auth, dashboard polish, observability v2, semantic caching, eval harness, docs site, and the plugin SDK. The public roadmap lives on the GitHub Projects board.

## License

Apache 2.0 — see [LICENSE](LICENSE).
