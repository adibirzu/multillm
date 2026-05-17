# MultiLLM v1.0 Requirements

**Project:** MultiLLM Gateway v1.0 — open-source multi-tenant LLM gateway
**Generated:** 2026-05-16 (auto from IDEA.md + research/SUMMARY.md)
**Methodology:** Phase-grouped requirements with stable REQ-IDs for downstream traceability.

---

## How to read this document

- REQ-IDs are stable. Once assigned, they do not get renumbered. New requirements take the next free number in their category.
- Each requirement is observable / testable. Vague capabilities ("good UX") are intentionally absent.
- **Phase Mapping** section at the bottom is filled by the roadmapper, then kept current as phases ship.
- v2 + Out-of-Scope at the end document deliberate exclusions to prevent rework.

---

## v1 Requirements

### OSS — Open-Source Readiness (Phase 1)

- [ ] **OSS-01**: `gitleaks detect --log-opts="--all"` produces zero findings over the full git history before first public push
- [ ] **OSS-02**: `trufflehog --results=verified` produces zero verified findings in CI on every PR
- [ ] **OSS-03**: All references to private infrastructure are scrubbed: OCI tenancy IDs, internal IPs (`130.61.*`, `10.*`), `~/.oca` paths, OCI APM data keys, personal email addresses, server hostnames
- [x] **OSS-04**: `.env.example` documents every environment variable the codebase reads via `os.environ[...]` or `os.getenv(...)`, including type, default, and one-line purpose
- [ ] **OSS-05**: Repository contains `LICENSE` (Apache 2.0), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md` with vulnerability disclosure process
- [ ] **OSS-06**: GitHub issue templates exist for: bug report, feature request, backend request, security report (private)
- [ ] **OSS-07**: GitHub pull request template enforces: description, linked issue, test plan, security checklist
- [ ] **OSS-08**: GitHub Actions CI runs on every PR: pytest with coverage gate (≥80%), ruff, mypy, gitleaks, codeql, dependency review
- [ ] **OSS-09**: Pre-commit hooks installed for: gitleaks, ruff, mypy. `pre-commit run --all-files` passes locally
- [ ] **OSS-10**: PyPI Trusted Publishing configured; release workflow uses GitHub OIDC; no long-lived `PYPI_API_TOKEN` secrets in any repo
- [ ] **OSS-11**: Release workflow produces PEP 740 Sigstore attestations alongside wheel artifacts
- [ ] **OSS-12**: Container images signed with cosign; signature published to GitHub Container Registry
- [ ] **OSS-13**: CI dependencies pinned by commit SHA (not tag) for security-critical actions (publish, sign, scan)
- [x] **OSS-14**: `Dockerfile` is multi-stage, produces a final image < 500 MB, runs as non-root user
- [x] **OSS-15**: `docker-compose.yml` brings the gateway up with empty backend credentials and answers `GET /health` within 30 s of `docker compose up`
- [ ] **OSS-16**: Quickstart in README walks a new user from `git clone` to first `/v1/messages` request with a local Ollama model in under 5 minutes
- [ ] **OSS-17**: Alembic migration framework scaffolded with: env.py wired to SQLite, `batch_alter_table()` patterns, `multillm migrate --dry-run`, automatic backup to `~/.multillm/backups/pre-<rev>-<ts>.db`, idempotency via `PRAGMA user_version`
- [x] **OSS-18**: All credentials referenced in `.env.example` resolve via env vars or vault references — zero hardcoded credentials in source
- [ ] **OSS-19**: `docs/operations/` exists with: deployment recipe, backup/restore, upgrade procedure, troubleshooting
- [ ] **OSS-20**: First-run web wizard at `/setup` — when no admin user exists, all routes (except `/health` and `/setup`) 302-redirect to the wizard. Wizard captures admin credentials (Argon2id), backend API keys, local-backend probe results, observability opt-ins. On completion, sets `setup_complete=true`; `/setup` returns 410 afterward until `multillm reset --confirm` is run
- [ ] **OSS-21**: License switched from MIT to Apache 2.0: `LICENSE` file replaced with canonical Apache 2.0 text; `pyproject.toml` `license = "Apache-2.0"`; SPDX headers (`# SPDX-License-Identifier: Apache-2.0`) added to every `multillm/*.py` and `tests/*.py`; copyright line reads `Copyright [year] MultiLLM contributors`
- [ ] **OSS-22**: Git history cleaned via `git filter-repo` before first public push; scrubs OCI tenancy IDs, internal IPs (`130.61.*`, `10.*`), `~/.oca` paths, OCI APM data keys, personal email addresses, internal hostnames; `gitleaks` + `trufflehog --results=verified` both report zero findings over the rewritten history; scan report archived at `.planning/phases/01-open-source-readiness/SECRET-SCAN-REPORT.md`
- [ ] **OSS-23**: One noop smoke-test alembic migration ships in `multillm/migrations/versions/`; `multillm migrate up` and `multillm migrate down` both run cleanly; backup is automatically created at `~/.multillm/backups/pre-<rev>-<ts>.db` before any `up`
- [ ] **OSS-24**: Distribution wired for GHCR (cosign-signed via Sigstore OIDC), PyPI (Trusted Publishing + PEP 740 attestations), and Homebrew tap (`multillm/homebrew-multillm` or per-account, auto-updated on tag); first public release is tagged `v1.0.0-rc.1` and appears in all three channels
- [ ] **OSS-25**: Pre-commit hook blocks the commit on any gitleaks finding (security-critical hooks are block-on-fail; ruff/mypy stay warn-only in P1, tightened to block-on-fail in P1 closeout)

### ARCH — Adapter Hot-Path Refactor (Phase 2a)

*Pre-requisite for AUTH-*. No behavior change; tests stay green.*

- [ ] **ARCH-01**: All inline `_call_<backend>` functions in `multillm/gateway.py` migrated to concrete `BaseAdapter` subclasses under `multillm/adapters/`
- [ ] **ARCH-02**: `gateway.py` `route_request()` and `route_streaming()` are each ≤ 3 lines (delegate to adapter)
- [ ] **ARCH-03**: `if/elif backend == "..."` chain in `gateway.py` is removed; adapter dispatch via registry mapping
- [ ] **ARCH-04**: `multillm/db/repo.py` Protocol introduced; every data-access method takes `tenant_id` as first non-self argument (grep-enforced invariant — even though tenancy lands in 2b, the shape lands here)
- [ ] **ARCH-05**: Full test suite (207 tests at refactor start) remains green after refactor; coverage delta = 0 or positive
- [ ] **ARCH-06**: No public API surface changes (Anthropic-compatible `/v1/messages`, `/routes`, `/api/*` all behave identically)
- [ ] **ARCH-07**: Adapter registry uses `importlib.metadata.entry_points()` foundation (group: `multillm.backends`) — discovered, not hardcoded — so Phase 9 plugin SDK is a small step, not a rewrite

### AUTH — Auth & Multi-Tenancy (Phase 2b)

- [ ] **AUTH-01**: User can create an account with email + password (Argon2id hash, password meets minimum length policy)
- [ ] **AUTH-02**: User can log in and receive a session cookie; session survives gateway restart (sessions persisted)
- [ ] **AUTH-03**: User can log out from any page; session invalidated server-side
- [ ] **AUTH-04**: Admin can create / suspend / delete users via dashboard
- [ ] **AUTH-05**: User can create per-tenant API keys with explicit scopes; keys are shown plaintext once on creation only
- [ ] **AUTH-06**: API keys use prefix convention: `mllm_live_<token_urlsafe(32)>` for production keys, `mllm_test_<token_urlsafe(32)>` for non-billing test keys
- [ ] **AUTH-07**: API keys are stored as SHA-256 hashes; plaintext is never logged or persisted
- [ ] **AUTH-08**: API key comparison uses `hmac.compare_digest` (timing-safe)
- [ ] **AUTH-09**: User can revoke any of their API keys; revoked keys return 401 immediately
- [ ] **AUTH-10**: Tenancy model: User → Organization → API Key → Request, enforced on every authenticated route
- [ ] **AUTH-11**: Tenant admin can set daily and monthly spend cap; exhausted budget returns HTTP 429 with `Retry-After` and machine-parseable error code
- [ ] **AUTH-12**: Budget decrement is atomic: `UPDATE budgets SET remaining = remaining - ? WHERE tenant=? AND remaining >= ? RETURNING remaining`. 50 concurrent requests against $1 budget overshoot by ≤ 10%
- [ ] **AUTH-13**: Tenant admin can set per-tenant request quota (RPM / RPD); exhaustion returns HTTP 429
- [ ] **AUTH-14**: Tenant admin can set per-tenant backend allow/deny list; denied backends return HTTP 403 with explanation
- [ ] **AUTH-15**: Two distinct API keys belonging to different tenants produce fully isolated usage records, session records, and shared-memory entries (zero cross-tenant rows in any query)
- [ ] **AUTH-16**: SQL injection regression test (`Authorization: Bearer ' OR 1=1 --`) returns 401, not 500
- [ ] **AUTH-17**: CI grep gate prevents string-interpolated SQL: `rg "execute\(.*f['\"]" multillm/` returns zero matches
- [ ] **AUTH-18**: Existing single-user install auto-upgrades to a `tenant_id='default'` user on first start after upgrade; data preserved; user is shown the new login credentials and a one-time setup token
- [ ] **AUTH-19**: Migration test against realistic fixture (1k sessions, 10k memories, FTS5 populated) completes successfully and FTS5 indexes are rebuilt correctly
- [ ] **AUTH-20**: Per-tenant rate limiting (slowapi) is enforced upstream of routing; 429 includes `Retry-After`
- [ ] **AUTH-21**: Admin role can view, create, delete tenants via dashboard; audit log records all tenant-CRUD operations

### DASH — Dashboard v2 Capability Showcase (Phase 3)

- [ ] **DASH-01**: Dashboard SPA built with SvelteKit 2 + adapter-static; output served by FastAPI `StaticFiles` at `/dashboard`; no Node runtime in production
- [ ] **DASH-02**: Dashboard supports two themes (light + dark); both pass global frontend design rules — no template look, intentional typography, real depth
- [ ] **DASH-03**: Live capability grid shows a tile per feature (Memory, Cache, Routing, Eval, Tenants, Backends, Streaming, Tools, Cost, SLO, Plugins) with real-time status pulled from `/api/*` endpoints
- [ ] **DASH-04**: Each capability tile exposes a "Try it" button that launches a guided demo for that feature
- [ ] **DASH-05**: Backend playground widget: user selects a model, types a prompt, sees streaming response inline with live token + cost counter
- [ ] **DASH-06**: Model compare widget: user picks 2–3 models, prompt once, sees streaming output for each side-by-side simultaneously
- [ ] **DASH-07**: Cost calculator widget: user inputs projected requests/day + token mix; widget estimates monthly spend across selected models
- [ ] **DASH-08**: Memory search widget: instant FTS5 search with snippet preview and category filter; results scoped to current tenant
- [ ] **DASH-09**: Tenant switcher (admin only) shows isolation visualization confirming distinct tenants see distinct data
- [ ] **DASH-10**: Live request feed via WebSocket: incoming requests appear in real time with PII redacted at write-time (server-side)
- [ ] **DASH-11**: Health map shows every configured backend with current circuit-breaker state (closed / half-open / open) and last-known p95 latency
- [ ] **DASH-12**: WebSocket broadcast uses `encode/broadcaster` abstraction; deployment with `WORKERS=2` does not break cross-worker fanout (CI verified)
- [ ] **DASH-13**: All dashboard queries that aggregate user data fail static lint if they do not include `WHERE tenant_id = ?`
- [ ] **DASH-14**: Visitor lands on dashboard and can demo every major v1.0 capability in under 60 seconds via dedicated widgets

### ROUTE — Advanced Routing (Phase 4)

- [ ] **ROUTE-01**: Routing engine is a pure function: `route(model_alias, body, ctx) -> (backend, model, fallback_chain)`. No global state.
- [ ] **ROUTE-02**: Routing rules loaded from `routes.yaml`; hot-reload via `watchfiles` swaps rules atomically (no in-flight request sees a partial swap)
- [ ] **ROUTE-03**: Cost-aware routing: alias `cheapest-coder` deterministically selects the lowest cost-per-token backend that supports coding capabilities (proven by reading `COST_TABLE`)
- [ ] **ROUTE-04**: Latency-aware routing: per-model p50/p95 tracked via existing health system; routing prefers fast models under load when cost is tied
- [ ] **ROUTE-05**: Fallback chains: primary → secondary → tertiary on error or timeout; proven by killing primary mid-stream and observing seamless failover with no client-visible error
- [ ] **ROUTE-06**: Capability matching: aliases can require `supports.tools`, `supports.vision`, `supports.json_mode`; auto-select models that satisfy the requirement
- [ ] **ROUTE-07**: Load balancing: weighted round-robin across replicas of the same model is selectable per route
- [ ] **ROUTE-08**: Sticky sessions: same tenant + same session_id → same backend within session TTL (cache locality)
- [ ] **ROUTE-09**: Routing decisions (chosen backend, model, fallback used) are recorded on the request row for downstream metrics
- [ ] **ROUTE-10**: Backwards compatibility: existing model aliases continue to resolve identically; no breaking change for current consumers

### OBS — Observability v2 (Phase 5)

- [ ] **OBS-01**: `GET /metrics` exposes Prometheus-format metrics: request rate, error rate, p50/p95/p99 latency, token counts, cost, breaker state per backend
- [ ] **OBS-02**: Metric labels are bounded: `backend`, `model`, `tenant_tier`, `status_code`. Never `user_id`, `tenant_id`, or `request_id` as a label
- [ ] **OBS-03**: 100-tenant synthetic load CI test produces a total time-series count < 5,000
- [ ] **OBS-04**: Multi-worker support: `PROMETHEUS_MULTIPROC_DIR` env var documented; `/metrics` aggregates across workers correctly when `WORKERS > 1`
- [ ] **OBS-05**: Grafana dashboards as code: raw JSON files in `deploy/grafana/dashboards/` load cleanly in a fresh Grafana 11 instance
- [ ] **OBS-06**: Prometheus Alertmanager rules in `deploy/prometheus/alerts.yml` cover: error-rate spike, p95 latency degradation, budget burn, breaker open. `promtool check rules` passes in CI
- [ ] **OBS-07**: SLO doc at `docs/operations/slo.md` defines: 99.5% availability, p95 < 2 s, error budget; references real baseline measurements from Phase 1
- [ ] **OBS-08**: OpenTelemetry trace sampling rate is env-tunable (`OTEL_TRACES_SAMPLER_ARG`); documented
- [ ] **OBS-09**: Log shipping recipes documented for: Loki, OCI Logging, CloudWatch
- [ ] **OBS-10**: Per-tenant cost / token attribution is queryable via SQLite (dashboard endpoint), NOT via Prometheus labels — prevents cardinality explosion while preserving per-tenant visibility

### CACHE — Caching v2 (Phase 6)

- [ ] **CACHE-01**: Cache key includes `tenant_id` BEFORE any semantic lookup. There is no global vector index across tenants — ever
- [ ] **CACHE-02**: Default cache mode is `strict` (exact match on `(tenant_id, model, system_prompt_hash, body_hash)`); semantic mode is per-tenant opt-in via dashboard
- [ ] **CACHE-03**: Semantic dedup uses `fastembed` ONNX runtime with `BAAI/bge-small-en-v1.5` embedding model; no `torch` dependency at runtime
- [ ] **CACHE-04**: Vector storage uses `sqlite-vec` extension; macOS Python compatibility (`enable_load_extension`) documented in `.env.example`
- [ ] **CACHE-05**: Cross-tenant canary test: tenant A stores response containing `TENANT_A_CANARY_TOKEN`; tenant B asks similar question; tenant B's response contains zero occurrences of the canary token
- [ ] **CACHE-06**: TTL policies are configurable per backend / model
- [ ] **CACHE-07**: Cache warming hooks: configurable list of prompts pre-cached on startup, scoped per tenant
- [ ] **CACHE-08**: Dashboard cache analytics widget shows: hit rate, cumulative cost saved, semantic vs strict hit breakdown
- [ ] **CACHE-09**: Cache modes selectable per tenant: `strict`, `semantic`, `off`

### EVAL — Eval Harness (Phase 7)

- [ ] **EVAL-01**: `multillm eval run <suite> --models a,b,c` produces a comparison table with accuracy, latency p50/p95, and cost per model
- [ ] **EVAL-02**: Reference suites ship for: code generation (HumanEval-style but project-curated), JSON-mode structured output, function calling, summarization, reasoning
- [ ] **EVAL-03**: Eval suites are vendor-neutral YAML; can be run with `promptfoo` runner via HTTP provider pointed at the gateway
- [ ] **EVAL-04**: Pre-cutoff public benchmarks (HumanEval, MMLU, GSM8K, etc.) carry a "potentially contaminated" badge in the report; never used as default suites
- [ ] **EVAL-05**: Dynamic suite generator: at eval time, produces fresh variations of base prompts to mitigate memorization-based score inflation
- [ ] **EVAL-06**: Custom user-defined eval suites can be added via YAML in `evals/` directory; auto-discovered
- [ ] **EVAL-07**: Nightly eval job (GitHub Actions) runs the default suite against the project's reference model set and posts results to a `docs/leaderboard/` page; alerts on >5% regression
- [ ] **EVAL-08**: Dashboard leaderboard UI surfaces latest eval results with methodology link on every row

### BACK — More Backends (Phase 8)

- [ ] **BACK-01**: Cohere backend supports Command R+ and Command R; passes the full existing test matrix (converter, streaming, tracking, health, breaker)
- [ ] **BACK-02**: Perplexity backend supports Sonar models; passes the full matrix
- [ ] **BACK-03**: Local vLLM backend auto-discovers models from a vLLM OpenAI-compatible endpoint; passes the full matrix
- [ ] **BACK-04**: Replicate backend (model-hub passthrough) for selected curated models; passes the full matrix
- [ ] **BACK-05**: HuggingFace Inference API backend; passes the full matrix
- [ ] **BACK-06**: Each new backend has a `COST_TABLE` entry and a dashboard chip
- [ ] **BACK-07**: Each new backend has a circuit breaker + health probe configured in `health.py`

### PLUG — Plugin / Extension API (Phase 9)

- [ ] **PLUG-01**: Adapter SDK published as `multillm-plugin-sdk` with: `BaseAdapter` ABC, `BaseMiddleware` ABC, lifecycle hook decorators, type-checked interfaces
- [ ] **PLUG-02**: Plugin discovery via `importlib.metadata.entry_points()` groups: `multillm.backends`, `multillm.middleware`, `multillm.routers`
- [ ] **PLUG-03**: Third party can `pip install multillm-plugin-foo` and the new backend appears in `GET /api/backends` with no gateway code changes
- [ ] **PLUG-04**: Middleware pipeline: ordered `pre_request` / `post_response` / `on_error` hooks; each hook is async; ordering deterministic
- [ ] **PLUG-05**: Plugin trust tiers: first-party (auto-installable, signature verified), curated third-party (admin-approved), arbitrary (admin opt-in only)
- [ ] **PLUG-06**: Plugin isolation: third-party plugin executes in subprocess via JSON-RPC over stdio; capability declarations enforce read/write boundaries
- [ ] **PLUG-07**: Malicious test plugin that tries to read `~/.multillm/auth.db` fails — isolation holds in regression test
- [ ] **PLUG-08**: Plugin marketplace UI in dashboard: browse curated plugin index, view permissions, install, configure
- [ ] **PLUG-09**: Plugin registry index is a community-maintained JSON file fetched at runtime; signature-verified
- [ ] **PLUG-10**: Example plugin repo published with template + tutorial covering: backend adapter, middleware, custom router

### DOCS — State-of-the-Art Docs Site (Phase 10)

- [ ] **DOCS-01**: MkDocs Material site at `docs/`; nav structure: Quickstart, Concepts, Backends, API Reference, Deployment, Operations, Contributing
- [ ] **DOCS-02**: API reference auto-generated from docstrings via `mkdocstrings-python`; no broken anchors
- [ ] **DOCS-03**: Architecture diagrams (Mermaid) embedded in Concepts section; versioned in repo
- [ ] **DOCS-04**: Migration guides between major versions; covers DB schema migrations and breaking-change cycles
- [ ] **DOCS-05**: Quickstart leads a new user from `git clone` to first request in < 5 minutes on a fresh machine (verified by a recorded fresh-machine walkthrough)
- [ ] **DOCS-06**: Search via local lunr (offline-capable); ranks code references appropriately
- [ ] **DOCS-07**: Site deploys to GitHub Pages via GH Actions on every tagged release; preview deploy on every PR
- [ ] **DOCS-08**: "Awesome MultiLLM" curated list (`docs/awesome.md`) tracks community deployments, integrations, and plugins
- [ ] **DOCS-09**: Methodology pages for cost calculator, eval contamination, semantic-cache safety published alongside the feature docs

---

## v2 Requirements (Deferred)

Features users may expect but not landing in v1.0:

- Magic-link / passwordless login (v1.0 ships email/password only)
- OAuth / OIDC / SAML / SSO (covered via Phase 9 plugin SDK; ships post-v1.0 as a curated plugin)
- WebSocket broadcast over Redis pub/sub (v1.0 ships `encode/broadcaster` abstraction; Redis backend is a configuration swap, deferred until scale demands it)
- Multi-region failover / active-active deployment
- Helm chart for Kubernetes (single `docker-compose.yml` covers v1.0)
- Postgres support as primary DB (SQLite is v1.0 default; Postgres compatibility is preserved by the repo Protocol but not validated in v1.0)
- Project-hosted demo at `demo.multillm.dev` (initially documented as self-host; project-hosted demo is a separate launch operation)

---

## Out of Scope

Explicit exclusions to prevent rework:

- **Mobile app** — dilutes focus before product-market fit
- **White-label / multi-region failover** — premature optimization
- **On-prem fine-tuning UI** — out of mission (proxy gateway, not training platform)
- **Voice / image generation backends** — text + code only for v1
- **Vendor-locked deployments (Vercel / Render)** — must remain `docker compose`-portable
- **Separate chat/playground frontend (former F3 in IDEA.md)** — folded into Dashboard v2 / Phase 3
- **Full prompt registry** — own category (Langfuse, MLflow); integrate via plugin
- **40+ guardrails framework** — own category (NeMo, LLM Guard); thin hook point only, ship one reference plugin
- **LLM-as-Judge eval** — research-fragile; only deterministic suites in v1.0
- **Built-in RAG ingestion / vector DB** — FTS5 is cross-LLM scratchpad, not RAG
- **Auto-prompt-optimizer** — black-box rewriting on a transparent proxy = trust loss
- **Smart auto-router via routing LLM** — deterministic rules cover 90% at zero added latency
- **SaaS managed plane** — self-hostable OSS is the brand

---

## Phase Mapping (Traceability)

*Filled in by `gsd-roadmapper` (2026-05-16). Phase 2 split accepted per SUMMARY.md recommendation: 2a (ARCH-*) + 2b (AUTH-*).*

| REQ-ID range | Count | Phase | Status |
|--------------|-------|-------|--------|
| OSS-01..OSS-19 | 19 | Phase 1 — Open-Source Readiness | Planned |
| ARCH-01..ARCH-07 | 7 | Phase 2a — Adapter Hot-Path Refactor | Planned |
| AUTH-01..AUTH-21 | 21 | Phase 2b — Auth & Multi-Tenancy | Planned |
| DASH-01..DASH-14 | 14 | Phase 3 — Dashboard v2 Capability Showcase | Planned |
| ROUTE-01..ROUTE-10 | 10 | Phase 4 — Advanced Routing | Planned |
| OBS-01..OBS-10 | 10 | Phase 5 — Observability v2 | Planned |
| CACHE-01..CACHE-09 | 9 | Phase 6 — Caching v2 (Semantic) | Planned |
| EVAL-01..EVAL-08 | 8 | Phase 7 — Eval Harness | Planned |
| BACK-01..BACK-07 | 7 | Phase 8 — More Backends | Planned |
| PLUG-01..PLUG-10 | 10 | Phase 9 — Plugin / Extension API | Planned |
| DOCS-01..DOCS-09 | 9 | Phase 10 — State-of-the-Art Docs Site | Planned |
| **Total** | **124** | **11 phases** | — |

**Coverage:** 124/124 v1 requirements mapped (100%). Zero orphans, zero duplicates.

---

## Notes for the Roadmapper

Carry into ROADMAP.md the following decisions captured during research:

1. **Strongly recommended: split Phase 2 into 2a (ARCH-*) and 2b (AUTH-*).** ARCH-* requirements have zero-behavior-change guarantee; AUTH-* depends on ARCH-* being landed. Splitting gives a clean rollback boundary and a single chokepoint for tenancy enforcement. **— Accepted in ROADMAP.md.**
2. **Phase 1 carries 4 catastrophic-pitfall preventions.** Do not under-size it. OSS-01/02/10/11/12/13/17 are load-bearing for security posture across the whole roadmap. **— Acknowledged; Phase 1 success criteria reflect this.**
3. **CACHE-* hard-depends on AUTH-15 / AUTH-19** being final. Mark explicitly in phase dependencies. **— Encoded as P2b → P6 dependency in ROADMAP.md.**
4. **F11 dashboard tiles double as acceptance criteria for Phases 4–9.** Each later phase should list the corresponding DASH-* tile activation as a phase exit criterion. **— Captured in STATE.md "Key decisions"; plan-phase agents will surface tile activation tasks per phase.**
5. **ROUTE-09 (routing-decision recording) is a hard prerequisite for OBS metrics** — Phase 4 must precede Phase 5. **— Encoded as P4 → P5 dependency in ROADMAP.md.**

---

*Last updated: 2026-05-16 by gsd-roadmapper (Phase Mapping populated)*
