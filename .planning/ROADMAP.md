# MultiLLM v1.0 — ROADMAP

**Project:** MultiLLM Gateway v1.0 — open-source multi-tenant LLM gateway
**Generated:** 2026-05-16 by gsd-roadmapper (auto mode, granularity=coarse)
**Status:** Active — Phase 1 starts next

---

## Vision

Transform MultiLLM from a single-user local gateway into a production-grade, open-source, multi-tenant LLM gateway. `git clone && docker compose up` yields a secure, observable, capability-rich platform with full data ownership and no vendor lock-in.

## Rationale: Phase 2 Split (accepted)

Phase 2 is split into **Phase 2a (ARCH-*)** and **Phase 2b (AUTH-*)** per SUMMARY.md recommendation:

- **2a** — adapter hot-path refactor + tenant-shape repo Protocol. Zero behavior change; the 207-test suite is the safety net. Establishes the single chokepoint where tenancy enforcement will land.
- **2b** — tenancy schema, auth, budgets, API key issuance. Behavior change, gated on 2a. SQLi/budget-race/cross-tenant fuzz tests are exit gates.

Splitting gives a clean rollback boundary and ensures tenancy enforcement happens at one wrapper rather than 12 elif branches. The roadmap is therefore **11 phases total** (1, 2a, 2b, 3, 4, 5, 6, 7, 8, 9, 10).

## Granularity

Coarse — each phase describes 1–3 concrete plans. Detailed task decomposition happens at `/gsd-plan-phase N` time.

---

## Phases

- [ ] **Phase 1: Open-Source Readiness** — Public-safe repo, supply-chain hardened, one-command bring-up, migration framework scaffolded
- [ ] **Phase 2a: Adapter Hot-Path Refactor** — Strangler-fig refactor of gateway.py into BaseAdapter dispatch; tenant-shape repo Protocol; zero behavior change
- [ ] **Phase 2b: Auth & Multi-Tenancy** — User accounts, API keys, per-tenant budgets, row-level isolation, default-tenant migration
- [ ] **Phase 3: Dashboard v2 Capability Showcase** — SvelteKit SPA with live capability tiles, playground, compare/cost widgets, tenant-scoped WebSocket feed
- [ ] **Phase 4: Advanced Routing** — Pure-function routing engine, hot-reload YAML rules, cost/latency/capability-aware aliases, fallback chains
- [ ] **Phase 5: Observability v2** — Prometheus `/metrics` with bounded cardinality, Grafana JSON, Alertmanager rules, SLO doc
- [ ] **Phase 6: Caching v2 (Semantic)** — Per-tenant vector pools via sqlite-vec + fastembed, strict-default mode, cross-tenant canary test
- [ ] **Phase 7: Eval Harness** — CLI `multillm eval run`, vendor-neutral YAML suites, contamination-aware leaderboard
- [ ] **Phase 8: More Backends** — Cohere, Perplexity, vLLM, Replicate, HuggingFace each passing the full adapter test matrix
- [ ] **Phase 9: Plugin / Extension API** — Adapter SDK, entry-point discovery, subprocess-isolated third-party plugins, marketplace UI
- [ ] **Phase 10: State-of-the-Art Docs Site** — MkDocs Material, auto-generated API reference, methodology pages, GH Pages deploy

---

## Phase Details

### Phase 1: Open-Source Readiness
**Goal**: Repo is safe for public release — zero secrets in history, supply-chain hardened, one-command bring-up works, migration framework ready for the first real migration in Phase 2b
**Depends on**: Nothing (first phase)
**Requirements**: OSS-01, OSS-02, OSS-03, OSS-04, OSS-05, OSS-06, OSS-07, OSS-08, OSS-09, OSS-10, OSS-11, OSS-12, OSS-13, OSS-14, OSS-15, OSS-16, OSS-17, OSS-18, OSS-19
**Success Criteria** (what must be TRUE):
  1. `gitleaks detect --log-opts="--all"` and `trufflehog --results=verified` both produce zero findings over the full git history
  2. New contributor can `git clone && cp .env.example .env && docker compose up` and hit `GET /health` within 30 seconds on a fresh machine
  3. PyPI Trusted Publishing is live with PEP 740 Sigstore attestations on a test release; no long-lived `PYPI_API_TOKEN` exists in any workflow
  4. Pre-commit (gitleaks, ruff, mypy) and GitHub Actions CI (pytest≥80%, ruff, mypy, gitleaks, CodeQL, dependency review) both pass cleanly
  5. `multillm migrate --dry-run` works against a realistic fixture, auto-backs up to `~/.multillm/backups/`, and rebuilds FTS5 indexes correctly
**Plans:** 3/9 plans executed
- [ ] 01-01-PLAN.md — License switch (MIT→Apache 2.0), SPDX headers, community files (CONTRIBUTING/COC/SECURITY), GitHub issue + PR templates
- [x] 01-02-PLAN.md — Pre-commit hooks (gitleaks block-on-fail, ruff/mypy warn), CI workflow (pytest≥80%, lint, scan), CodeQL, dependency-review, Dependabot
- [x] 01-03-PLAN.md — Alembic migration scaffold + smoke migration + multillm CLI (migrate up/down/--dry-run/status, auto-backup) [TDD]
- [x] 01-04-PLAN.md — Multi-stage Dockerfile (non-root, <500 MB), docker-compose.yml (one-command bring-up), .env.example with AST coverage test
- [ ] 01-05-PLAN.md — Release pipeline: PyPI Trusted Publishing + PEP 740 attestations, GHCR + cosign keyless signing, Homebrew tap auto-update
- [ ] 01-06-PLAN.md — README rewrite (5-min Quickstart, badges, backends grid), docs/operations/ runbooks (deployment/backup/upgrade/troubleshooting)
- [ ] 01-07-PLAN.md — First-run /setup wizard: SetupRedirectMiddleware, Argon2id passwords, 4-pane vanilla HTML+JS, multillm reset --confirm [TDD]
- [ ] 01-08-PLAN.md — HARD SYNC: git filter-repo secret scrub + zero-findings gate + force-push + credential rotation (autonomous: false)
- [ ] 01-09-PLAN.md — Tag v1.0.0-rc.1 + verify all three distribution channels (PyPI attestations, GHCR cosign, Homebrew formula) (autonomous: false)

### Phase 2a: Adapter Hot-Path Refactor
**Goal**: `gateway.py` no longer carries inline `_call_<backend>` chains — every backend dispatches through `BaseAdapter` subclasses via a registry. Repo Protocol is introduced with `tenant_id` as the first non-self argument on every method, even though tenancy data lands in 2b
**Depends on**: Phase 1 (migration framework, clean repo)
**Requirements**: ARCH-01, ARCH-02, ARCH-03, ARCH-04, ARCH-05, ARCH-06, ARCH-07
**Success Criteria** (what must be TRUE):
  1. `route_request()` and `route_streaming()` in `gateway.py` are each ≤3 lines, delegating to the adapter registry
  2. Full 207-test suite remains green; coverage delta is zero or positive
  3. `if/elif backend == "..."` chains are gone — backend dispatch is registry-based
  4. `multillm/db/repo.py` Protocol exists; every data-access method takes `tenant_id` as the first non-self arg (grep-enforced invariant)
  5. Adapter registry foundation uses `importlib.metadata.entry_points()` (group: `multillm.backends`) so Phase 9 plugin SDK is a small step, not a rewrite
**Plans**: TBD (target: 1–2 plans — adapter migration; repo Protocol + registry foundation)

### Phase 2b: Auth & Multi-Tenancy
**Goal**: Two distinct API keys belonging to different tenants produce fully isolated usage, sessions, and shared-memory rows. Existing single-user installs auto-upgrade to a `default` tenant without manual steps
**Depends on**: Phase 2a (adapter chokepoint, repo Protocol)
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-06, AUTH-07, AUTH-08, AUTH-09, AUTH-10, AUTH-11, AUTH-12, AUTH-13, AUTH-14, AUTH-15, AUTH-16, AUTH-17, AUTH-18, AUTH-19, AUTH-20, AUTH-21
**Success Criteria** (what must be TRUE):
  1. User can create an account, log in, log out; sessions survive gateway restart; admin can suspend/delete users
  2. SQLi fuzz test (`' OR 1=1 --` as bearer) returns 401, not 500; CI grep gate prevents string-interpolated SQL
  3. Two-tenant isolation fuzz: zero cross-tenant rows in any query touching usage, sessions, or memory
  4. Concurrent budget stress (50 requests vs $1 cap) overshoots by ≤10% via atomic `UPDATE … WHERE remaining >= ? RETURNING remaining`
  5. Realistic-fixture migration (1k sessions, 10k memories, populated FTS5) completes with backup + FTS5 rebuild; API keys are stored hash-only with `hmac.compare_digest` verification
**Plans**: TBD (target: 2–3 plans — auth/tenancy schema + migration; API keys + budgets + quotas; admin CRUD + audit log)

### Phase 3: Dashboard v2 Capability Showcase
**Goal**: A visitor lands on the dashboard and can demo every major v1.0 capability in under 60 seconds via dedicated widgets. Both light and dark themes feel intentional, not template-stamped
**Depends on**: Phase 2b (tenant model, isolation invariants, PII redaction at write-time)
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, DASH-05, DASH-06, DASH-07, DASH-08, DASH-09, DASH-10, DASH-11, DASH-12, DASH-13, DASH-14
**Success Criteria** (what must be TRUE):
  1. SvelteKit + adapter-static SPA is served by FastAPI `StaticFiles` at `/dashboard`; no Node runtime in production
  2. Capability grid lights up tiles for every major feature; each tile exposes a working "Try it" guided demo
  3. Backend playground, model-compare (2–3 streams side-by-side), and cost-calculator widgets work with live token + cost counters
  4. WebSocket live request feed works correctly under `WORKERS=2` (cross-worker fanout via `encode/broadcaster`); PII is redacted at write-time
  5. Aggregation queries fail static lint if they omit `WHERE tenant_id = ?`; admin tenant switcher confirms isolation visually
**Plans**: TBD (target: 2–3 plans — SPA scaffold + design system + capability grid; playground/compare/cost/memory widgets; WebSocket hub + live feed + tenant switcher)
**UI hint**: yes

### Phase 4: Advanced Routing
**Goal**: Routing decisions become deterministic, declarative, and observable. `cheapest-coder` deterministically picks the lowest-cost backend supporting coding; killing the primary mid-stream produces seamless failover; routing decisions land on each request row for metric labels in Phase 5
**Depends on**: Phase 2a (adapter registry), Phase 2b (tenant context for sticky sessions and per-tenant allow/deny)
**Requirements**: ROUTE-01, ROUTE-02, ROUTE-03, ROUTE-04, ROUTE-05, ROUTE-06, ROUTE-07, ROUTE-08, ROUTE-09, ROUTE-10
**Success Criteria** (what must be TRUE):
  1. `routing.engine.resolve(model_alias, body, ctx) -> ResolutionPlan` is a pure function with no global state; existing aliases resolve identically
  2. `routes.yaml` hot-reloads via `watchfiles` atomically — no in-flight request observes a partial swap
  3. Fallback chain test: kill primary mid-stream; client sees seamless failover with no visible error
  4. `cheapest-coder` alias deterministically selects the lowest cost-per-token backend supporting coding (verified against `COST_TABLE`)
  5. Routing decisions (chosen backend, model, fallback used) are recorded on the request row, available for Phase 5 metric labels
**Plans**: TBD (target: 1–2 plans — engine + rules.yaml + hot-reload; cost/latency/capability/sticky strategies)

### Phase 5: Observability v2
**Goal**: Operators can deploy MultiLLM with Prometheus + Grafana + Alertmanager, get error/latency/cost/breaker visibility per backend with bounded cardinality, and reference real SLO numbers measured against the Phase 1 baseline
**Depends on**: Phase 4 (routing-decision labels), Phase 2b (tenant_tier label discipline)
**Requirements**: OBS-01, OBS-02, OBS-03, OBS-04, OBS-05, OBS-06, OBS-07, OBS-08, OBS-09, OBS-10
**Success Criteria** (what must be TRUE):
  1. `GET /metrics` exposes request rate, error rate, p50/p95/p99 latency, token counts, cost, and breaker state per backend
  2. Labels are bounded to `backend`, `model`, `tenant_tier`, `status_code` — never `user_id`, `tenant_id`, or `request_id`
  3. 100-tenant synthetic load CI test produces total time-series count < 5,000; multi-worker `/metrics` aggregates correctly via `PROMETHEUS_MULTIPROC_DIR`
  4. Grafana JSON in `deploy/grafana/dashboards/` loads cleanly in a fresh Grafana 11; Alertmanager rules in `deploy/prometheus/alerts.yml` pass `promtool check rules`
  5. `docs/operations/slo.md` defines 99.5% availability and p95 < 2s referencing baseline measurements captured in Phase 1
**Plans**: TBD (target: 1–2 plans — metrics endpoint + cardinality guard; Grafana JSON + Alertmanager rules + SLO doc)

### Phase 6: Caching v2 (Semantic)
**Goal**: Tenants can opt into semantic dedup safely. The cross-tenant canary test exists and passes — no tenant ever sees another tenant's cached response, even on near-duplicate prompts
**Depends on**: Phase 2b (tenant_id in cache key; semantic-mode opt-in is per-tenant)
**Requirements**: CACHE-01, CACHE-02, CACHE-03, CACHE-04, CACHE-05, CACHE-06, CACHE-07, CACHE-08, CACHE-09
**Success Criteria** (what must be TRUE):
  1. Cache key includes `tenant_id` before any semantic lookup; there is no global vector index across tenants
  2. Default mode is `strict` (exact match on `(tenant_id, model, system_prompt_hash, body_hash)`); semantic is per-tenant opt-in only
  3. Cross-tenant canary test passes: tenant A stores `TENANT_A_CANARY_TOKEN`; tenant B's semantically-similar query response contains zero occurrences of the canary
  4. Embeddings use `fastembed` ONNX runtime with `BAAI/bge-small-en-v1.5`; no `torch` dependency at runtime; vectors stored in `sqlite-vec`
  5. Dashboard cache analytics widget shows hit rate, cumulative $ saved, and semantic-vs-strict hit breakdown
**Plans**: TBD (target: 1–2 plans — per-tenant vector pools + sqlite-vec/fastembed integration; cache modes + TTL + dashboard analytics)

### Phase 7: Eval Harness
**Goal**: `multillm eval run <suite> --models a,b,c` produces a defensible comparison table. Pre-cutoff public benchmarks carry a contamination warning; dynamic suite generation mitigates memorization-based inflation
**Depends on**: Phase 4 (routing aliases under eval), Phase 5 (metric integration for nightly eval)
**Requirements**: EVAL-01, EVAL-02, EVAL-03, EVAL-04, EVAL-05, EVAL-06, EVAL-07, EVAL-08
**Success Criteria** (what must be TRUE):
  1. `multillm eval run <suite> --models a,b,c` produces a comparison table with accuracy, p50/p95 latency, and cost per model
  2. Reference suites ship for code generation, JSON-mode structured output, function calling, summarization, and reasoning — vendor-neutral YAML, runnable via promptfoo `http` provider
  3. Pre-cutoff public benchmarks (HumanEval/MMLU/GSM8K) carry a "potentially contaminated" badge; dynamic suite generator produces fresh prompt variations at eval time
  4. Nightly eval job (GitHub Actions) runs the default suite and posts results to `docs/leaderboard/`; alerts on >5% regression
  5. Dashboard leaderboard surfaces latest results with a methodology link on every row
**Plans**: TBD (target: 1–2 plans — CLI runner + reference suites + dynamic generator; leaderboard UI + nightly job)

### Phase 8: More Backends
**Goal**: Five new backends (Cohere, Perplexity, vLLM, Replicate, HuggingFace) each pass the same converter / streaming / tracking / health / breaker test matrix as the existing 16
**Depends on**: Phase 2a (adapter pattern landed in hot path)
**Requirements**: BACK-01, BACK-02, BACK-03, BACK-04, BACK-05, BACK-06, BACK-07
**Success Criteria** (what must be TRUE):
  1. Cohere (Command R+, Command R) and Perplexity (Sonar) backends pass the full adapter test matrix
  2. Local vLLM backend auto-discovers models from a vLLM OpenAI-compatible endpoint
  3. Replicate and HuggingFace Inference API backends pass the full matrix
  4. Each new backend has a `COST_TABLE` entry, a dashboard chip, a circuit breaker, and a health probe
  5. Total backend count is 21; aggregate test count grows by the matrix multiplier without flakes
**Plans**: TBD (target: 1–2 plans — Cohere/Perplexity/HF cloud trio; vLLM + Replicate local/passthrough pair)

### Phase 9: Plugin / Extension API
**Goal**: Third parties can `pip install multillm-plugin-foo` and have a new backend or middleware appear with no gateway code changes — and a malicious plugin trying to read `~/.multillm/auth.db` fails because process isolation holds
**Depends on**: Phase 2a (adapter SDK base), Phase 4 (middleware pipeline patterns), Phase 6 (tenant-aware cache hook patterns)
**Requirements**: PLUG-01, PLUG-02, PLUG-03, PLUG-04, PLUG-05, PLUG-06, PLUG-07, PLUG-08, PLUG-09, PLUG-10
**Success Criteria** (what must be TRUE):
  1. `multillm-plugin-sdk` published with `BaseAdapter` / `BaseMiddleware` ABCs, lifecycle hook decorators, and type-checked interfaces
  2. Plugin discovery via `importlib.metadata.entry_points()` groups: `multillm.backends`, `multillm.middleware`, `multillm.routers`
  3. Trust tiers enforced: first-party auto-installable + signature-verified; curated third-party admin-approved; arbitrary admin opt-in only
  4. Third-party plugins execute in subprocess via JSON-RPC over stdio; malicious test plugin trying to read `~/.multillm/auth.db` fails in regression test
  5. Marketplace UI in dashboard browses curated plugin index, shows permissions, supports install/configure; example plugin repo published with template + tutorial
**Plans**: TBD (target: 2–3 plans — SDK + entry-point discovery + middleware pipeline generalization; subprocess isolation + signing/trust tiers; marketplace UI + example plugin)

### Phase 10: State-of-the-Art Docs Site
**Goal**: A new user reaches first `/v1/messages` request in under 5 minutes from `git clone`. The docs site documents what actually shipped, not aspirational state
**Depends on**: All prior phases (documents shipped reality)
**Requirements**: DOCS-01, DOCS-02, DOCS-03, DOCS-04, DOCS-05, DOCS-06, DOCS-07, DOCS-08, DOCS-09
**Success Criteria** (what must be TRUE):
  1. MkDocs Material site at `docs/` covers Quickstart, Concepts, Backends, API Reference, Deployment, Operations, Contributing
  2. API reference auto-generates from docstrings via `mkdocstrings-python` with no broken anchors; Mermaid architecture diagrams render in Concepts
  3. Quickstart is verified by a recorded fresh-machine walkthrough: `git clone` to first request in under 5 minutes
  4. Methodology pages exist for cost calculator, eval contamination, and semantic-cache safety alongside their feature docs
  5. Site deploys to GitHub Pages via Actions on every tagged release; preview deploys on every PR; offline lunr search ranks code references appropriately
**Plans**: TBD (target: 1–2 plans — MkDocs scaffold + structure + API auto-gen; methodology pages + walkthrough + GH Pages deploy)
**UI hint**: yes

---

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Open-Source Readiness | 3/9 | In Progress|  |
| 2a. Adapter Hot-Path Refactor | 0/TBD | Not started | - |
| 2b. Auth & Multi-Tenancy | 0/TBD | Not started | - |
| 3. Dashboard v2 Capability Showcase | 0/TBD | Not started | - |
| 4. Advanced Routing | 0/TBD | Not started | - |
| 5. Observability v2 | 0/TBD | Not started | - |
| 6. Caching v2 (Semantic) | 0/TBD | Not started | - |
| 7. Eval Harness | 0/TBD | Not started | - |
| 8. More Backends | 0/TBD | Not started | - |
| 9. Plugin / Extension API | 0/TBD | Not started | - |
| 10. State-of-the-Art Docs Site | 0/TBD | Not started | - |

---

## Phase Dependency Graph

```
P1 (OSS readiness + Docker + migration scaffold)
 ↓
P2a (adapter refactor + repo Protocol — no behavior change)
 ↓
P2b (tenancy + auth + budgets — behavior change, gated on 2a)
 ↓                ↓                  ↓
P3 (dashboard)   P4 (routing)       P8 (more backends)
                  ↓
                 P5 (observability — needs routing-decision labels)
                  ↓
                 P6 (semantic cache — tenant-scoped pools)
                  ↓
                 P7 (eval harness)
                  ↓
P3 ┐                                  ┌ P4
   └───→ P9 (plugin SDK — generalizes 2a/4/6 patterns) ←┘
P6 ┘                                  └ P8
                 ↓
                 P10 (docs — documents what shipped)
```

**Hard invariants:**
- P2a blocks P2b (refactor must land before behavior change)
- P2b blocks P3 (dashboard must ship tenant-aware) and P6 (per-tenant cache pools mandatory)
- P4 blocks P5 (routing-decision data appears as metric labels)
- P9 runs after 2a/4/6 (generalizes patterns proven in those phases)
- P10 runs last (documents shipped reality, not aspiration)

---

## Coverage Validation

| Category | REQ-IDs | Count | Phase | Mapped? |
|----------|---------|-------|-------|---------|
| OSS | OSS-01..OSS-19 | 19 | Phase 1 | ✓ |
| ARCH | ARCH-01..ARCH-07 | 7 | Phase 2a | ✓ |
| AUTH | AUTH-01..AUTH-21 | 21 | Phase 2b | ✓ |
| DASH | DASH-01..DASH-14 | 14 | Phase 3 | ✓ |
| ROUTE | ROUTE-01..ROUTE-10 | 10 | Phase 4 | ✓ |
| OBS | OBS-01..OBS-10 | 10 | Phase 5 | ✓ |
| CACHE | CACHE-01..CACHE-09 | 9 | Phase 6 | ✓ |
| EVAL | EVAL-01..EVAL-08 | 8 | Phase 7 | ✓ |
| BACK | BACK-01..BACK-07 | 7 | Phase 8 | ✓ |
| PLUG | PLUG-01..PLUG-10 | 10 | Phase 9 | ✓ |
| DOCS | DOCS-01..DOCS-09 | 9 | Phase 10 | ✓ |
| **Total** | — | **124** | 11 phases | **100%** |

✓ All 124 v1 requirements mapped to exactly one phase. Zero orphans, zero duplicates.

---

*Last updated: 2026-05-16 by gsd-roadmapper (auto mode)*
