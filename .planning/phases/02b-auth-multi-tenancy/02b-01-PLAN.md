---
phase: 02b-auth-multi-tenancy
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - multillm/migrations/versions/0003_auth_tenancy.py
  - multillm/db/sessions.py
  - multillm/db/tracking.py
  - multillm/db/memory.py
  - multillm/db/__init__.py
  - multillm/tracking.py
  - multillm/memory.py
  - multillm/sessions.py
  - tests/test_auth_tenancy_migration.py
  - tests/test_sqli_regression.py
  - tests/test_db_repos.py
  - .github/workflows/ci.yml
autonomous: true
requirements:
  - AUTH-15
  - AUTH-16
  - AUTH-17
  - AUTH-18
tags:
  - schema
  - migrations
  - tenancy
  - sqli
  - protocols
user_setup: []

must_haves:
  truths:
    - "Running 'multillm migrate up' on a stock DB creates api_keys + budgets tables and backfills tenant_id='default' onto usage/sessions/memory rows."
    - "Concrete TrackingRepo / SessionRepo / MemoryRepo classes exist under multillm/db/ and satisfy the Phase 2a Protocols at runtime (isinstance(repo, TrackingRepo) is True)."
    - "Existing call sites (multillm/tracking.py, multillm/memory.py, multillm/sessions.py) keep their public functions but delegate to the new repos so the 378-test Phase 2a baseline stays green."
    - "POSTing /v1/messages with 'Authorization: Bearer ' OR 1=1 --' returns HTTP 401 (not 500, not 200)."
    - "rg \"execute\\(.*f['\\\"]\" multillm/ tests/ returns zero matches (no f-string-interpolated SQL anywhere)."
    - "A CI step named 'SQL injection guard' fails the workflow if a future commit reintroduces an f-string SQL pattern."
    - "Migration is idempotent: 'multillm migrate down && multillm migrate up' restores identical row counts and column shapes."
  artifacts:
    - path: "multillm/migrations/versions/0003_auth_tenancy.py"
      provides: "Alembic migration creating api_keys + budgets tables and adding tenant_id columns to usage/sessions/memory."
      contains: "revision: str = \"0003_auth_tenancy\""
    - path: "multillm/db/tracking.py"
      provides: "Concrete TrackingRepo implementation (record_usage, get_dashboard, get_summary) using parameterized SQL only."
      exports: ["TrackingRepoSqlite"]
    - path: "multillm/db/sessions.py"
      provides: "Concrete SessionRepo implementation (list_sessions, get_session, create_session, append_request)."
      exports: ["SessionRepoSqlite"]
    - path: "multillm/db/memory.py"
      provides: "Concrete MemoryRepo implementation (list_memories, search_memories, get_memory, store_memory, delete_memory)."
      exports: ["MemoryRepoSqlite"]
    - path: "tests/test_sqli_regression.py"
      provides: "Three SQLi fuzz vectors against /v1/messages auth path; asserts 401 for each."
      contains: "' OR 1=1 --"
    - path: "tests/test_auth_tenancy_migration.py"
      provides: "Idempotency + backfill verification for migration 0003."
    - path: ".github/workflows/ci.yml"
      provides: "Step 'SQL injection guard' running the grep gate."
      contains: "SQL injection guard"
  key_links:
    - from: "multillm/tracking.py"
      to: "multillm/db/tracking.py"
      via: "module-level delegation (existing functions call TrackingRepoSqlite(conn) under the hood)"
      pattern: "from multillm.db.tracking import TrackingRepoSqlite"
    - from: "multillm/migrations/versions/0003_auth_tenancy.py"
      to: "multillm/migrations/runner.py"
      via: "alembic revision chain (down_revision = '0002_setup_state')"
      pattern: "down_revision.*0002_setup_state"
    - from: "tests/test_sqli_regression.py"
      to: "multillm/gateway.py"
      via: "httpx TestClient POST /v1/messages with malicious Authorization header"
      pattern: "Authorization.*OR 1=1"
---

<objective>
Land the schema + data-access foundation for Phase 2b's local-first auth slice: a single alembic migration creates `api_keys` and `budgets` tables and backfills `tenant_id="default"` onto existing usage/sessions/memory rows; concrete `*RepoSqlite` classes implement the Phase 2a Protocols against parameterized SQL only; existing `multillm/tracking.py`, `multillm/memory.py`, `multillm/sessions.py` keep their public surface but delegate to the new repos so Phase 2a's 378-test suite stays green.

Purpose: closes AUTH-15 (cross-tenant isolation enforced at the query layer), AUTH-16 (SQLi regression test), AUTH-17 (CI grep gate), AUTH-18 (auto-upgrade migration). This plan deliberately does NOT touch the gateway middleware — Plan 02b-02 wires API key + budget enforcement on top of these repos.

Output: one new migration, three new db modules, three refactored data-access modules, three new test files, one CI step.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@.planning/phases/02b-auth-multi-tenancy/02b-CONTEXT.md
@multillm/db/repo.py
@multillm/migrations/versions/0002_setup_state.py

<interfaces>
<!-- Locked decisions from 02b-CONTEXT.md — bind to these exactly. -->

D-2b-02 schema (api_keys):
  id INTEGER PRIMARY KEY AUTOINCREMENT
  tenant_id TEXT NOT NULL                     -- always "default" in 2b
  key_hash TEXT NOT NULL UNIQUE               -- hashlib.sha256(key.encode()).hexdigest() — 64 hex chars
  key_prefix TEXT NOT NULL                    -- first 12 chars of plaintext key, e.g. "mllm_live_Ab"
  label TEXT
  scopes TEXT NOT NULL DEFAULT '["*"]'        -- JSON array (planner choice for forward-flex)
  created_at TEXT NOT NULL                    -- ISO-8601 UTC
  revoked_at TEXT                             -- NULL = active, ISO-8601 UTC when revoked
  INDEX idx_api_keys_hash_active (key_hash) WHERE revoked_at IS NULL

D-2b-04 schema (budgets):
  tenant_id TEXT PRIMARY KEY
  daily_cap_cents INTEGER NOT NULL DEFAULT 0          -- 0 = no cap
  monthly_cap_cents INTEGER NOT NULL DEFAULT 0
  daily_remaining_cents INTEGER NOT NULL DEFAULT 0
  monthly_remaining_cents INTEGER NOT NULL DEFAULT 0
  day_started_at TEXT NOT NULL                        -- ISO-8601 date "YYYY-MM-DD"
  month_started_at TEXT NOT NULL                      -- ISO-8601 month "YYYY-MM"

Backfill (D-2b-05):
  ALTER TABLE usage    ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'
  ALTER TABLE sessions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'
  ALTER TABLE memory   ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'
  -- sqlite cannot ADD a NOT NULL column without a default in batch mode; we use alembic batch_alter_table.

Phase 2a Protocol shape (multillm/db/repo.py:25-61) — 12 methods total, every first non-self arg is `tenant_id: str`. The concrete *RepoSqlite classes MUST keep this signature exactly so `isinstance(obj, SessionRepo)` returns True at runtime via `@runtime_checkable`.

Default seed (D-2b-05):
  INSERT OR IGNORE INTO budgets (tenant_id, daily_cap_cents, monthly_cap_cents,
      daily_remaining_cents, monthly_remaining_cents, day_started_at, month_started_at)
  VALUES ('default', 0, 0, 0, 0, :today, :this_month)
  -- 0/0 caps means "unlimited" — Plan 02b-02 budget middleware short-circuits when cap == 0.

CI grep gate (AUTH-17, D-2b-06):
  rg "execute\(.*f['\"]" multillm/ tests/ && exit 1 || exit 0
  -- exits non-zero if any f-string SQL exists.
</interfaces>

<code_anchors>
- multillm/db/repo.py:25-61 — Protocol surface to implement against
- multillm/auth.py:1-105 — existing optional middleware (NOT modified in this plan; Plan 02b-02 replaces it)
- multillm/migrations/versions/0002_setup_state.py — pattern to mimic (revision/down_revision, _table_exists guard, INSERT OR IGNORE seed)
- multillm/tracking.py — current call sites; preserve module-level functions, delegate to TrackingRepoSqlite internally
- multillm/memory.py — same delegation pattern
- multillm/sessions.py — same delegation pattern
</code_anchors>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Alembic migration 0003_auth_tenancy — create api_keys + budgets, backfill tenant_id</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/migrations/versions/0002_setup_state.py
    /Users/abirzu/dev/multillm/multillm/migrations/runner.py
    /Users/abirzu/dev/multillm/multillm/db/repo.py
  </read_first>
  <files>
    multillm/migrations/versions/0003_auth_tenancy.py
    tests/test_auth_tenancy_migration.py
  </files>
  <action>
    Create alembic migration `versions/0003_auth_tenancy.py` mirroring the 0002 pattern (`_table_exists` guard, ISO-8601 timestamps, INSERT OR IGNORE seed). Set `revision = "0003_auth_tenancy"` and `down_revision = "0002_setup_state"` per D-2b-05.

    The `upgrade()` function MUST, in this order:
    1. Create `api_keys` table with the column shape from the `<interfaces>` block (D-2b-02). Include UNIQUE on key_hash and a partial index `idx_api_keys_hash_active` over `key_hash WHERE revoked_at IS NULL`.
    2. Create `budgets` table with the cents-integer shape from D-2b-04. tenant_id is PRIMARY KEY.
    3. Use `op.batch_alter_table(...)` to add `tenant_id TEXT NOT NULL DEFAULT 'default'` to each of `usage`, `sessions`, `memory` IF those tables exist (guard with `_table_exists` — fresh installs may not have them yet, and we must not fail). SQLite cannot `ALTER COLUMN ... NOT NULL` natively, so batch mode is mandatory per the project's CLAUDE.md alembic note.
    4. Seed one `budgets` row for `tenant_id='default'` with 0/0 caps (means "unlimited" — budget middleware in Plan 02b-02 short-circuits when cap == 0) and today's UTC date/month for `day_started_at` / `month_started_at`. Use `INSERT OR IGNORE` so re-running is a no-op.

    The `downgrade()` function MUST drop `budgets`, drop `api_keys`, and use `batch_alter_table` to drop the `tenant_id` column from each of the three legacy tables. Guard each drop with `_table_exists` for the same reason as upgrade.

    Per D-2b-08, the migration runs ON THE GATEWAY'S DATA DB (`MULTILLM_DATA_DIR / "multillm.db"` by default — confirm path from `multillm/migrations/runner.py`). Do not touch the memory FTS5 DB if it lives in a separate file.

    Write `tests/test_auth_tenancy_migration.py` with three pytest test functions:
    - `test_upgrade_creates_tables`: run migration; assert `api_keys`, `budgets` exist; assert one seed row in `budgets` with tenant_id='default'.
    - `test_backfill_populates_existing_rows`: pre-seed 5 rows into a synthetic `usage` table (CREATE TABLE usage (...) without tenant_id, INSERT 5 rows), run upgrade, assert all 5 rows now have `tenant_id='default'` and `COUNT(*) WHERE tenant_id IS NULL == 0`.
    - `test_migration_idempotent`: run upgrade, downgrade, upgrade again — assert no exceptions, assert seed row still present (INSERT OR IGNORE wins).

    Per D-2b-04: cents-as-integer to avoid float drift.
    Per AUTH-18 spec: existing data preserved with tenant_id="default" backfilled.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_auth_tenancy_migration.py -v &&
      python -m multillm.migrations.runner up &&
      python -c "import sqlite3, pathlib, os; db=os.environ.get('MULTILLM_DATA_DIR', os.path.expanduser('~/.multillm'))+'/multillm.db'; c=sqlite3.connect(db); names={r[0] for r in c.execute('SELECT name FROM sqlite_master WHERE type=\"table\"').fetchall()}; assert {'api_keys','budgets'} <= names, names; assert c.execute('SELECT tenant_id FROM budgets').fetchone() == ('default',)"
    </automated>
  </verify>
  <done>Migration 0003_auth_tenancy exists, idempotent across down/up, creates api_keys + budgets, backfills tenant_id='default' on legacy tables, and the three new tests pass.</done>
</task>

<task type="auto">
  <name>Task 2: Concrete TrackingRepoSqlite + multillm/tracking.py delegation</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/db/repo.py
    /Users/abirzu/dev/multillm/multillm/tracking.py
  </read_first>
  <files>
    multillm/db/tracking.py
    multillm/db/__init__.py
    multillm/tracking.py
    tests/test_db_repos.py
  </files>
  <action>
    Implement concrete `TrackingRepoSqlite` in `multillm/db/tracking.py` against the Phase 2a Protocol (`multillm/db/repo.py:39-46`). The class accepts a `sqlite3.Connection` (or path) in `__init__` and implements:
    - `record_usage(self, tenant_id: str, usage: dict[str, Any]) -> None`
    - `get_dashboard(self, tenant_id: str, *, hours: int = 168, project: str | None = None) -> dict[str, Any]`
    - `get_summary(self, tenant_id: str, *, hours: int = 24) -> dict[str, Any]`

    Every SQL statement MUST be parameterized — `cursor.execute("... WHERE tenant_id = ? AND ts > ?", (tenant_id, cutoff))`. NO f-string interpolation of any variable into the SQL string. Identifier-level dynamics (e.g. selecting a column by name) are forbidden — hardcode column lists.

    In `multillm/tracking.py`, preserve the existing module-level public function names exactly as Phase 2a tests expect them (don't change signatures). Internally, each public function now constructs a `TrackingRepoSqlite(conn)` with `tenant_id="default"` and delegates. The COST_TABLE and OTel exporters remain in tracking.py unchanged.

    Re-export `TrackingRepoSqlite` from `multillm/db/__init__.py` alongside the existing Protocol re-exports.

    Add these tests to `tests/test_db_repos.py`:
    - `test_tracking_repo_implements_protocol`: `isinstance(TrackingRepoSqlite(conn), TrackingRepo) is True`.
    - `test_record_usage_isolates_by_tenant`: insert one row for tenant_id="default" and one for tenant_id="other"; assert get_dashboard("default") returns 1 row and get_dashboard("other") returns 1 row, with no cross-bleed.
    - `test_tracking_module_delegates`: call `multillm.tracking.record_usage(...)` (existing public API) and assert a row landed via direct sqlite query on `WHERE tenant_id = 'default'`.

    Per D-2b-03: the gateway always passes "default" — but the repo MUST accept arbitrary tenant_id values so the second-tenant isolation test in AUTH-15 is meaningful.
    Per D-2b-06: parameterized queries only.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_db_repos.py::test_tracking_repo_implements_protocol tests/test_db_repos.py::test_record_usage_isolates_by_tenant tests/test_db_repos.py::test_tracking_module_delegates -v &&
      pytest -q tests/test_tracking.py -v &&
      grep -nE 'def \w+\(self, tenant_id:' multillm/db/tracking.py | grep -v '^#' | wc -l | awk '$1 >= 3 {exit 0} {exit 1}' &&
      ! rg "execute\(.*f['\"]" multillm/db/tracking.py multillm/tracking.py
    </automated>
  </verify>
  <done>TrackingRepoSqlite implements the Protocol with parameterized SQL only; multillm/tracking.py delegates internally; existing tracking tests + new repo tests all pass.</done>
</task>

<task type="auto">
  <name>Task 3: Concrete SessionRepoSqlite + MemoryRepoSqlite + delegation</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/db/repo.py
    /Users/abirzu/dev/multillm/multillm/sessions.py
    /Users/abirzu/dev/multillm/multillm/memory.py
    /Users/abirzu/dev/multillm/multillm/db/tracking.py
  </read_first>
  <files>
    multillm/db/sessions.py
    multillm/db/memory.py
    multillm/db/__init__.py
    multillm/sessions.py
    multillm/memory.py
    tests/test_db_repos.py
  </files>
  <action>
    Mirror the Task-2 pattern for the other two repos.

    `multillm/db/sessions.py` — `SessionRepoSqlite` implementing 4 methods (`list_sessions`, `get_session`, `create_session`, `append_request`) per `multillm/db/repo.py:25-35`. Every query parameterized; every method filters/inserts WHERE tenant_id = ?.

    `multillm/db/memory.py` — `MemoryRepoSqlite` implementing 5 methods (`list_memories`, `search_memories`, `get_memory`, `store_memory`, `delete_memory`) per `multillm/db/repo.py:49-61`. The FTS5 search query MUST stay parameterized — `cursor.execute("SELECT ... FROM memory_fts WHERE memory_fts MATCH ? AND tenant_id = ?", (query, tenant_id))`. If the existing memory.py uses any f-string for FTS query construction, that's exactly the AUTH-17 violation — replace it.

    Update `multillm/sessions.py` and `multillm/memory.py` to delegate to the new repos with `tenant_id="default"` (D-2b-03). Keep the existing module-level public function names so the Phase 2a 378-test suite stays green.

    Re-export `SessionRepoSqlite` and `MemoryRepoSqlite` from `multillm/db/__init__.py`.

    Add to `tests/test_db_repos.py`:
    - `test_session_repo_implements_protocol`, `test_memory_repo_implements_protocol` (isinstance Protocol checks).
    - `test_sessions_cross_tenant_isolation`: create 1 session for "default", 1 for "other"; assert list_sessions("default") returns 1, list_sessions("other") returns 1.
    - `test_memory_cross_tenant_isolation`: store 1 memory for "default", 1 for "other"; search "default" with the keyword used in "other"'s row → returns 0 rows.
    - `test_memory_fts_query_is_parameterized`: store a memory with title "safe"; search with query string `"safe' OR 1=1 --"`; assert no Python error and result count is 0 (not "all rows because injection worked").

    Per AUTH-15: cross-tenant isolation is the core invariant — these tests are the test-suite-level proof.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_db_repos.py -v &&
      pytest -q tests/test_sessions.py tests/test_memory.py -v &&
      grep -nE 'def \w+\(self, tenant_id:' multillm/db/sessions.py multillm/db/memory.py multillm/db/tracking.py | grep -v '^#' | wc -l | awk '$1 >= 12 {exit 0} {exit 1}' &&
      ! rg "execute\(.*f['\"]" multillm/db/ multillm/sessions.py multillm/memory.py
    </automated>
  </verify>
  <done>Both Repo classes pass isinstance(Protocol) checks; cross-tenant isolation tests pass; existing session/memory tests still pass; grep invariant ≥12 tenant_id-first signatures across multillm/db/.</done>
</task>

<task type="auto">
  <name>Task 4: Audit + fix any remaining f-string SQL in multillm/</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/tracking.py
    /Users/abirzu/dev/multillm/multillm/memory.py
    /Users/abirzu/dev/multillm/multillm/sessions.py
  </read_first>
  <files>
    multillm/**/*.py (only files where the audit finds a violation)
  </files>
  <action>
    Run the AUTH-17 grep gate locally: `rg -nE "execute\(.*f['\"]" multillm/ tests/`. For EVERY match found:
    1. Inspect the call site and determine the dynamic component.
    2. If the dynamic part is a value → convert to `cursor.execute("... WHERE col = ?", (value,))`.
    3. If the dynamic part is an identifier (table or column name) → replace with a hardcoded value from a whitelist dict (no dynamic SQL identifiers). If multiple identifiers are legitimately needed (e.g. selecting from different tables based on type), use an if/elif over explicit literal queries — not string interpolation.
    4. Re-run the grep — must report zero matches before proceeding.

    Do not refactor anything beyond the SQLi fix in this task (no opportunistic cleanup). Each fix is a minimal diff.

    If the audit finds zero violations, that's a valid outcome — commit an empty change as a `chore` documenting the audit was performed, with the rg command output in the commit body.

    Per D-2b-06 / AUTH-17: this audit is the gate that lets the CI rule in Task 5 stay green.
  </action>
  <verify>
    <automated>
      ! rg -nE "execute\(.*f['\"]" multillm/ tests/ &&
      pytest -q
    </automated>
  </verify>
  <done>Zero f-string-SQL matches across multillm/ and tests/; full pytest suite (≥378 tests) green.</done>
</task>

<task type="tdd" tdd="true">
  <name>Task 5: SQLi regression tests + CI grep gate (AUTH-16 + AUTH-17)</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/gateway.py
    /Users/abirzu/dev/multillm/.github/workflows/ci.yml
  </read_first>
  <files>
    tests/test_sqli_regression.py
    .github/workflows/ci.yml
  </files>
  <behavior>
    - Test 1: POST /v1/messages with header `Authorization: Bearer ' OR 1=1 --` returns HTTP 401 (not 500).
    - Test 2: POST /v1/messages with header `Authorization: Bearer ' UNION SELECT * FROM api_keys --` returns HTTP 401.
    - Test 3: POST /v1/messages with header `Authorization: Bearer '; DROP TABLE api_keys; --` returns HTTP 401, and the api_keys table still exists afterward.
    - All three must execute under both auth modes: MULTILLM_API_KEY set (parametrize fixture).
    - The current 0.x AuthMiddleware (multillm/auth.py:99) uses `secrets.compare_digest` — this means even before Plan 02b-02's full rewrite, these tests should ALREADY return 401 because the injection string doesn't match the env-set key. The tests are correctness regressions, not new behavior. Plan 02b-02 will tighten the assertions further (revoked key path), but this plan's slice is sufficient for AUTH-16.
  </behavior>
  <action>
    Write `tests/test_sqli_regression.py` with the three behaviors above using FastAPI's TestClient. Use `monkeypatch.setenv("MULTILLM_API_KEY", "test-secret-not-used")` in a fixture so the AuthMiddleware engages. Build minimal valid Anthropic-format request bodies — but the test should fail at the auth boundary before any DB query runs, so the body content is irrelevant beyond being well-formed JSON.

    Test 3 includes a post-call assertion: query the api_keys table directly via sqlite3 (using the test DB path) and assert it still exists (`SELECT name FROM sqlite_master WHERE name='api_keys'` returns one row). This proves the DROP TABLE injection didn't fire.

    Add a CI step to `.github/workflows/ci.yml` named exactly `SQL injection guard`. The step runs:
        rg -nE "execute\(.*f['\"]" multillm/ tests/ && exit 1 || exit 0
    Place it after the test step so it runs even on green test runs. The step uses the `rg` (ripgrep) binary, which is available on GitHub-hosted ubuntu-latest runners.

    Per D-2b-06: this is the dual gate — runtime regression test + static CI grep.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_sqli_regression.py -v &&
      grep -q "SQL injection guard" .github/workflows/ci.yml &&
      grep -q 'execute(.*f' .github/workflows/ci.yml &&
      pytest -q
    </automated>
  </verify>
  <done>Three SQLi vectors all return 401; api_keys table survives the DROP attempt; CI workflow has the named `SQL injection guard` step; full suite green.</done>
</task>

<task type="auto">
  <name>Task 6: SUMMARY.md + STATE.md update for Plan 02b-01</name>
  <read_first>
    /Users/abirzu/dev/multillm/.planning/STATE.md
    /Users/abirzu/dev/multillm/.planning/phases/02a-adapter-hot-path-refactor/02a-02-SUMMARY.md
  </read_first>
  <files>
    .planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md
    .planning/STATE.md
  </files>
  <action>
    Write `02b-01-SUMMARY.md` using the GSD summary template. Required sections:
    - Outcome (one paragraph: what landed, what closes which AUTH-XX requirements).
    - Artifacts (table of files modified with line counts).
    - Verification evidence (paste outputs of: pytest -q tail, `rg "execute\(.*f['\"]" multillm/ tests/` (expect 0 matches), grep invariant count, `sqlite3 .../multillm.db ".schema api_keys"`).
    - Decisions made during execution (specifically: single migration vs split — locked as SINGLE per planner discretion; scopes column shape — locked as JSON array; any audit findings from Task 4).
    - Patterns established for future phases (the *RepoSqlite delegation pattern, batch_alter_table for SQLite ALTER COLUMN, JSON-array scopes, INSERT OR IGNORE seeding).
    - Closes: AUTH-15, AUTH-16, AUTH-17, AUTH-18.

    Update `.planning/STATE.md` Phase 2b section: mark Plan 02b-01 as complete, list the new migration revision, note that Plan 02b-02 (auth middleware + budget enforcement) is unblocked.
  </action>
  <verify>
    <automated>
      test -f .planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md &&
      grep -q "AUTH-15" .planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md &&
      grep -q "AUTH-16" .planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md &&
      grep -q "AUTH-17" .planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md &&
      grep -q "AUTH-18" .planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md &&
      grep -q "02b-01" .planning/STATE.md
    </automated>
  </verify>
  <done>SUMMARY.md exists with all required sections; STATE.md reflects 02b-01 complete and 02b-02 unblocked.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| HTTP client → gateway | Untrusted Authorization header crosses here; SQLi vectors enter via this boundary. |
| gateway → SQLite | Application-controlled, but any f-string interpolation here re-exposes the boundary above. |
| migration runner → SQLite | Trusted (alembic), but idempotency failures can corrupt tenant_id backfill. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-2b-01-01 | T (Tampering) | Authorization header → DB query | mitigate | Parameterized queries everywhere; AUTH-17 CI grep gate prevents f-string SQL reintroduction (Task 5). |
| T-2b-01-02 | T (Tampering) | Memory FTS5 search query | mitigate | `memory_fts MATCH ?` parameterized — Task 3 includes explicit `test_memory_fts_query_is_parameterized` test using the literal payload `safe' OR 1=1 --`. |
| T-2b-01-03 | I (Info Disclosure) | Cross-tenant data leak via missing WHERE clause | mitigate | Every concrete *RepoSqlite method takes `tenant_id` as first arg and includes `WHERE tenant_id = ?` (Tasks 2-3); isolation tests prove it (`test_*_cross_tenant_isolation`). |
| T-2b-01-04 | T (Tampering) | Migration leaves NULL tenant_id rows on backfill | mitigate | `ALTER TABLE ... ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'` — SQLite applies the default to existing rows atomically; test_backfill_populates_existing_rows asserts COUNT(*) WHERE tenant_id IS NULL == 0. |
| T-2b-01-05 | R (Repudiation) | No audit trail for migration execution | accept | Local-first scope; alembic_version table records revision id, sufficient for solo operator. AUTH-21 audit log is explicitly deferred per D-2b-01. |
| T-2b-01-06 | D (DoS) | Idempotency failure on re-run corrupts seed row | mitigate | `INSERT OR IGNORE` on budgets seed; `_table_exists` guards everywhere; test_migration_idempotent asserts down/up cycle preserves state. |
| T-2b-01-07 | S (Spoofing) | API key gate not yet enforced | accept (this plan) | Plan 02b-02 wires the middleware; this plan's SQLi regression test fixture sets MULTILLM_API_KEY so the legacy compare_digest path engages and returns 401 for the injection vectors. |
</threat_model>

<verification>
Phase 2b plan-01 ends green when all of:
1. `pytest -q` reports ≥ 378 + new tests (target ≥ 392; six new test functions across three new files).
2. `rg -nE "execute\(.*f['\"]" multillm/ tests/` returns zero matches.
3. `git grep -nE 'def \w+\(self, tenant_id:' multillm/db/` returns ≥ 12 (the 9 Protocol methods + 3 concrete classes-worth of identical signatures = at least 12 matches, often higher).
4. `python -m multillm.migrations.runner down && python -m multillm.migrations.runner up` exits 0 both times.
5. `.github/workflows/ci.yml` contains a step named `SQL injection guard`.
6. `02b-01-SUMMARY.md` exists and references all four closed requirements.
</verification>

<success_criteria>
1. Cross-tenant isolation enforced: every query path in usage/sessions/memory filters by `WHERE tenant_id = ?` (AUTH-15).
2. SQLi fuzz: three injection vectors all return 401, none reach DB execution (AUTH-16).
3. CI grep gate active: `SQL injection guard` step fails the workflow on any future f-string SQL (AUTH-17).
4. Auto-upgrade migration: idempotent across down/up, backfills tenant_id='default' on legacy rows, preserves existing data (AUTH-18).
</success_criteria>

<output>
After all tasks complete, create `.planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md` per Task 6. Each task ends with one atomic commit using format `<type>(02b-01): <subject>` and `pytest -q` green at every commit.
</output>
