# Phase 2a — Plan Check

**Reviewed:** 2026-05-18
**Verdict:** NEEDS-REVISION
**Scope:** 02a-01-PLAN.md (6 tasks) + 02a-02-PLAN.md (19 tasks)

## Goal-backward coverage

Mapping ROADMAP "Success Criteria" → task(s) → verdict:

| SC# | Criterion | Delivered by | Verdict |
|-----|-----------|--------------|---------|
| 1   | `route_request` / `route_streaming` each ≤ 3 lines | 02a-02 Task 18 | CONCERN — planner reinterpreted "≤ 3 lines" as "≤ 3 dispatch lines" rather than total function length. The provided `route_request` body has ~14 lines. Documented interpretation is internally consistent; the bridge gate is "no `elif backend ==`". But this is an explicit reframing of the ROADMAP wording; orchestrator should confirm it's acceptable (or planner must collapse further by moving route resolution / health gating into a helper). |
| 2   | 351 tests green; coverage delta ≥ 0 | 02a-01 Task 6 (baseline) + 02a-02 Task 19 (delta gate) | PASS |
| 3   | No `if/elif backend == "..."` in gateway.py | 02a-02 Task 18 grep gate; Task 19 re-asserts | PASS |
| 4   | `multillm/db/repo.py` Protocol with `tenant_id`-first signature on every method | 02a-01 Task 4 + tests/test_db_repo_protocol.py | PASS |
| 5   | Registry uses `importlib.metadata.entry_points()` | 02a-01 Task 1 + Task 2 + Task 3 | PASS |

**Overall SC coverage:** 4 PASS / 1 CONCERN.

## ARCH requirement closure

| ARCH-XX | Description | Plan/Task | Verdict |
|---------|-------------|-----------|---------|
| ARCH-01 | All inline `_call_<backend>` migrated to `BaseAdapter` subclasses | 02a-02 Tasks 1–17 (per-backend) + Task 19 (deletion) | PASS |
| ARCH-02 | `route_request` / `route_streaming` ≤ 3 lines | 02a-02 Task 18 | CONCERN (see SC#1 above) |
| ARCH-03 | if/elif chain removed | 02a-02 Task 18 (grep gate) + Task 19 (re-grep) | PASS |
| ARCH-04 | `multillm/db/repo.py` Protocol with tenant_id-first | 02a-01 Task 4 | PASS |
| ARCH-05 | Full suite green; coverage delta ≥ 0 | 02a-01 Task 6 + 02a-02 Task 19 | PASS |
| ARCH-06 | Zero public API surface change | 02a-01 verification gate (curl smoke) + 02a-02 verification gate | PASS (smoke is manual/non-blocking, but 351-test suite is the proof) |
| ARCH-07 | Registry uses entry_points() | 02a-01 Tasks 1–3 | PASS |

**Frontmatter `requirements:` cross-check:**
- 02a-01 declares `[ARCH-04, ARCH-05, ARCH-06, ARCH-07]` — note ARCH-05/ARCH-06 are listed but only partially closed (baseline + ollama-only proof); the plan's own `<success_criteria>` honestly labels them "Partial". OK.
- 02a-02 declares `[ARCH-01, ARCH-02, ARCH-03, ARCH-05, ARCH-06]` — closes the partials.
- Union of both: ARCH-01..ARCH-07. **Complete.**

## Per-dimension scores

| Dim | Verdict | Notes |
|-----|---------|-------|
| A. Goal-backward coverage | CONCERN | SC#1 reframing of "≤ 3 lines" needs explicit orchestrator sign-off; otherwise all five criteria are addressed. |
| B. ARCH-01..ARCH-07 mapping | PASS | Union of frontmatter `requirements:` covers all 7. Each ARCH has a specific task. |
| C. Task atomicity (one commit per backend) | PASS | 02a-02 Tasks 1–17 are 1-backend-per-task with explicit commit messages. Tasks 18 and 19 are the dispatch-collapse and inline-deletion commits respectively. Bisect granularity matches D-2a-01. |
| D. Verify gate quality | CONCERN | Most gates are executable (`pytest`, `grep -c`, `python -c`). Two issues: (a) several per-backend tasks (Tasks 6–10, 12, 13, 14, 15, 16, 17) verify only `grep -c 'get_adapter("<name>")'` and `pytest`; they do NOT verify the adapter `send()`/`stream()` actually handles the quirks the action text instructs the executor to port. There's no automated check that, e.g., AzureOpenAIAdapter.stream() wraps in JSONResponse, or OCAAdapter.send() handles SSE-when-non-streaming. The 351 existing tests are the catch-all but may not exercise every quirk path. (b) Task 19's import-pruning step lists ~20 names to consider — the verify gate runs `ruff` indirectly via the action text, but the actual `verify` block doesn't `ruff check` or `python -m pyflakes`, so silent over-pruning could land. |
| E. Read-first-files explicit | PASS | Every task has `<read_first>` with absolute paths. CONTEXT.md anchor list (file:line) is referenced in plan interfaces. Tasks 6–10 (deepseek..fireworks) read only gateway.py, which is acceptable given Tasks 5 establishes the pattern. |
| F. Coverage gate fidelity | PASS | 02a-01 Task 6 writes `coverage-baseline.json`; 02a-02 Task 19's verify block loads both JSONs, extracts `totals.percent_covered`, compares with `-0.01` epsilon. Reproducible. README sidecar at Task 6 documents the comparison rule. |
| G. Pre-flagged risk coverage | CONCERN (4/6 fully mitigated, 2/6 partially) | See per-risk breakdown below. |

## Pre-flagged risk coverage (per orchestrator's 6 risks)

1. **Adapter-class drift hazard (per-backend quirks)** — PARTIAL (Y for instructions, N for automated verify). Action text in Tasks 4 (openrouter), 11 (anthropic), 13 (oca), 14 (azure_openai), 15 (bedrock), 16/17 (CLI subprocess) explicitly enumerates each quirk and instructs the executor to port it. However, the `<verify>` blocks for these tasks do not include behavior assertions for the quirks themselves; they rely on the 351-test suite. Risk: a quirk could be ported incorrectly without specific test coverage.
2. **`pip install -e .` for entry-point discovery** — Y. 02a-01 Task 2 `<verify>` runs `pip install -e . --no-deps -q` before asserting entry_points enumerate to 18.
3. **Coverage baseline reproducibility** — Y. 02a-01 Task 6 captures via committed JSON; 02a-02 Task 19 compares the exact same `totals.percent_covered` field with `-0.01` epsilon.
4. **"≤ 3 lines" interpretation documented** — Y. 02a-02 Task 18 action explicitly documents the dispatch-primitive interpretation and identifies the 3 load-bearing lines.
5. **AnthropicAdapter error string drift** — N (NOT addressed). Risk #5 in the orchestrator's list is not called out as a specific verification in 02a-02 Task 11; the action mentions the passthrough behavior and the `claude-*` fallback but does not instruct manual verification of the inline error path's exact error string vs the adapter's error string. If existing tests assert specific error messages, they will catch drift; otherwise this is a latent risk.
6. **`register_adapter()` shim preserved as intentional** — Y. 02a-01 Task 1 action explicitly states "KEEP as a backward-compat shim", adds `test_register_adapter_shim`, and threat-model T-02a-01-04 documents the acceptance.

## Concrete findings

**Severity legend:** [BLOCKER] = must fix before execute. [HIGH] = strongly recommended. [MEDIUM] = improvement. [LOW] = polish.

1. **[HIGH] Quirk porting has no per-task automated verification.** Tasks 11 (anthropic), 13 (oca), 14 (azure_openai), 15 (bedrock), 16/17 (CLI subprocess) instruct the executor to port specific behaviors into the adapter but the `<verify>` block only runs `pytest -q` (which relies on existing tests catching everything) and `grep -c 'get_adapter(...)`. Recommendation pointer: add an inline `python -c "from multillm.adapters.<x> import <X>Adapter; a = <X>Adapter(); assert hasattr(a, 'stream') and callable(a.stream); ..."` style assertion to each quirk-heavy task, or instruct the executor to add a unit test scoped to each adapter's quirk path as part of the per-backend task. Without this, regressions could slip through the 351-test suite if no existing test exercises the exact quirk path.

2. **[HIGH] SC#1 "≤ 3 lines" reinterpretation needs orchestrator sign-off.** The ROADMAP wording is unambiguous: "`route_request()` and `route_streaming()` in `gateway.py` are each ≤ 3 lines". The plan reframes this as "≤ 3 dispatch lines, error handling/route resolution exempt", with `route_request` ending up at ~14 lines total. This is a defensible reading but it is a reframing. Pointer: either (a) orchestrator confirms the reframing in writing (CONTEXT.md addendum), or (b) Task 18 is revised to push route resolution into `_select_route()` (already exists), health gating into a `_check_health(backend)` helper, and the retry-vs-CLI branching into a `_dispatch(backend, ...)` helper, so the visible function body is genuinely ≤ 3 executable lines.

3. **[MEDIUM] AnthropicAdapter error path (orchestrator's risk #5) is not explicitly verified.** Task 11's `<verify>` checks for ≥ 3 `get_adapter("anthropic")` references but not that error responses match the inline path's format. Pointer: add to Task 11 action a sentence "before swapping, locate the inline `_call_anthropic_real` error-raising code paths and confirm `AnthropicAdapter.send()` raises with identical status codes and detail strings".

4. **[MEDIUM] Task 19 import-pruning verify block doesn't run a lint pass.** The action text says "Run `ruff --fix --select F401 multillm/gateway.py`" but the `<verify>` block does not. A silent over-pruning that pytest happens not to catch (e.g., an import referenced only by a code path not exercised by tests) would slip. Pointer: add `python -m pyflakes multillm/gateway.py || ruff check --select F401 multillm/gateway.py` to Task 19's verify gate.

5. **[LOW] Tasks 6–10 (deepseek..fireworks) `<read_first>` is minimal.** They list only `multillm/gateway.py`. Task 5 already establishes the family pattern, so this is acceptable, but adding `multillm/adapters/cloud_openai_compat.py` would let the executor verify the factory exists without rediscovering it. Pointer: add the cloud_openai_compat reference to each task's `<read_first>`.

6. **[LOW] Task 5 verify block asserts `a.base_url`** but the `CloudOpenAICompatAdapter` exposes that attribute by name; if the class instead names it `_base_url` (private), the assertion will fail. Pointer: confirm attribute name during execution or change assertion to `a.name == 'groq'` only.

7. **[LOW] Task 18 verify regex check** scans the source for `^async def route_(request|streaming).*\n(?:.*\n){1,4}$` per the action text but the actual `<verify>` block only checks `elif backend ==` count = 0. The line-count regex is described but not enforced. Pointer: either add the regex check to verify or remove the description.

## Verdict rationale

**Verdict: NEEDS-REVISION** — but borderline. The plan set is fundamentally sound: ARCH-01..ARCH-07 are all mapped, the foundation/bulk split honors D-2a-01, the coverage delta gate is real and reproducible, per-backend atomicity matches the bisect requirement, and the entry_points + Protocol shape are correctly scoped. None of the findings invalidate the design; the plans would likely succeed.

What pushes this to NEEDS-REVISION rather than PASS is finding #2 (SC#1 reinterpretation): the ROADMAP wording is concrete and short, and the planner has explicitly reframed it. The orchestrator should either accept the reframing (in which case a one-line note in CONTEXT.md or a sign-off in the plan-checker response is sufficient and the verdict flips to PASS) or push Task 18 to extract helpers so the visible function bodies are genuinely ≤ 3 lines. Either path is short.

Finding #1 (no automated per-quirk verification) is the second weight on the verdict. Five of the per-backend tasks instruct careful quirk porting but rely entirely on the existing 351-test suite to catch regressions. If those tests have gaps for OCA SSE, Bedrock Converse, or Azure deployment-URL routing, the per-task gate will pass while behavior silently drifts. Adding even a one-line `python -c "..."` smoke per quirk would close this.

The remaining findings are MEDIUM/LOW and could be addressed during execution by a careful executor without revision. The pre-flagged risks 2, 3, 4, 6 are fully mitigated; risks 1 and 5 are partially mitigated (instructions present, automated verification absent).

**Recommendation to orchestrator:** push back to planner with the two HIGH findings only. The LOW/MEDIUM findings can be picked up in the executor's review or as inline corrections during execution. After planner addresses (a) SC#1 reinterpretation (clarify or collapse further) and (b) quirk verification (add per-task smoke assertions for the five quirk-heavy tasks), this plan set is ready to go to gsd-executor.
