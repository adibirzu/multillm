---
phase: 01-open-source-readiness
plan: 08
subsystem: secrets-and-history
tags: [filter-repo, secret-scrub, history-rewrite, force-push, destructive, manual-gate, mailmap, oss-22]

requires:
  - phase: 01-01..01-07
    provides: All shipped wave-1 + wave-2 plans (preconditions for the manual gate)
provides:
  - Cleaned `origin/main` with all D-02 patterns scrubbed and author email rewritten
  - SECRET-SCAN-REPORT.md as the audit trail (gitleaks + trufflehog before/after, SHA mapping, sign-off)
  - filter-repo-spec.txt + mailmap-rewrite.txt as the reproducible scrub artifacts
  - credentials-rotated.md as the (empty by scanner evidence) rotation log
affects: [01-09]

tech-stack:
  added: [git-filter-repo, trufflehog]
  patterns:
    - "Safety-clone-first pattern: rewrite history in /tmp/multillm-rewrite, push from clone, sync working repo via fetch+reset — never run filter-repo against the working tree"
    - "Lease-protected force-push: `--force-with-lease=main:<expected-sha>` asserts the remote hasn't moved before allowing rewrite"
    - "Mailmap-driven author rewrite: `--mailmap` rewrites Author and Committer signatures invisibly to `--replace-text`"

key-files:
  created:
    - .planning/phases/01-open-source-readiness/SECRET-SCAN-REPORT.md
    - .planning/phases/01-open-source-readiness/filter-repo-spec.txt
    - .planning/phases/01-open-source-readiness/mailmap-rewrite.txt
    - .planning/phases/01-open-source-readiness/credentials-rotated.md
  modified:
    - (entire git history of `main` rewritten — 12 commits, OLD `38d4807` → NEW `98768cd`)
    - (entire git history of `gsd/phase-01-open-source-readiness` rewritten — 44 commits, OLD `c145b12` → NEW `72cd53b`; local only, not yet on remote)

key-decisions:
  - "D-02 spec interpretation: applied operator-selected Option A (full content + mailmap), even though pre-flight found both scanners already at zero. Defense-in-depth: future contributors may add code that introduces patterns; having the rewrite as part of the v1.0 launch story is operationally clean."
  - "Filter-repo spec format: NO comment syntax supported. Specs must be pattern-only. Comments live in SECRET-SCAN-REPORT.md, never inline. The first attempt's `#`-prefixed comments corrupted 156 files before the safety clone caught it."
  - "Mailmap vs replace-text: rewriting commit author signatures requires --mailmap; --replace-text touches blob content only. Without mailmap, `adibirzu@gmail.com` would persist on every commit's Author header invisible to file-content scans."
  - "Push strategy: lease-protected force-push (`--force-with-lease=main:<sha>`) over branch-rename + default-switch. Lease is simpler, safer when sole-maintainer, and survives token-scope mishaps without requiring GitHub-UI navigation."

patterns-established:
  - "Pre-push pytest hook as the structural safety net: ECC pre-push ran 351 tests against the rewritten clone and would have refused any corrupted tree before it reached origin (caught the `***REMOVED***` corruption in attempt 1)"
  - "Two-scanner exit gate: gitleaks (rule-based) + trufflehog (entropy + verified) gives independent coverage. Even when both report zero, manual D-02 sweep adds a third pass for patterns not in either ruleset"
  - "Definitive verification = fresh clone of remote, not local clone. The local rewritten clone could differ from what GitHub actually accepted (network truncation, refspec misconfiguration). A fresh clone of `origin/main` proves what third parties will see"

requirements-completed: [OSS-01, OSS-02, OSS-03, OSS-22]

duration: ~60 min (including auth troubleshooting and the corrupted-attempt redo)
completed: 2026-05-18
---

# Phase 01 Plan 08 — History rewrite, secret scrub, force-push

**Force-pushed a cleaned `origin/main` (SHAs rewritten, author email replaced with a placeholder, zero gitleaks/trufflehog findings) after running `git filter-repo` in a safety clone, surviving one self-induced corruption incident, and resolving a stale `GITHUB_TOKEN` auth break — all gated by two operator checkpoints.**

## Performance

- **Duration**: ~60 min wall-clock, ~10 min of which was filter-repo + scanning, the rest being auth troubleshooting and the corrupted-attempt redo
- **Completed**: 2026-05-18
- **Tasks**: 3 (pre-flight checkpoint, filter-repo + scan, force-push checkpoint) — all complete
- **Operator checkpoints**: 2 — both granted (pre-flight spec sign-off, force-push authorization)
- **Commits rewritten**: 44 (main + phase branch combined)
- **Files corrupted by attempt 1**: 156 — discarded, no impact on working repo or origin
- **Files corrupted by attempt 2 (final)**: 0
- **Pre-push test runs**: 2 (one against local dry-run target, one against the force-pushed main) — 351 passes each

## Accomplishments

### Task 1 — Pre-flight + spec sign-off

- Verified all wave-1 + wave-2 SUMMARYs present (`01-01..01-07`).
- Installed `trufflehog` via Homebrew to satisfy the D-03 two-scanner exit gate.
- Ran pre-scrub baseline scans: **gitleaks 0, trufflehog 0** across 44 commits.
- Ran manual D-02 pattern sweep — 73 hits found, but every single one was a documentation reference (`.gitleaks.toml` rule definitions, `.planning/REQUIREMENTS.md` describing OSS-03 wording, `01-02-SUMMARY.md` and `01-04-SUMMARY.md` describing the scrub workflow). Zero real leaks.
- Pre-phase-1 commit audit: **0 D-02 hits** in commits before plan 01-01. The premise that "history is full of leaks" did not hold up.
- Identified the **single real residue**: `adibirzu@gmail.com` baked into every commit's Author header — invisible to `--replace-text`, requires `--mailmap` or `--email-callback`.
- Authored `filter-repo-spec.txt`, `mailmap-rewrite.txt`, and `SECRET-SCAN-REPORT.md` with three-option decision matrix (full / mailmap-only / skip).
- Operator selected **Option A — full spec + mailmap** despite the pre-flight finding of zero scanner hits.

### Task 2 — Filter-repo execution (with one corrupted attempt)

**Attempt 1 (corrupted, discarded):** The initial `filter-repo-spec.txt` included `#`-prefixed comment lines for human readers. `git filter-repo --replace-text` treats every non-empty line as a pattern; there is no comment syntax. A bare-`#` line became a pattern matching every `#` in the repo, replaced with the default `***REMOVED***`. 156 files corrupted across all 44 commits — Python shebangs broken, SPDX headers broken, TOML section markers broken. The plan's threat model named this as T-01-08-01 with mitigation "Task 2 runs in a SAFETY CLONE; if scan fails or operator regrets, `rm -rf /tmp/multillm-rewrite` and re-iterate." That mitigation worked exactly as designed. ECC pre-push hook ran pytest and refused the push; nothing reached origin.

**Attempt 2 (clean):** Stripped all comment lines from the spec. Re-cloned to `/tmp/multillm-rewrite`, re-ran filter-repo with the cleaned spec + mailmap. Post-rewrite verification:

| Check | Result |
|-------|--------|
| `***REMOVED***` corruption check | 0 files |
| gitleaks against rewritten history | 0 findings |
| trufflehog (full) | 0 findings |
| trufflehog (--only-verified) | 0 findings |
| Manual D-02 sweep — `130.61.*` | 0 (was 9) |
| Manual D-02 sweep — `adibirzu@gmail.com` (metadata) | 0 (was 44) |
| Author/Committer signature | `contact@example.invalid` only |

### Task 3 — Credential rotation + force-push

**Auth fix.** First push attempt failed: stale `GITHUB_TOKEN` from `~/.zshrc` overrode the freshly-stored osxkeychain credentials. Resolved by `unset GITHUB_TOKEN && gh auth setup-git` in operator's terminal. The auth fix is persistent within the operator's shell session but agent-side Bash sessions re-inherit the env var — workaround was prefixing pushes with `unset GITHUB_TOKEN &&`.

**Force-push:**
- `git push --force-with-lease=main:38d4807a8312d5bcce935464ae7df0c7ffc42453 origin main` — **succeeded.** `38d4807 → 98768cd`. Pre-push hook ran 351 tests green. GitHub reported `+ 38d4807...98768cd main -> main (forced update)`.
- `git push -u origin gsd/phase-01-open-source-readiness` — **refused.** Token lacks `workflow` scope; the phase branch contains `.github/workflows/build-image.yml` from plan 01-05. This is a token-scope issue, not a rewrite issue. Resolution: reissue the PAT with `workflow` scope, then push. Not blocking 01-08's primary goal (cleaned main).

**Remote verification.** Fresh clone of `https://github.com/adibirzu/multillm.git` to `/tmp/multillm-verify`. Ran gitleaks + trufflehog against the cloned origin. **Both 0 findings.** Author signature on `git log --all --pretty="%ae"`: `contact@example.invalid` only. D-03 exit gate satisfied on the remote — the definitive verification per the plan's success criterion.

**Credential rotation (D-04).** No scanner-driven rotations required (both scanners 0, manual sweep all documentation references). `credentials-rotated.md` retains the precautionary table for operator-discretion rotations.

## Verification gates

| Gate | Result |
|------|--------|
| All wave-1+2 SUMMARYs present | pass |
| Pre-scrub baseline scans run | gitleaks 0, trufflehog 0 |
| Filter-repo spec operator-approved | Option A approved |
| Safety clone created (`/tmp/multillm-rewrite`) | yes; corrupted attempt discarded cleanly |
| Working repo untouched until task 3 | yes |
| Post-rewrite gitleaks (D-03 first scanner) | 0 |
| Post-rewrite trufflehog (D-03 second scanner) | 0 |
| Force-push to origin/main | success; `38d4807 → 98768cd` |
| Pre-push pytest gate on cleaned main | 351 passed |
| Fresh-clone scan of `origin/main` (definitive) | gitleaks 0, trufflehog 0 |
| Old SHA `38d4807` invalidated on remote | yes — replaced by lease-protected force-push |
| Author signature on remote `git log` | `contact@example.invalid` only |
| Credential rotation list complete | yes (scanner-driven rotations: none) |
| SECRET-SCAN-REPORT.md audit trail | written, includes corrupted-attempt incident notes |

## Threat-mitigation evidence

| Threat | Mitigation as planned | How it actually fired |
|--------|----------------------|-----------------------|
| T-01-08-01 (filter-repo over-reach destroys legitimate code via greedy regex) | "Task 2 runs in a SAFETY CLONE; if scan fails or operator regrets, `rm -rf /tmp/multillm-rewrite` and re-iterate." | Triggered. Corrupted attempt 1 stayed in `/tmp/multillm-rewrite`. ECC pre-push hook + pyproject parse-error caught it. `rm -rf` + re-run yielded a clean attempt 2. **The safety pattern worked exactly as designed.** |
| T-01-08-02 (secrets remain in history after rewrite via false-negative regex coverage) | Two independent scanners gate the exit, plus a fresh-clone verification scan against the remote. | Both scanners reported 0 pre-AND post-rewrite. Fresh-clone verification confirmed 0 against `origin/main`. Triple-checked. |
| T-01-08-03 (force-push during another contributor's pending work) | `--force-with-lease` (not `--force`). | Explicit lease `--force-with-lease=main:38d4807` enforced. No other contributors active; lease check passed without contention. |
| T-01-08-04 (rewrite without audit trail; future reference to old SHA) | SHA mapping table in SECRET-SCAN-REPORT.md. | 12-row table captured; main `38d4807 → 98768cd`, phase `c145b12 → 72cd53b`. Older commits also rewritten; table notes that any external reference can be redirected via the mapping. |
| T-01-08-05 (rotated-but-still-cached credentials) | D-04 conservative rotation regardless of scan classification. | No scanner-driven rotations required. Precautionary rotation table preserved in `credentials-rotated.md` for operator discretion. |
| T-01-08-06 (git-filter-repo not installed → operator runs deprecated git filter-branch) | Pre-flight checks for `git-filter-repo`, falls back to `pip install --user git-filter-repo`, never falls back to `filter-branch`. | `git-filter-repo` was already installed via `pip install --user`. Confirmed in pre-flight tools table. |

## Downstream impact

- **Plan 01-09 (rc.1 publication)** — unblocked. Cleaned `origin/main` is the prerequisite for v1.0.0-rc.1 tagging. Plan 01-09 may now proceed.
- **Token scope** — operator should reissue the PAT with `workflow` scope so the phase branch can be pushed and so future `.github/workflows/*` modifications don't get refused.
- **Audit fidelity** — `.planning/SUMMARY.md` files were preserved through the rewrite (the spec only replaced literal pattern matches, never destructive `[A-Z0-9]{40,}` sweeps). The `01-04-SUMMARY.md` documentation of pattern names survives intact.

## Notes

- The `git filter-repo` "no comment syntax" gotcha is a real ecosystem footgun. Future GSD plans that touch filter-repo specs should include a literal warning in the plan text — the comment-led approach is intuitive but catastrophic.
- The `[A-Z0-9]{40,}` pattern originally proposed in plan 01-08's interfaces block was intentionally omitted from the final spec. Pre-flight found zero APM-key-shaped strings; the unconstrained regex would have matched commit SHAs and base64 fixtures, breaking the rewrite. Worth documenting as a planning anti-pattern.
- Public-repo timing: `https://github.com/adibirzu/multillm.git` has been visible since before phase 1 began. The rewrite-and-force-push pattern works for our case (single-maintainer, no external forks observed) but the standard caveat applies — anyone who cloned during the dirty window has the unrewritten history.
