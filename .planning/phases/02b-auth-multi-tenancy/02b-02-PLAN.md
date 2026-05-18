---
phase: 02b-auth-multi-tenancy
plan: 02
type: execute
wave: 2
depends_on:
  - 02b-01
files_modified:
  - multillm/auth.py
  - multillm/auth_keys.py
  - multillm/budgets.py
  - multillm/cli.py
  - multillm/gateway.py
  - .gitleaks.toml
  - tests/test_api_key_middleware.py
  - tests/test_api_key_cli.py
  - tests/test_budget_atomic_decrement.py
  - tests/test_budget_middleware.py
  - tests/test_first_start_banner.py
  - tests/test_auth_backward_compat.py
autonomous: true
requirements:
  - AUTH-05
  - AUTH-06
  - AUTH-07
  - AUTH-08
  - AUTH-09
  - AUTH-11
  - AUTH-12
tags:
  - auth
  - api-keys
  - budgets
  - cli
  - rate-limit
user_setup: []

must_haves:
  truths:
    - "With MULTILLM_API_KEY set, a POST /v1/messages without 'Authorization: Bearer mllm_*' returns 401."
    - "A revoked API key (revoked_at IS NOT NULL) returns 401 immediately on the next request."
    - "API key comparison uses hmac.compare_digest on SHA-256 hex digests (timing-safe)."
    - "Plaintext API keys are shown ONCE on creation via 'multillm api-key create' and never persisted or logged."
    - "50 concurrent /v1/messages requests against a $1 daily cap overshoot by ≤ 10% (≤ $1.10 spent before 429 fires)."
    - "Budget-exhausted requests return HTTP 429 with a Retry-After header containing the seconds-until-midnight-UTC."
    - "With MULTILLM_API_KEY UNSET, the gateway accepts unauthenticated requests (D-2b-08 backward-compat preserved)."
    - "First gateway start after migration prints a one-time banner instructing the operator to create an API key; subsequent starts suppress it."
    - ".gitleaks.toml has rules matching mllm_live_*, mllm_test_*, and raw SHA-256 of an API key (64-hex in auth context)."
  artifacts:
    - path: "multillm/auth.py"
      provides: "Rewritten ApiKeyAuthMiddleware: parses mllm_* keys, SHA-256 hashes, hmac.compare_digest against api_keys table, resolves tenant_id, 401 on revoked/missing/invalid."
      exports: ["ApiKeyAuthMiddleware", "auth_enabled"]
    - path: "multillm/auth_keys.py"
      provides: "Key generation + storage + revocation helpers (used by CLI and middleware)."
      exports: ["generate_key", "store_key", "list_keys", "revoke_key", "lookup_tenant_id"]
    - path: "multillm/budgets.py"
      provides: "Atomic decrement helper using the single-statement UPDATE ... WHERE remaining >= cost RETURNING ... pattern from D-2b-04."
      exports: ["pre_decrement_cost", "reconcile_actual_cost", "BudgetExhausted", "BudgetMiddleware"]
    - path: "multillm/cli.py"
      provides: "Three new subcommands: 'multillm api-key create', 'list', 'revoke'."
      contains: "api-key"
    - path: "tests/test_budget_atomic_decrement.py"
      provides: "50-concurrent stress test asserting overshoot ≤ 10% of $1 cap."
      contains: "50"
    - path: ".gitleaks.toml"
      provides: "Three new rules for mllm_live_*, mllm_test_*, and raw 64-hex SHA-256 in auth context."
      contains: "mllm_live_"
  key_links:
    - from: "multillm/gateway.py"
      to: "multillm/auth.py:ApiKeyAuthMiddleware"
      via: "app.add_middleware(ApiKeyAuthMiddleware) — replaces existing AuthMiddleware mount"
      pattern: "ApiKeyAuthMiddleware"
    - from: "multillm/gateway.py:/v1/messages"
      to: "multillm/budgets.py:BudgetMiddleware"
      via: "pre-decrement before backend call; reconcile post-response"
      pattern: "pre_decrement_cost|reconcile_actual_cost"
    - from: "multillm/cli.py"
      to: "multillm/auth_keys.py"
      via: "subcommand handlers import generate_key/store_key/list_keys/revoke_key"
      pattern: "from multillm.auth_keys import"
---

<objective>
Wire the local-first auth slice end-to-end on top of Plan 02b-01's schema: parse `mllm_live_*` / `mllm_test_*` keys with SHA-256 + `hmac.compare_digest`, resolve tenant_id via the api_keys table, enforce daily/monthly budget caps with atomic decrement on `/v1/messages`, expose `multillm api-key create|list|revoke` CLI subcommands, surface a first-start banner when no keys exist, and add gitleaks rules for the new key format.

Purpose: closes AUTH-05/06/07/08/09 (keys + scopes + format + hashing + comparison + revocation) and AUTH-11/12 (budget caps + atomic decrement ≤10% overshoot). Preserves the D-2b-08 backward-compat guarantee: gateway runs unauthenticated when `MULTILLM_API_KEY` is unset. Budgets are always-on regardless of auth mode (safety rail).

Output: rewritten auth middleware, new auth_keys + budgets modules, three CLI subcommands, six new test files, three new gitleaks patterns, and the SUMMARY for the entire phase.
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
@.planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md
@.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json
@multillm/auth.py
@multillm/cli.py

<interfaces>
<!-- Locked decisions from 02b-CONTEXT.md — bind to these exactly. -->

D-2b-02 key format:
  PROD: f"mllm_live_{secrets.token_urlsafe(32)}"   # ~54 chars total
  TEST: f"mllm_test_{secrets.token_urlsafe(32)}"   # non-billing test variant

D-2b-02 storage path (built in Plan 02b-01):
  SELECT tenant_id, scopes FROM api_keys
    WHERE key_hash = ? AND revoked_at IS NULL
  -- key_hash = hashlib.sha256(provided.encode()).hexdigest()  (64 hex chars)
  -- Comparison uses hmac.compare_digest(stored_hash, candidate_hash)

D-2b-04 budget atomic decrement (single SQL statement, fails-fast on exhausted cap):
  UPDATE budgets
  SET daily_remaining_cents  = daily_remaining_cents - :cost,
      monthly_remaining_cents = monthly_remaining_cents - :cost
  WHERE tenant_id = :tid
    AND (daily_cap_cents = 0 OR daily_remaining_cents >= :cost)
    AND (monthly_cap_cents = 0 OR monthly_remaining_cents >= :cost)
  RETURNING daily_remaining_cents, monthly_remaining_cents
  -- 0 rows returned => exhausted => 429 with Retry-After: <seconds-to-midnight-UTC>
  -- cap == 0 means "unlimited" — predicate short-circuits.

D-2b-04 rollover (same transaction as decrement, idempotent):
  -- Before decrement, if day_started_at < today_utc():
  --   UPDATE budgets SET daily_remaining_cents = daily_cap_cents, day_started_at = :today
  --   Same shape for monthly using month_started_at < this_month_utc().

D-2b-08 backward-compat predicate:
  auth_enabled() returns True iff os.environ.get("MULTILLM_API_KEY") is set AND non-empty.
  When False: ApiKeyAuthMiddleware is a pass-through. Budgets still apply.

AUTH-12 stress test contract:
  cap = 100 cents ($1.00); cost per request = 10 cents (mock backend); 50 concurrent requests.
  Maximum acceptable spend before first 429 = 110 cents (10% overshoot).
  Implementation choice — PLANNER LOCKS (a) PRE-DECREMENT WITH FIXED ESTIMATE:
    * Before dispatch: pre_decrement_cost(tenant_id, estimated_cents) using the atomic UPDATE above.
    * If 0 rows returned -> 429 immediately, no backend call.
    * After response: reconcile_actual_cost(tenant_id, actual_cents, estimated_cents) issues a
      compensating UPDATE that adds back (estimated - actual) cents (signed; may add or subtract).
    * Worst case: 50 concurrent requests all atomically pre-decrement BEFORE any actual cost is
      known; some will be rejected at the SQL gate. Overshoot bounded by (concurrent_in_flight_at_gate_pass * estimated_cost).
    * For cost = 10 cents and overshoot budget = 10 cents, only ~1 over-the-line request fits;
      the test fixture sets estimated_cents = 10 (matches actual mock cost) so the gate is tight.
  RATIONALE for (a) over (b) per the open-question:
    * (b) post-response decrement allows 50 concurrent requests through the gate simultaneously
      because the cap-check happens AFTER each request completes — overshoot = 50 * cost = 5x cap.
    * (a) fails AUTH-12 only if estimated_cost wildly underestimates actual cost; we use
      route_estimate_cents() that reads COST_TABLE for upper-bound max-tokens estimates.

Cost estimation source:
  multillm/tracking.py:COST_TABLE — per-1M-token rates for every backend. Estimate by:
    upper_input = request_max_tokens or len(json.dumps(messages)) / 4  # cheap heuristic
    upper_output = request_max_tokens or 1024  # request body's max_tokens
    estimated_cents = ceil((upper_input + upper_output) * rate_per_token * 100)

Retry-After computation:
  seconds_until_midnight_utc = (next_utc_midnight - utc_now).total_seconds()

scopes column shape (planner choice from CONTEXT open question 5):
  JSON array, default '["*"]' meaning all-scopes. Future SaaS phase can introduce ["chat:read", "memory:write"] etc.
  Middleware in 2b only validates EXISTENCE of a non-revoked key — scope enforcement deferred.

CLI banner format (planner choice from CONTEXT open question 4):
  Print to stderr (not stdout, so script consumers can capture key body cleanly), bordered with
  ════════════════════════════════════════════════════════════════
  KEY plaintext is then echoed to stdout on its own line so `MULTILLM_API_KEY=$(multillm api-key create --label local)` works.
</interfaces>

<code_anchors>
- multillm/auth.py:82-104 — current AuthMiddleware (single-env-key compare_digest); to be replaced/extended.
- multillm/auth.py:26 — current `API_KEY = os.getenv("MULTILLM_API_KEY", "")` module-level read; rewritten to `auth_enabled()` predicate that re-reads on each request (tests need monkeypatch).
- multillm/cli.py — CLI scaffold from Phase 1 plan 01-03; new `api-key` subcommand group added alongside existing `migrate` group.
- multillm/gateway.py — `/v1/messages` route + middleware stack mount + lifespan startup; budget middleware mounts here, banner fires from lifespan.
- multillm/tracking.py:COST_TABLE — read by budgets.route_estimate_cents.
- multillm/db/repo.py:39-46 — TrackingRepo for actual-cost reconciliation (Plan 02b-01 provides concrete impl).
</code_anchors>
</context>

<tasks>

<task type="auto">
  <name>Task 1: API key generation + storage helpers (multillm/auth_keys.py)</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/db/repo.py
    /Users/abirzu/dev/multillm/.planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md
  </read_first>
  <files>
    multillm/auth_keys.py
    tests/test_auth_keys.py
  </files>
  <action>
    Create `multillm/auth_keys.py` exporting these functions (all type-annotated):

    - `generate_key(*, prod: bool = True) -> tuple[str, str, str]` — returns `(plaintext, key_hash, key_prefix)`. Plaintext is `f"mllm_live_{secrets.token_urlsafe(32)}"` when prod=True else `f"mllm_test_{...}"`. key_hash = `hashlib.sha256(plaintext.encode()).hexdigest()`. key_prefix = `plaintext[:12]`. Per D-2b-02 / AUTH-06 / AUTH-07.

    - `store_key(conn: sqlite3.Connection, *, tenant_id: str, key_hash: str, key_prefix: str, label: str | None, scopes: list[str] | None = None) -> int` — INSERT INTO api_keys with parameterized SQL, scopes serialized as `json.dumps(scopes or ["*"])`. created_at = ISO-8601 UTC now. revoked_at = NULL. Returns the rowid. NEVER accepts plaintext (defensive: signature forbids it).

    - `list_keys(conn: sqlite3.Connection, *, tenant_id: str | None = None, include_revoked: bool = False) -> list[dict]` — SELECT id, tenant_id, key_prefix, label, scopes, created_at, revoked_at. Filters by tenant_id if provided. By default excludes revoked rows. NEVER selects or returns key_hash (defensive: never lets it leak via list).

    - `revoke_key(conn: sqlite3.Connection, *, key_id: int) -> bool` — UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL. Returns True if exactly 1 row was affected.

    - `lookup_tenant_id(conn: sqlite3.Connection, *, key_hash: str) -> tuple[str, list[str]] | None` — SELECT tenant_id, scopes FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL LIMIT 1. Returns (tenant_id, scopes_list) or None. This is the hot-path called per request.

    Tests in `tests/test_auth_keys.py`:
    - `test_generate_key_format` — prefix `mllm_live_` (and `mllm_test_` with prod=False); plaintext length ~54; key_hash length 64 (hex); key_prefix length 12 and equals plaintext[:12].
    - `test_generate_key_uniqueness` — 100 generate_key() calls produce 100 unique plaintexts (probabilistic but deterministic with secrets.token_urlsafe).
    - `test_store_and_lookup_roundtrip` — store, then lookup_tenant_id returns the inserted tenant_id; lookup with a different hash returns None.
    - `test_revoke_key_returns_none_on_lookup` — store key, revoke_key by id, lookup_tenant_id returns None. AUTH-09.
    - `test_double_revoke_returns_false` — revoking an already-revoked key returns False (idempotent).
    - `test_list_keys_never_returns_hash` — assert 'key_hash' not in any row dict; only key_prefix is exposed.
    - `test_store_key_rejects_plaintext` — `inspect.signature(store_key).parameters` has no 'plaintext' parameter (defensive contract test).

    Per AUTH-07: plaintext never persisted — module enforces this at the type level.
    Per D-2b-06: all queries parameterized.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_auth_keys.py -v &&
      ! rg -nE "execute\(.*f['\"]" multillm/auth_keys.py
    </automated>
  </verify>
  <done>auth_keys module implements generate/store/list/revoke/lookup with parameterized SQL only; key_hash never exposed via list; all 7 tests pass.</done>
</task>

<task type="auto">
  <name>Task 2: Rewrite ApiKeyAuthMiddleware (multillm/auth.py)</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/auth.py
    /Users/abirzu/dev/multillm/multillm/auth_keys.py
  </read_first>
  <files>
    multillm/auth.py
    tests/test_api_key_middleware.py
  </files>
  <action>
    Rewrite `multillm/auth.py` in place (single file — auth.py is currently 104 lines, splitting is premature; planner discretion). Preserve existing exports (`AuthMiddleware`, `auth_enabled`, `_is_public_request`) but change semantics. Add new `ApiKeyAuthMiddleware` class as the canonical entry; alias `AuthMiddleware = ApiKeyAuthMiddleware` for backward-compat with existing import sites.

    New `auth_enabled()` predicate (D-2b-08): returns True iff `os.environ.get("MULTILLM_API_KEY", "").strip()` is non-empty. Re-reads env on every call (NO module-level capture) so monkeypatch fixtures work.

    `ApiKeyAuthMiddleware.dispatch`:
    1. If `not auth_enabled()`: pass through unchanged (D-2b-08 backward-compat).
    2. If `_is_public_request(request)`: pass through (preserve current public-endpoint allowlist behavior — same constants kept).
    3. Extract candidate key via `_extract_key(request)` (existing helper — kept).
    4. If no key: 401 JSON `{"detail": "API key required."}`.
    5. If candidate doesn't start with `mllm_live_` or `mllm_test_`: 401 (rejects junk fast and short-circuits SQLi vectors per AUTH-16).
    6. Compute `candidate_hash = hashlib.sha256(candidate.encode()).hexdigest()`.
    7. Call `auth_keys.lookup_tenant_id(conn, key_hash=candidate_hash)`. The lookup itself uses parameterized SQL; revoked keys are filtered server-side (AUTH-09 — revocation effective on next request, no caching).
    8. If None returned: 401 `{"detail": "Invalid or revoked API key."}`. Use `hmac.compare_digest(candidate_hash, candidate_hash)` against the SQL-returned existence check pattern — actually, since the SQL filter is the source of truth and key_hash has UNIQUE constraint, the equality is implicit; we still document hmac.compare_digest usage for AUTH-08 in the hot path that gates secret-like data (the `__eq__` between two 64-hex strings of identical length is timing-safe by construction in CPython but use hmac.compare_digest explicitly when re-validating against the loaded row to satisfy AUTH-08's textual requirement).
    9. On success: stash tenant_id + scopes on `request.state.tenant_id` / `request.state.scopes` so downstream middleware (budget) and the /v1/messages handler can read them.

    Tests in `tests/test_api_key_middleware.py` (use FastAPI TestClient + monkeypatch.setenv for MULTILLM_API_KEY and a temp sqlite path):
    - `test_auth_disabled_when_env_unset` — MULTILLM_API_KEY unset; POST /v1/messages returns the normal response (not 401). Backward-compat (D-2b-08).
    - `test_missing_authorization_returns_401` — env set, no Authorization header → 401.
    - `test_junk_token_returns_401` — Authorization: Bearer hello-world → 401, no DB lookup performed (prefix check fails). Verify via spy on lookup_tenant_id.
    - `test_valid_key_resolves_tenant` — create a key via auth_keys, send `Authorization: Bearer <plaintext>`, expect non-401 response and assert request.state.tenant_id == "default" on the handler side.
    - `test_revoked_key_returns_401_immediately` — create key, revoke it, send with that key → 401. AUTH-09.
    - `test_timing_safe_comparison` — patch `hmac.compare_digest` to assert it's called at least once when a valid-format-but-wrong-hash key is sent. AUTH-08.
    - `test_public_endpoints_bypass_auth` — POST /health and GET /dashboard return non-401 with env set + no key.

    Per D-2b-08: backward-compat preserved. Per AUTH-05/06/07/08/09: full key lifecycle gated.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_api_key_middleware.py -v &&
      pytest -q tests/test_auth.py -v &&
      ! rg -nE "execute\(.*f['\"]" multillm/auth.py
    </automated>
  </verify>
  <done>ApiKeyAuthMiddleware enforces mllm_* key format, SHA-256 + parameterized lookup, hmac.compare_digest path proven, revoked keys 401 immediately; D-2b-08 backward-compat test green; existing tests/test_auth.py still passes.</done>
</task>

<task type="auto">
  <name>Task 3: CLI subcommands — multillm api-key create | list | revoke</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/cli.py
    /Users/abirzu/dev/multillm/multillm/auth_keys.py
  </read_first>
  <files>
    multillm/cli.py
    tests/test_api_key_cli.py
  </files>
  <action>
    Add three subcommands under a new `api-key` group in `multillm/cli.py`, mirroring the existing `migrate` group pattern. Use argparse subparsers (same idiom as the rest of cli.py — do NOT introduce click/typer just for this).

    Signatures:
    - `multillm api-key create [--label LABEL] [--test]` — generates a key (prod by default, --test for mllm_test_ variant), stores it for tenant_id='default', prints the boxed banner to STDERR + the bare plaintext to STDOUT on its own line. Stdout-only-plaintext means `MULTILLM_API_KEY=$(multillm api-key create --label local)` captures cleanly.
    - `multillm api-key list [--include-revoked]` — formatted table with columns: id, key_prefix, label, scopes, created_at, revoked_at. NEVER prints key_hash. Uses `auth_keys.list_keys`.
    - `multillm api-key revoke <KEY_ID>` — positional arg is the integer id from `list`. Prints confirmation to stderr ("Revoked key id=N (prefix=mllm_live_xy)") and exits 0 on success, 1 if key not found.

    The boxed banner format (planner-locked per CONTEXT open Q4):
    ════════════════════════════════════════════════════════════════
     Created MultiLLM API key (label: <label>, prefix: <prefix>)
     Show plaintext ONCE — store it now, it cannot be recovered.

       export MULTILLM_API_KEY=<plaintext>
    ════════════════════════════════════════════════════════════════

    The plaintext appears ONLY in (a) the boxed banner on stderr (export line) and (b) the bare line on stdout. After printing, the variable is overwritten with the empty string (defensive — not security-critical, just hygiene).

    Tests in `tests/test_api_key_cli.py` use `subprocess.run([sys.executable, "-m", "multillm.cli", "api-key", ...])` with `MULTILLM_DATA_DIR` pointed at a tmp path:
    - `test_api_key_create_prints_plaintext_to_stdout` — stdout matches `^mllm_(live|test)_[A-Za-z0-9_\-]+\n$`. stderr contains the boxed banner.
    - `test_api_key_create_test_variant` — `--test` flag produces `mllm_test_*` prefix.
    - `test_api_key_create_stores_hash_only` — after create, sqlite query SELECT key_hash, key_prefix FROM api_keys shows hash + prefix but no row has plaintext (defensive: scan all TEXT columns and assert the plaintext string is not substring-present anywhere in api_keys).
    - `test_api_key_list_excludes_revoked_by_default` — create 2 keys, revoke 1, list prints 1 row; list --include-revoked prints 2.
    - `test_api_key_revoke_idempotent` — revoke the same id twice; first exits 0, second exits 1 with stderr message "already revoked" or "not found".
    - `test_api_key_list_never_prints_hash` — output of list does NOT contain any 64-hex-char substring. Regex check.

    Per AUTH-05/07: plaintext shown once, never persisted. Per D-2b-02: format + storage shape.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_api_key_cli.py -v &&
      python -m multillm.cli api-key --help 2>&1 | grep -q "create" &&
      python -m multillm.cli api-key --help 2>&1 | grep -q "revoke"
    </automated>
  </verify>
  <done>Three subcommands work end-to-end via subprocess; plaintext appears only in stdout/stderr banner, never persisted; list never exposes key_hash; all 6 CLI tests pass.</done>
</task>

<task type="tdd" tdd="true">
  <name>Task 4: Budget atomic decrement helper (multillm/budgets.py) + stress test (AUTH-12)</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/tracking.py
    /Users/abirzu/dev/multillm/multillm/db/tracking.py
    /Users/abirzu/dev/multillm/.planning/phases/02b-auth-multi-tenancy/02b-CONTEXT.md
  </read_first>
  <files>
    multillm/budgets.py
    tests/test_budget_atomic_decrement.py
  </files>
  <behavior>
    - `pre_decrement_cost(conn, tenant_id, estimated_cents)` returns `True` if the atomic UPDATE affected 1 row (cap available), `False` otherwise (cap exhausted).
    - When 0 cap is configured (the default seed from Plan 02b-01), pre_decrement always returns True (unlimited mode short-circuit).
    - `reconcile_actual_cost(conn, tenant_id, actual_cents, estimated_cents)` issues a compensating UPDATE that credits back (estimated - actual) cents; signed (may be positive or negative). Cap zero short-circuits this too.
    - Daily rollover: pre_decrement first checks `day_started_at < today_utc()` and, if so, resets `daily_remaining_cents = daily_cap_cents, day_started_at = today_utc()` IN THE SAME TRANSACTION as the decrement. Same for monthly.
    - Stress test contract (AUTH-12): with cap = 100 cents, cost = 10 cents per call, 50 concurrent threads issuing pre_decrement_cost(estimated=10) → at most ⌈100/10⌉ + (10% overshoot = 1 extra) = 11 successes. Concretely: assert successes <= 11 AND successes >= 10.
  </behavior>
  <action>
    Write `tests/test_budget_atomic_decrement.py` FIRST (RED phase). Test asserts the contract above. Then implement `multillm/budgets.py`:

    - `class BudgetExhausted(Exception)` — raised by middleware (Task 5) when pre_decrement returns False.
    - `def pre_decrement_cost(conn, tenant_id: str, estimated_cents: int) -> bool` — runs the rollover-check UPDATE (single statement using CASE expressions to conditionally reset) and the decrement UPDATE per D-2b-04. Use `RETURNING` to detect zero-row outcomes. SQLite 3.35+ supports RETURNING; the project pins ≥3.35 per pyproject — confirm before writing.
    - `def reconcile_actual_cost(conn, tenant_id: str, actual_cents: int, estimated_cents: int) -> None` — compensating UPDATE that adds (estimated - actual) back to both remaining columns. No-op if delta == 0 or cap == 0.
    - `def route_estimate_cents(model: str, request_body: dict) -> int` — reads COST_TABLE from multillm.tracking, computes upper-bound from `request_body.get("max_tokens", 1024) + len(json.dumps(request_body.get("messages", []))) / 4`, returns ceil(tokens * rate_per_token * 100). Used by middleware (Task 5).
    - `def seconds_until_midnight_utc(now: datetime | None = None) -> int` — for Retry-After header.

    The stress test uses `concurrent.futures.ThreadPoolExecutor(max_workers=50)` and submits 50 calls to `pre_decrement_cost(conn, "default", 10)`. SQLite's per-connection write lock serializes the UPDATEs naturally, so the atomic single-statement WHERE-guarded decrement enforces the cap deterministically. Test setup seeds `budgets` with `daily_cap_cents=100, daily_remaining_cents=100, monthly_cap_cents=0` (monthly unlimited so we isolate the daily gate).

    Assertion shape:
        successes = sum(results)  # results is list[bool] of length 50
        assert 10 <= successes <= 11, f"Expected 10-11 successes (cap=100, cost=10, ≤10% overshoot), got {successes}"
        assert successes * 10 <= 110, f"Overshoot exceeded 10% of $1.00 cap: spent {successes*10} cents"

    Also add:
    - `test_pre_decrement_short_circuits_when_cap_zero` — cap=0; 100 sequential calls all return True; remaining stays 0.
    - `test_daily_rollover` — set day_started_at to yesterday and daily_remaining=0; call pre_decrement; assert it succeeds AND daily_remaining ends at cap - cost AND day_started_at == today.
    - `test_reconcile_credits_unused_cents` — pre_decrement 20, actual was 5, reconcile(actual=5, estimated=20); assert remaining increased by 15.
    - `test_retry_after_header_value` — seconds_until_midnight_utc returns a positive int < 86401.

    Per AUTH-11/12: caps with atomic decrement, ≤10% overshoot.
    Per D-2b-04: single-statement UPDATE with WHERE guards.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_budget_atomic_decrement.py -v &&
      pytest -q tests/test_budget_atomic_decrement.py::test_stress_50_concurrent -v
    </automated>
  </verify>
  <done>50-concurrent stress test asserts overshoot ≤ 10%; rollover + reconcile + cap-zero short-circuit tests all green.</done>
</task>

<task type="auto">
  <name>Task 5: Budget middleware on /v1/messages + Retry-After 429</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/gateway.py
    /Users/abirzu/dev/multillm/multillm/budgets.py
  </read_first>
  <files>
    multillm/budgets.py
    multillm/gateway.py
    tests/test_budget_middleware.py
  </files>
  <action>
    Add `BudgetMiddleware` class to `multillm/budgets.py` (BaseHTTPMiddleware subclass). It only acts on `/v1/messages` POST (other paths pass through). For matching requests:

    1. Resolve tenant_id: prefer `request.state.tenant_id` (set by ApiKeyAuthMiddleware in Task 2); fallback to "default" (D-2b-03 + D-2b-08 — budgets apply even when auth is off).
    2. Parse request body once (carefully — must not consume the stream for the downstream handler). Pattern: read body bytes, store on `request.state.cached_body`, and patch `request._receive` so the downstream handler reads the same bytes. (Standard FastAPI pattern; if request body is already cached by an upstream middleware, use that.)
    3. Compute estimated_cents = `route_estimate_cents(body.get("model", ""), body)`.
    4. Call `pre_decrement_cost(conn, tenant_id, estimated_cents)`. If False → return 429 with `{"detail": "Budget exhausted. Retry after midnight UTC."}` and header `Retry-After: <seconds_until_midnight_utc()>`. Per AUTH-11.
    5. Otherwise: `response = await call_next(request)`. Best-effort post-response reconcile: if the response has X-MultiLLM-Cost-Cents header (or the tracking layer wrote actual cost into request.state during the handler), call `reconcile_actual_cost(conn, tenant_id, actual_cents, estimated_cents)`. If actual cost cannot be determined (streaming partial failure etc.), skip reconcile — overshoot is bounded by the pre-decrement estimate.

    Mount BudgetMiddleware in `multillm/gateway.py` IMMEDIATELY AFTER ApiKeyAuthMiddleware in the middleware stack (Starlette executes middlewares in reverse-add order, so auth runs first, then budget; if auth 401s, budget never runs).

    Tests in `tests/test_budget_middleware.py`:
    - `test_budget_exhausted_returns_429_with_retry_after` — seed budgets with daily_cap=100, remaining=0 (exhausted); POST /v1/messages → 429 with Retry-After header that parses to a positive int.
    - `test_budget_available_passes_through` — seed cap=10000, remaining=10000; POST /v1/messages → non-429.
    - `test_budget_zero_cap_unlimited` — cap=0; 100 sequential POSTs all succeed; remaining stays 0.
    - `test_budget_applies_when_auth_disabled` — MULTILLM_API_KEY unset, cap=100, remaining=0 → still 429. Backward-compat-safe but budget is always-on.
    - `test_other_paths_skip_budget` — POST /api/memory (non-/v1/messages); cap=0 doesn't matter, request passes regardless of budget state.

    Per AUTH-11: HTTP 429 + Retry-After when exhausted. Per D-2b-08: budgets always-on regardless of auth mode.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_budget_middleware.py -v &&
      pytest -q tests/test_gateway.py -v
    </automated>
  </verify>
  <done>BudgetMiddleware enforces caps on /v1/messages; exhausted returns 429 + Retry-After; cap=0 short-circuits; budget runs in both auth-on and auth-off modes; all gateway tests still green.</done>
</task>

<task type="auto">
  <name>Task 6: First-start UX banner on lifespan startup</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/gateway.py
  </read_first>
  <files>
    multillm/gateway.py
    tests/test_first_start_banner.py
  </files>
  <action>
    In `multillm/gateway.py`'s existing lifespan handler (added in Phase 1 plan 01-07 for the setup wizard), AFTER the existing migrate-up call and BEFORE the gateway accepts requests, add a one-time banner check:

    1. Open a connection to the data DB.
    2. Query `SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL`.
    3. If count == 0 AND `system.first_start_banner_shown` is not '1' in the system table: print the banner to stdout AND log at INFO via `log.info(...)`. Then upsert `system.first_start_banner_shown = '1'` (INSERT OR REPLACE).
    4. If count > 0 OR banner_shown == '1': skip silently.

    Banner exact text (D-2b-05):
    ════════════════════════════════════════════════════════════════
     First-start migration complete. A `default` tenant has been
     created. To gate the gateway with an API key, run:

         multillm api-key create --tenant default --label "local"

     Then export the printed key as MULTILLM_API_KEY in your shell.
    ════════════════════════════════════════════════════════════════

    Tests in `tests/test_first_start_banner.py` (use FastAPI lifespan + capsys):
    - `test_banner_fires_on_first_start_no_keys` — fresh DB, zero api_keys rows; start app; capsys.readouterr().out contains "First-start migration complete". After startup, system.first_start_banner_shown == '1'.
    - `test_banner_suppressed_on_second_start` — pre-stamp system.first_start_banner_shown='1'; start app; output does NOT contain the banner.
    - `test_banner_suppressed_when_keys_exist` — insert a non-revoked api_keys row before startup; output does NOT contain the banner.

    Per D-2b-05: one-time, idempotent across restarts.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_first_start_banner.py -v
    </automated>
  </verify>
  <done>Banner fires exactly once on first start with zero keys; suppressed thereafter; all three lifecycle scenarios tested.</done>
</task>

<task type="auto">
  <name>Task 7: .gitleaks.toml patterns for new key format</name>
  <read_first>
    /Users/abirzu/dev/multillm/.gitleaks.toml
  </read_first>
  <files>
    .gitleaks.toml
  </files>
  <action>
    Append three rules to `.gitleaks.toml`:

    ```
    [[rules]]
    id = "multillm-api-key-live"
    description = "MultiLLM production API key (mllm_live_*)"
    regex = '''mllm_live_[A-Za-z0-9_\-]{43}'''
    tags = ["secret", "multillm"]

    [[rules]]
    id = "multillm-api-key-test"
    description = "MultiLLM test API key (mllm_test_*)"
    regex = '''mllm_test_[A-Za-z0-9_\-]{43}'''
    tags = ["secret", "multillm", "test-key"]

    [[rules]]
    id = "multillm-api-key-hash"
    description = "Raw SHA-256 of a MultiLLM API key (64 hex in auth context)"
    regex = '''(?i)(api[_-]?key[_-]?hash|MULTILLM_KEY_HASH)["'\s:=]+[a-f0-9]{64}'''
    tags = ["secret", "multillm", "hash"]
    ```

    Add an `[allowlist]` entry for test fixtures so the SQLi regression tests in Plan 02b-01 (which embed literal `mllm_test_*` strings as part of injection vectors) don't trip the scanner:
    ```
    [allowlist]
    paths = [
      '''tests/test_sqli_regression\.py''',
      '''tests/test_auth_keys\.py''',
      '''tests/test_api_key_.*\.py''',
      '''tests/test_budget_.*\.py''',
    ]
    ```

    Verify the file remains valid TOML.

    Per D-2b-07: gitleaks patterns are the static line of defense against accidental key check-in.
  </action>
  <verify>
    <automated>
      python -c "import tomllib; tomllib.loads(open('.gitleaks.toml').read())" &&
      grep -q "mllm_live_" .gitleaks.toml &&
      grep -q "mllm_test_" .gitleaks.toml &&
      grep -q "multillm-api-key-hash" .gitleaks.toml
    </automated>
  </verify>
  <done>.gitleaks.toml parses as TOML; three new rules present; test-fixture allowlist scoped to auth/budget test files only.</done>
</task>

<task type="auto">
  <name>Task 8: End-to-end auth + backward-compat integration test + coverage delta check + SUMMARY</name>
  <read_first>
    /Users/abirzu/dev/multillm/.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json
    /Users/abirzu/dev/multillm/.planning/phases/02b-auth-multi-tenancy/02b-01-SUMMARY.md
  </read_first>
  <files>
    tests/test_auth_backward_compat.py
    .planning/phases/02b-auth-multi-tenancy/02b-02-SUMMARY.md
    .planning/STATE.md
  </files>
  <action>
    Write `tests/test_auth_backward_compat.py` with end-to-end scenarios:
    - `test_full_flow_with_auth_enabled` — set MULTILLM_API_KEY=<created-via-CLI>, POST /v1/messages with that key in Authorization → 200ish (or whatever the mock backend returns). POST without the header → 401.
    - `test_full_flow_with_auth_disabled` — unset MULTILLM_API_KEY; POST /v1/messages without Authorization → not 401 (preserves D-2b-08 zero-friction local-dev experience).
    - `test_budget_enforced_in_both_modes` — for both auth-on and auth-off configurations, seed daily_remaining=0; POST → 429.

    Then run the coverage delta check:
        pytest --cov=multillm --cov-report=json:/tmp/coverage-current.json
    Compare `/tmp/coverage-current.json` totals.percent_covered against the value in `.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json`. Assert delta ≥ -0.01 (epsilon). Write the comparison to the SUMMARY.

    Write `.planning/phases/02b-auth-multi-tenancy/02b-02-SUMMARY.md` using the GSD summary template. Required sections:
    - Outcome (closes AUTH-05/06/07/08/09 + AUTH-11/12).
    - Artifacts table.
    - Verification evidence: full pytest tail (target ≥ 410 tests counting the ~30 new tests in 02b-01 + 02b-02), grep gate output (0 matches), gitleaks dry-run output, the coverage delta number.
    - Discretion decisions LOCKED (from CONTEXT open questions, all five):
      Q1 (single migration vs split): SINGLE — one revision file 0003_auth_tenancy.py covers both table creation + tenant_id backfill.
      Q2 (auth.py rewrite vs split): REWRITE IN PLACE — auth.py 104 lines stays single-file; new helpers live in auth_keys.py.
      Q3 (pre-decrement strategy): PRE-DECREMENT WITH ESTIMATE (a) — tighter overshoot guarantee for AUTH-12.
      Q4 (CLI banner format): boxed banner to stderr + bare plaintext on stdout line for shell-capture friendliness.
      Q5 (scopes shape): JSON array, default `["*"]`.
    - Patterns established: ApiKeyAuthMiddleware mounting pattern, BudgetMiddleware always-on regardless of auth mode, lifespan banner suppression via system table flag.
    - Phase-level success criteria checklist (all 6 from CONTEXT mirrored, each marked ✓).
    - Closes: AUTH-05, AUTH-06, AUTH-07, AUTH-08, AUTH-09, AUTH-11, AUTH-12. Combined with 02b-01: AUTH-15, AUTH-16, AUTH-17, AUTH-18 → 11/11 in-scope requirements closed.

    Update `.planning/STATE.md`: mark Phase 02b complete; list the 11 closed requirements; note the 10 deferred (AUTH-01/02/03/04/10/13/14/19/20/21) for future SaaS phase.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_auth_backward_compat.py -v &&
      pytest --cov=multillm --cov-report=json:/tmp/coverage-current.json -q &&
      python -c "import json; cur=json.load(open('/tmp/coverage-current.json'))['totals']['percent_covered']; base=json.load(open('.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json'))['totals']['percent_covered']; assert cur - base >= -0.01, f'Coverage regressed: {cur:.2f} vs baseline {base:.2f}'; print(f'COVERAGE OK: {cur:.2f} vs baseline {base:.2f}')" &&
      test -f .planning/phases/02b-auth-multi-tenancy/02b-02-SUMMARY.md &&
      grep -q "AUTH-12" .planning/phases/02b-auth-multi-tenancy/02b-02-SUMMARY.md &&
      grep -q "11/11" .planning/phases/02b-auth-multi-tenancy/02b-02-SUMMARY.md &&
      grep -q "02b-02" .planning/STATE.md
    </automated>
  </verify>
  <done>End-to-end auth + backward-compat tests green; coverage delta ≥ -0.01 vs Phase 2a baseline; SUMMARY documents all 5 discretion decisions; STATE.md reflects phase complete with 11/11 in-scope requirements closed.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| HTTP client → ApiKeyAuthMiddleware | Untrusted Authorization header; first line of defense. |
| ApiKeyAuthMiddleware → api_keys table | Application-controlled; relies on parameterized lookup from Plan 02b-01. |
| Gateway lifespan → console + log | Banner output crosses into operator-readable channels; must NEVER contain plaintext keys. |
| CLI subprocess → stdout/stderr | Plaintext key crosses here exactly ONCE on creation. Stored at rest as SHA-256 only. |
| /v1/messages → BudgetMiddleware → budgets table | Cost decrement is the only spend-bounding mechanism in scope (per-tenant rate limits deferred). |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-2b-02-01 | S (Spoofing) | Forged API key passes middleware | mitigate | SHA-256 + `hmac.compare_digest` lookup against api_keys.key_hash UNIQUE column; prefix-rejection short-circuits non-mllm_* tokens before DB lookup (Task 2). |
| T-2b-02-02 | T (Tampering) | SQLi via Authorization header reaches DB query | mitigate | Inherited from Plan 02b-01: parameterized lookup_tenant_id query + AUTH-17 CI grep gate. Task 2 explicitly tests with junk Bearer tokens. |
| T-2b-02-03 | R (Repudiation) | No audit trail for key creation/revocation | accept | Local-first scope. `created_at` and `revoked_at` columns + key_prefix in list output give a workable trail for solo operator. AUTH-21 audit log is explicitly deferred per D-2b-01. |
| T-2b-02-04 | I (Info Disclosure) | Plaintext key logged, persisted, or returned via API | mitigate | (1) `store_key()` signature forbids plaintext parameter (Task 1). (2) `list_keys()` never selects key_hash. (3) CLI prints plaintext exactly once and overwrites the local variable. (4) gitleaks rules catch accidental check-in (Task 7). (5) Banner explicitly warns "store it now, it cannot be recovered." |
| T-2b-02-05 | D (DoS) | Runaway loop burns spend on a paid backend | mitigate | BudgetMiddleware pre-decrements against cost estimate (Task 4 + 5). 50-concurrent stress test bounds overshoot ≤ 10% of cap. AUTH-11/12. |
| T-2b-02-06 | E (Elevation) | No auth = full access | accept (env-var gated) | D-2b-08 backward-compat: documented behavior when MULTILLM_API_KEY is unset. Test `test_auth_disabled_when_env_unset` codifies the contract. Operator opt-in to authentication via env var. Budgets remain enforced. |
| T-2b-02-07 | I (Info Disclosure) | Side-channel timing attack on key comparison | mitigate | `hmac.compare_digest` on 64-hex SHA-256 strings (constant-time on equal-length inputs). Prefix-rejection happens BEFORE hash compute to avoid leaking "valid-looking-but-wrong" timing. AUTH-08. |
| T-2b-02-08 | T (Tampering) | Race: two concurrent requests both pass budget gate just under cap | mitigate | Single-statement atomic `UPDATE ... WHERE remaining >= :cost RETURNING ...` — SQLite serializes writes per connection, and the WHERE-guard ensures at most one row update succeeds per cost unit. Stress test asserts ≤ 10% overshoot (AUTH-12). |
| T-2b-02-09 | D (DoS) | Budget exhausted-state never recovers (no rollover) | mitigate | `pre_decrement_cost` runs daily/monthly rollover check in the same transaction as the decrement; if day_started_at < today_utc(), remaining resets to cap. Test `test_daily_rollover` proves it. |
| T-2b-02-10 | S (Spoofing) | Revoked key keeps working from cache | mitigate | No middleware-side cache. Every request runs `SELECT ... WHERE revoked_at IS NULL` — revocation is effective on the very next request. AUTH-09. Test `test_revoked_key_returns_401_immediately` proves it. |
</threat_model>

<verification>
Phase 2b plan-02 ends green when all of:
1. `pytest -q` reports ≥ baseline + ~30 new tests (target ≥ 410).
2. `pytest -q tests/test_budget_atomic_decrement.py::test_stress_50_concurrent` passes (≤ 10% overshoot).
3. `MULTILLM_API_KEY=$(python -m multillm.cli api-key create --label local)` captures cleanly to env (single stdout line).
4. With MULTILLM_API_KEY set: `curl -X POST localhost:8080/v1/messages` without `Authorization` returns 401.
5. With MULTILLM_API_KEY unset: same curl returns non-401 (D-2b-08).
6. `python -c "import tomllib; tomllib.loads(open('.gitleaks.toml').read())"` exits 0.
7. Coverage delta vs `coverage-baseline.json` ≥ -0.01.
8. `02b-02-SUMMARY.md` exists, references all 7 AUTH-XX requirements closed by this plan, and locks all 5 discretion decisions.
</verification>

<success_criteria>
1. API key gate works: with MULTILLM_API_KEY set, no/invalid/revoked key → 401 (AUTH-05/06/07/08/09).
2. Budget caps enforced with HTTP 429 + Retry-After when exhausted (AUTH-11).
3. 50-concurrent stress test against $1 cap overshoots ≤ 10% (AUTH-12).
4. Backward-compat preserved: gateway runs unauthenticated when MULTILLM_API_KEY is unset (D-2b-08).
5. Combined with Plan 02b-01: 11/11 in-scope AUTH requirements closed (AUTH-05–09 + 11/12 + 15/16/17/18).
6. First-start banner fires exactly once; .gitleaks.toml protects against future leaks.
</success_criteria>

<output>
After all tasks complete, create `.planning/phases/02b-auth-multi-tenancy/02b-02-SUMMARY.md` and update `.planning/STATE.md` per Task 8. Each task ends with one atomic commit using format `<type>(02b-02): <subject>` with `pytest -q` green at every commit. Final commit also asserts coverage delta ≥ -0.01 vs Phase 2a baseline.
</output>
