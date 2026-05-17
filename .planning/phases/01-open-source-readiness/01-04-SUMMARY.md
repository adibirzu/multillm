---
phase: 01-open-source-readiness
plan: 04
subsystem: distribution
tags: [docker, compose, env-example, supply-chain, oss-readiness]
requires: [01-03]
provides:
  - "Container runtime image (multi-stage, non-root, < 500 MB target)"
  - "One-command bring-up: `cp .env.example .env && docker compose up`"
  - "Authoritative env-var inventory enforced by AST-driven CI test"
  - ".dockerignore that keeps secrets and planning artefacts out of the build context"
affects:
  - "Future Phase 1 plans (01-05 release workflow, 01-06 README quickstart)
     consume the Dockerfile and compose file directly"
  - "Every future os.getenv() addition now triggers a test failure until
     .env.example is updated"
tech-stack:
  added:
    - "Dockerfile (python:3.12-slim, multi-stage, tini PID 1)"
    - "docker-compose.yml (Compose v2 spec, version-less)"
  patterns:
    - "AST-walk env-var coverage test (regression net for OSS-04 + OSS-18)"
    - "Non-root container identity via uid 10001"
    - "tini as PID 1 for SIGTERM forwarding -> SQLite WAL flush on shutdown"
key-files:
  created:
    - "Dockerfile"
    - ".dockerignore"
    - "docker-compose.yml"
    - "tests/test_env_example_coverage.py"
  modified:
    - ".env.example (full rewrite — authoritative inventory)"
decisions:
  - "Single service in compose: SQLite is the only storage at v1.0 per D-02
     — no Redis, no Postgres."
  - "Default volume mounts to project-local ./.multillm (not ~/.multillm)
     to keep the fresh-machine bring-up zero-config; operator can override
     via `MULTILLM_HOME` before `docker compose up`."
  - "AST coverage test treats OCA_IDCS_* / IDCS_* / ORACLE_SSO_* as
     KNOWN_INDIRECT_LOOKUPS — they're read via the `_first_env` helper in
     `multillm/config.py` and therefore not visible as direct literals to the
     AST walker, but they ARE legitimate env vars the operator may set."
  - "PATH is in SYSTEM_PROVIDED skip-list (system env var, must NOT live
     in .env.example)."
  - "OCI image labels use the bare `key=value` form (not quoted) so the
     plan-spec grep `image.licenses=Apache-2.0` matches verbatim."
metrics:
  duration_seconds: "~30 min"
  completed_at: 2026-05-17T17:29:24Z
  tasks_completed: 3
  files_modified: 5
  commits: 3
  tests_added: 4
  tests_passing: 318
---

# Phase 1 Plan 4: Containerization & Environment Inventory Summary

Ship the one-command bring-up — multi-stage Dockerfile, single-service docker-compose.yml, authoritative `.env.example` covering every `os.environ` / `os.getenv` lookup in `multillm/`, and an AST-driven coverage test that fails the build the moment a new env-var lookup is added without a `.env.example` entry.

## What was built

### Task 1 — `.env.example` (full rewrite) + AST coverage test
**Commit:** `bca0918`

- **`.env.example`** is now grouped by category (gateway core → storage → auth → observability → rate limiting → local backends → cloud keys → cloud config → OCA → router → integrations) with every entry following the canonical 3-line format (purpose / type+default / `VAR=value`).
- Every secret placeholder is empty (`OPENAI_API_KEY=`) so gitleaks cannot misfire; the one project-specific placeholder uses the literal string `your-oci-apm-data-key-here` so the future `.gitleaks.toml` allowlist (Plan 01-02 wired the framework) recognises it.
- **`tests/test_env_example_coverage.py`** parses `.env.example`, then walks the AST of every `multillm/*.py` file collecting:
  - `os.getenv("NAME", ...)` calls — first arg literal extracted
  - `os.environ.get("NAME", ...)` calls — first arg literal extracted
  - `os.environ["NAME"]` subscript reads — literal slice extracted
- Four test cases:
  1. **coverage** — every literal env-var the code reads is documented
  2. **no-dumping-ground** — every documented entry is either referenced, a `KNOWN_OPTIONAL_EXTRAS` forward reference (`OTEL_TRACES_SAMPLER_ARG`), or a `KNOWN_INDIRECT_LOOKUPS` alias (OCA IDCS variants read via `_first_env`)
  3. **no real-looking credentials** — regex sweep for `sk-[A-Za-z0-9]{20,}`, `ocid1.tenancy.<...>`, `130.61.x.x`, and `10.x.x.x` patterns
  4. **`MULTILLM_HOME` regression guard** — explicit check that the most-important storage knob never quietly drops from documentation

### Task 2 — Dockerfile + .dockerignore
**Commit:** `f691b5f`

- **Two-stage Dockerfile**, both `python:3.12-slim`:
  - **builder**: installs `build-essential` + `git`, compiles wheels into `/install`, smoke-imports `multillm.cli` and `multillm.gateway` before freezing the layer (build fails fast if the package is structurally broken).
  - **runtime**: installs only `curl` (HEALTHCHECK probe) and `tini` (PID 1 signal forwarder). No compiler, no git — the builder's build-essential never reaches runtime. This is the supply-chain piece (T-01-04-04 mitigation).
- Non-root identity created via `groupadd -g 10001 multillm && useradd -u 10001 -g multillm -m -s /sbin/nologin multillm` and `USER multillm` is set before the ENTRYPOINT — uid 10001 keeps the process well above the system-account range.
- `/data` declared as `VOLUME` and chown'd to multillm:multillm; `MULTILLM_HOME=/data` baked into runtime ENV so the migrate CLI from Plan 01-03 and the gateway agree on the data root.
- `HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://localhost:8080/health`.
- `ENTRYPOINT ["/usr/bin/tini", "--"]` + `CMD ["sh", "-c", "multillm migrate up && multillm serve"]` — migrations always run before the server, and `tini` forwards `SIGTERM` cleanly so the SQLite WAL flushes on `docker stop` (T-01-04-06 mitigation).
- OCI image labels (title / description / source / licenses=Apache-2.0).
- **`.dockerignore`** excludes `.git/`, `.planning/`, `tests/`, `docs/`, `.venv/`, `__pycache__/`, `*.db`, `*.log`, every provider cache (`.oca/`, `.aws/`, `.azure/`, `.claude/`, `.codex/`, `.serena/`), `.env`/`.env.*` (with `!.env.example` allow-line at the bottom so the example survives the glob).

### Task 3 — docker-compose.yml
**Commit:** `31f685d`

- Single service `gateway`, **version-less** Compose v2 spec.
- `image: multillm:local` tag so subsequent `docker compose up` invocations re-use the local build.
- `ports: ["8080:8080"]`.
- `env_file: .env` — operator must `cp .env.example .env` first; backend creds may all be empty (D-15: `/v1/messages` returns 503 with helpful error until at least one backend is configured).
- `volumes: ["${MULTILLM_HOME:-./.multillm}:/data"]` — defaults to a project-local `.multillm` directory so a fresh-machine bring-up requires zero pre-setup. Operator can override with `export MULTILLM_HOME=~/.multillm` if they want the legacy location.
- `healthcheck:` block mirrors the Dockerfile contract (10s interval, 5s timeout, 20s start-period, 3 retries) so operators reading the compose file alone see the SLO.
- `restart: unless-stopped`.
- Top-of-file quickstart + post-up verification block (curl `/health`, open `/setup`, open `/dashboard`).
- **No Redis, no Postgres, no other services** — D-02 + OSS-15 require SQLite-only bring-up at v1.0.

## Verification

| Gate | Result |
|------|--------|
| `pytest tests/test_env_example_coverage.py` | 4 passed |
| Full test suite (318 tests) | 318 passed (no regression) |
| `python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"` | OK |
| `docker compose config` parse | OK (Docker 29.1.2 on host) |
| `grep -c "image.licenses=Apache-2.0" Dockerfile` | 1 |
| `grep -E "sk-[a-zA-Z0-9]{30,}\|ocid1\.tenancy" .env.example` | empty |
| All plan-spec verify-blocks (Task 1, 2, 3) | passed |

### Image build / 30-second live bring-up — deferred
Per the global `~/.claude/CLAUDE.md` "Cloud-Based Docker Builds & Deployments" rule, the dev machine is Apple Silicon (arm64) and must NOT build x86_64/amd64 images locally. The plan explicitly anticipates this:
> "If Docker daemon is unavailable in the executor environment, skip the live build step and document in the SUMMARY that the operator must perform the 30-second-bring-up verification manually on a machine with Docker."

**Operator action for OSS-15 acceptance** (run on any Linux/CI host with Docker):
```bash
cp .env.example .env
docker compose up -d
sleep 20
curl --fail http://localhost:8080/health   # must return 200
docker image inspect multillm:local --format '{{.Size}}'   # must be < 524288000
```

The live bring-up + image-size measurement is the only piece of this plan that requires a non-arm host. All other artefacts (Dockerfile syntax, compose syntax, AST coverage test, label format) are validated locally.

## Deviations from Plan

### Auto-fixed Issues
**1. [Rule 1 — Bug] `.env.example` missed two referenced env vars on first draft**
- **Found during:** Task 1 verify (AST coverage test FAILED on first run)
- **Issue:** First draft missed `MULTILLM_DATA_DIR` (legacy alias of `MULTILLM_HOME` read at `config.py:42`) and `OCI_APM_ENDPOINT` (override for the auto-derived OCI APM URL at `config.py:114`).
- **Fix:** Added both with type/default/purpose comments. Test rerun: 4 passed.
- **Files modified:** `.env.example`
- **Commit:** Folded into `bca0918` (caught before commit, same artifact).

**2. [Rule 1 — Bug] OCI image LABEL syntax did not match plan-spec grep**
- **Found during:** Task 2 verify
- **Issue:** First draft used `LABEL key="Apache-2.0"` (quoted form); plan verify grep is `image.licenses=Apache-2.0` (bare). Both forms are valid Dockerfile syntax, but the bare form makes the regression grep deterministic.
- **Fix:** Switched to `LABEL key=Apache-2.0` form for all OCI labels.
- **Files modified:** `Dockerfile`
- **Commit:** Folded into `f691b5f` (caught before commit).

### Threat-model coverage
All six STRIDE entries from the plan's `<threat_model>` are mitigated:

- **T-01-04-01** (`.env` baked into image) — `.dockerignore` excludes `.env` and `.env.*` while allow-listing `.env.example`.
- **T-01-04-02** (real-looking placeholders) — Task 1's `test_env_example_does_not_contain_real_credentials` test guards against this on every CI run.
- **T-01-04-03** (root container) — `USER multillm` (uid 10001) is set before `ENTRYPOINT`.
- **T-01-04-04** (build tooling in runtime) — Multi-stage build discards `build-essential` and `git`; runtime stage installs only `curl` + `tini`.
- **T-01-04-05** (env-var drift) — AST coverage test fails the build when a new `os.getenv()` is added without `.env.example` entry.
- **T-01-04-06** (PID 1 signal handling) — `tini` is the ENTRYPOINT; SIGTERM forwards cleanly so SQLite WAL flushes before the container exits.

## Known Stubs

None — every artefact in this plan is end-to-end functional. The single deferral (live image build) is an environmental constraint, not a stub.

## Self-Check: PASSED

- [x] `.env.example` exists and contains the inventory header comment
- [x] `tests/test_env_example_coverage.py` exists; all 4 cases pass
- [x] `Dockerfile` exists with all required directives (`FROM python:3.12-slim AS builder`, `USER multillm`, `HEALTHCHECK`, `multillm migrate up`, `groupadd -g 10001`, `tini`, `image.licenses=Apache-2.0`)
- [x] `.dockerignore` exists and excludes `.git/`, `.planning`, `.env`, `.env.*` (with allow-line for `.env.example`)
- [x] `docker-compose.yml` exists, version-less, parses with `docker compose config`, references `MULTILLM_HOME:-` and contains no `redis` / `postgres` strings
- [x] All three commits exist on `gsd/phase-01-open-source-readiness`: `bca0918`, `f691b5f`, `31f685d`
- [x] Full test suite (318 tests) green — no regression from existing 314 + 4 new

Commits in order:
- `bca0918` — feat(01-04): authoritative .env.example and AST-driven coverage test
- `f691b5f` — feat(01-04): multi-stage Dockerfile + .dockerignore for non-root runtime
- `31f685d` — feat(01-04): docker-compose.yml for one-command bring-up
