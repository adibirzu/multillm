---
phase: 02a-adapter-hot-path-refactor
created: 2026-05-18
status: discussed; ready for plan-phase
spec_loaded: false
---

# Phase 2a — Adapter Hot-Path Refactor (CONTEXT)

## Domain

Phase 2a kills the dual code path in MultiLLM. Today `multillm/gateway.py` (1936 lines) carries 10 inline `_call_<backend>` functions and an `if/elif backend == "..."` chain in `route_request()` and `route_streaming()`. The parallel `multillm/adapters/*.py` class hierarchy (13 adapter files + `BaseAdapter` + an in-memory registry) is already half-built and unused on the hot path. CLAUDE.md flags this in "Development Notes": *"Gateway uses inline routing functions in gateway.py, not the adapter registry — both must be kept in sync."*

Phase 2a:
1. Wires the registry as the only dispatch mechanism
2. Switches `route_request()` / `route_streaming()` to 1-line delegates
3. Retires every inline `_call_<backend>` and the if/elif chain
4. Introduces `multillm/db/repo.py` Protocol with `tenant_id` as the first non-self argument on every method (shape-only — real tenancy data lands in Phase 2b)
5. Wires `importlib.metadata.entry_points(group='multillm.backends')` for backend discovery (Phase 9 plugin SDK becomes additive)

Zero behavior change on public API (`/v1/messages`, `/routes`, `/api/*`). 351-test suite stays green; coverage delta zero or positive.

## Canonical refs

These files MUST be read by researcher and planner before authoring any plan:

- `.planning/ROADMAP.md` — Phase 2a goal + success criteria (lines 67-79)
- `.planning/REQUIREMENTS.md` — ARCH-01 through ARCH-07 invariants
- `multillm/gateway.py` — the 1936-line file being refactored; inline functions at lines 228..442, dispatch chain at lines 665..720, `route_request()` at 788, `route_streaming()` at 656
- `multillm/adapters/base.py` — the target `BaseAdapter` ABC (37 lines, already cleanly shaped)
- `multillm/adapters/registry.py` — current in-memory dict (28 lines); will be rewritten to use `entry_points()`
- `multillm/adapters/__init__.py` — current entry shape for the adapter package
- `CLAUDE.md` — "Development Notes" section warns about the dual code path; "Architecture" section sketches the target topology
- `pyproject.toml` — receives the `[project.entry-points."multillm.backends"]` section

No SPEC.md for this phase — implementation decisions follow from REQUIREMENTS.md ARCH-01..ARCH-07.

## Locked decisions

### D-2a-01: Plan structure — 2 plans, foundation + bulk

Matches the ROADMAP target "1–2 plans". The split:

- **Plan 02a-01: Foundation** — wire `entry_points()`-based registry, register all 13 built-in adapters via `[project.entry-points."multillm.backends"]` in pyproject.toml, introduce `multillm/db/repo.py` Protocol with `tenant_id` shape, update existing repo call sites to pass `tenant_id="default"`, migrate exactly ONE trivial backend (recommended: `ollama` — local, no auth, every dev can exercise it) end-to-end through the new path as a proof. Foundation plan does NOT touch the inline functions for other backends — both paths coexist after this plan.
- **Plan 02a-02: Bulk migration** — migrate the remaining 12 backends to the adapter+registry path. Each backend migration is an atomic commit so `git bisect` can isolate a regression to a single backend. After all backends migrate, switch `route_request()` / `route_streaming()` to ≤3-line delegates, delete the if/elif chain, delete the inline `_call_<backend>` functions.

Rationale: 1 plan would be too big for clean bisect-rollback if regressions appear. 4 plans (per-family) over-engineers the boundary. 2 plans gives a clean foundation+execution split that mirrors how the rest of the project structures phases.

### D-2a-02: entry_points discovery wired in Plan 02a-01 (built-ins included)

`multillm/adapters/registry.py` is rewritten to consume `importlib.metadata.entry_points(group='multillm.backends')` from day one. All 13 built-in adapters self-declare in pyproject.toml:

```toml
[project.entry-points."multillm.backends"]
anthropic = "multillm.adapters.anthropic:AnthropicAdapter"
azure_openai = "multillm.adapters.azure_openai:AzureOpenAIAdapter"
bedrock = "multillm.adapters.bedrock:BedrockAdapter"
codex_cli = "multillm.adapters.codex_cli:CodexCliAdapter"
gemini = "multillm.adapters.gemini:GeminiAdapter"
gemini_cli = "multillm.adapters.gemini_cli:GeminiCliAdapter"
lmstudio = "multillm.adapters.lmstudio:LmStudioAdapter"
oca = "multillm.adapters.oca:OcaAdapter"
ollama = "multillm.adapters.ollama:OllamaAdapter"
openai = "multillm.adapters.openai:OpenAIAdapter"
openai_compat = "multillm.adapters.openai_compat:OpenAICompatAdapter"
openrouter = "multillm.adapters.openrouter:OpenRouterAdapter"
# (cloud_openai_compat covers groq/deepseek/mistral/together/xai/fireworks; declared as a family entry in registry init)
```

This makes Phase 9 (plugin SDK) a one-line cost for third-party packages: they declare the same entry-point group and the registry picks them up at import. ARCH-07 closes in Phase 2a, not deferred.

Rationale: the alternative (entry_points scaffold but built-ins stay hardcoded) leaves a coupling Phase 9 would have to refactor anyway. Doing it once now is cleaner.

### D-2a-03: `tenant_id` is a required positional argument, no default

The `multillm/db/repo.py` Protocol shape:

```python
class SessionRepo(Protocol):
    def list_sessions(self, tenant_id: str, *, limit: int = 50) -> list[Session]: ...
    def get_session(self, tenant_id: str, session_id: str) -> Session | None: ...
    def create_session(self, tenant_id: str, session: Session) -> Session: ...
    # ... etc, tenant_id always first non-self
```

Every existing call site in Phase 2a passes the literal string `"default"`:

```python
repo.list_sessions("default", limit=20)
repo.get_session("default", session_id)
```

Phase 2b will replace `"default"` with real tenant values from request context. The grep invariant for Phase 2b setup:

```bash
git grep -nE 'repo\.\w+\(\s*"default"' multillm/   # finds every "must replace in 2b" call site
git grep -nE 'def \w+\(self, tenant_id:' multillm/db/   # confirms tenant_id-first on every Protocol method
```

Rationale: required-no-default is the strongest contract. The `tenant_id="default"` literal is intentional clutter that Phase 2b cleans up; the grep above is the bridge. Naming a `DEFAULT_TENANT` constant adds a layer that doesn't help — `"default"` is short, intentional, and easy to find-and-replace.

### D-2a-04: Behavior parity = 351 tests + coverage delta enforcement

Each plan commit gates on:

1. `pytest -q` exits 0 (351/351)
2. `pytest --cov=multillm --cov-report=term` shows coverage delta ≥ 0 vs the pre-commit baseline

No golden-file snapshot suite, no parallel-execution validation. The existing test suite already exercises the inline path heavily after Phase 1's coverage push; the adapter classes have their own unit tests. Coverage delta enforcement ensures we don't silently lose test coverage during the swap.

Rationale: the 351-test suite is the strongest signal available without inventing a new test mechanism. Golden-file replay was considered but rejected — the marginal confidence over the existing suite is small and the fixture authoring cost is real (~1 day for 13 backends × representative requests). If a regression escapes the 351 tests, Phase 2a is the wrong time to discover it; we'd need a Phase 2c "test gap closure" plan instead.

## Code context

### Inline functions to retire (gateway.py)

| Function | Line | Backends covered |
|----------|------|------------------|
| `_call_openai_compat` | 228 | openai, openrouter, groq, deepseek, mistral, together, xai, fireworks, lmstudio (generic) |
| `_call_ollama` | 246 | ollama |
| `_call_anthropic_real` | 288 | anthropic |
| `_call_oca` | 301 | oca |
| `_call_gemini` | 360 | gemini (Google AI direct) |
| `_call_codex_cli` | 390 | codex_cli |
| `_call_gemini_cli` | 395 | gemini_cli |
| `_call_openai_compat_backend` | 413 | (helper used by several openai-compat backends) |
| `_call_azure_openai` | 427 | azure_openai |
| `_call_bedrock` | 442 | bedrock |

### Dispatch chain to retire (gateway.py:665-720)

```python
# Today (gateway.py:665):
if backend == "ollama":     return await _call_ollama(model, body)
elif backend == "lmstudio":  ...
elif backend == "openrouter": ...
# ... 10+ branches
```

### Target shape (post-Plan 02a-02)

```python
# gateway.py:
async def route_request(body, model_alias=None, route=None) -> dict:
    adapter = get_adapter(route["backend"])
    return await adapter.send(body, route["model"], model_alias)
```

### Repo Protocol scope (Plan 02a-01)

`multillm/db/repo.py` is new. Existing data access is scattered (tracking.py, memory.py, sessions.py). Plan 02a-01 introduces the Protocol; it does NOT refactor existing data access modules to USE it — that's Phase 2b's job. Phase 2a only needs the Protocol shape to exist and pass type-check, so Phase 2b can implement against a stable interface.

## Deferred ideas

None. No scope creep was suggested during this discussion.

## Open questions for planner

These are NOT decisions — they're items for the planner to resolve from REQUIREMENTS.md and the codebase:

- Order of backend migration within Plan 02a-02 (the discussion picked "ollama first" for Plan 02a-01 proof, but the bulk order is planner's choice)
- Whether `cloud_openai_compat` family (groq, deepseek, mistral, together, xai, fireworks) becomes 6 separate entry-point entries or one family-level entry with internal dispatch (the existing `cloud_openai_compat.py` adapter already covers them; the cleaner choice is one entry per backend name so users can `import multillm.adapters.groq` etc., but the implementation can still share code)
- Coverage baseline capture mechanism (probably `coverage.json` snapshot committed to plan dir; planner decides)

## Success criteria recap (from ROADMAP)

1. `route_request()` and `route_streaming()` ≤ 3 lines each
2. 351-test suite stays green; coverage delta ≥ 0
3. No `if/elif backend == "..."` survives in `gateway.py`
4. `multillm/db/repo.py` Protocol exists; `tenant_id` first non-self arg on every method (grep-enforceable)
5. Adapter registry uses `importlib.metadata.entry_points()` (group: `multillm.backends`)

(ARCH-06 — zero public API surface change — verified by the existing test suite plus manual smoke on the dashboard at `http://localhost:8080/dashboard` after the bulk plan.)
