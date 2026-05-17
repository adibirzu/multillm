---
phase: 01-open-source-readiness
plan: 03
subsystem: migrations
tags: [migrations, alembic, sqlite, cli, tdd, click, backup, fts5]

requires: []
provides:
  - Alembic migration framework wired to SQLite at $MULTILLM_HOME/multillm.db
  - Smoke migration 0001_smoke_test using batch_alter_table (D-05 proof of pattern)
  - Automatic pre-migration backup helper (~/.multillm/backups/pre-<rev>-<ts>.db)
  - multillm migrate {up,down,status,--dry-run} CLI
  - multillm serve subcommand delegating to the legacy gateway main
  - FTS5 rebuild helper with SQL-injection-safe identifier validation (D-06, P2b inherits)
  - multillm.cli:app as new top-level console_script (multillm-gateway preserved)
affects: [02-tenancy-multi-tenant-schema, 06-caching-fts5-memory]

tech-stack:
  added:
    - "alembic 1.18.1 (runtime dep — gateway needs migrations at startup)"
    - "sqlalchemy 2.0.49 (alembic dependency, pinned explicitly)"
    - "click 8.4.0 (top-level CLI dispatch; smaller deps than typer)"
  patterns:
    - "render_as_batch=True in alembic env.py — SQLite ALTER COLUMN workaround"
    - "Pre-DDL backup contract: migrate_up calls create_backup BEFORE alembic upgrade"
    - "Late-bound BACKUP_DIR proxy so MULTILLM_HOME monkeypatching works without module reload in tests"
    - "Identifier-validation regex (^[A-Za-z_][A-Za-z0-9_]*$) for any SQL identifier that cannot be parameterized"
    - "_handle_migration_errors decorator: maps CommandError -> exit 1, FileNotFoundError -> exit 2"

key-files:
  created:
    - multillm/migrations/__init__.py
    - multillm/migrations/env.py
    - multillm/migrations/script.py.mako
    - multillm/migrations/versions/__init__.py
    - multillm/migrations/versions/0001_smoke_test.py
    - multillm/migrations/backup.py
    - multillm/migrations/runner.py
    - multillm/migrations/fts5.py
    - multillm/cli.py
    - alembic.ini
    - tests/test_migrations_backup.py
    - tests/test_migrations_runner.py
    - tests/test_migrations_cli.py
    - tests/test_migrations_fts5.py
  modified:
    - multillm/__main__.py
    - pyproject.toml

key-decisions:
  - "Backup-before-up only (not before-down) — D-05 contract. Operators can pre-snapshot manually before destructive rollbacks; documented in migrate_down docstring."
  - "Late-bound BACKUP_DIR via _BackupDirProxy: a bare module-level Path = resolve_home()/'backups' would freeze at import time and break monkeypatched MULTILLM_HOME tests. The proxy resolves per attribute access and keeps the public API path-shaped."
  - "Idempotency check on migrate_up via current_revision() == resolved_target — no second backup, no DDL when already at head (Test 5 contract)."
  - "Smoke migration is a no-op guard if system table absent — Phase 1 does not bootstrap the system table; runner fixture pre-creates it. Phase 2b's first real migration will follow the same defensive pattern."
  - "FTS5 helper validates identifiers via strict regex BEFORE interpolation (T-01-03 follow-up + D-06). SQLite cannot parameterize identifiers; safer to reject than escape."
  - "multillm/__main__.py now shows CLI help by default (was: launched gateway). Backward compat for gateway launch via 'multillm serve' subcommand AND the preserved 'multillm-gateway' console_script."

patterns-established:
  - "Per-test MULTILLM_HOME monkeypatching: every migration test sets MULTILLM_HOME -> tmp_path and asserts the resolved DB lives inside. No test writes to the developer's real ~/.multillm/."
  - "Pre-DDL backup is enforced inside runner.migrate_up; CLI cannot bypass it. Defense-in-depth for T-01-03-01."
  - "Behavior-adding tasks committed in RED -> GREEN -> REFACTOR triplet. Each stage gets a distinct commit (test, feat, refactor)."

requirements-completed: [OSS-17, OSS-23]

metrics:
  duration_seconds: 443
  duration_human: "~7m 23s"
  tasks_completed: 3
  files_created: 14
  files_modified: 2
  tests_added: 25
  commits: 5
  completed_date: "2026-05-17"
---

# Phase 1 Plan 03: Alembic Migration Framework + multillm CLI Summary

End-to-end Alembic scaffold with automatic pre-migration SQLite backup, a `multillm migrate {up,down,status,--dry-run}` Click CLI, one smoke migration that exercises `batch_alter_table()` (D-05), and an FTS5 rebuild helper with SQL-injection-safe identifier validation (D-06 — ships in P1 but exercised for real in P2b).

## Tasks

| Task | Description | Cycle | Commit |
| ---- | ----------- | ----- | ------ |
| 1 | Write failing tests for migrations.backup.create_backup | RED | 72bfa3c |
| 2 | Implement backup.py, env.py, smoke migration, runner.py, alembic.ini, fts5.py + tests | GREEN | ff6eb33 |
| 3a | Runner + CLI integration tests (RED for CLI; runner tests already green vs Task 2) | RED | c16126a |
| 3b | multillm CLI with migrate + serve subcommands | GREEN | 00a6d8c |
| 3c | Centralize CLI error handling via _handle_migration_errors decorator | REFACTOR | de6b4d1 |

## Commits

- **72bfa3c** — `test(01-03): add failing tests for migrations.backup create_backup`
- **ff6eb33** — `feat(01-03): implement migrations.backup, env, runner, smoke migration`
- **c16126a** — `test(01-03): runner and CLI integration tests (RED)`
- **00a6d8c** — `feat(01-03): multillm CLI with migrate + serve subcommands (GREEN)`
- **de6b4d1** — `refactor(01-03): centralize CLI error handling`

## TDD Cycles Observed

- **Task 1:** RED only — six tests authored, all failing on `ModuleNotFoundError: multillm.migrations`. Committed as RED.
- **Task 2:** GREEN — implemented backup/env/runner/smoke/fts5 modules; all 15 tests (6 backup + 9 fts5) pass. REFACTOR pass was a no-op: constants were already named (`_TABLE`, `_COLUMN`, `_SAFE_IDENT`, `_DEFAULT_HOME_NAME`), the proxy class had its docstring, and no additional cleanup added clarity.
- **Task 3:** RED -> GREEN -> REFACTOR. The runner tests authored alongside the CLI tests turned out to already pass against Task 2's runner implementation (good — Task 2's TDD test for "behavior" was met by the runner's contract). The CLI tests were the actual RED. After GREEN, refactored to consolidate `try/except CommandError / FileNotFoundError` into a single `_handle_migration_errors` decorator.

## Dependency Versions Pinned

```toml
# pyproject.toml [project] dependencies
"alembic>=1.18",      # resolved: 1.18.1
"sqlalchemy>=2.0",    # resolved: 2.0.49
"click>=8.1",         # resolved: 8.4.0
```

The runtime `click` was upgraded from 8.0.4 (system) to 8.4.0 to satisfy the >=8.1 floor; the local `oci-cli` package complains about the version conflict but that is unrelated to MultiLLM and is not a regression — pip's resolver warning, not a runtime failure.

## Test Inventory

25 new tests across four modules:

| Module | Tests | Behaviors |
| ------ | ----- | --------- |
| `tests/test_migrations_backup.py` | 6 | Path schema, byte-identity, 0o700 dir mode, distinct filenames on consecutive calls, FileNotFoundError, MULTILLM_HOME override |
| `tests/test_migrations_runner.py` | 5 | dry_run lists smoke, migrate_up adds column + writes backup, current_revision after up, migrate_down reverses, idempotent re-run |
| `tests/test_migrations_cli.py` | 5 | --dry-run prints pending, up emits Backup written + Migrated to, down --target=base reverses, status reports revision, --help lists subcommands |
| `tests/test_migrations_fts5.py` | 9 | Rebuild preserves rows, non-FTS5 raises OperationalError, 7 SQL-injection patterns rejected |

All 25 pass. The pre-existing 289 tests in `tests/` continue to pass (verified with a smoke run).

## Verification (from plan)

| Check | Result |
| ----- | ------ |
| `pytest tests/test_migrations_*.py -v` reports all pass | 25/25 PASS |
| `multillm migrate --dry-run` lists `0001_smoke_test` | PASS (test_cli_dry_run_lists_pending) |
| `multillm migrate up` produces "Backup written:" and "Migrated to:" + creates one backup file | PASS (test_cli_up_emits_backup_and_revision_lines) |
| `multillm migrate down --target=base` reverses cleanly | PASS (test_cli_down_reverses_cleanly) |
| Re-running `multillm migrate up` is a no-op (no second backup) | PASS (test_migrate_up_is_idempotent) |
| `grep -c "render_as_batch=True" multillm/migrations/env.py` returns 1+ | 3 (configure x2 + docstring) |
| `grep -c "batch_alter_table" multillm/migrations/versions/0001_smoke_test.py` returns 2+ | 5 (upgrade + downgrade + docstring) |

## Success Criteria

| # | Criterion | Status |
| --- | --------- | ------ |
| 1 | Three test modules ship green | PASS (four — added fts5 per D-06) |
| 2 | `multillm` registered console script | PASS (`pyproject.toml [project.scripts]`) |
| 3 | `multillm migrate --dry-run` is non-destructive | PASS |
| 4 | `multillm migrate up` writes backup BEFORE DDL | PASS |
| 5 | Smoke migration uses `batch_alter_table()` | PASS |
| 6 | Idempotency: re-run is no-op | PASS |
| 7 | Legacy `multillm-gateway` script preserved | PASS (still in `[project.scripts]`) |
| 8 | FTS5 rebuild helper + validation + 3-test suite | PASS (9 tests; expanded the negative cases) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Alembic 1.18 `path_separator` deprecation warning**

- **Found during:** Task 3 RED phase test run
- **Issue:** Alembic 1.18 emits `DeprecationWarning: No path_separator found in configuration; falling back to legacy splitting on spaces, commas, and colons for prepend_sys_path.` whenever the runner loaded the config. Tests still passed but the warning would have failed any future `filterwarnings = error` policy.
- **Fix:** Added `path_separator = os` to `alembic.ini` (alongside the existing `version_path_separator = os`).
- **Files modified:** `alembic.ini`
- **Commit:** `00a6d8c` (folded into the Task 3 GREEN commit since both touch the same file)

**2. [Plan Augmentation] Expanded FTS5 identifier-validation negative-case suite from 1 to 7**

- **Found during:** Authoring `tests/test_migrations_fts5.py`
- **Issue:** Plan called for one malicious-identifier test (`"foo; DROP TABLE x"`). Limited coverage for a security-sensitive code path.
- **Fix:** Parameterized the test against 7 distinct attack shapes: SQL-injection (`foo; DROP TABLE x`, `foo'); DROP TABLE x;--`), empty string, leading-digit, space, dash, backtick.
- **Files modified:** `tests/test_migrations_fts5.py`
- **Commit:** `ff6eb33`

### Other Notes

- **`python -m multillm` behavior change.** Previously launched the gateway directly; now invokes the CLI app and shows help. Gateway launch reachable via `python -m multillm serve` or the preserved `multillm-gateway` console_script. This is per plan ("(replaces the gateway-only entry; gateway is now reachable via `multillm serve` AND the legacy `multillm-gateway` script which is preserved)"); calling out explicitly because any docker-compose or systemd unit that runs `python -m multillm` will need to add ` serve`.

## Threat Mitigation Verification

| Threat | Status | Evidence |
| ------ | ------ | -------- |
| T-01-03-01 (Tampering: unbackedup migrate up) | Mitigated | `migrate_up` calls `create_backup` BEFORE `command.upgrade`; FileNotFoundError aborts before any DDL. `test_migrate_up_runs_smoke_and_writes_one_backup` asserts the backup file exists after up. |
| T-01-03-02 (Disclosure: backup permissions) | Mitigated | `BACKUP_DIR.mkdir(mode=0o700)` + `os.chmod(0o700)` on existing dirs. `test_create_backup_creates_backup_dir_with_restrictive_mode` asserts `st_mode & 0o777 == 0o700`. |
| T-01-03-03 (Elevation: arbitrary Python in versions/) | Accepted | No third-party migrations in P1. |
| T-01-03-04 (DoS: disk filled by backups) | Accepted | Documented. P5 observability + P2b `--prune-backups` will revisit. |

**Additional finding (out-of-band):** SQL-injection vector through table-name interpolation in `rebuild_fts5_indexes`. Mitigated proactively via `_SAFE_IDENT` regex + parameterized negative-case test suite (see Deviation #2). Not in original threat register because D-06 ships the helper without exercising it; the validation is a forward-defensive measure for P2b.

## Notes for Downstream Plans

- **P2b (tenancy):** Inherit `batch_alter_table` usage from the smoke migration template at `multillm/migrations/versions/0001_smoke_test.py`. Use `rebuild_fts5_indexes(conn, 'memory_fts')` after the tenant_id column is added to the FTS5 memory store.
- **P6 (semantic cache):** sqlite-vec extension load is independent of alembic; the migration framework does not need to know about vec0 tables, but any new schema for the cache should still come through an alembic revision (do not hand-roll `CREATE TABLE IF NOT EXISTS` outside the runner from P2b forward).
- **Phase 1 closeout (Plan 09):** No SHA pinning needed here — all dependencies are pinned via PyPI semver, not GitHub actions.

## Self-Check: PASSED

- All 14 created files exist and are committed.
- All 5 commits (72bfa3c, ff6eb33, c16126a, 00a6d8c, de6b4d1) are reachable from HEAD on branch `gsd/phase-01-open-source-readiness`.
- `python -m pytest tests/test_migrations_*.py -v` reports 25/25 passing.
- `python -c "from multillm.cli import app; from multillm.migrations.runner import migrate_up, migrate_down, migrate_dry_run, current_revision; from multillm.migrations.fts5 import rebuild_fts5_indexes; from multillm.migrations.backup import create_backup, BACKUP_DIR"` imports cleanly.
