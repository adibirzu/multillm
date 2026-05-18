---
phase: 02a-adapter-hot-path-refactor
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - multillm/adapters/registry.py
  - multillm/adapters/__init__.py
  - multillm/adapters/setup.py
  - multillm/adapters/cloud_openai_compat.py
  - pyproject.toml
  - multillm/db/__init__.py
  - multillm/db/repo.py
  - multillm/gateway.py
  - tests/test_registry_entry_points.py
  - tests/test_db_repo_protocol.py
  - .planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json
autonomous: true
requirements: [ARCH-04, ARCH-05, ARCH-06, ARCH-07]
tags: [refactor, adapters, registry, protocol, foundation]
must_haves:
  truths:
    - "Adapter registry resolves backend names from importlib.metadata.entry_points(group='multillm.backends')"
    - "All 13 built-in adapters self-declare in pyproject.toml [project.entry-points.\"multillm.backends\"]"
    - "multillm/db/repo.py defines SessionRepo, TrackingRepo, MemoryRepo Protocols with tenant_id as the first non-self argument on every method"
    - "Ollama route in gateway.py dispatches through get_adapter('ollama').send()/.stream() — proving the new path end-to-end"
    - "Inline _call_<backend> functions for the OTHER 12 backends still exist and still serve their traffic (dual-path coexistence is intentional after Plan 02a-01)"
    - "351-test suite remains green; coverage delta vs pre-plan baseline is ≥ 0"
    - "coverage-baseline.json is committed under the phase dir for Plan 02a-02 to gate against"
  artifacts:
    - path: "multillm/adapters/registry.py"
      provides: "entry_points()-backed registry with get_adapter(), list_adapters(), register_adapter() (test-compat shim)"
    - path: "pyproject.toml"
      provides: "[project.entry-points.\"multillm.backends\"] table with 18 entries (12 single-class adapters + 6 cloud_openai_compat family entries)"
    - path: "multillm/db/repo.py"
      provides: "SessionRepo, TrackingRepo, MemoryRepo Protocols with tenant_id-first signatures"
    - path: "multillm/db/__init__.py"
      provides: "Package marker exporting the three Protocols"
    - path: "multillm/adapters/cloud_openai_compat.py"
      provides: "Module-level factory callables make_groq/make_deepseek/make_mistral/make_together/make_xai/make_fireworks that entry-points resolve to"
    - path: ".planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json"
      provides: "Coverage snapshot to gate Plan 02a-02 delta against"
  key_links:
    - from: "multillm/gateway.py route_request() ollama branch"
      to: "get_adapter('ollama').send(...)"
      via: "registry lookup"
      pattern: "get_adapter\\(\"ollama\"\\)"
    - from: "pyproject.toml entry-points"
      to: "multillm/adapters/*:*Adapter (and cloud_openai_compat:make_*)"
      via: "importlib.metadata.entry_points(group='multillm.backends')"
      pattern: "\\[project\\.entry-points\\.\"multillm\\.backends\"\\]"
---

<objective>
Phase 2a Plan 01 — Foundation. Wire `importlib.metadata.entry_points(group='multillm.backends')` as the canonical registry-population path, declare all 13 built-in adapters (12 single classes + 6 cloud_openai_compat family factories = 18 entry-point lines) in `pyproject.toml`, introduce `multillm/db/repo.py` with `tenant_id`-first Protocol signatures, and migrate exactly one backend (ollama) end-to-end through `get_adapter('ollama').send()/.stream()` to prove the path works. Both dispatch paths coexist after this plan — only ollama goes through the registry; the other 12 backends still take the inline `_call_<backend>` route. Capture a coverage baseline JSON so Plan 02a-02 can enforce delta ≥ 0.

Purpose: close ARCH-04 (repo Protocol shape) and ARCH-07 (entry_points registry) on a foundation that does not touch behavior for any backend other than ollama. The 351-test suite is the safety net — every task ends with `pytest -q` green.

Output: rewritten registry, new db package, ollama wired through registry, baseline coverage.json, all behind-the-scenes.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/STATE.md
@.planning/phases/02a-adapter-hot-path-refactor/02a-CONTEXT.md
@.planning/REQUIREMENTS.md
@CLAUDE.md

<interfaces>
<!-- Locked decisions (CONTEXT.md) that constrain every task in this plan -->

D-2a-01: 2 plans, foundation + bulk. Plan 01 (this plan) wires foundation + proves with ollama only. Plan 02 migrates the other 12. The two PLANs are atomic per task; Plan 02 uses one commit per backend so git bisect can isolate a regression to a single backend.

D-2a-02: entry_points() is canonical from day one. pyproject.toml carries the [project.entry-points."multillm.backends"] table for ALL 13 built-ins (cloud_openai_compat is a family of 6 — see Task 3 for the chosen factory-callable structure). ARCH-07 closes in Phase 2a, not Phase 9.

D-2a-03: tenant_id is a required positional, no default. Protocol shape:
```python
class SessionRepo(Protocol):
    def list_sessions(self, tenant_id: str, *, limit: int = 50) -> list[Session]: ...
    def get_session(self, tenant_id: str, session_id: str) -> Session | None: ...
```
Plan 02a-01 introduces the shape only. It does NOT refactor tracking.py/memory.py to USE these Protocols — Phase 2b owns that. No existing call sites use these methods today (the Protocol is brand new), so no `tenant_id="default"` literal insertions are required in this plan beyond the example tests.

D-2a-04: Behavior parity = `pytest -q` exits 0 (351/351) AND coverage delta ≥ 0 vs the baseline captured in Task 6.

<!-- Code anchors (file:line) from CONTEXT.md — executor reads these directly, does not re-discover -->

- multillm/gateway.py:228-442 — inline _call_<backend> functions (left untouched except ollama path in Task 5)
- multillm/gateway.py:665-720 — route_streaming() if/elif dispatch chain (only ollama branch swapped in Task 5)
- multillm/gateway.py:737-785 — _route_single_request() if/elif dispatch (only ollama branch swapped in Task 5)
- multillm/gateway.py:77 — `from .adapters.setup import register_all_adapters` (replace with entry_points-based load in Task 1)
- multillm/gateway.py:192 — `register_all_adapters()` call site (replaced by entry_points discovery)
- multillm/adapters/base.py — BaseAdapter ABC, 37 lines, target shape
- multillm/adapters/registry.py — current in-memory dict, ~28 lines, gets rewritten in Task 1
- multillm/adapters/setup.py — current manual register_all_adapters() function, kept but converted to a delegate to entry_points load (or deleted; Task 1 chooses)
- multillm/adapters/cloud_openai_compat.py — parametrized CloudOpenAICompatAdapter(name, base_url, key_fn); 6 entry-point factories live here (Task 3)
- All 13 adapter classes already exist with `name` attribute set to canonical backend name — confirmed by grep at planning time

<!-- Entry-point structure (locked in Task 2) -->

[project.entry-points."multillm.backends"]
anthropic     = "multillm.adapters.anthropic:AnthropicAdapter"
azure_openai  = "multillm.adapters.azure_openai:AzureOpenAIAdapter"
bedrock       = "multillm.adapters.bedrock:BedrockAdapter"
codex_cli     = "multillm.adapters.codex_cli:CodexCLIAdapter"
gemini        = "multillm.adapters.gemini:GeminiAdapter"
gemini_cli    = "multillm.adapters.gemini_cli:GeminiCLIAdapter"
lmstudio      = "multillm.adapters.lmstudio:LMStudioAdapter"
oca           = "multillm.adapters.oca:OCAAdapter"
ollama        = "multillm.adapters.ollama:OllamaAdapter"
openai        = "multillm.adapters.openai:OpenAIAdapter"
openai_compat = "multillm.adapters.openai_compat:OpenAICompatAdapter"
openrouter    = "multillm.adapters.openrouter:OpenRouterAdapter"
groq          = "multillm.adapters.cloud_openai_compat:make_groq"
deepseek      = "multillm.adapters.cloud_openai_compat:make_deepseek"
mistral       = "multillm.adapters.cloud_openai_compat:make_mistral"
together      = "multillm.adapters.cloud_openai_compat:make_together"
xai           = "multillm.adapters.cloud_openai_compat:make_xai"
fireworks     = "multillm.adapters.cloud_openai_compat:make_fireworks"

The registry resolves each entry: if the resolved object is a class (subclass of BaseAdapter), instantiate with no args; if it is a callable (factory), call it; if it is already a BaseAdapter instance, use it directly. This makes the 6 cloud_openai_compat family entries clean — `make_groq()` returns `CloudOpenAICompatAdapter("groq", "https://api.groq.com/openai", lambda: GROQ_KEY)`.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Rewrite multillm/adapters/registry.py to use importlib.metadata.entry_points()</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/adapters/registry.py
    /Users/abirzu/dev/multillm/multillm/adapters/base.py
    /Users/abirzu/dev/multillm/multillm/adapters/setup.py
  </read_first>
  <files>multillm/adapters/registry.py, multillm/adapters/__init__.py, tests/test_registry_entry_points.py</files>
  <action>
Rewrite `multillm/adapters/registry.py` so the registry is populated lazily on first lookup by reading `importlib.metadata.entry_points(group='multillm.backends')`. Required public surface:

- `get_adapter(name: str) -> BaseAdapter | None` — returns the cached instance, populating cache on first call by iterating entry points in the `multillm.backends` group and resolving each (D-2a-02).
- `list_adapters() -> dict[str, bool]` — returns `{name: adapter.is_configured()}` for every discovered adapter. Triggers discovery if cache is empty.
- `register_adapter(adapter: BaseAdapter)` — KEEP as a backward-compat shim that inserts directly into the cache. Existing tests and `multillm/adapters/setup.py` rely on it; do not remove.
- `_discover_adapters()` — internal helper that iterates `entry_points(group='multillm.backends')`. For each entry: `obj = ep.load()`. Then resolve: if `inspect.isclass(obj)` and `issubclass(obj, BaseAdapter)` → instantiate `obj()`; elif `callable(obj)` (factory) → call `obj()`; else assume it is already an instance. Insert into cache under `ep.name` (NOT `adapter.name` — the entry-point key is authoritative so `groq`/`deepseek`/etc. resolve correctly via their factories). Log a warning and skip any entry point that raises during load.
- Cache lives in module-level `_adapters: dict[str, BaseAdapter] = {}`. Add a `_discovery_done: bool` flag so discovery runs exactly once per process (idempotent).
- Add `reset_for_tests() -> None` that clears `_adapters` and `_discovery_done`; tests that mutate the registry rely on it.

Update `multillm/adapters/__init__.py` to also export `reset_for_tests` in `__all__`.

Add SPDX header to both files (`# SPDX-License-Identifier: Apache-2.0` + `# Copyright 2026 MultiLLM contributors`). Preserve existing docstrings; update them to describe entry-points discovery.

Create `tests/test_registry_entry_points.py` with at least three tests:
1. `test_discovery_finds_ollama` — `reset_for_tests(); assert get_adapter("ollama") is not None; assert get_adapter("ollama").name == "ollama"`
2. `test_register_adapter_shim` — assert that `register_adapter(some_fake_adapter)` still inserts into the cache and is retrievable.
3. `test_unknown_backend_returns_none` — `get_adapter("nonexistent")` returns `None`.

Do NOT delete `multillm/adapters/setup.py` or `register_all_adapters()` yet — Plan 02a-02 Task 14 retires the manual registration path.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_registry_entry_points.py -x &&
      pytest -q 2>&1 | tail -3 | grep -E "351 passed|passed"
    </automated>
  </verify>
  <done>registry.py uses entry_points() discovery, three new tests pass, full 351-suite still green, register_adapter() shim preserved for setup.py compatibility.</done>
</task>

<task type="auto">
  <name>Task 2: Declare all 18 entry points in pyproject.toml</name>
  <read_first>
    /Users/abirzu/dev/multillm/pyproject.toml
    /Users/abirzu/dev/multillm/multillm/adapters/setup.py
  </read_first>
  <files>pyproject.toml</files>
  <action>
Insert a `[project.entry-points."multillm.backends"]` table into `pyproject.toml` immediately after the `[project.scripts]` block. Use the exact 18 entries from the `<interfaces>` block in this plan's context (12 single-class adapters + 6 cloud_openai_compat family factories — anthropic, azure_openai, bedrock, codex_cli, gemini, gemini_cli, lmstudio, oca, ollama, openai, openai_compat, openrouter, groq, deepseek, mistral, together, xai, fireworks).

Each cloud_openai_compat family entry points to a factory callable that does NOT exist yet — Task 3 creates them. This is intentional: the entry-points table is declarative; the factories are wired in Task 3 and the verify gate runs after Task 3 lands.

For this task's verify gate: confirm the TOML parses and the registry can at least discover the 12 class-based entries. The 6 family entries will fail to load until Task 3 lands — registry must log a warning and skip them gracefully (Task 1 already implements this).

Run `pip install -e .` (or `pip install -e . --no-deps` to skip dependency churn) so the entry-points are written into the installed metadata. Document this requirement at the top of the table with a comment: `# Editable reinstall (pip install -e .) required after editing this table.`

Add a Python validation snippet in this task's verify block to confirm the table parses and entry-points enumerate.
  </action>
  <verify>
    <automated>
      pip install -e . --no-deps -q 2>&1 | tail -3 &&
      python -c "from importlib.metadata import entry_points; eps = list(entry_points(group='multillm.backends')); names = sorted(ep.name for ep in eps); assert len(names) == 18, f'expected 18 got {len(names)}: {names}'; print('OK', names)" &&
      pytest -q 2>&1 | tail -3
    </automated>
  </verify>
  <done>pyproject.toml has [project.entry-points."multillm.backends"] with 18 entries; importlib.metadata.entry_points() enumerates all 18; 351-test suite still green (factories not yet imported, registry logs warnings and skips them).</done>
</task>

<task type="auto">
  <name>Task 3: Add family-factory callables to cloud_openai_compat.py for groq/deepseek/mistral/together/xai/fireworks</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/adapters/cloud_openai_compat.py
    /Users/abirzu/dev/multillm/multillm/adapters/setup.py
  </read_first>
  <files>multillm/adapters/cloud_openai_compat.py</files>
  <action>
Add 6 module-level factory functions to `multillm/adapters/cloud_openai_compat.py` matching the entry-points declared in Task 2:

```python
def make_groq() -> CloudOpenAICompatAdapter:
    from ..config import GROQ_KEY
    return CloudOpenAICompatAdapter("groq", "https://api.groq.com/openai", lambda: GROQ_KEY)

def make_deepseek() -> CloudOpenAICompatAdapter:
    from ..config import DEEPSEEK_KEY
    return CloudOpenAICompatAdapter("deepseek", "https://api.deepseek.com", lambda: DEEPSEEK_KEY)

def make_mistral() -> CloudOpenAICompatAdapter:
    from ..config import MISTRAL_KEY
    return CloudOpenAICompatAdapter("mistral", "https://api.mistral.ai", lambda: MISTRAL_KEY)

def make_together() -> CloudOpenAICompatAdapter:
    from ..config import TOGETHER_KEY
    return CloudOpenAICompatAdapter("together", "https://api.together.xyz", lambda: TOGETHER_KEY)

def make_xai() -> CloudOpenAICompatAdapter:
    from ..config import XAI_KEY
    return CloudOpenAICompatAdapter("xai", "https://api.x.ai", lambda: XAI_KEY)

def make_fireworks() -> CloudOpenAICompatAdapter:
    from ..config import FIREWORKS_KEY
    return CloudOpenAICompatAdapter("fireworks", "https://api.fireworks.ai/inference", lambda: FIREWORKS_KEY)
```

Inline imports of config keys avoid circular-import risk at module load time. The URLs are lifted verbatim from `multillm/adapters/setup.py:45-50` so behavior is identical to current production state.

After this task, the registry's `_discover_adapters()` must successfully resolve all 18 entry points. Confirm with the verify gate.
  </action>
  <verify>
    <automated>
      python -c "from multillm.adapters.registry import reset_for_tests, get_adapter, list_adapters; reset_for_tests(); names = sorted(list_adapters().keys()); assert len(names) == 18, f'expected 18 got {len(names)}: {names}'; assert 'groq' in names and 'fireworks' in names; print('OK', names)" &&
      pytest -q 2>&1 | tail -3
    </automated>
  </verify>
  <done>All 18 entry points resolve cleanly; registry returns 18 adapters; 351-test suite green.</done>
</task>

<task type="auto">
  <name>Task 4: Create multillm/db/ package with Protocol shape (tenant_id-first, no defaults)</name>
  <read_first>
    /Users/abirzu/dev/multillm/.planning/phases/02a-adapter-hot-path-refactor/02a-CONTEXT.md
  </read_first>
  <files>multillm/db/__init__.py, multillm/db/repo.py, tests/test_db_repo_protocol.py</files>
  <action>
Create `multillm/db/__init__.py` (SPDX header + package docstring + re-export of `SessionRepo`, `TrackingRepo`, `MemoryRepo`).

Create `multillm/db/repo.py` with three `typing.Protocol` classes. EVERY method takes `tenant_id: str` as the first non-self positional argument with NO default value (D-2a-03). The shapes:

```python
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors
"""Repository Protocols for tenant-aware data access.

Phase 2a introduces the SHAPE only — tenant_id is required positional everywhere.
Phase 2b will wire concrete implementations in tracking.py/memory.py/sessions.py.
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SessionRepo(Protocol):
    def list_sessions(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]: ...
    def get_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None: ...
    def create_session(self, tenant_id: str, session: dict[str, Any]) -> dict[str, Any]: ...
    def append_request(self, tenant_id: str, session_id: str, request: dict[str, Any]) -> None: ...


@runtime_checkable
class TrackingRepo(Protocol):
    def record_usage(self, tenant_id: str, usage: dict[str, Any]) -> None: ...
    def get_dashboard(self, tenant_id: str, *, hours: int = 168, project: str | None = None) -> dict[str, Any]: ...
    def get_summary(self, tenant_id: str, *, hours: int = 24) -> dict[str, Any]: ...


@runtime_checkable
class MemoryRepo(Protocol):
    def list_memories(self, tenant_id: str, *, limit: int = 20) -> list[dict[str, Any]]: ...
    def search_memories(self, tenant_id: str, query: str, *, limit: int = 10) -> list[dict[str, Any]]: ...
    def get_memory(self, tenant_id: str, memory_id: str) -> dict[str, Any] | None: ...
    def store_memory(self, tenant_id: str, memory: dict[str, Any]) -> dict[str, Any]: ...
    def delete_memory(self, tenant_id: str, memory_id: str) -> bool: ...
```

The `dict[str, Any]` placeholders are intentional — Phase 2b replaces them with TypedDicts or dataclasses when concrete implementations land. For Phase 2a we only need the shape to exist and be grep-friendly.

Create `tests/test_db_repo_protocol.py` with at least three tests:
1. `test_session_repo_method_signatures` — for every method on `SessionRepo`, use `inspect.signature` to assert the first non-self parameter is named `tenant_id`, is positional (kind is `POSITIONAL_OR_KEYWORD`), and has NO default.
2. `test_tracking_repo_method_signatures` — same check on `TrackingRepo`.
3. `test_memory_repo_method_signatures` — same check on `MemoryRepo`.

These tests are the grep-enforced invariant from D-2a-03 expressed as code; they will fail if anyone in a later phase reorders args or adds a `tenant_id` default.

Add `multillm/db/` to `[tool.setuptools.packages.find]` if needed — recheck pyproject `include = ["multillm*"]` already covers it (it does; `multillm*` is a wildcard).

NOTE: Plan 02a-01 does NOT refactor tracking.py/memory.py to USE these Protocols. That is Phase 2b's job. This task only stabilizes the shape.
  </action>
  <verify>
    <automated>
      pytest -q tests/test_db_repo_protocol.py -x &&
      git grep -nE 'def \w+\(self, tenant_id:' multillm/db/ | wc -l | awk '{ if ($1 < 12) { print "FAIL: expected ≥12 tenant_id-first methods, got " $1; exit 1 } else { print "OK " $1 " methods" } }' &&
      pytest -q 2>&1 | tail -3
    </automated>
  </verify>
  <done>multillm/db/repo.py has SessionRepo + TrackingRepo + MemoryRepo Protocols with tenant_id-first on every method; grep invariant passes; 3 new tests green; full 351-suite still green.</done>
</task>

<task type="auto">
  <name>Task 5: Migrate ollama as the proof backend — gateway.py ollama branch goes through registry</name>
  <read_first>
    /Users/abirzu/dev/multillm/multillm/gateway.py
    /Users/abirzu/dev/multillm/multillm/adapters/ollama.py
  </read_first>
  <files>multillm/gateway.py</files>
  <action>
In `multillm/gateway.py`, locate the two ollama dispatch sites and replace them so ollama traffic flows through the adapter registry. The other 11+ backends remain on their inline `_call_<backend>` path — dual-path coexistence is intentional after this plan.

Two edits, both inside `multillm/gateway.py`:

1. In `_route_single_request()` (around gateway.py:739), replace:
   ```python
   if backend == "ollama":
       return await _call_ollama(real_model, body)
   ```
   with:
   ```python
   if backend == "ollama":
       from .adapters.registry import get_adapter  # local import to avoid cycle
       adapter = get_adapter("ollama")
       if adapter is None:
           raise HTTPException(status_code=500, detail="ollama adapter not registered")
       return await adapter.send(body, real_model, model_alias)
   ```

2. In `route_streaming()` (around gateway.py:665), replace:
   ```python
   if backend == "ollama":
       return await stream_ollama(OLLAMA_URL, body, real_model, model_alias)
   ```
   with:
   ```python
   if backend == "ollama":
       from .adapters.registry import get_adapter
       adapter = get_adapter("ollama")
       if adapter is None:
           raise HTTPException(status_code=500, detail="ollama adapter not registered")
       return await adapter.stream(body, real_model, model_alias)
   ```

Leave `_call_ollama()` in place at gateway.py:246 — Plan 02a-02's final retirement task deletes it along with the rest. Leaving it preserves any other call site that might still reach it (e.g., tests that import the symbol directly).

Do NOT touch any other backend branch in either function. ARCH-06 requires zero behavior change on public surface — `pytest -q` is the proof.
  </action>
  <verify>
    <automated>
      python -c "import re; src=open('multillm/gateway.py').read(); assert 'adapter = get_adapter(\"ollama\")' in src, 'ollama registry dispatch not wired in gateway.py'; print('OK')" &&
      pytest -q 2>&1 | tail -3 | grep -E "351 passed"
    </automated>
  </verify>
  <done>Both ollama dispatch sites in gateway.py go through get_adapter('ollama'); 351-test suite green; inline _call_ollama() remains in source (deleted by Plan 02a-02 Task 14).</done>
</task>

<task type="auto">
  <name>Task 6: Capture coverage baseline for Plan 02a-02 delta enforcement</name>
  <read_first>
    /Users/abirzu/dev/multillm/.planning/phases/02a-adapter-hot-path-refactor/02a-CONTEXT.md
  </read_first>
  <files>.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json</files>
  <action>
Run the full test suite with coverage in JSON output mode and commit the resulting summary as the baseline that Plan 02a-02's final task will gate against (D-2a-04).

Command:
```bash
pytest --cov=multillm --cov-report=json:.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json -q
```

If `pytest-cov` is not installed at runtime, install via `pip install pytest-cov` (already in `[project.optional-dependencies].test` per pyproject.toml).

The resulting `coverage-baseline.json` has shape `{"meta": {...}, "files": {...}, "totals": {"percent_covered": 87.3, "num_statements": ..., "covered_lines": ..., ...}}`. The single field Plan 02a-02 will compare is `totals.percent_covered` — assert `>= baseline.totals.percent_covered`.

Add a brief README sidecar at `.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.md` (one paragraph) documenting:
- When this baseline was captured (commit SHA + timestamp from `git rev-parse HEAD` and `date -u`)
- The comparison rule used by Plan 02a-02: `totals.percent_covered` delta ≥ 0
- That the baseline must be regenerated only by re-running Plan 02a-01 from a clean state — not edited by hand

Commit this task with message `chore(02a-01): capture coverage baseline for Plan 02a-02 delta gate`.
  </action>
  <verify>
    <automated>
      test -f .planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json &&
      python -c "import json; d=json.load(open('.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json')); pct=d['totals']['percent_covered']; assert pct > 0; print(f'Baseline coverage: {pct:.2f}%')" &&
      pytest -q 2>&1 | tail -3
    </automated>
  </verify>
  <done>coverage-baseline.json exists under the phase dir; totals.percent_covered captured; baseline README written; full 351-test suite green; ready for Plan 02a-02.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Entry-point loader → adapter code | `importlib.metadata.entry_points()` resolves arbitrary import paths. In Phase 2a all paths are in-tree (`multillm.adapters.*`), so the trust boundary is internal. Phase 9 will add third-party plugins crossing this boundary — that hardening is out of scope here. |
| Plan 02a-01 → Plan 02a-02 | The coverage baseline JSON is a trust artifact: Plan 02a-02 enforces delta against it. Hand-editing the file would defeat the gate. |
| Test process → registry cache | `reset_for_tests()` mutates module-level state. Tests must call it explicitly; production must not. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation |
|-----------|----------|-----------|-------------|------------|
| T-02a-01-01 | Tampering | ollama dispatch path swap | mitigate | The 351-test suite exercises the ollama path. Per-task `pytest -q` gate catches any behavior drift before commit. |
| T-02a-01-02 | Tampering | entry-points discovery silently dropping a backend | mitigate | Task 3 verify asserts `len(list_adapters()) == 18`. Task 1 logs a warning on every load failure so silent drops are visible in CI logs. |
| T-02a-01-03 | Elevation of privilege | Protocol shape locked-in via Plan 02a-01 forces Phase 2b downstream | accept | This is intentional — D-2a-03 wants the contract to be the strongest signal. The grep invariant + 3 new Protocol signature tests catch any drift in Phase 2b. |
| T-02a-01-04 | Tampering | `register_adapter()` shim still inserts into cache | accept | Backwards-compat is required so `setup.py:register_all_adapters()` keeps working through Plan 02a-02. Test added in Task 1 confirms the shim works. |
| T-02a-01-05 | Repudiation | Coverage baseline could be hand-edited | mitigate | The README sidecar from Task 6 documents that the file is generated, not authored. Plan 02a-02's final task re-runs the same pytest command and compares — drift would be visible. |
| T-02a-01-06 | Tampering | pyproject.toml entry-points table installed but not picked up because pip didn't reinstall | mitigate | Task 2 verify includes `pip install -e . --no-deps` and a Python assertion that all 18 entries enumerate. |
| T-02a-01-07 | Information disclosure | Adapter factory closes over `lambda: GROQ_KEY` etc. — config values flow through factories | accept | Same pattern lives in `multillm/adapters/setup.py` today; this task copies the pattern, not introduces it. No new exposure surface. |
</threat_model>

<verification>
End-of-plan gates (every task's verify block must already have passed before this plan is marked complete):

1. `pytest -q` exits 0 with `351 passed` (ARCH-05, D-2a-04).
2. `python -c "from importlib.metadata import entry_points; assert len(list(entry_points(group='multillm.backends'))) == 18"` passes (ARCH-07).
3. `python -c "from multillm.adapters.registry import reset_for_tests, list_adapters; reset_for_tests(); assert len(list_adapters()) == 18"` passes — every entry point resolves cleanly.
4. `git grep -nE 'def \w+\(self, tenant_id:' multillm/db/` returns ≥ 12 matches (ARCH-04).
5. `grep -n 'get_adapter("ollama")' multillm/gateway.py` returns ≥ 2 matches (ollama wired through registry on both streaming and non-streaming paths).
6. `.planning/phases/02a-adapter-hot-path-refactor/coverage-baseline.json` exists and has valid `totals.percent_covered`.
7. Public API smoke (manual; not blocking): `curl -X POST http://localhost:8080/v1/messages -d '{"model":"ollama/llama3","messages":[{"role":"user","content":"hi"}]}'` returns an Anthropic-format response identical in shape to pre-plan output (ARCH-06).
</verification>

<success_criteria>
Plan 02a-01 closes:
1. ARCH-04 — `multillm/db/repo.py` Protocol exists with `tenant_id`-first on every method, grep-enforceable.
2. ARCH-07 — adapter registry foundation uses `importlib.metadata.entry_points(group='multillm.backends')`; 18 built-in entries declared in `pyproject.toml`.
3. Partial ARCH-05 — 351-test suite green; coverage baseline captured for Plan 02a-02 to gate against.
4. Partial ARCH-06 — public API surface unchanged for ollama (the proof backend); other 12 backends still serve unchanged via inline path.

Plan 02a-01 does NOT close (deferred to Plan 02a-02):
- ARCH-01 (all inline `_call_<backend>` migrated) — only ollama migrated here.
- ARCH-02 (`route_request`/`route_streaming` ≤ 3 lines) — if/elif chains still present.
- ARCH-03 (if/elif chains removed) — chains still present, only ollama branches swapped.
</success_criteria>

<output>
After completion, create `.planning/phases/02a-adapter-hot-path-refactor/02a-01-SUMMARY.md` documenting: which entry-points landed, which Protocol methods exist, baseline coverage percentage, and any deviations from this plan.
</output>
