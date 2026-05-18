---
phase: 02b-auth-multi-tenancy
created: 2026-05-18
status: discussed (local-first scope); ready for plan-phase
spec_loaded: false
scope: local-first-slice
---

# Phase 2b — Auth & Multi-Tenancy (CONTEXT, local-first slice)

## Domain

The ROADMAP Phase 2b goal was full multi-tenant SaaS-style auth: user accounts, login flows, multi-org tenancy, admin CRUD, audit log, migration-fixture validation, per-tenant quotas, rate limiting, etc. — 21 AUTH requirements totaling roughly 4-6 weeks of work.

**Operator pivot (2026-05-18):** the project is being developed local-first ("I want to use it locally, and enhance it over time"), not as a multi-tenant SaaS. Public publication of distribution artifacts was already deferred in Phase 1 (plan 01-09). Phase 2b's full SaaS posture is similarly deferred. **This phase delivers the ~11-requirement local-first slice.**

What 2b delivers locally:
1. **API key auth** — the gateway is gated by a real `mllm_live_*` / `mllm_test_*` key. Without it, a `curl` to `/v1/messages` returns 401.
2. **Cross-tenant DB isolation** — every data-access call site threads `tenant_id="default"` through the Phase 2a Protocols (`SessionRepo`, `TrackingRepo`, `MemoryRepo`). Queries are safe-by-construction; adding a real second tenant later is a one-line change at the auth-middleware level.
3. **SQLi prevention** — regression test + CI grep gate ensure parameterized queries everywhere.
4. **Spend caps** — daily/monthly budget per tenant with atomic decrement. Stops runaway loops from burning $20 on Anthropic API in 5 minutes.
5. **Auto-upgrade bridge** — existing data (tracking, memory, sessions) gets backfilled with `tenant_id="default"` on first start after the migration runs. No login screen.

## Canonical refs

These files MUST be read by researcher and planner before authoring any plan:

- `.planning/ROADMAP.md` — Phase 2b section (lines covering goal, requirements, success criteria)
- `.planning/REQUIREMENTS.md` — AUTH-05/06/07/08/09, AUTH-11/12, AUTH-15, AUTH-16/17, AUTH-18 (the in-scope set); AUTH-01/02/03/04/10/13/14/19/20/21 are EXPLICITLY DEFERRED, do not generate tasks for them
- `multillm/db/repo.py` — the Protocol shapes from Phase 2a (`SessionRepo`, `TrackingRepo`, `MemoryRepo` with `tenant_id`-first signatures, 12 methods). Phase 2b implements concrete repos against these Protocols.
- `multillm/db/__init__.py` — package marker
- `multillm/tracking.py`, `multillm/memory.py`, `multillm/sessions.py` — existing data-access modules. Phase 2b refactors these to implement the Protocols + accept `tenant_id` on every call.
- `multillm/auth.py` — existing optional API-key middleware (Phase 0 vintage). Phase 2b extends or replaces this.
- `multillm/migrations/runner.py` + `multillm/migrations/versions/` — alembic scaffolding from Phase 1 plan 01-03. The schema migration in 02b-01 lands as a new `versions/000N_*.py` file.
- `multillm/gateway.py` — auth middleware mounting point, `/v1/messages` budget check site, lifespan-startup migration trigger
- `.gitleaks.toml` — secret-scan rules; new patterns added for `mllm_live_*` / `mllm_test_*` so accidental check-ins get caught

No SPEC.md for this phase — the requirements selection above IS the spec.

## Locked decisions

### D-2b-01: Local-first scope — explicit IN / OUT list

**IN (11 requirements):**

| Req | Description |
|-----|-------------|
| AUTH-05 | Per-tenant API keys with explicit scopes; plaintext shown once on creation |
| AUTH-06 | Key format: `mllm_live_<token_urlsafe(32)>` (production) / `mllm_test_<token_urlsafe(32)>` (non-billing test) |
| AUTH-07 | API keys stored as SHA-256 hashes; plaintext never logged or persisted |
| AUTH-08 | Comparison uses `hmac.compare_digest` (timing-safe) |
| AUTH-09 | User can revoke API keys; revoked keys return 401 immediately |
| AUTH-11 | Daily and monthly spend cap; exhausted returns HTTP 429 with `Retry-After` |
| AUTH-12 | Atomic decrement; 50 concurrent vs $1 budget overshoots ≤ 10% |
| AUTH-15 | Two-tenant isolation: zero cross-tenant rows in usage / sessions / memory queries |
| AUTH-16 | SQLi regression test (`Authorization: Bearer ' OR 1=1 --`) returns 401, not 500 |
| AUTH-17 | CI grep gate: `rg "execute\(.*f['\"]" multillm/` returns zero matches |
| AUTH-18 | Single-user install auto-upgrades to `tenant_id='default'`; existing data preserved |

**OUT (deferred for a future SaaS phase, AUTH-2c or similar):**

| Req | Why deferred |
|-----|--------------|
| AUTH-01 / AUTH-02 / AUTH-03 | User accounts (email/password, sessions, logout) — no UI need for local single-user |
| AUTH-04 | Admin user CRUD — single-user has no admin distinction |
| AUTH-10 | Multi-org tenancy hierarchy — single tenant is enough for local-first |
| AUTH-13 / AUTH-14 / AUTH-20 | Per-tenant request quotas + backend allow/deny + slowapi rate limiting — premature for single-user |
| AUTH-19 | 1k-session / 10k-memory migration fixture validation — no real users to migrate yet |
| AUTH-21 | Admin tenant CRUD + audit log — no multi-tenant operations to audit |

### D-2b-02: API key format and storage

- **Format**: `mllm_live_<token_urlsafe(32)>` and `mllm_test_<token_urlsafe(32)>` per AUTH-06. `secrets.token_urlsafe(32)` produces ~43 chars of base64url, so a full key is ~54 chars including the prefix.
- **Storage**: SQLite `api_keys` table with columns `(id, tenant_id, key_hash, key_prefix, label, scopes, created_at, revoked_at)`. `key_hash` is `hashlib.sha256(key.encode()).hexdigest()` (64 hex chars). `key_prefix` is the literal first 12 chars of the key for human identification in the dashboard (`mllm_live_xy`...). Plaintext is shown once on creation and never persisted.
- **Comparison**: `hmac.compare_digest(stored_hash, hashlib.sha256(incoming_key.encode()).hexdigest())` — timing-safe.
- **Revocation**: setting `revoked_at` to a non-NULL timestamp. Lookup query joins `WHERE revoked_at IS NULL`.

### D-2b-03: Single tenant always "default"

The Protocol implementations from 02b-01 always pass `tenant_id="default"` from the gateway middleware. The API key → tenant_id resolution is `SELECT tenant_id FROM api_keys WHERE key_hash = ?` — and since every key currently belongs to `tenant_id="default"`, that's the only value the middleware ever produces. A future SaaS phase changes the key creation flow to allow other tenant_id values; everything else stays unchanged.

The grep invariant for Phase 2a's `tenant_id="default"` literals (`git grep -nE 'repo\.\w+\(\s*"default"' multillm/`) becomes the **bridge** to a real multi-tenant future: every literal needs to become a tenant lookup. Phase 2b adds the lookup mechanism; the literals stay as-is.

### D-2b-04: Budget storage and atomic decrement

- **Schema**: `budgets` table with columns `(tenant_id PRIMARY KEY, daily_cap_cents, monthly_cap_cents, daily_remaining_cents, monthly_remaining_cents, day_started_at, month_started_at)`. Cents-as-integer avoids floating-point drift.
- **Daily/monthly rollover**: pre-decrement check compares `day_started_at` / `month_started_at` to current UTC date; if rolled over, `daily_remaining = daily_cap` (and same for monthly) in the same transaction as the decrement.
- **Atomic decrement** (AUTH-12): single SQL statement that fails-fast if either cap is exhausted:
  ```sql
  UPDATE budgets
  SET daily_remaining_cents  = daily_remaining_cents - :cost,
      monthly_remaining_cents = monthly_remaining_cents - :cost
  WHERE tenant_id = :tid
    AND daily_remaining_cents >= :cost
    AND monthly_remaining_cents >= :cost
  RETURNING daily_remaining_cents, monthly_remaining_cents
  ```
  If 0 rows are returned, the cap is exhausted — gateway returns 429 with `Retry-After: <seconds-to-next-day-rollover>`.
- **Cost source**: `COST_TABLE` in `tracking.py` already has per-1M-token rates for every backend. The middleware computes cost from response usage and runs the decrement post-response (so we don't reserve cost we won't spend on errors). Tradeoff: a single request can overshoot by its own cost — AUTH-12's "≤10% overshoot under 50 concurrent" requires per-request reservation OR optimistic-with-clamp. Plan tradeoff to lock during plan-phase.

### D-2b-05: Auto-upgrade migration (AUTH-18)

- **Migration runner trigger**: lifespan startup calls `multillm migrate up` automatically (existing migration runner from Phase 1 plan 01-03). New schema migration `versions/000N_auth_tenancy.py` creates `api_keys` + `budgets` tables AND backfills `tenant_id = "default"` columns onto existing `usage`, `sessions`, `memory` tables.
- **Idempotency**: re-running the migration is a no-op. Tested via `multillm migrate down && multillm migrate up` cycle.
- **First-start UX**: on first start after the migration runs, the gateway prints to stdout AND logs at INFO level:
  ```
  ════════════════════════════════════════════════════════════════
   First-start migration complete. A `default` tenant has been
   created. To gate the gateway with an API key, run:
       multillm api-key create --tenant default --label "local"
   Then export the printed key as MULTILLM_API_KEY in your shell.
  ════════════════════════════════════════════════════════════════
  ```
  The gateway runs unauthenticated until `MULTILLM_API_KEY` is set OR the operator manually flips a config flag — this preserves the existing zero-friction local development experience (D-2b-08).

### D-2b-06: SQLi prevention (AUTH-16/17)

- **All queries parameterized**: no f-string interpolation into SQL. Existing modules audited; any violation gets a TODO comment + fix in this phase.
- **CI grep gate**: `.github/workflows/ci.yml` adds a step running `rg "execute\(.*f['\"]" multillm/ tests/ && exit 1 || exit 0` — exits non-zero only if a match is found. The grep pattern is the same as AUTH-17 specifies.
- **Regression test** (AUTH-16): `tests/test_sqli_regression.py` posts `Authorization: Bearer ' OR 1=1 --` to `/v1/messages` and asserts status 401 (not 500, not 200). Mirror tests for `' UNION SELECT *` and `'; DROP TABLE api_keys; --`. The middleware's `hmac.compare_digest` makes all of these into 401 by construction.

### D-2b-07: Plan structure — 2 plans

Matches ROADMAP target. Concrete split (planner refines specifics):

- **Plan 02b-01: Schema + Protocol implementations + SQLi hardening**
  - Schema migration `versions/000N_auth_tenancy.py` — creates `api_keys`, `budgets` tables; backfills `tenant_id="default"` onto existing usage/sessions/memory tables; commits via alembic.
  - Concrete `SessionRepo`, `TrackingRepo`, `MemoryRepo` implementations in `multillm/db/{sessions,tracking,memory}.py`, implementing the Phase 2a Protocols. Existing `multillm/tracking.py` / `multillm/memory.py` / `multillm/sessions.py` keep their outward API (test compat) but delegate to the new repos.
  - Audit all existing data-access call sites; replace any f-string-interpolated SQL with parameterized queries.
  - `tests/test_sqli_regression.py` lands here.
  - CI grep gate added to ci.yml.

- **Plan 02b-02: API keys + budgets + auto-upgrade**
  - `multillm/auth.py` rewritten/extended: `mllm_live_*` / `mllm_test_*` parsing, SHA-256 verification via `hmac.compare_digest`, tenant resolution, 401 on revoked/missing/invalid keys.
  - `multillm api-key create / list / revoke` CLI subcommands (extends the alembic-flavored CLI from Phase 1 plan 01-03).
  - Budget enforcement middleware on `/v1/messages`: pre-decrement against cost estimate (clamp at 0 instead of overshoot), post-response reconcile against actual cost.
  - First-start UX banner on lifespan startup if no API keys exist yet.
  - `tests/test_budget_atomic_decrement.py` — 50-concurrent-request stress test against a $1 cap, asserts overshoot ≤ 10%.
  - Three new patterns added to `.gitleaks.toml`: `mllm_live_*`, `mllm_test_*`, raw SHA-256 of an API key (64-hex).

### D-2b-08: Backward compatibility — gateway runs unauthenticated if no `MULTILLM_API_KEY`

This is the critical local-first guarantee. The gateway preserves its current `MULTILLM_API_KEY`-gated mode:
- If `MULTILLM_API_KEY` env var is set: auth middleware is enabled, requests without `Authorization: Bearer ...` matching a non-revoked key return 401.
- If `MULTILLM_API_KEY` is NOT set: auth middleware is disabled, requests pass through (existing behavior). Budgets still apply (always-on safety rail).

This makes the upgrade path from a current install zero-friction: existing operators don't need to do anything to keep their setup working. To turn auth on, they create an API key via `multillm api-key create` and export the result.

## Code context

### Existing data-access modules (read by planner before structuring 02b-01)

| Module | Lines | Current API |
|--------|-------|-------------|
| `multillm/tracking.py` | (read at plan-phase) | `record_usage`, `get_usage_summary`, `get_dashboard_stats`, `get_sessions`, `get_session_detail`, `get_active_sessions`, `init_otel`, ... |
| `multillm/memory.py` | (read at plan-phase) | `list_memories`, `search_memories`, `get_memory`, `store_memory`, `delete_memory`, ... |
| `multillm/sessions.py` | (read at plan-phase) | Session lifecycle helpers used by tracking.py |
| `multillm/auth.py` | (read at plan-phase) | Current optional API-key middleware (Phase 0 vintage) |
| `multillm/migrations/runner.py` | from Phase 1 plan 01-03 | Alembic CLI driver — used by the `multillm migrate up` command and at gateway startup |

### Bridge grep invariants

Phase 2b implementations satisfy two grep invariants used by future phases:

1. `git grep -nE 'def \w+\(self, tenant_id:' multillm/db/` — returns ≥ 12 (the Phase 2a Protocol shape stays intact; concrete repos add MORE tenant_id-first methods, not fewer)
2. `git grep -nE 'repo\.\w+\(\s*"default"' multillm/` — currently 0 (no existing call sites use the new repos). After 02b-01 ships, this should return one match per data-access call site that uses the repo Protocol. Future SaaS-tenancy phase replaces each literal with a real tenant context lookup.

## Deferred ideas

Captured for a future SaaS-tenancy phase (potentially `Phase 2c` or `Phase 8`):

- **AUTH-01/02/03/04**: Web UI for user account management (email/password, sessions, logout, admin CRUD). Defer until there's a real multi-user deployment story.
- **AUTH-10**: Multi-org tenancy hierarchy (User → Organization → API Key). Premature; single-tenant works.
- **AUTH-13/14/20**: Per-tenant request quotas (RPM/RPD), backend allow/deny lists, slowapi rate limiting. Useful for multi-tenant abuse prevention; overkill for local single-user (a runaway loop is bounded by the budget cap).
- **AUTH-19**: 1k-session / 10k-memory migration fixture validation. No real users to migrate yet; the migration is tested with empty-table baseline + a small synthetic fixture instead.
- **AUTH-21**: Admin tenant CRUD + audit log. Trivially deferable.

## Open questions for planner

These are NOT decisions — they're items for the planner to resolve from REQUIREMENTS.md and the codebase:

- Exact ordering of schema migration (single `000N_auth_tenancy.py` vs two migrations: one for `api_keys` + `budgets`, one for the `tenant_id` backfill). Planner picks based on existing migration patterns in `multillm/migrations/versions/`.
- Whether `multillm/auth.py` is rewritten in place or replaced by `multillm/auth/middleware.py` + `multillm/auth/keys.py` (split-file vs single-file decision based on existing module size).
- Whether the budget pre-decrement uses cost-estimate-from-route-table (cheap, may overshoot by one request) or per-token-after-response (accurate, may permit ≤10% overshoot under concurrency per AUTH-12). Planner picks based on the test expectation in AUTH-12.
- Whether CLI `multillm api-key create` prints the key in a banner or as raw stdout (operator-script ergonomics).
- The exact shape of the auth-keys row's `scopes` column — JSON array? CSV? bitmask? Planner picks based on cost-of-querying-individual-scopes (probably JSON for forward-flexibility).

## Success criteria recap

From ROADMAP Phase 2b (filtered to in-scope requirements):

1. **API key gate works**: with `MULTILLM_API_KEY` set, requests without a valid `Authorization: Bearer mllm_*` return 401. Revoked keys return 401 immediately.
2. **SQLi fuzz returns 401**: `Authorization: Bearer ' OR 1=1 --` returns 401, not 500. CI grep gate prevents f-string-interpolated SQL.
3. **Cross-tenant isolation holds for `tenant_id="default"`**: every query in `usage`, `sessions`, `memory` paths includes `WHERE tenant_id = ?` and is parameterized.
4. **Concurrent budget stress**: 50 requests vs $1 cap overshoots by ≤ 10%.
5. **Auto-upgrade migration**: existing tracking/memory/sessions rows get backfilled with `tenant_id="default"` on first start; idempotent across down/up cycles.
6. **Backward-compat preserved**: gateway runs unauthenticated when `MULTILLM_API_KEY` is unset (D-2b-08).

## What this phase does NOT deliver

(documented so reviewers / future-self can recognize when to revisit):

- No user account UI (email / password / session cookies).
- No admin dashboard.
- No multi-org tenancy.
- No per-tenant request quota or rate limiting (just budget cap).
- No backend allow/deny list per tenant.
- No audit log.
- No 1k/10k-row migration validation suite.

These belong to a future SaaS phase, scheduled when the project actually has more than one user.
