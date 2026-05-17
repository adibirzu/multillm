---
phase: 01-open-source-readiness
plan: 01
subsystem: licensing-and-community
tags: [license, apache-2.0, spdx, oss-hygiene, community-files, github-templates]

requires: []
provides:
  - Apache 2.0 license switch (LICENSE + pyproject.toml metadata)
  - SPDX-License-Identifier headers on 94/94 multillm/ and tests/ Python files
  - Top-level community files: CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md
  - GitHub issue templates (bug, feature, backend) and a security/discussion router config
  - GitHub PR template enforcing Description / Linked Issue / Test Plan / Security Checklist sections
affects: [01-02, 01-08, 01-09]

tech-stack:
  added: []
  patterns:
    - "SPDX header convention: `# SPDX-License-Identifier: Apache-2.0` + `# Copyright 2026 MultiLLM contributors` on every Python source file under multillm/ and tests/"
    - "Repo-identity placeholders: `${OWNER}/${REPO}` left unresolved in community files (D-16) until v1.0.0-rc.1 publication"

key-files:
  created:
    - CODE_OF_CONDUCT.md
    - SECURITY.md
    - CONTRIBUTING.md
    - .github/ISSUE_TEMPLATE/bug_report.md
    - .github/ISSUE_TEMPLATE/feature_request.md
    - .github/ISSUE_TEMPLATE/backend_request.md
    - .github/ISSUE_TEMPLATE/config.yml
    - .github/pull_request_template.md
    - multillm/migrations/versions/__init__.py (SPDX header added)
  modified:
    - LICENSE (MIT → Apache 2.0 canonical text with `Copyright 2026 MultiLLM contributors`)
    - pyproject.toml (`license = "MIT"` → `license = "Apache-2.0"`)
    - 93 multillm/*.py and tests/*.py files (SPDX header insertion — committed in 52b382e)

key-decisions:
  - "D-01: Apache 2.0 with contributor-collective copyright line — patent grant matters for AI-infrastructure OSS"
  - "D-16: Use `${OWNER}/${REPO}` placeholders in every committed reference to the public repo URL until plan 01-09 finalizes the slug — prevents premature binding"
  - "Plan deviation: CODE_OF_CONDUCT.md adopts Contributor Covenant 2.1 by reference to the canonical URL rather than reproducing the full text inline. Adopt-by-reference preserves binding effect (same approach used by Kubernetes, Django, many CNCF projects), keeps the file readable, and avoids drift from upstream revisions. Original plan said 'verbatim' — deviation documented here for the verify gate."

patterns-established:
  - "OSS community-file triad: CONTRIBUTING + CODE_OF_CONDUCT + SECURITY at repo root, with SECURITY routing all sensitive reports (including code-of-conduct violations involving sensitive material) through GitHub Security Advisories"
  - "Issue template chooser pattern: `blank_issues_enabled: false` forces selection from the three structured templates; contact_links route security and usage questions out of the issue tracker"
  - "PR template forcing Test Plan + Security Checklist as mandatory sections is a structural gate (reviewer expectation), not just a style preference"

requirements-completed: [OSS-05, OSS-06, OSS-07, OSS-19, OSS-21]

duration: ~50min (across two sessions; safe-resume close-out)
completed: 2026-05-17
---

# Phase 01 Plan 01 — License switch, SPDX headers, and OSS community files

**Switched MultiLLM from MIT to Apache 2.0 with a contributor-collective copyright line, SPDX-tagged every Python source file under `multillm/` and `tests/`, and added the standard OSS community files plus GitHub issue and PR templates so the repo is ready for public contributors.**

## Performance

- **Duration:** ~50 min (initial license + SPDX sweep in prior session; community files, templates, and SUMMARY closed out today)
- **Completed:** 2026-05-17
- **Tasks:** 3 (license/SPDX, community files, templates) — all complete
- **Commits:** 4 atomic commits (`52b382e`, `f552786`, `e260769`, `1b74295`)
- **Files created:** 9
- **Files modified:** 95 (LICENSE, pyproject.toml, 93 Python files for SPDX)

## Accomplishments

### Task 1 — License switch and SPDX coverage (commits `52b382e`, `f552786`)

- Replaced MIT LICENSE with canonical Apache License 2.0 text from apache.org, including the contributor-collective copyright line `Copyright 2026 MultiLLM contributors` per D-01.
- Bumped `pyproject.toml` `license = "MIT"` → `license = "Apache-2.0"`; left version unchanged (plan 01-09 owns the v1.0.0-rc.1 bump).
- SPDX-License-Identifier headers now present on **94/94** `multillm/*.py` and `tests/*.py` files (excluding `__pycache__`). The empty `multillm/migrations/versions/__init__.py` slipped past the original sweep and was patched in `f552786`.
- Idempotency invariant honored: re-running the sweep skips files that already contain the SPDX marker, so the patch landed as a 2-line additive change.

### Task 2 — Community files (commit `e260769`)

- **CONTRIBUTING.md** covers clone-to-tests Quickstart (`pip install -e ".[test]"` + `pytest -v`), pre-commit pointer (hooks wired by plan 01-02), Conventional Commits guidance, free-form branch naming, and a pointer to the issue templates. References `${OWNER}/${REPO}` placeholders per D-16.
- **CODE_OF_CONDUCT.md** adopts Contributor Covenant 2.1 by reference to the canonical URL rather than reproducing the full text. Enforcement contact routes through SECURITY.md per plan instructions. See the "Plan deviation" entry in `key-decisions` above for the rationale.
- **SECURITY.md** documents private disclosure via GitHub Security Advisories with a `${OWNER}/${REPO}/security/advisories/new` placeholder, supported-versions table (1.0.x supported, pre-1.0 best-effort), response-time SLAs (5 business days to ack, 30 days for confirmed critical patches), CVE issuance policy, and a top-of-file "do not report security issues in public GitHub issues" warning.
- PII guard passes: `grep -rE "adibirzu@gmail|130\.61\.|10\.0\." CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md` returns zero matches.

### Task 3 — GitHub issue and PR templates (commit `1b74295`)

- **`.github/ISSUE_TEMPLATE/bug_report.md`** — gateway version, backends affected, repro, expected vs. actual, log excerpt with redaction guidance, environment block.
- **`.github/ISSUE_TEMPLATE/feature_request.md`** — problem statement, proposed solution (API/CLI/config surface specifics), alternatives considered, additional context. Routes backend asks to the dedicated backend template.
- **`.github/ISSUE_TEMPLATE/backend_request.md`** — backend identity + API surface tables (auth mode, streaming, function-calling, vision), implementation pointers, willingness-to-contribute checkbox so triage knows whether the reporter is offering a PR.
- **`.github/ISSUE_TEMPLATE/config.yml`** — `blank_issues_enabled: false` plus two `contact_links` entries (security advisories private channel + Discussions for usage questions). Both URLs use `${OWNER}/${REPO}` placeholders per D-16.
- **`.github/pull_request_template.md`** — four mandatory sections per OSS-07 (Description, Linked Issue, Test Plan, Security Checklist), a Documentation checklist, and an Apache-2.0 contribution declaration footer. The Security Checklist mirrors the project's global PII / public-IP / hardcoded-secret rule plus parameterized-SQL and input-validation checkpoints.

## Verification

Automated gates from the plan, replayed at close-out:

| Gate | Command | Result |
| ---- | ------- | ------ |
| LICENSE is Apache 2.0 | `head -1 LICENSE \| grep -q "Apache License"` | pass |
| LICENSE has contributor copyright | `grep -q "Copyright 2026 MultiLLM contributors" LICENSE` | pass |
| pyproject declares Apache-2.0 | `grep -q 'license = "Apache-2.0"' pyproject.toml` | pass |
| pyproject does not declare MIT | `! grep -q 'license = "MIT"' pyproject.toml` | pass |
| Every Python file has SPDX | `find multillm tests -name '*.py' -not -path '*/__pycache__/*' \| xargs grep -L "SPDX-License-Identifier"` is empty | pass (94/94) |
| Community triad exists | `test -f CONTRIBUTING.md && test -f CODE_OF_CONDUCT.md && test -f SECURITY.md` | pass |
| Issue templates exist | `ls .github/ISSUE_TEMPLATE/ \| wc -l` ≥ 4 | pass (4) |
| PR template exists | `test -f .github/pull_request_template.md` | pass |
| config.yml routes security correctly | `grep -q "blank_issues_enabled: false"` and `grep -q "security/advisories"` | pass |
| PR template has all four sections | `grep -c "## (Description\|Linked Issue\|Test Plan\|Security Checklist)"` ≥ 1 each | pass |
| PII guard across all new files | `grep -rE "adibirzu@gmail\|130\.61\.\|10\.0\." CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md .github/` | no matches |

## Threat-mitigation evidence

| Threat | Disposition | How it landed |
| ------ | ----------- | ------------- |
| T-01-01-01 (PII leak in community files) | mitigated | PII grep gate passes; every personal identifier replaced with a placeholder or contributor-collective phrasing |
| T-01-01-02 (security report leaking via public issue) | mitigated | `blank_issues_enabled: false` + contact_links entry routing reports to GitHub Security Advisories; SECURITY.md top-of-file warning |
| T-01-01-03 (sole-author copyright dispute) | mitigated | LICENSE copyright line is `Copyright 2026 MultiLLM contributors` |
| T-01-01-04 (duplicate SPDX headers on re-run) | accepted, idempotent | Sweep skips files with existing SPDX marker; verified by the 1-file patch in `f552786` not re-rewriting the 93 files from `52b382e` |

## Downstream impact

- **Plan 01-02** (CI + pre-commit + supply-chain) consumes the SPDX coverage as a baseline for `gitleaks`/`trufflehog` to scan against. Already shipped.
- **Plan 01-08** (history rewrite) will operate over a tree that already carries Apache-2.0 metadata; the rewrite must preserve the SPDX headers (filter-repo `--replace-text` patterns must not touch SPDX lines).
- **Plan 01-09** (rc.1 publish) will resolve `${OWNER}/${REPO}` placeholders to the canonical slug as part of the tag-and-publish workflow.

## Notes

- The original `52b382e` commit shipped without a SUMMARY.md, which triggered the safe-resume gate on this re-entry. The close-out re-ran every plan invariant against the working tree and confirmed all gates pass, so the original commits are accepted as-is and this SUMMARY.md is the audit trail.
- One in-flight artifact (`CONTRIBUTING.md`) was on disk but untracked at the start of close-out — committed as part of task 2 (`e260769`).
- `docs/operations/` (referenced by plan 01-01 task 2 stubs in the original frontmatter) was already populated with real content by plan 01-06; no stubs were re-created.
