# Contributing to MultiLLM Gateway

Thanks for your interest in improving MultiLLM. This guide gets you from a fresh clone to a passing test run, then explains how to send your change.

## Quick start

```bash
git clone https://github.com/${OWNER}/${REPO}.git
cd ${REPO}
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
pytest -v
```

Python 3.11 or 3.12 is required. The full test suite should pass on a fresh checkout — if it does not, please open an issue before continuing.

## Project layout

| Path | Purpose |
|------|---------|
| `multillm/` | Gateway source (FastAPI app, adapters, tracking, memory) |
| `tests/` | pytest suite (unit, integration, streaming, adapter-specific) |
| `docs/` | Operations and contributor documentation (Phase 10 expands this) |
| `.planning/` | GSD planning artifacts (phase plans, requirements, roadmap) |
| `.github/` | Issue templates, PR template, CI workflows |

See `CLAUDE.md` at the repo root for module-by-module architecture notes.

## Pre-commit hooks

Once Plan 01-02 lands, this repo will ship with a `.pre-commit-config.yaml` covering:

- **gitleaks** — secret scanning (block-on-fail; cannot be skipped)
- **ruff** — Python linting and formatting
- **mypy** — static type checking

Install the hooks the first time you contribute:

```bash
pip install pre-commit
pre-commit install
```

After that, every `git commit` automatically runs the configured hooks. Push only after a clean local run.

## Running tests

```bash
# Full suite
pytest -v

# Single module
pytest tests/test_gateway.py -v

# Only fast tests (skip slow integration tests)
pytest -v -m "not slow"

# With coverage
pytest --cov=multillm --cov-report=term-missing
```

We aim for **80 percent line coverage minimum** on `multillm/`. New modules ship with their own tests; refactors that drop coverage will be asked to backfill.

## Code style

- **Formatter and linter:** `ruff` (config in `pyproject.toml` once Plan 01-02 lands)
- **Type checker:** `mypy` (strict on new modules; gradual elsewhere)
- **Line length:** 100 columns
- **Imports:** sorted by `ruff` (replaces isort)
- **Async style:** Prefer `async def` for I/O; never block the event loop with `requests` (use `httpx`)

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>

<optional body explaining the why>
```

Types we use: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`.

Scope is usually a module name or a plan identifier like `01-01`. Example:

```
feat(adapters): add LM Studio streaming support
fix(tracking): correct cost rounding for fractional token counts
```

## Branch naming

Branch naming is free-form for now. Common patterns:

- `feature/<short-name>` for new capabilities
- `fix/<issue-number>` for bug fixes referencing an issue
- `docs/<topic>` for documentation-only changes

`main` is the only branch users see and is always releasable.

## Pull request flow

1. Fork the repo and create a topic branch from `main`.
2. Make focused commits (one logical change per commit). Squash later if you prefer one commit per PR.
3. Run `pytest -v` locally — all tests must pass.
4. Push and open a PR. The PR template will prompt you for description, linked issue, test plan, and a security checklist.
5. A maintainer will review within a few business days. CI must be green before merge.

## Issue templates

Before opening an issue, please use one of the templates under [.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/):

- **Bug report** — something is broken
- **Feature request** — new capability you'd like to see
- **Backend request** — propose adding a new LLM provider

**Security issues must NOT be filed in public issues.** See [SECURITY.md](SECURITY.md) for the private disclosure process.

## Adding a new backend

Backend adapters live under `multillm/adapters/`. The shortest path:

1. Open a backend-request issue first so we can align on scope and naming.
2. Create `multillm/adapters/<backend>.py` extending the base adapter pattern (see `multillm/adapters/base.py`).
3. Wire pricing data into the `COST_TABLE` in `multillm/tracking.py` if the backend has paid usage.
4. Add a test module under `tests/test_<backend>_adapter.py` mirroring the structure of `tests/test_codex_cli_adapter.py`.
5. Update the README backend table.

## Reporting vulnerabilities

See [SECURITY.md](SECURITY.md). Do not report vulnerabilities in public issues or PRs.

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). By participating you agree to uphold it.

## License

By contributing you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
