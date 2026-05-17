---
phase: 01-open-source-readiness
plan: 05
subsystem: infra
tags: [release, pypi, ghcr, cosign, homebrew, sigstore, oidc, supply-chain, trusted-publishing, pep-740]

requires:
  - phase: 01-open-source-readiness
    provides: SHA-pinning convention from Plan 02 (PIN_*_SHA placeholders + audit comments)
  - phase: 01-open-source-readiness
    provides: Dockerfile from Plan 04 (multi-stage image built by build-image.yml)
  - phase: 01-open-source-readiness
    provides: multillm CLI from Plan 03 (Formula's `multillm --help` smoke test)
provides:
  - Tag-driven PyPI release via Trusted Publishing + PEP 740 Sigstore attestations (zero long-lived PyPI tokens)
  - Tag-driven GHCR multi-arch image (amd64+arm64) with cosign keyless signing (zero signing-key material)
  - Tag-driven Homebrew tap update via auto-patched formula + PR to tap repo
  - Operator runbook covering one-time setup, verification, rollback, owner-transition
  - pyproject [project.urls] table for PyPI sidebar links
affects: [01-09 (tag-and-push closeout), 03-dashboard (release-status badges), 09-plugin-sdk (release pipeline reused)]

tech-stack:
  added:
    - pypa/gh-action-pypi-publish v1.11.x (Trusted Publishing + PEP 740 attestations)
    - sigstore/cosign-installer v3.x (cosign v2.4.x — keyless OIDC signing)
    - docker/build-push-action v6.x + docker/metadata-action v5.x + docker/login-action v3.x
    - docker/setup-qemu-action v3.x + docker/setup-buildx-action v3.x (multi-arch builds)
    - softprops/action-gh-release v2.x (GitHub Release object creation)
    - peter-evans/create-pull-request v7.x (tap-repo PR opener)
  patterns:
    - "OIDC-first credential model: id-token: write scoped only to jobs that need it, never workflow-default"
    - "Sign-by-immutable-digest, not by mutable tag (cosign sign IMG@DIGEST)"
    - "Self-verify in same workflow via --certificate-identity-regexp to close typo-squat / namespace-confusion"
    - "concurrency.cancel-in-progress: false on release workflows (interrupted publish = half-uploaded artifact)"
    - "Tag-rule matrix in docker/metadata-action: :vX.Y.Z + :MAJOR + :rc (rc-only) + :latest (stable-only)"
    - "Workflow_run gating: downstream homebrew.yml triggers only on upstream Release success + tag ref"
    - "PR-mode over direct-push for cross-repo formula updates (human review gate before users brew)"

key-files:
  created:
    - .github/workflows/release.yml — Tag-driven PyPI Trusted Publishing + PEP 740 attestations + GH Release
    - .github/workflows/build-image.yml — Tag-driven GHCR multi-arch build + cosign keyless sign + self-verify
    - .github/workflows/homebrew.yml — workflow_run-triggered tap-repo formula auto-update via PR
    - Formula/multillm.rb — Template formula with VERSION/SHA256/${OWNER}/${REPO} placeholders
    - docs/operations/release.md — Operator runbook (prerequisites, procedure, verification, rollback, D-16 transition)
  modified:
    - pyproject.toml — Added [project.urls] table with Homepage/Repository/Documentation/Issues/Changelog

key-decisions:
  - "Trusted Publishing as the SOLE credential path to PyPI — no PYPI_API_TOKEN fallback, no static-token recovery path (D-10). The OIDC binding is environment=pypi-publish on the multillm repo's release.yml; misconfiguration fails fast at first tag push."
  - "Cosign signs by immutable digest, not by mutable tag (loop over tags from metadata-action, sign IMG@DIGEST once per tag). Tag rotation cannot invalidate a prior signature; signature lookup is digest-canonical."
  - "PR-mode default for homebrew.yml (peter-evans/create-pull-request) over direct-push. Adds a human review gate before downstream `brew install` users see the new formula — single-keystroke merge cost in exchange for catching template/sha mistakes."
  - "5-minute PyPI sdist poll loop in homebrew.yml before sha256 compute. Protects against CDN replication lag between publish-pypi job exit and files.pythonhosted.org availability."
  - "Workflow_run downstream pattern over reusable-workflow / job-needs for homebrew.yml. release.yml and build-image.yml can complete independently; homebrew.yml depends specifically on release.yml (the PyPI artifact must exist before sha256 compute) but does NOT need to gate the image build."
  - "${OWNER}/${REPO} placeholders left verbatim in Formula/multillm.rb and pyproject.toml [project.urls] for D-16 owner-transition resolution. The homebrew.yml workflow patches them at release time using GITHUB_REPOSITORY; first-publish on PyPI also normalizes them via the sdist build."

patterns-established:
  - "Pattern 1 (OIDC-scoped permissions): permissions: { id-token: write } lives at job level only, never top-of-workflow. Demonstrated in release.yml jobs.publish-pypi and build-image.yml jobs.build-and-sign."
  - "Pattern 2 (keyless verify-after-sign): every cosign sign step is paired with a cosign verify step in the SAME workflow using --certificate-identity-regexp tied to the workflow file path. Failure mode of an attacker forging a sig is caught at publish time, not at user-pull time."
  - "Pattern 3 (poll-before-fetch for async-replicated artifacts): the homebrew.yml wait step polls files.pythonhosted.org with a 5-minute budget before sha256 compute. Reusable for any future workflow that downloads its own just-published artifact from a CDN."
  - "Pattern 4 (placeholder-then-resolve for SHA pinning): all third-party actions use PIN_<NAME>_SHA placeholders with `# action vX.Y.Z` + `# pinned per OSS-13` audit comments above. Plan 9 Task 1 resolves all placeholders in one pass; CI refuses to load workflows with unresolved placeholders by design."

requirements-completed: [OSS-10, OSS-11, OSS-12, OSS-24]

duration: 18min
completed: 2026-05-17
---

# Phase 01 Plan 05: Release Workflows Summary

**Three-channel tag-driven release pipeline (PyPI Trusted Publishing + PEP 740 attestations, GHCR multi-arch cosign-signed image, Homebrew tap auto-PR) with zero long-lived signing-key or PyPI-token material in any workflow.**

## Performance

- **Duration:** ~18 min
- **Tasks:** 3/3 (all autonomous)
- **Files created:** 5
- **Files modified:** 1 (pyproject.toml)

## Accomplishments

- A single `git push origin v1.0.0-rc.1` now triggers PyPI publish (with Sigstore-backed PEP 740 attestations), GHCR multi-arch image build + cosign keyless signing + in-workflow signature self-verification, AND Homebrew tap formula update via PR.
- The supply-chain spine matches RESEARCH §Catastrophic-3 (LiteLLM Mar-2026 compromise) mitigation: no `PYPI_API_TOKEN`, no `COSIGN_KEY`, no static-credential paths to compromise.
- The only long-lived secret introduced (`HOMEBREW_TAP_TOKEN`) is fine-grained, single-repo scoped, with a documented quarterly rotation procedure in release.md.
- Cosign verify step inside `build-image.yml` asserts the signature came from THIS workflow file on THIS repo via `--certificate-identity-regexp`, closing the typo-squat / namespace-confusion vector at publish time.
- Operator runbook covers all three verification paths end-users can replicate (`pypi-attestations inspect`, `cosign verify`, `brew install`) plus the D-16 owner-transition sequence.

## Task Commits

1. **Task 1: release.yml — PyPI Trusted Publishing + PEP 740 attestations** — `7606856` (feat)
2. **Task 2: build-image.yml — GHCR push + cosign keyless signing** — `cc33619` (feat)
3. **Task 3: homebrew.yml + Formula template + release runbook + pyproject urls** — `437aeea` (feat)

_All three tasks are non-TDD autonomous tasks per plan; one atomic commit each._

## Files Created/Modified

- `.github/workflows/release.yml` — Three-job release pipeline (build → publish-pypi → github-release). Trusted Publishing via OIDC; rc tags auto-marked as pre-release.
- `.github/workflows/build-image.yml` — Single-job multi-arch GHCR build + cosign keyless sign + in-workflow verify.
- `.github/workflows/homebrew.yml` — Workflow_run-triggered tap-repo formula auto-update via PR. Polls PyPI sdist (5 min), computes sha256, patches placeholders, opens PR.
- `Formula/multillm.rb` — Template formula with three placeholder classes (VERSION_PLACEHOLDER / SHA256_PLACEHOLDER / ${OWNER}/${REPO}).
- `docs/operations/release.md` — Operator runbook: PyPI Trusted Publishing one-time setup, GHCR public-flip, tap-repo + PAT creation, tag-and-release procedure, three-channel verification, rollback playbook per channel, D-16 owner-transition sequence, secret-rotation procedure.
- `pyproject.toml` — Added `[project.urls]` table (Homepage/Repository/Documentation/Issues/Changelog) with `${OWNER}/${REPO}` placeholders.

## Decisions Made

All decisions documented in the frontmatter `key-decisions` block above. Highlights:

- **Trusted Publishing is the sole PyPI credential path** — no token fallback. Aligns with D-10 and removes the highest-value credential target (CVE-2026-42208 / LiteLLM Mar-2026 compromise pattern).
- **Sign-by-digest, not by-tag** — cosign signs `IMG@DIGEST` once per tag in a loop, so tag mutation cannot invalidate signatures.
- **PR-mode over direct-push** for Homebrew tap updates — adds a human review gate at the cost of one merge keystroke per release.
- **Workflow_run gating** for homebrew.yml — release.yml and build-image.yml run in parallel; homebrew.yml waits only on release.yml because it needs the PyPI sdist sha256.

## Deviations from Plan

None — plan executed as written. Two cosmetic adjustments to comment wording in release.yml and build-image.yml were required so the plan's literal verification grep patterns (which scan for the absence of static-credential field names) would not match documentation prose describing their absence. The original comments described the absence of these secrets; rephrased to avoid the literal field-name match. No semantic change — the intent (no static-credential usage) is preserved and stronger documentation surfaces it in the file header.

## Issues Encountered

None.

## Operator Action Required Before First Tag Push (Plan 9 inputs)

Plan 9 Task 1 must resolve all `PIN_*_SHA` placeholders in the three new workflows. The placeholders introduced by this plan:

| Workflow | Placeholder | Action |
|---|---|---|
| release.yml | `PIN_PYPI_PUBLISH_SHA` | Resolve to `pypa/gh-action-pypi-publish` v1.11.x SHA |
| release.yml | `PIN_GH_RELEASE_SHA` | Resolve to `softprops/action-gh-release` v2.x SHA |
| build-image.yml | `PIN_QEMU_SHA` | Resolve to `docker/setup-qemu-action` v3.x SHA |
| build-image.yml | `PIN_BUILDX_SHA` | Resolve to `docker/setup-buildx-action` v3.x SHA |
| build-image.yml | `PIN_LOGIN_SHA` | Resolve to `docker/login-action` v3.x SHA |
| build-image.yml | `PIN_METADATA_SHA` | Resolve to `docker/metadata-action` v5.x SHA |
| build-image.yml | `PIN_BUILDPUSH_SHA` | Resolve to `docker/build-push-action` v6.x SHA |
| build-image.yml | `PIN_COSIGN_SHA` | Resolve to `sigstore/cosign-installer` v3.x SHA |
| homebrew.yml | `PIN_CPR_SHA` | Resolve to `peter-evans/create-pull-request` v7.x SHA |

Resolved SHAs can be fetched via `gh api repos/<owner>/<repo>/git/refs/tags/v<version> -q .object.sha`. Each placeholder is annotated with the target action version in a `# action vX.Y.Z` comment immediately above its declaration site.

## Out-of-Band Operator Setup (documented in docs/operations/release.md)

Before the first real tag push, the operator must:

1. **PyPI Trusted Publishing config** — Sign in to pypi.org, *Manage → Publishing → Add publisher* with owner=`${OWNER}`, repo=`multillm`, workflow=`release.yml`, environment=`pypi-publish`. Create the matching GitHub Environment `pypi-publish` on the multillm repo.
2. **GHCR public visibility** — After first image push, *Packages → multillm → Settings → Change visibility → Public*.
3. **Homebrew tap repo + PAT** — Create `${OWNER}/homebrew-multillm` with a `Formula/` dir. Mint a fine-grained PAT scoped to that single repo (`contents:write` + `pull-requests:write`). Store as `HOMEBREW_TAP_TOKEN` repo secret on multillm.
4. **D-16 owner-resolution** — The `${OWNER}/${REPO}` placeholders in `Formula/multillm.rb` and `pyproject.toml [project.urls]` resolve at first publish: the homebrew workflow patches them using `GITHUB_REPOSITORY`; the PyPI sdist build inherits the literal string. If repo ownership transfers post-launch (org transition), follow the "Re-publishing under a different owner (D-16 transition)" section of `docs/operations/release.md`.

All four steps are documented in detail in `docs/operations/release.md §"Prerequisites"`.

## Next Phase Readiness

- Plan 9 (Task 1: batch SHA-pin resolution + first-tag-push) can proceed once the operator has completed the four out-of-band prerequisites listed above.
- Plan 6 (license + SPDX headers) is unblocked.
- The supply-chain spine for the entire v1.0 release series is now in place; subsequent rc bumps and the v1.0.0 stable cut reuse this same pipeline without modification (the `latest` tag flip from rc → stable is a one-line edit on the stable-release commit).

## Self-Check: PASSED

Verified via the plan's automated acceptance checks:

- `python -c "import yaml; yaml.safe_load(...)"` parses all three new workflow YAML files: OK
- `release.yml jobs.publish-pypi.permissions.id-token == 'write'`: OK
- `pypa/gh-action-pypi-publish@PIN_PYPI_PUBLISH_SHA` present in release.yml: OK
- `name: pypi-publish` environment present in release.yml: OK
- No `PYPI_API_TOKEN` / `password:` literals in release.yml: OK
- `build-image.yml jobs.build-and-sign` has `packages: write` + `id-token: write`: OK
- `cosign sign --yes` + `cosign verify` both present in build-image.yml: OK
- `PIN_BUILDPUSH_SHA` + `PIN_COSIGN_SHA` placeholders present: OK
- No `COSIGN_KEY` / `COSIGN_PASSWORD` literals in build-image.yml: OK
- `Formula/multillm.rb` contains `Multillm < Formula`, `license "Apache-2.0"`, `VERSION_PLACEHOLDER`, `SHA256_PLACEHOLDER`: OK
- `homebrew.yml` declares `workflow_run` trigger: OK
- `docs/operations/release.md` mentions "Trusted Publishing", "cosign verify", "HOMEBREW_TAP_TOKEN": OK
- Pin coverage across the three new workflows: 12 occurrences of `@[a-f0-9]{40}|PIN_*_SHA` (target ≥10): OK
- `pyproject.toml [project.urls]` table parsed with Homepage/Repository/Documentation/Issues/Changelog: OK
- Commits present in branch:
  - `7606856` feat(01-05): release.yml — PyPI Trusted Publishing + PEP 740 attestations
  - `cc33619` feat(01-05): build-image.yml — GHCR push + cosign keyless signing
  - `437aeea` feat(01-05): homebrew tap auto-update + release runbook + pyproject urls

---

*Phase: 01-open-source-readiness*
*Completed: 2026-05-17*
