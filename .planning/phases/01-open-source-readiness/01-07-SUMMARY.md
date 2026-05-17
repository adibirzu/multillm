---
phase: 01-open-source-readiness
plan: 07
subsystem: setup-wizard
tags: [setup-wizard, first-run, argon2id, jinja2, redirect-middleware, alembic, vanilla-js, oss-readiness]

# Dependency graph
requires:
  - phase: 01-open-source-readiness
    plan: 03
    provides: alembic migration runner + multillm CLI + smoke migration (0001) — chained into 0002_setup_state
provides:
  - Argon2id password module (multillm.setup.passwords) — Phase 2b consumes verbatim
  - Setup state machine (multillm.setup.state) with SetupState enum + advance/complete/reset_setup
  - SetupRedirectMiddleware redirecting non-allowlisted routes to /setup until setup_complete='1'
  - Four-pane wizard (admin → backends → local-probe → observability) at /setup
  - admin_users table (id, email UNIQUE, password_hash, created_at) — Phase 2b extends to multi-user
  - system table seeded with setup_complete='0' on first migration
  - multillm reset --confirm CLI subcommand
affects: 01-08 (D-12 wizard surface), 02b (auth/tenancy inherits admin_users + password hashing)

# Tech tracking
tech-stack:
  added:
    - "argon2-cffi 25.1.0 (declared as >=23.1 in pyproject.toml)"
    - "jinja2 >=3.1 (explicit, was previously transitive via Starlette)"
  patterns:
    - "Two-table state machine: system (key/value) + setup_state (per-pane payloads cleared on completion)"
    - "Redirect middleware as outermost layer (added last) so it bypasses AuthMiddleware"
    - "Server-rendered HTML + vanilla progressive-enhancement JS — no JS framework"
    - "Parameterized SQL everywhere — no f-string SQL (Phase 2b CI grep gate pre-empted)"
    - "StaticFiles MUST be mounted on the FastAPI app (not the APIRouter) — exposed via mount_static() helper"

key-files:
  created:
    - "multillm/setup/__init__.py — package marker"
    - "multillm/setup/passwords.py — Argon2id hash_password/verify_password helpers, MIN_PASSWORD_LEN=12"
    - "multillm/setup/state.py — SetupState enum + advance/complete/reset_setup + is_complete"
    - "multillm/setup/middleware.py — SetupRedirectMiddleware with /health and /setup* allowlist"
    - "multillm/setup/routes.py — APIRouter for GET / + POST /admin /backends /observability /complete + GET /probe-local + mount_static helper"
    - "multillm/setup/templates/wizard.html — four-pane server-rendered wizard"
    - "multillm/setup/templates/complete.html — success surface"
    - "multillm/setup/static/wizard.css — editorial design tokens (oklch palette, two themes)"
    - "multillm/setup/static/wizard.js — progressive enhancement, ~4 KB unminified, zero deps"
    - "multillm/migrations/versions/0002_setup_state.py — system / setup_state / admin_users + seed setup_complete='0'"
    - "tests/test_setup_passwords.py — 7 tests"
    - "tests/test_setup_state.py — 9 tests"
    - "tests/test_setup_middleware.py — 7 tests"
    - "tests/test_setup_routes.py — 10 tests (incl. e2e integration test from Task 3)"
  modified:
    - "multillm/gateway.py — import SetupRedirectMiddleware + setup router; add middleware LAST (outermost); include_router + mount_setup_static"
    - "multillm/cli.py — add `multillm reset --confirm` subcommand"
    - "pyproject.toml — pin argon2-cffi>=23.1 and jinja2>=3.1"
    - "tests/test_migrations_runner.py — Rule 1 bug fix: head revision moved from 0001_smoke_test to 0002_setup_state"
    - "tests/test_migrations_cli.py — Rule 1 bug fix: head revision assertion updated"

key-decisions:
  - "Argon2id with argon2-cffi's PasswordHasher defaults (OWASP-2026 aligned, ~50ms verify on commodity CPU)"
  - "MIN_PASSWORD_LEN=12 (above NIST 800-63B-rev4 minimum of 8) — server-enforced; client check is UX-only"
  - "Setup state derived from DB rows, not stored as enum — single source of truth, no drift risk"
  - "complete() WIPES setup_state of all panes (T-01-07-03 mitigation) — backend API keys exist only during the wizard window"
  - "Middleware order: add LAST in gateway.py so it runs FIRST (Starlette stack semantics) — clarified vs plan wording which said 'BEFORE existing middleware adds'"
  - "StaticFiles mounted on the FastAPI app via mount_static() helper, not on the APIRouter — APIRouter.mount() is not forwarded by include_router()"
  - "Local backend probe done inline in routes.py (asyncio + httpx + `which` subprocess) — health.py exposes only background-loop functions, no probe_backend(name) public surface"
  - "Wizard works fully offline: probe-local catches every exception per-backend, every POST handler treats inputs as optional, no network required to complete setup"

patterns-established:
  - "Outermost-middleware mount: setup redirect runs before AuthMiddleware so /setup is reachable on first boot"
  - "Static mount helper: APIRouter exposes routes only; the application mounts static files via a module-level helper that knows the canonical prefix"
  - "Per-pane state advance: advance(conn, pane, payload) is the single write path; admin pane also upserts admin_users"
  - "Server-rendered initial pane: body[data-state] reflects setup_state, JS resumes from the right pane on refresh"
  - "Probe-never-500 contract: every probe catches BaseException and returns {reachable: False, models: [], error: <type>}"

requirements-completed: [OSS-20]

# Metrics
duration: 28min
completed: 2026-05-17
---

# Phase 01 Plan 07: First-Run Setup Wizard Summary

**Ship the first-run `/setup` wizard backed by Argon2id password hashing, a redirect middleware enforcing initial-config flow, and a single-admin state machine that Phase 2b inherits whole.**

## Performance

- **Duration:** 28 min
- **Started:** 2026-05-17T17:16Z (approximate, RED commit timestamp)
- **Completed:** 2026-05-17T17:44Z
- **Tasks:** 3/3
- **Files modified:** 5 (multillm/cli.py, multillm/gateway.py, pyproject.toml, tests/test_migrations_runner.py, tests/test_migrations_cli.py)
- **Files created:** 14 (5 setup-package modules + 2 templates + 2 static + 1 migration + 4 test modules)

### Per-pane wizard latency (single-process TestClient, warm DB)

| Endpoint                  | Latency  | Notes                                          |
|---------------------------|----------|------------------------------------------------|
| GET /setup                | ~10 ms   | Jinja2 render + 1 sqlite SELECT                |
| POST /setup/admin         | ~50 ms   | Dominated by Argon2id hashing (OWASP-aligned)  |
| POST /setup/backends      | ~4 ms    | UPSERT + filter                                |
| GET /setup/probe-local    | ~94 ms   | 4 backend probes in parallel, 2 s timeout each |
| POST /setup/observability | ~4 ms    | UPSERT                                         |
| POST /setup/complete      | ~3 ms    | UPDATE + DELETE                                |

Every interactive pane is well under the 100 ms target. probe-local is bound by network/subprocess latency, not gateway work, and runs probes concurrently.

## Accomplishments

- First-run wizard reachable at `/setup`, redirect middleware funnels every other route there until `setup_complete='1'`.
- Argon2id password hashing module ready for Phase 2b to import verbatim — single point of truth for password policy.
- Migration `0002_setup_state` chains cleanly off `0001_smoke_test`; head is now `0002_setup_state`.
- `multillm reset --confirm` re-enables the wizard for testing or re-bootstrap; bare `multillm reset` refuses with exit 1.
- 33 new tests across four modules, plus 6 pre-existing migration tests updated for the new head. Full suite is 351 passing.

## Task Commits

1. **Task 1 (TDD): passwords + state + migration 0002**
   - `e965753` `test(01-07): failing tests for setup.passwords and setup.state (RED)`
   - `5aad856` `feat(01-07): argon2id password hashing + setup state machine + migration 0002 (GREEN)`
2. **Task 2 (TDD): middleware + routes + wizard UI**
   - `18f6c4e` `test(01-07): wizard middleware + routes failing tests (RED)`
   - `bb21bc3` `feat(01-07): SetupRedirectMiddleware + /setup routes + wizard UI (GREEN)`
3. **Task 3: multillm reset --confirm + integration test**
   - `efc6079` `feat(01-07): multillm reset --confirm + full first-run integration test (GREEN)` (also carries the migration-test head-revision bug fix; see Deviations)

## TDD Gate Compliance

| Task | RED   | GREEN | REFACTOR                          |
|------|-------|-------|-----------------------------------|
| 1    | e965753 | 5aad856 | none needed (UPSERT helper extracted directly in GREEN) |
| 2    | 18f6c4e | bb21bc3 | none needed                       |
| 3    | (covered by Task 2 RED for the e2e test, since it lives in tests/test_setup_routes.py) | efc6079 | none needed |

All RED commits preceded their corresponding GREEN commits; tests confirmed failing with `ModuleNotFoundError` (Task 1) and `404`/route-not-found errors (Task 2) before implementation.

## Deviations from Plan

### Auto-fixed (Rule 1)

**1. [Rule 1 - Bug] Pre-existing migration tests asserted obsolete head revision**
- **Found during:** Full suite run after Task 1 GREEN
- **Issue:** `tests/test_migrations_runner.py` and `tests/test_migrations_cli.py` had hard-coded assertions that the alembic head equals `0001_smoke_test`. Plan 01-07's deliverable (migration 0002) intentionally advances head to `0002_setup_state`, invalidating those assertions.
- **Fix:** Updated the five affected assertions to either reference `0002_setup_state` (head-specific tests) or expect both revisions in the dry-run list. Production behaviour is unchanged.
- **Files modified:** `tests/test_migrations_runner.py`, `tests/test_migrations_cli.py`
- **Commit:** `efc6079`

### Clarifications (no behaviour change)

**1. Middleware ordering language**
- The plan said "Add SetupRedirectMiddleware BEFORE existing middleware adds." Starlette's BaseHTTPMiddleware stack runs **last-added first** (each `add_middleware` call wraps the existing stack as an outer layer). To meet the plan's GOAL ("run before any auth middleware so /setup itself stays reachable"), the middleware is added **LAST** in `gateway.py`. Documented inline in the gateway diff.

**2. `health.probe_backend(name)` does not exist in the current `multillm/health.py`**
- The plan's <interfaces> assumed a public single-backend probe function. The existing module only exposes a background loop. To keep this plan self-contained and avoid a Phase-1 detour into refactoring `health.py`, the wizard probe is implemented inline in `multillm/setup/routes.py` using asyncio + httpx + `which` for CLI binaries. The contract (`{reachable, models, error?}`) and the "never-500" guarantee match the plan.

**3. StaticFiles mount lives on the FastAPI app, not the APIRouter**
- `APIRouter.mount(...)` is not forwarded through `app.include_router(prefix=...)`. The wizard exposes `mount_static(app)` so both `gateway.py` and the test harness's `_build_app()` mount the static dir at `/setup/static` directly.

## Threat Model Mitigations

| Threat ID    | Mitigation outcome                                                                                   |
|--------------|------------------------------------------------------------------------------------------------------|
| T-01-07-01 (S) | Documented in this SUMMARY + (will land in) SECURITY.md: gateway binds to loopback by default. |
| T-01-07-02 (T) | `MIN_PASSWORD_LEN=12` enforced server-side in `hash_password`; covered by `test_hash_password_rejects_short_password` and `test_post_admin_rejects_short_password`. |
| T-01-07-03 (I) | `complete()` runs `DELETE FROM setup_state`. Covered by `test_complete_sets_flag_and_clears_setup_state`. |
| T-01-07-04 (I) | `POST /setup/admin` returns only `{state: "admin_created"}` — no hash echo. |
| T-01-07-05 (E) | Middleware added LAST so it wraps AuthMiddleware; allowlist hard-codes `/health` and `/setup` prefixes. |
| T-01-07-06 (R) | Accepted for P1; `setup_state.completed_at` provides a coarse audit trail. |
| T-01-07-07 (D) | Accepted; argon2-cffi's ~50 ms verify time + loopback default + Phase 2b rate-limit plan covers this. |

## Tech Inheritance — Phase 2b Notes

Phase 2b (auth & multi-tenancy, plan TBD) SHOULD reuse these without re-implementation:

- `multillm.setup.passwords` — already OWASP-aligned; just import and use.
- `admin_users` table — extend with `tenant_id`, `role`, `last_login_at`; do not recreate.
- `multillm.setup.state.SetupState` — extend with additional states (e.g. `TENANT_BOOTSTRAPPED`) but keep the derivation-from-DB pattern.
- Parameterized-SQL discipline — every statement in the setup package uses `?` substitution; Phase 2b's CI grep gate (forbidding f-string SQL) will pass on this code out of the box.

## Verification

- All four new test modules pass: `pytest tests/test_setup_*.py -v` → 33/33
- Pre-existing migration tests pass after head-revision updates: 10/10
- Full suite: **351 passed in 9.84s**
- `argon2-cffi==25.1.0` installed and pinned at `>=23.1` in pyproject.toml
- Migration 0002 chains off 0001_smoke_test: `multillm migrate up` from a fresh DB applies both
- No JS framework references in `multillm/setup/static/wizard.js`: confirmed via `grep -Ei "jquery|react|vue|svelte"` returning empty
- `multillm reset --confirm` exits 0 and clears state; `multillm reset` (no flag) exits 1 with refuse message
- End-to-end integration test (`test_full_first_run_flow_then_reset_re_enables_wizard`) walks the full happy path including reset

## Self-Check: PASSED

Files created/modified verified present:

- `multillm/setup/__init__.py` — FOUND
- `multillm/setup/passwords.py` — FOUND
- `multillm/setup/state.py` — FOUND
- `multillm/setup/middleware.py` — FOUND
- `multillm/setup/routes.py` — FOUND
- `multillm/setup/templates/wizard.html` — FOUND
- `multillm/setup/templates/complete.html` — FOUND
- `multillm/setup/static/wizard.css` — FOUND
- `multillm/setup/static/wizard.js` — FOUND
- `multillm/migrations/versions/0002_setup_state.py` — FOUND
- `tests/test_setup_passwords.py` — FOUND
- `tests/test_setup_state.py` — FOUND
- `tests/test_setup_middleware.py` — FOUND
- `tests/test_setup_routes.py` — FOUND

Commits verified in git log:

- `e965753` — FOUND (Task 1 RED)
- `5aad856` — FOUND (Task 1 GREEN)
- `18f6c4e` — FOUND (Task 2 RED)
- `bb21bc3` — FOUND (Task 2 GREEN)
- `efc6079` — FOUND (Task 3 GREEN + migration test bug fix)
