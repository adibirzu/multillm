# MultiLLM Gateway

> Open-source multi-tenant LLM gateway. Route one API to 16+ backends, ship `docker compose up`, own your data.

[![CI](https://github.com/${OWNER}/multillm/actions/workflows/ci.yml/badge.svg)](https://github.com/${OWNER}/multillm/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/multillm.svg)](https://pypi.org/project/multillm/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Image size](https://img.shields.io/docker/image-size/${OWNER}/multillm/latest)](https://github.com/${OWNER}/multillm/pkgs/container/multillm)

## Why MultiLLM

- **Self-hostable in one command.** `docker compose up` brings up the whole gateway. No vendor account, no per-seat pricing, no telemetry that leaves your network.
- **Multi-tenant from day one.** API key issuance, per-tenant budgets, and quota tracking are built in (Phase 2b lands the full auth surface; the wizard provisions the first admin today).
- **Built for multi-LLM workflows.** Cross-LLM shared memory (FTS5), council mode for parallel queries, side-by-side compare, LLM-as-judge for ranking answers — first-class capabilities, not bolt-ons.

## Quickstart (5 minutes)

The shortest path from `git clone` to a working `/v1/messages` request, using a local Ollama backend.

**Prerequisites:** Docker (with `docker compose`) and Ollama already running locally (`ollama serve` and `ollama pull llama3.2`).

1. **Clone**

   ```bash
   git clone https://github.com/${OWNER}/multillm.git
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
   ┌────────┴───────────┐
   │  16 backends       │
   │  Ollama / LM Studio│
   │  OpenAI / Anthropic│
   │  Gemini / Groq …   │
   └────────────────────┘
```

Data lives in `MULTILLM_HOME` (defaults to `~/.multillm/` or the compose-mounted `./.multillm/`): SQLite tracking, FTS5 shared memory, automatic pre-migration backups. For production deployment recipes (Docker Compose, systemd, Kubernetes) see [docs/operations/deployment.md](docs/operations/deployment.md).

## Backends

| Backend       | Type       | Auth mode      | Streaming |
| ------------- | ---------- | -------------- | --------- |
| Ollama        | Local      | —              | ✓ (SSE)   |
| LM Studio     | Local      | —              | ✓ (SSE)   |
| Codex CLI     | Local      | Local CLI      | ✓         |
| Gemini CLI    | Local      | Local CLI      | ✓         |
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
| OCA           | Enterprise | OAuth (PKCE)   | ✓ (SSE)   |

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
| POST       | `/v1/messages`              | Anthropic Messages API proxy             |
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

Coverage gate is 80% (enforced in CI).

## Contributing

Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, code-style expectations, and the pre-commit hooks you'll want installed locally. Security-sensitive reports go through [SECURITY.md](SECURITY.md), not the public issue tracker.

This project is Apache 2.0 licensed — the patent grant is intentional and protects contributors and downstream users.

## Status & roadmap

Phase 1 (open-source readiness) is in progress. Phases 2–10 cover multi-tenant auth, dashboard polish, observability v2, semantic caching, eval harness, docs site, and the plugin SDK. The public roadmap lives on the GitHub Projects board.

## License

Apache 2.0 — see [LICENSE](LICENSE).
