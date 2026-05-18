---
phase: 02a-adapter-hot-path-refactor
plan: 01
subsystem: dispatch-registry
tags: [refactor, adapters, registry, entry-points, protocol, foundation]

requires:
  - phase: 01-open-source-readiness
    provides: clean origin/main, 351 tests passing
provides:
  - importlib.metadata.entry_points()-based adapter registry (multillm/adapters/registry.py)
  - 17-entry [project.entry-points."multillm.backends"] table in pyproject.toml
  - 6 cloud_openai_compat family factories (make_groq/make_deepseek/make_mistral/make_together/make_xai/make_fireworks)
  - multillm/db/repo.py SessionRepo/TrackingRepo/MemoryRepo Protocols with tenant_id-first signatures
  - ollama proof-of-concept dispatching through get_adapter('ollama').send()/.stream()
  - coverage-baseline.json for Plan 02a-02's delta gate
affects: [02a-02 Bulk migration consumes all of the above]

tech-stack:
  added: [importlib.metadata.entry_points, typing.Protocol]
  patterns:
    - "entry_points-based plugin discovery — Phase 9 third-party plugins declare under the same group and the registry picks them up automatically"
    - "Lazy single-pass discovery via _discovery_done flag with idempotent reset_for_tests() escape hatch"
    - "Test mocking targets the adapter's send() method (the behavioral boundary) rather than implementation-specific symbols — durable across migrations"

key-files:
  created:
    - multillm/db/__init__.py
    - multillm/db/repo.py
    - tests/test_registry_entry_points.py
    - tests/test_db_repo_protocol.py
    - .planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json
    - .planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.README.md
  modified:
    - multillm/adapters/registry.py (rewritten — entry_points discovery + register_adapter shim + reset_for_tests)
    - multillm/adapters/__init__.py (re-export reset_for_tests)
    - multillm/adapters/cloud_openai_compat.py (added 6 family-factory callables)
    - pyproject.toml (added [project.entry-points."multillm.backends"] table)
    - multillm/gateway.py (ollama dispatch through registry; get_adapter import)
    - tests/test_gateway.py (3 ollama tests now mock OllamaAdapter.send via AsyncMock)

key-decisions:
  - "Honored D-2a-02 day-one entry_points: built-ins self-declare in pyproject.toml; ARCH-07 closes in Phase 2a, not deferred to Phase 9"
  - "Honored D-2a-03 required-positional tenant_id with no default; signature invariant enforced via both runtime Python tests AND a git-grep one-liner that returns exactly 12 matches across multillm/db/"
  - "Plan deviation: 17 entry points, not 18. The plan listed openai_compat as a class-based adapter but it is actually a shared helper module (call_openai_compat function), no class exists. Dropped from the entry-points table. OpenAI-compat backends use the family factories or dedicated classes."
  - "Plan deviation: ollama-discovery test split out of Task 1 and added back in Task 2 (chicken-and-egg — the test requires the entry-points table). Final state: 4 tests in test_registry_entry_points.py, all green."
  - "Inline _call_ollama and stream_ollama imports preserved in gateway.py as dead code on the ollama path. Plan 02a-02's final task batches retirement of all 13 inline functions in one reviewable commit."

patterns-established:
  - "Build-up sequence: discovery mechanism (Task 1) → contract declaration (Task 2) → contract fulfillment (Task 3) → shape introduction (Task 4) → end-to-end proof on one backend (Task 5) → baseline capture (Task 6). Each step independently bisectable."
  - "Dual-path coexistence: setup.py:register_all_adapters() still runs at gateway startup AND entry_points discovery runs on first get_adapter() call. Both routes converge on the same in-process cache. register_adapter() preserved as a shim."
  - "Test mocking pattern unification: every test that stubs adapter behavior uses `@patch('multillm.adapters.<x>.<X>Adapter.send', new_callable=AsyncMock)`. Pattern was already in use for codex_cli and gemini_cli; ollama joins it."

requirements-completed: [ARCH-04, ARCH-07]
requirements-partial: [ARCH-05, ARCH-06]

duration: ~75min (interactive, 6 commits)
completed: 2026-05-18
---

# Phase 02a Plan 01 — Foundation (registry + entry_points + Protocol)

**Wired `importlib.metadata.entry_points()`-based adapter discovery, declared 17 built-in backends in `pyproject.toml`, introduced `multillm/db/` Protocols with tenant_id-first signatures, and migrated ollama end-to-end through the registry as the proof backend — all in 6 atomic commits with 359 tests green at every step.**

## Performance

- **Duration**: ~75 min (interactive mode, 6 atomic commits, single session)
- **Completed**: 2026-05-18
- **Tasks**: 6 (all complete)
- **Commits**: `f1c67bb`, `4afc1be`, `0ab65e4`, `ec07f62`, `c182e3c`, `87d9edf`
- **Test delta**: 351 (start of Phase 2a) → 359 (end of Plan 02a-01) — 8 new tests, zero regressions
- **Coverage baseline**: 62.53% line coverage, 3352/5361 statements

## Accomplishments

### Task 1 (`f1c67bb`) — registry rewrite
- `multillm/adapters/registry.py` now consumes `importlib.metadata.entry_points(group='multillm.backends')` lazily on first lookup.
- `_discover_adapters()` handles class entries (instantiate), callable factories (call), and pre-built instances (insert as-is). Skips with a logged warning on load failure so one broken plugin doesn't poison the registry.
- `register_adapter()` preserved as a backward-compat shim for `multillm/adapters/setup.py:register_all_adapters()` and for tests.
- `reset_for_tests()` clears cache + discovery flag.
- 3 new tests in `tests/test_registry_entry_points.py`.

### Task 2 (`4afc1be`) — entry-points table
- Added `[project.entry-points."multillm.backends"]` to `pyproject.toml` with 17 entries (11 single-class adapters + 6 cloud_openai_compat family factories).
- **Deviation from plan**: dropped `openai_compat` because the module has no adapter class (it's a shared helper). Plan said 18; reality is 17.
- 4th test added: `test_discovery_finds_ollama` confirms registry resolves `ollama` through the table.

### Task 3 (`0ab65e4`) — family factories
- 6 new module-level callables in `multillm/adapters/cloud_openai_compat.py`: `make_groq`, `make_deepseek`, `make_mistral`, `make_together`, `make_xai`, `make_fireworks`.
- URLs and key bindings lifted verbatim from `setup.py:45-50` for behavioral parity.
- After this commit, registry resolves all 17 entries cleanly (no load failures).

### Task 4 (`ec07f62`) — db Protocol shape
- New `multillm/db/__init__.py` and `multillm/db/repo.py`.
- Three `typing.Protocol` classes: `SessionRepo` (4 methods), `TrackingRepo` (3 methods), `MemoryRepo` (5 methods) = 12 methods.
- Every method takes `tenant_id: str` as first non-self positional, no default.
- 4 new tests in `tests/test_db_repo_protocol.py` enforce the signature invariant via `inspect.signature` reflection.
- **Grep invariant**: `git grep -nE 'def [a-z_]+\(self, tenant_id:' multillm/db/` returns exactly 12. This is the Phase 2b bridge.
- **ARCH-04 closed.**

### Task 5 (`c182e3c`) — ollama through registry
- `multillm/gateway.py` ollama dispatch sites in both `route_streaming()` (line 665) and `_route_single_request()` (line 740) now go through `get_adapter("ollama").send()/.stream()`.
- Inline `_call_ollama()` and `stream_ollama` import preserved as dead code on the ollama path — retired in Plan 02a-02's final cleanup task.
- 3 gateway tests updated to mock `OllamaAdapter.send` via `AsyncMock` instead of `_call_ollama`. Pattern matches existing codex_cli/gemini_cli tests.
- Other 11 backends remain on inline `_call_<backend>` dispatch. Dual-path coexistence is intentional per D-2a-01.

### Task 6 (`87d9edf`) — coverage baseline
- `pytest --cov=multillm --cov-report=json:` written to `.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json`.
- README sibling file documents regeneration command and environment-dependency caveat (boto3, google-genai).
- Baseline: **62.53% line coverage**. Plan 02a-02 Task 20 gates `totals.percent_covered` delta ≥ -0.01.

## Verification gates (all pass)

| Gate | Result |
|------|--------|
| `python -c "from multillm.adapters import get_adapter, list_adapters, reset_for_tests"` | clean import |
| `python -c "from importlib.metadata import entry_points; assert len(list(entry_points(group='multillm.backends'))) == 17"` | 17 entries |
| `python -c "from multillm.adapters.registry import reset_for_tests, list_adapters; reset_for_tests(); assert len(list_adapters()) == 17"` | all 17 resolve |
| `git grep -nE 'def [a-z_]+\(self, tenant_id:' multillm/db/ \| wc -l` | 12 |
| `pytest -q` | 359/359 passed |
| `coverage-baseline.json` exists and is well-formed | yes (62.53% baseline) |

## Threat-mitigation evidence

| Threat | Disposition | How it landed |
|--------|-------------|---------------|
| T-2a-01 (entry-point load failure poisons registry) | mitigated | `_discover_adapters()` wraps each `ep.load()` in try/except; logs warning and skips. Observed in action: 6 factory entries failed to load between Tasks 2 and 3, registry survived and served the working 11. |
| T-2a-02 (signature invariant drift in db/repo.py) | mitigated | Dual enforcement: `inspect.signature` test plus 12-match git-grep one-liner. Either catches a regression. |
| T-2a-03 (ollama path regression at swap) | mitigated | 351 → 359 tests pass; 3 gateway tests updated to mock the new boundary. End-to-end proof. |

## Plan deviations (committed audit trail)

1. **17 entries, not 18.** `openai_compat` dropped because no class exists in that module. Documented in Task 2 commit body + this SUMMARY.
2. **ollama-discovery test split.** Originally in Task 1; required Task 2's table to pass. Moved test to Task 2; Task 1's test file has 3 always-passing tests for the registry mechanism itself. Net: same test count, cleaner per-task gates.

## Open for Plan 02a-02

- Order of backend migration within 02a-02 (the planner already locked simplest-first per `02a-02-PLAN.md`)
- The 12 remaining inline `_call_<backend>` functions — each migration is one atomic commit
- Helper extraction (`_check_health`, `_dispatch_with_resilience`) to enable literal ≤3-line `route_request`/`route_streaming` (Task 18)
- Coverage delta gate against this baseline at Task 20

Plan 02a-02 is ready to execute via `/gsd-execute-phase 2a --wave 2 --interactive`.
