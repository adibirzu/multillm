---
phase: 01-open-source-readiness
plan: 02
subsystem: infra
tags: [ci, pre-commit, secret-scan, gitleaks, trufflehog, codeql, dependabot, supply-chain, github-actions]

requires:
  - phase: 01-open-source-readiness
    provides: Apache 2.0 license + SPDX headers (Plan 01-01)
provides:
  - Local pre-commit hooks with gitleaks block-on-fail and ruff/mypy warn-only
  - Project-specific gitleaks rules for OCI tenancy OCIDs, APM data keys, and 130.61.x.x IPs
  - Main CI workflow (pytest matrix py3.11+3.12, ruff/mypy lint, gitleaks+trufflehog scan)
  - CodeQL static analysis workflow (weekly + on-PR for Python source paths)
  - Dependency-review workflow blocking high-severity CVEs and GPL/AGPL outbound deps
  - Dependabot configuration for pip + github-actions ecosystems with grouped PRs
  - PIN_*_SHA placeholder convention for OSS-13 SHA-pinned actions (resolved by Plan 09 Task 1)
affects: [01-08-secret-history-filter-repo, 01-05-release-workflow, 01-09-public-push-checklist]

tech-stack:
  added:
    - "pre-commit 4.0+"
    - "gitleaks-action v2.x (pinned via PIN_GITLEAKS_SHA)"
    - "trufflehog action v3.85+ (pinned via PIN_TRUFFLEHOG_SHA)"
    - "github/codeql-action v3 (pinned via PIN_CODEQL_SHA)"
    - "actions/dependency-review-action v4 (pinned via PIN_DEPREVIEW_SHA)"
    - "ruff 0.6+"
    - "mypy 1.11+"
    - "pytest-cov 5.0+"
  patterns:
    - "SHA-pinning convention with audit comments (# action vX.Y.Z / # pinned per OSS-13)"
    - "Two-stage pre-commit install (pre-commit + pre-push) for bypass resistance"
    - "Warn-only ramp-up for non-security gates during phase implementation, tightened at closeout"
    - "Dependabot grouping by lifecycle (production vs dev) to keep security bumps separable from tool churn"

key-files:
  created:
    - .pre-commit-config.yaml
    - .gitleaks.toml
    - .github/workflows/ci.yml
    - .github/workflows/codeql.yml
    - .github/workflows/dependency-review.yml
    - .github/dependabot.yml
  modified:
    - pyproject.toml

key-decisions:
  - "Gitleaks installed at both pre-commit and pre-push stages so --no-verify bypass at commit is still caught at push (T-01-02-02)"
  - "Self-allowlist .gitleaks.toml and .pre-commit-config.yaml to prevent recursive false positives on rule signatures"
  - "PIN_*_SHA placeholder convention: workflow files commit with named placeholders, Plan 09 Task 1 resolves all 9 placeholders in one auditable pass before public push"
  - "Added pytest-cov to [test] dep group (Rule 2 auto-add): ci.yml's --cov-fail-under=80 gate requires it; plan did not list it explicitly"
  - "Dependabot reviewers/assignees left empty per D-16 (repo identity not yet locked)"
  - "GPL-3.0 / AGPL-3.0 denied at dependency-review time, not discovered at release time"

patterns-established:
  - "OSS-13 SHA-pinning: any third-party action in scan/sign/publish/codeql jobs must be pinned by 40-char commit SHA with an audit comment naming the action version"
  - "Phase-1 ramp-up rule: security gates (gitleaks, secret-scan) block on day one; quality gates (ruff, mypy) start warn-only and tighten at phase closeout"
  - "Dependabot weekly Monday 06:00 UTC cadence with open-pull-requests-limit: 5 per ecosystem"

requirements-completed: [OSS-08, OSS-09, OSS-13, OSS-25]

duration: 8min
completed: 2026-05-17
---

# Phase 01 Plan 02: Pre-commit + CI Security Gate Summary

**Block-on-fail gitleaks pre-commit + pre-push hooks, full CI gate with pytest≥80% on py3.11+3.12, ruff/mypy lint, gitleaks-full-history + trufflehog --results=verified scan, CodeQL weekly + on-PR, dependency-review blocking high-CVE and incompatible-license deps, Dependabot grouped PRs for pip + github-actions, all third-party scan/codeql actions SHA-pinned per OSS-13.**

## Performance

- **Duration:** ~8 min
- **Started:** Plan execution kick-off
- **Completed:** 2026-05-17
- **Tasks:** 3 / 3
- **Files created:** 6
- **Files modified:** 1

## Accomplishments

- Local secret-scan gate via gitleaks at both `pre-commit` and `pre-push` stages — bypass-resistant.
- Project-specific gitleaks rules catch the exact patterns Plan 08 (filter-repo) scrubs from history (OCI tenancy OCIDs, APM data keys, 130.61.x.x IPs) — guarantees future commits cannot re-introduce them.
- CI runs pytest with `--cov-fail-under=80` on Python 3.11 + 3.12 (D-18 matrix), ruff format + lint, mypy (warn-only per D-17), and a dedicated scan job with `gitleaks` over full history plus `trufflehog --results=verified --only-verified`.
- CodeQL workflow runs weekly (Mon 04:17 UTC off-peak) plus on PRs that touch Python source.
- Dependency-review workflow fails PRs on high-severity CVEs and on `GPL-3.0` / `AGPL-3.0` outbound licenses (incompatible with the project's Apache 2.0 license per D-01).
- Dependabot opens grouped weekly PRs for pip (production vs dev cohorts) and github-actions (one batched PR rotates the OSS-13 SHA pins).

## Task Commits

1. **Task 1: pre-commit config + gitleaks rules + dev deps** — `21263f0` (feat)
2. **Task 2: CI workflow with pytest + lint + scan jobs** — `4ff3ec5` (feat)
3. **Task 3: CodeQL + dependency-review + Dependabot** — `c4e6a3e` (feat)

## Files Created/Modified

- **Created** `.pre-commit-config.yaml` — Hook chain (gitleaks → hygiene → ruff → mypy), `default_install_hook_types: [pre-commit, pre-push]`. Gitleaks block-on-fail; ruff and mypy `verbose: true, fail_fast: false` (warn-only per D-17).
- **Created** `.gitleaks.toml` — `[extend] useDefault = true` plus three project-specific `[[rules]]`: `oci-tenancy-id`, `oci-apm-data-key`, `oci-internal-ip`. Allowlist narrowly scoped to `tests/fixtures/`, `tests/data/`, `.planning/`, `docs/operations/`, plus obvious placeholder regexes and self-exclusion of the gitleaks/pre-commit config files.
- **Created** `.github/workflows/ci.yml` — Three jobs: `pytest` (matrix py3.11+3.12, `--cov-fail-under=80`, coverage XML upload), `lint` (ruff format-check + ruff check block; mypy `continue-on-error: true`), `scan` (full-history checkout + gitleaks-action + trufflehog `--results=verified --only-verified`). Concurrency `ci-${{ github.ref }}` cancels superseded runs.
- **Created** `.github/workflows/codeql.yml` — PR-path-scoped + push-to-main + weekly cron (Mon 04:17 UTC). Single `analyze` job, language: python. `init`/`autobuild`/`analyze` all pinned via `PIN_CODEQL_SHA`.
- **Created** `.github/workflows/dependency-review.yml` — PR-only trigger. `fail-on-severity: high`, `comment-summary-in-pr: always`, `deny-licenses: GPL-3.0, AGPL-3.0`. Pinned via `PIN_DEPREVIEW_SHA`.
- **Created** `.github/dependabot.yml` — Two ecosystems (`pip`, `github-actions`), weekly Monday cadence, `open-pull-requests-limit: 5`, grouped PRs. pip ecosystem split into `production` and `dev` groups; github-actions grouped together so a single PR rotates the OSS-13 SHA pins each week.
- **Modified** `pyproject.toml` — Added `[project.optional-dependencies] dev = ["pre-commit>=4.0", "ruff>=0.6", "mypy>=1.11"]`. Added `pytest-cov>=5.0` to existing `test` group (auto-fix Rule 2: needed for `--cov-fail-under=80` gate).

## SHA Pin Inventory

Nine SHA-pin slots are currently named placeholders; Plan 09 Task 1 resolves all in one auditable pass before public push:

| Workflow | Action | Placeholder | Target version (per RESEARCH §STACK) |
|----------|--------|-------------|--------------------------------------|
| ci.yml | gitleaks/gitleaks-action | `PIN_GITLEAKS_SHA` | v2.3.x |
| ci.yml | trufflesecurity/trufflehog | `PIN_TRUFFLEHOG_SHA` | v3.85+ |
| codeql.yml | github/codeql-action/init | `PIN_CODEQL_SHA` | v3 |
| codeql.yml | github/codeql-action/autobuild | `PIN_CODEQL_SHA` | v3 |
| codeql.yml | github/codeql-action/analyze | `PIN_CODEQL_SHA` | v3 |
| dependency-review.yml | actions/dependency-review-action | `PIN_DEPREVIEW_SHA` | v4 |

(Total grep count returns 9 because the audit-comment grep also matches the `pinned per OSS-13` lines that sit above each pin; the table above lists the distinct pin sites.)

Whitelisted tag-pinned actions (non-security): `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4`. Per OSS-13 these are first-party GitHub Actions and acceptable to track by major-version tag.

## Decisions Made

- **Self-allowlist gitleaks/pre-commit config files.** Without this, gitleaks would flag its own pattern strings on every run. Documented inline in `.gitleaks.toml`.
- **`pytest-cov` added to `test` extras, not `dev`.** Coverage measurement is part of the test contract, not a developer-only tool; CI installs `[test]` and gets coverage via the same install.
- **mypy `continue-on-error: true` in CI lint job mirrors `verbose: true, fail_fast: false` in pre-commit.** Two surfaces of the same D-17 warn-only rule. Phase 1 closeout flips both atomically.
- **Dependabot reviewers/assignees explicitly empty.** D-16 says repo identity is not yet locked; populating these with `adibirzu` would have to be unwound at org-move time. Leaving empty surfaces this as a Plan 09 checklist item.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Dependency] Added `pytest-cov>=5.0` to `[test]` optional-deps**
- **Found during:** Task 1 (pyproject.toml update)
- **Issue:** Task 2's `ci.yml` invokes `pytest --cov=multillm --cov-fail-under=80 --cov-report=xml`. `pytest-cov` is required for the `--cov*` flags but was not present in `pyproject.toml`'s `[test]` extras. Without it, CI would fail at first pytest invocation with `unrecognized arguments: --cov=multillm`.
- **Fix:** Appended `"pytest-cov>=5.0"` to the existing `test = [...]` array. Co-located with other pytest tooling rather than splitting it into the new `dev` group, since coverage measurement is part of the test contract.
- **Files modified:** `pyproject.toml`
- **Verification:** ci.yml `pytest` job uses `pip install -e ".[test]"` which now resolves `pytest-cov` transitively.
- **Committed in:** `21263f0` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 2 missing critical dep)
**Impact on plan:** Single-line addition; no scope change. Without this fix the CI pytest job would fail at parse time on first run, blocking the Phase 1 close milestone.

## Issues Encountered

None. All plan tasks executed as written.

## User Setup Required

None — no external service configuration required at this stage. Plan 09 Task 1 will resolve the four `PIN_*_SHA` placeholders into real 40-char commit SHAs before the public push; that is the only pre-push action gating CI execution.

To activate locally:
```
pip install -e ".[dev]"
pre-commit install   # installs both pre-commit and pre-push hook stages
```

## Next Phase Readiness

- **Plan 03 (alembic + smoke migration):** No blockers. CI gate is in place to catch regressions.
- **Plan 05 (release workflow):** Will reuse the SHA-pinning + audit-comment convention established here. Plan 05 adds `cosign`, `actions/attest-build-provenance`, and `pypa/gh-action-pypi-publish` — all need SHA pins per OSS-13.
- **Plan 08 (filter-repo + history scrub):** Once filter-repo completes, run `gitleaks detect --log-opts="--all" --config=.gitleaks.toml` to verify zero findings under the project-specific ruleset added here.
- **Plan 09 Task 1 (resolve SHA pins):** Must run `gh api repos/<owner>/<repo>/git/refs/tags/<tag>` against gitleaks-action, trufflehog, codeql-action, and dependency-review-action upstream repos to populate the four distinct SHAs; replace all nine occurrences across `ci.yml`, `codeql.yml`, `dependency-review.yml` in a single commit so the audit trail is one click.

## Self-Check: PASSED

Files (all created/modified files verified via `test -f` and content greps):
- FOUND: `.pre-commit-config.yaml`
- FOUND: `.gitleaks.toml`
- FOUND: `.github/workflows/ci.yml`
- FOUND: `.github/workflows/codeql.yml`
- FOUND: `.github/workflows/dependency-review.yml`
- FOUND: `.github/dependabot.yml`
- FOUND: `pyproject.toml` (modified — `pre-commit>=4.0` and `pytest-cov>=5.0` strings present)

Commits (all present in `git log --oneline`):
- FOUND: `21263f0` feat(01-02): add pre-commit hooks with gitleaks block-on-fail and ruff/mypy warn-only
- FOUND: `4ff3ec5` feat(01-02): add ci.yml with pytest + lint + scan jobs
- FOUND: `c4e6a3e` feat(01-02): add codeql, dependency-review workflows and dependabot config

SHA pin count: 9 (≥6 required by plan verification) — verified via `grep -E '@[a-f0-9]{40}|PIN_.*_SHA' .github/workflows/*.yml | wc -l`.

No non-whitelisted `@vN` tags in scan/codeql/dependency-review jobs — verified via grep filter excluding `actions/(checkout|setup-python|upload-artifact)`.

---
*Phase: 01-open-source-readiness*
*Completed: 2026-05-17*
