# Phase 2a — Discussion Log

**Date:** 2026-05-18
**Mode:** default (single-question turns, AskUserQuestion)
**Areas discussed:** all 4 identified

---

## Area 1 — Migration sequencing

**Question:** How granular should the migration plan(s) be?
**Options presented:**
- (selected) 2 plans — foundation + bulk migration (matches ROADMAP)
- 4 plans — grouped by adapter family
- 1 plan — all-at-once strangler swap

**Decision:** 2 plans. Plan 02a-01 wires foundation (registry, entry_points, tenant Protocol scaffold) and migrates one trivial backend (ollama) as proof. Plan 02a-02 migrates the remaining 12 backends in atomic per-backend commits, then retires the inline functions and the if/elif chain.

**Notes:** Per-backend commits inside Plan 02a-02 preserve bisect-rollback granularity even though plans are coarse.

---

## Area 2 — Entry-points vs hardcoded

**Question:** When does the entry_points() discovery foundation get wired?
**Options presented:**
- (selected) In Plan 02a-01 (foundation) — built-ins register via entry_points
- In Plan 02a-01 scaffold, built-ins stay hardcoded for now
- Defer entry_points to Phase 9 — ship dict-based in 2a

**Decision:** Built-ins self-declare via `[project.entry-points."multillm.backends"]` in pyproject.toml. Registry consumes `importlib.metadata.entry_points(group='multillm.backends')` from Plan 02a-01. ARCH-07 closes in Phase 2a (not deferred).

**Notes:** Phase 9 plugin SDK becomes a one-line cost — third-party packages declare the same entry-point group.

---

## Area 3 — Tenant Protocol shape

**Question:** How should `tenant_id` appear in the data-access Protocol?
**Options presented:**
- (selected) Required, no default — grep-enforceable invariant
- Required positional, with a `DEFAULT_TENANT` constant
- Default value (`tenant_id: str = "default"`)

**Decision:** Required positional, no default value. Every existing call site passes the literal string `"default"` in Phase 2a. Phase 2b replaces those literals with real tenant values from request context.

**Notes:** Bridge grep for Phase 2b setup: `git grep -nE 'repo\.\w+\(\s*"default"' multillm/` enumerates every "must replace in 2b" call site.

---

## Area 4 — Behavior-parity proof

**Question:** What's the proof-of-behavior-parity bar for Phase 2a?
**Options presented:**
- (selected) Trust the 351 tests; add coverage delta enforcement
- Add golden-file routing tests at the boundary
- Parallel-execution validation in CI
- Recorded production-style smoke + golden

**Decision:** Each plan commit gates on `pytest -q` passing AND `pytest --cov` showing coverage delta ≥ 0 vs the pre-commit baseline. No golden-file snapshot suite, no parallel-execution validation.

**Notes:** If a regression escapes the 351-test suite, Phase 2a is the wrong time to discover it — a follow-up "test gap closure" plan would be the right response, not heavier mid-refactor proofs.

---

## Canonical refs added during discussion

(none beyond what was identified in pre-analysis — user did not reference additional docs during the session)

## Deferred ideas

None. No scope creep was suggested.

## Claude's discretion

The following items were not raised in the discussion; planner resolves from REQUIREMENTS.md and codebase:

- Order of backend migration within Plan 02a-02
- Whether `cloud_openai_compat` family resolves as 6 separate entry-point entries or 1 family-level entry
- Coverage baseline capture mechanism (probably `coverage.json` snapshot committed per plan)
