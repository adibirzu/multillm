---
phase: 01-open-source-readiness
plan: 09
status: deferred
subsystem: release-distribution
tags: [release, pypi, ghcr, homebrew, rc, deferred, operator-discretion]

requires:
  - phase: 01-05, 01-08
    provides: Release workflows wired, cleaned origin/main
provides:
  - (deferred — no artifacts published; rc.1 publication moved to operator-discretion)
  - SHA-pinned action references in .github/workflows/*.yml (resolved 17 placeholders during the partial preflight)
  - Tap repo adibirzu/homebrew-multillm bootstrapped (empty Formula/ directory ready for future use)
affects: [v1.0 milestone close — pending operator publication when desired]

tech-stack:
  added: []
  patterns: []

key-files:
  created: []
  modified:
    - .github/workflows/*.yml (17 PIN_*_SHA placeholders resolved)
    - Formula/multillm.rb (template ready)
    - pyproject.toml (version 1.0.0rc1; name reverted to "multillm" after PyPI namespace conflict surfaced)

key-decisions:
  - "DEFERRED: rc.1 publication to PyPI / GHCR / Homebrew is deferred to operator discretion. Operator pivoted to focus on functional phases (Phase 2a onward) and explicit local-first development. Publication is no longer a blocker for the v1.0 milestone close."
  - "PyPI namespace conflict surfaced during preflight: name `multillm` is owned by VerifAI Inc since 2023-08-19, currently at version 0.1024, project URL github.com/verifai/multiLLM. Cannot publish to that namespace without coordination."
  - "Rename to `multillm-gateway` was applied then reverted. The rename adds friction for local users (`pip install multillm-gateway` is non-obvious) and the underlying conflict only matters at PyPI publish time. When publication is resurrected, the rename or a different distribution name (mllm, multillm-oss, etc.) is the first decision to make."

requirements-completed: []
requirements-deferred: [OSS-10, OSS-11, OSS-12]

duration: ~30min (partial preflight; not executed to completion)
completed: 2026-05-18 (status: deferred — not shipped)
---

# Phase 01 Plan 09 — Release publication (DEFERRED)

**Plan 01-09 was started but deferred during preflight. The operator chose to pivot from public publication toward local-first functional development. Phase 1 closes at 8/9 plans with 01-09 explicitly marked as operator-discretion.**

## Status

**Deferred.** This plan was not executed to completion. The preflight surfaced one hard blocker (PyPI namespace conflict) and several setup gaps that would have required significant operator setup with limited near-term value. The operator chose to stop and focus on Phase 2+ functional work.

## What got done before the deferral

### 1. PIN_*_SHA resolution (kept)

All 17 placeholder action SHAs in `.github/workflows/*.yml` were resolved to 40-char commit SHAs via `gh api repos/<owner>/<repo>/git/refs/tags/<tag>`. Mapping captured in the parent commit message of `03c06e4 chore(01-09): bump to 1.0.0rc1, rename PyPI dist, resolve action SHAs` plus `/tmp/sha-resolutions.txt`.

| Placeholder | Resolved to |
|-------------|-------------|
| PIN_DEPREVIEW_SHA | actions/dependency-review-action@4901385134134e04cec5fbe5ddfe3b2c5bd5d976 |
| PIN_CODEQL_SHA | github/codeql-action@78ed0c7291d93e40c51b085850dc669a4c3ab73b |
| PIN_GITLEAKS_SHA | gitleaks/gitleaks-action@dcedce43c6f43de0b836d1fe38946645c9c638dc |
| PIN_TRUFFLEHOG_SHA | trufflesecurity/trufflehog@fda044631b344997a4556f52aadbd7c8275d0802 |
| PIN_PYPI_PUBLISH_SHA | pypa/gh-action-pypi-publish@ac9137700382092e954a82f6661e939778fc9e6c |
| PIN_GH_RELEASE_SHA | softprops/action-gh-release@3bb12739c298aeb8a4eeaf626c5b8d85266b0e65 |
| PIN_CPR_SHA | peter-evans/create-pull-request@c5a7806660adbe173f04e3e038b0ccdcd758773c |
| PIN_QEMU_SHA | docker/setup-qemu-action@c7c53464625b32c7a7e944ae62b3e17d2b600130 |
| PIN_BUILDX_SHA | docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f |
| PIN_LOGIN_SHA | docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9 |
| PIN_METADATA_SHA | docker/metadata-action@c299e40c65443455700f0fdfc63efafe5b349051 |
| PIN_BUILDPUSH_SHA | docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8 |
| PIN_COSIGN_SHA | sigstore/cosign-installer@f713795cb21599bc4e5c4b58cbad1da852d7eeb9 |

These pins are kept because OSS-13 (every third-party action pinned by SHA) is independent of OSS-10/11/12 (the publish requirements being deferred). Future workflow runs benefit immediately.

### 2. Version bump (kept)

- `pyproject.toml` `version = "1.0.0rc1"`
- `multillm/__init__.py` `__version__ = "1.0.0rc1"`
- `tests/test_doctor.py` fixture version

Kept because the version string is the project's internal milestone marker. `multillm --version` reports `1.0.0rc1`, which matches the phase-1 closeout intent regardless of whether the artifact ships externally.

### 3. Tap repo (kept, empty)

Created `https://github.com/adibirzu/homebrew-multillm` (public) with a bootstrap commit installing an empty `Formula/.gitkeep`. Harmless if unused. Ready for the future publish flow.

## What got reverted

### PyPI distribution rename to `multillm-gateway` (REVERTED in commit `b7921f7`)

The rename was applied to `pyproject.toml`, `.github/workflows/homebrew.yml`, `Formula/multillm.rb`, `docs/operations/release.md`, `docs/operations/deployment.md`, `docs/operations/upgrade.md` in commit `03c06e4`, then fully reverted in commit `b7921f7` after the operator's local-first pivot.

Reason: the rename only matters at PyPI publish time. With publication deferred, the rename adds friction (`pip install multillm-gateway` is non-obvious to anyone who finds the repo). When publication resumes, the renaming decision is the first item to revisit.

## What did NOT happen

| Item | Status | Notes |
|------|--------|-------|
| `git tag v1.0.0-rc.1` | not pushed | The annotated tag was never created |
| PyPI publish | not attempted | Trusted Publishing config never completed; namespace conflict open |
| GHCR image build | not triggered | `build-image.yml` never fired |
| Homebrew tap formula push | not attempted | `homebrew.yml` never fired |
| `gh attestation verify` / `cosign verify` verifications | N/A | No artifacts to verify |
| Live smoke test from rc.1 channel | N/A | No published rc.1 to test |
| `DISTRIBUTION-VERIFICATION.md` | not written | No verification took place |

## Resurrection criteria

When the operator decides to publish, run `/gsd-execute-phase 1 --wave 4 --interactive` again. The preflight will re-detect the PyPI namespace conflict; resolve via one of:

1. **Rename to a distinct PyPI name** (re-apply commit `03c06e4`'s rename diff, possibly to a different name like `mllm`, `multillm-oss`, `multillm-fr`)
2. **Skip PyPI entirely** — edit `release.yml` to drop the publish-pypi job; rely on GHCR + Homebrew only
3. **Contact VerifAI Inc** — request namespace transfer (extremely unlikely to succeed)

Other prerequisites that need operator UI work at resurrection time:

- PyPI Trusted Publishing pending publisher for the chosen project name (https://pypi.org/manage/account/publishing/)
- `HOMEBREW_TAP_TOKEN` repo secret with fine-grained PAT scoped to `adibirzu/homebrew-multillm`
- GitHub environment `pypi-publish` in `adibirzu/multillm` repo settings

All other preflight infrastructure (SHA pins, version, tap repo, cleaned origin/main from plan 01-08) is already in place and will not need to be redone.

## Phase 1 milestone status

Phase 1 closes at **8/9 plans shipped**. Plan 01-09 is explicitly marked deferred, not failed. The success criteria for Phase 1 that depend on plan 01-09 (OSS-10 PyPI release, OSS-11 GHCR release, OSS-12 Homebrew tap) are deferred alongside the plan.

The remaining Phase 1 success criteria — local bring-up via `docker compose up`, first-run setup wizard, history scrubbed and verified clean, alembic migrations, env var inventory, all CI and supply-chain hardening — are all shipped and verified.

## Downstream impact

- Phase 2a (Adapter Hot-Path Refactor) — unblocked. Can start immediately.
- Future Phase 1 closure (publication) — operator-discretion, no schedule attached.
- Test count remains at 351 passing.
