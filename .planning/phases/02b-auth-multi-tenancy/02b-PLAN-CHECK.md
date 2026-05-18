# Phase 2b — Plan Check

**Reviewed:** 2026-05-18
**Verdict:** PASS (with 2 CONCERNs — non-blocking; executor should be alerted)

## Scope-creep check

**Zero violations.** Both plans' `requirements:` frontmatter list only in-scope IDs:
- 02b-01: {AUTH-15, 16, 17, 18}
- 02b-02: {AUTH-05, 06, 07, 08, 09, 11, 12}
- Union = exactly the 11 in-scope IDs from D-2b-01.

No tasks close any deferred requirement (AUTH-01/02/03/04/10/13/14/19/20/21). The CLI subcommands deliberately stop at create/list/revoke (no user/org/RBAC plumbing). Budgets are spend-only — no RPM/RPD plumbing. Banner mentions only `default` tenant. D-2b-01 honored.

## Goal-backward coverage

| # | Success criterion (CONTEXT) | Delivered by | Verdict |
|---|----------------------------|--------------|---------|
| 1 | API key gate works (set → 401 w/o valid Bearer; revoked → 401) | 02b-02 T2 (middleware) + T1 (revocation lookup) + T3 (CLI). Tests `test_missing_authorization_returns_401`, `test_revoked_key_returns_401_immediately`, `test_valid_key_resolves_tenant`. | PASS |
| 2 | SQLi fuzz returns 401; CI grep gate active | 02b-01 T5 (3 fuzz tests + `SQL injection guard` CI step). T4 audit precondition ensures grep gate stays green. | PASS |
| 3 | Cross-tenant isolation for tenant_id="default" (parameterized + WHERE tenant_id=?) | 02b-01 T2/T3 (concrete *RepoSqlite). `test_record_usage_isolates_by_tenant`, `test_sessions_cross_tenant_isolation`, `test_memory_cross_tenant_isolation`, `test_memory_fts_query_is_parameterized`. Grep invariant ≥12 enforced. | PASS |
| 4 | Concurrent budget stress (50 vs $1, ≤10% overshoot) | 02b-02 T4 (`test_stress_50_concurrent`, asserts 10 ≤ successes ≤ 11). | PASS |
| 5 | Auto-upgrade migration idempotent | 02b-01 T1 (`test_migration_idempotent` exercises down/up cycle; `test_backfill_populates_existing_rows`). | PASS |
| 6 | Backward-compat preserved (unset → unauthenticated) | 02b-02 T2 (`test_auth_disabled_when_env_unset`), T5 (`test_budget_applies_when_auth_disabled`), T8 (`test_full_flow_with_auth_disabled`). | PASS |

## In-scope AUTH closure

| AUTH-XX | Description | Plan/Task | Verdict |
|---------|-------------|-----------|---------|
| AUTH-05 | Per-tenant keys w/ scopes; plaintext shown once | 02b-02 T1 (generate_key) + T3 (CLI create) | PASS |
| AUTH-06 | `mllm_live_<token_urlsafe(32)>` / `mllm_test_*` format | 02b-02 T1 (`test_generate_key_format`) | PASS |
| AUTH-07 | SHA-256 hashed at rest; plaintext never persisted | 02b-02 T1 (store_key signature forbids plaintext; defensive contract test) + T3 (`test_api_key_create_stores_hash_only`) | PASS |
| AUTH-08 | `hmac.compare_digest` for timing-safe compare | 02b-02 T2 (`test_timing_safe_comparison` spy) | PASS |
| AUTH-09 | Revocation effective on next request | 02b-02 T1 (`test_revoke_key_returns_none_on_lookup`) + T2 (`test_revoked_key_returns_401_immediately`); no middleware cache | PASS |
| AUTH-11 | Daily/monthly cap → 429 + Retry-After | 02b-02 T5 (`test_budget_exhausted_returns_429_with_retry_after`) | PASS |
| AUTH-12 | Atomic decrement; 50 concurrent ≤10% overshoot | 02b-02 T4 (`test_stress_50_concurrent`) | PASS |
| AUTH-15 | Cross-tenant isolation (zero cross-bleed in usage/sessions/memory) | 02b-01 T2/T3 isolation tests | PASS |
| AUTH-16 | SQLi regression test returns 401 | 02b-01 T5 (3 vectors) | PASS |
| AUTH-17 | CI grep gate (zero f-string-SQL matches) | 02b-01 T4 (audit) + T5 (CI step `SQL injection guard`) | PASS |
| AUTH-18 | Auto-upgrade migration backfills tenant_id='default' | 02b-01 T1 (test_backfill_populates_existing_rows) | PASS |

11/11 in-scope requirements have explicit task + test coverage.

## Per-dimension scores

| Dim | Verdict | Notes |
|-----|---------|-------|
| A. Goal-backward coverage | PASS | All 6 CONTEXT success criteria mapped to specific tasks + tests. |
| B. Scope-creep check | PASS | No deferred req touched; frontmatter sums to exactly the 11 in-scope IDs. |
| C. In-scope AUTH closure | PASS | All 11 AUTH IDs claimed in frontmatter; each mapped to test. |
| D. Task atomicity | PASS | 14 tasks → 14 atomic commits. T3 (sessions+memory together) is the only multi-module task and is justified (mirror pattern of T2). Each ends with explicit `<type>(02b-XX): <subject>` commit per output blocks. |
| E. Verify gate quality | CONCERN | Migration idempotency test exists at unit level (`test_migration_idempotent`) BUT the Task 1 `<verify>` block doesn't actually run `migrate down && migrate up` end-to-end — only `migrate up` via the inline python check. Idempotency is exercised in pytest but not asserted in the verify shell command. SQLi gate verify in T5 (02b-01) runs both the regression suite AND `grep -q "SQL injection guard"` ✓. Stress test verify (02b-02 T4) runs the test ✓. Coverage delta verify (02b-02 T8) does run pytest --cov + compare ✓. |
| F. Pre-flagged risk coverage | CONCERN | See risk-by-risk table below — 5/7 fully mitigated, 2/7 partial. |
| G. Backward-compat (D-2b-08) | PASS | `auth_enabled()` re-reads env per request (no module-level capture) per T2 action text. Three tests cover the unset path: `test_auth_disabled_when_env_unset` (T2), `test_budget_applies_when_auth_disabled` (T5), `test_full_flow_with_auth_disabled` (T8). |

## Pre-flagged risk coverage

| # | Risk | Mitigated? | Evidence |
|---|------|------------|----------|
| 1 | SQLite RETURNING ≥3.35 | Y | T4 action explicitly says "SQLite 3.35+ supports RETURNING; the project pins ≥3.35 per pyproject — confirm before writing." Documentation in plan + version assumption surfaced. |
| 2 | 50-concurrent stress test connection model | PARTIAL | T4 uses ThreadPoolExecutor(max_workers=50). The action says "SQLite's per-connection write lock serializes the UPDATEs naturally" — implying a shared connection. This is exactly the failure mode the planner flagged (one connection ≠ one-per-worker). The plan does NOT explicitly mandate one connection per thread. **CONCERN** — executor needs explicit guidance: `sqlite3.connect()` per-worker (and `check_same_thread=False`) or thread-local connection. Sharing one connection across 50 threads will either serialize trivially (defeats the concurrency premise) or raise `ProgrammingError`. |
| 3 | Cost-estimation overshoot determinism | Y | T4 behavior: "cap = 100 cents; cost per request = 10 cents (mock backend); 50 concurrent requests" — both cap and cost pinned. Test setup seeds budgets directly with `daily_cap_cents=100`. |
| 4 | BudgetMiddleware body double-read | PARTIAL | T5 action acknowledges: "Parse request body once (carefully — must not consume the stream for the downstream handler). Pattern: read body bytes, store on `request.state.cached_body`, and patch `request._receive` so the downstream handler reads the same bytes. (Standard FastAPI pattern; if request body is already cached by an upstream middleware, use that.)" — the awareness is present but the instruction stops at "use that". **CONCERN** — executor should be told to grep `multillm/gateway.py` and any existing middlewares (`auth.py`, prior body-reading code) for an existing cached-body pattern BEFORE implementing patch-on-receive, since the gateway already streams large bodies (SSE) and a naive double-read will deadlock streaming. |
| 5 | T4 (02b-01) f-string SQL audit may surface large diff | Y | T4 action: "Do not refactor anything beyond the SQLi fix in this task (no opportunistic cleanup). Each fix is a minimal diff. If the audit finds zero violations, that's a valid outcome — commit an empty change as a `chore` documenting the audit was performed." Escape valve is the minimal-diff rule + chore-only commit option. |
| 6 | MULTILLM_API_KEY env-var semantics shift | Y | T6 (02b-02) banner explicitly tells the operator the migration created a `default` tenant and points to `multillm api-key create`. D-2b-08 preserves the unset behavior so old installs aren't broken. SUMMARY (T8) is required to document the locked discretion. README/docs surfacing not explicitly in scope — but the lifespan banner serves as the in-band signal. |
| 7 | Test count target ≥410 optimistic | Y | T8 verify uses coverage delta as the gate (`cur - base >= -0.01`), NOT an absolute count. The "≥410" appears only in T8 SUMMARY narrative as a target, not in the verify command. Coverage delta is the merit gate. |

## Concrete findings

1. **(MINOR — T4 02b-02) Stress test connection model.** Add to the action: "Each worker MUST open its own `sqlite3.Connection` to the same DB file; do not share a connection across threads. SQLite's filesystem-level locking serializes the atomic UPDATEs; the WHERE-guard fails-fast on exhausted rows." Without this, the test will either be trivially serialized (no real concurrency) or raise SQLite threading errors.

2. **(MINOR — T5 02b-02) Body double-read.** Add to the action: "Before implementing the `request._receive` patch, grep `multillm/` for existing `await request.body()` call sites and for any cached-body middleware. If one exists upstream of BudgetMiddleware in the mount order, consume the cached body; do NOT re-patch `_receive` twice (causes hang on streaming SSE). If none exists, the patch-on-receive pattern is correct."

3. **(NIT — T1 02b-01) Migration idempotency in verify gate.** Consider expanding the `<verify>` block to actually invoke `python -m multillm.migrations.runner down && python -m multillm.migrations.runner up` rather than only `up`. The pytest test covers it, but the verify shell command is the executor's runtime sanity check.

Neither finding 1 nor 2 invalidates the plan — they're guidance to make T4 and T5 succeed on the first execution attempt instead of looping through a revision cycle.

## Verdict rationale

**Overall: PASS.** Both plans are tight, well-scoped, and goal-aligned. Every one of the 6 CONTEXT success criteria maps to one or more specific tasks with dedicated tests. Every one of the 11 in-scope AUTH requirements appears in exactly one plan's `requirements:` frontmatter with no double-counting and no deferred-req leakage. Task atomicity is good (14 atomic commits across 14 tasks). The threat model on each plan is non-token: it actually enumerates STRIDE categories with disposition + mitigation reference.

The 2 CONCERN-level findings (stress test connection model, body double-read) are real engineering hazards but do not signal a misunderstanding of goals — they signal that the executor will hit two specific stack-overflow-grade gotchas if they execute the plan literally without inspecting the existing codebase first. Both are fixable with a 2-line addition to the relevant task's action text and do not require a plan revision.

The pre-flagged risk coverage is 5/7 fully mitigated and 2/7 partial — and the 2 partials are exactly the ones the planner already flagged as risks. The planner's self-disclosure was accurate; the plan text doesn't fully close them, but the awareness is documented (T4 mentions thread serialization, T5 mentions cached-body fallback). The executor will benefit from an in-line nudge but this is not a blocking gap.

Recommendation: **proceed to gsd-executor with two heads-up notes** (concerns 1 and 2 above) appended to the orchestrator handoff. Do not loop back through plan revision — the cost of a 2-line action-text amendment exceeds the cost of giving the executor 2 explicit advisories at handoff time.
