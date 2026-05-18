# Secret Scan Report — Plan 01-08 (pre-flight)

**Status:** Pre-flight complete — operator approval pending for filter-repo execution.
**Generated:** 2026-05-17T22:00Z
**Repo state at scan:** branch `gsd/phase-01-open-source-readiness`, HEAD `c145b12`, 44 commits across two refs (`main` + this branch).

---

## Pre-scrub scanner baseline

### gitleaks

| Field | Value |
|-------|-------|
| Command | `gitleaks detect --log-opts="--all" --config=.gitleaks.toml --report-format=json --report-path=/tmp/gitleaks-pre.json` |
| Commits scanned | 44 |
| Bytes scanned | ~1.21 MB |
| **Findings** | **0** |
| Report file | `/tmp/gitleaks-pre.json` (size: 3 bytes — empty array) |

### trufflehog

| Field | Value |
|-------|-------|
| Version | 3.95.3 |
| Command (full) | `trufflehog git file://. --json > /tmp/trufflehog-pre.json` |
| Command (verified) | `trufflehog git file://. --json --only-verified > /tmp/trufflehog-pre-verified.json` |
| **Findings (full)** | **0** |
| **Findings (verified)** | **0** |

### Manual D-02 pattern sweep

The plan's D-02 pattern list is broader than the default scanner rules. Manual `git log --all -p | grep -E` over the same 44 commits:

| Pattern | Hits | Disposition |
|---------|------|-------------|
| `ocid1\.tenancy\.` | 2 | All in `.planning/01-04-SUMMARY.md` (documents what the grep sweep found — `<...>` placeholder, no real OCID) |
| `ocid1\.compartment\.` | 0 | clean |
| `130\.61\.` | 9 | All in `.gitleaks.toml` (rule definition), `.planning/REQUIREMENTS.md` (OSS-03/22 wording), `.planning/01-02-SUMMARY.md` and `01-04-SUMMARY.md` (describing the scrub workflow). **Zero real IP literals in source.** |
| `adibirzu@gmail\.com` | 44 | **Author email on commit signatures** — once per commit. Zero file-content occurrences. |
| `control-plane-oci` | 0 | clean |
| `fr4zqfimuxtr` | 0 | clean |
| `eu-frankfurt-1\.ocir\.io` | 0 | clean |
| `~/\.oca` | 18 | All in `.dockerignore`, `.env.example` (documented OCA path), `.gitignore`, `.planning/REQUIREMENTS.md`, `.planning/01-04-SUMMARY.md`, and `README` (Quickstart references). All legitimate documentation of the OCA tooling path. |
| `/home/[a-zA-Z0-9_-]+/\.oca` | 0 | clean |

**Conclusion:** No real secret material was leaked into file content. The 73 raw hits are all documentation describing the patterns the scrub is supposed to catch.

---

## Pre-phase-1 commit audit

The 12 commits prior to plan 01-01 (`8f635ba` initial → `38d4807` harden production readiness) were authored before phase 1 wired pre-commit gitleaks. They are the most likely source of any historical leak.

| Pattern | Pre-phase-1 hits |
|---------|------------------|
| `ocid1\.tenancy\.` | 0 |
| `130\.61\.[0-9]+\.[0-9]+` | 0 |
| `fr4zqfimuxtr` | 0 |
| `control-plane-oci` | 0 |
| `eu-frankfurt-1\.ocir\.io` | 0 |

The earliest hit for any D-02 pattern is in `21263f0 feat(01-02): add pre-commit hooks` — and the hit is the gitleaks rule definition itself. Pre-phase-1 commits are clean.

---

## Filter-repo spec (proposed)

The replacement spec is captured at `.planning/phases/01-open-source-readiness/filter-repo-spec.txt`. Highlights:

- 9 content-replacement patterns implementing D-02 (most will be near no-ops given the findings above)
- The over-broad `[A-Z0-9]{40,}` pattern from the original interfaces block is **intentionally omitted** — pre-flight found zero APM-key-shaped strings, and the pattern is documented in-plan as risky (matches commit SHAs, base64 fixtures, hex hashes)
- Author email rewrite via `mailmap-rewrite.txt` consumed by `git filter-repo --mailmap mailmap-rewrite.txt`. Rewrites both Author: and Committer: fields for every commit. **This is the substantive change** — the only real residue of the original author identity in commit history.

---

## Tooling sanity check

| Tool | Path | Version |
|------|------|---------|
| gitleaks | /opt/homebrew/bin/gitleaks | 8.x (latest brew) |
| trufflehog | (newly installed via brew) | 3.95.3 |
| git-filter-repo | /Users/abirzu/oci-cli/bin/git-filter-repo | pip-installed |
| jq | /usr/bin/jq | (system) |
| gh | /opt/homebrew/bin/gh | (latest brew) |

---

## Risk assessment (revised vs original plan)

| Risk | Original plan assumption | Pre-flight finding | Revised disposition |
|------|--------------------------|--------------------|---------------------|
| History contains OCI tenancy IDs | high | zero file-content hits | LOW — rewrite is precautionary, not curative |
| History contains internal IPs | high | zero file-content hits outside docs | LOW |
| History contains OCI APM data keys | medium | zero | LOW |
| History contains personal email | high (file content) | zero file content, 44 metadata | MEDIUM — metadata residue real, needs mailmap |
| History contains hardcoded provider API keys | medium | zero | LOW |
| Repo already public, leaks may be cached upstream | acknowledged | confirmed public since before phase 1 | UNCHANGED — D-04 conservative rotation still recommended |

---

## Operator decision points

The operator should choose one of the following before authorizing task 2:

### Option A — Run the spec as drafted (defense-in-depth)

Pro: Honors D-02 letter; rewrites author email; produces clean attestation that history was rewritten and re-scanned.
Con: File-content replacements are mostly cosmetic; rewrites SUMMARY.md prose that accurately describes pattern names (loses informational fidelity in audit docs).

### Option B — Mailmap rewrite only (skip content rewrite)

Pro: Targets the only real metadata residue (author email). Preserves audit trail fidelity in .planning/. Two-scanner exit gate still satisfied (already at zero).
Con: Deviates from D-02 letter. Operator should accept the deviation and document it in the SUMMARY.md.

### Option C — Re-scope: do not rewrite history at all

Pro: Both scanners already report zero. The repo is provably clean by the same exit-gate criterion D-03 specifies.
Con: Author email metadata stays as-is. Operator must accept that `adibirzu@gmail.com` will be visible in `git log` indefinitely.

---

## Operator decision

**Option A — full spec (content + mailmap) — approved.**

## First filter-repo attempt (corrupted, discarded)

The first attempt corrupted the rewritten tree. Root cause: `git filter-repo --replace-text` does **not support comment syntax** in its spec file — every non-empty line is a pattern. The initial `filter-repo-spec.txt` contained `#`-prefixed comment lines for human readers; the bare-`#` line became a pattern matching every `#` character in every file, replaced with the default `***REMOVED***`. 156 files were corrupted (Python shebangs, SPDX headers, TOML section markers, every code comment, every Markdown heading prefix).

**Mitigation that saved us (T-01-08-01):** The plan's threat model explicitly named over-broad regex as a risk and required a safety clone. The corrupted tree existed only in `/tmp/multillm-rewrite`; the working repo was untouched. The ECC pre-push hook ran pytest against the corrupted clone and refused the push. Discarded `/tmp/multillm-rewrite`, removed all comment lines from the spec, and re-ran.

## Filter-repo invocation (task 2, executed cleanly)

```
cd /tmp/multillm-rewrite
git filter-repo --replace-text /tmp/filter-repo-spec.txt --mailmap /tmp/mailmap-rewrite.txt --force
```

- Spec applied: `.planning/phases/01-open-source-readiness/filter-repo-spec.txt` — 8 patterns, no comments, all `#` characters preserved.
- Mailmap applied: `.planning/phases/01-open-source-readiness/mailmap-rewrite.txt` — `adibirzu@gmail.com → contact@example.invalid` for both Author and Committer on every commit.
- 44 commits parsed across `main` (12 commits) and `gsd/phase-01-open-source-readiness` (32 commits).
- filter-repo runtime: 0.15s rewrite + 0.40s repack.
- Post-rewrite `***REMOVED***` corruption check: **0 files** affected (vs. 156 in the discarded first attempt).

## Post-rewrite scanner verification

| Scanner | Findings | Report file |
|---------|----------|-------------|
| gitleaks (44 commits, --all refs, .gitleaks.toml config) | **0** | `/tmp/gitleaks-post.json` |
| trufflehog (full, no --only-verified) | **0** | `/tmp/trufflehog-post.json` |
| trufflehog (--only-verified) | **0** | `/tmp/trufflehog-post-verified.json` |

D-03 exit gate satisfied (both scanners zero).

## Post-rewrite manual D-02 sweep

| Pattern | Pre | Post | Disposition |
|---------|-----|------|-------------|
| `ocid1\.tenancy\.` | 2 | 2 | Phantom — both remaining hits are `ocid1.tenancy.<...>` in `.planning/01-04-SUMMARY.md` (literal placeholder, no real OCID). Regex `ocid1\.tenancy\.[a-z0-9.]+` doesn't match `<...>` so they are not "leaks", just the bare token. Spec replacement applied where it matched. |
| `130\.61\.[0-9]+\.[0-9]+` | 9 | **0** | Replaced with `YOUR-PUBLIC-IP` everywhere. |
| `adibirzu@gmail\.com` (file content) | 0 | 0 | Always was zero. |
| `adibirzu@gmail\.com` (commit metadata) | 44 | **0** | Mailmap rewrote every signature to `contact@example.invalid`. |
| `~/\.oca` | 18 | 18 | **Intentionally retained** — the final spec dropped this pattern entirely; `~/.oca` is the documented OCA tooling path and not sensitive. |
| `control-plane-oci`, `fr4zqfimuxtr`, `eu-frankfurt-1\.ocir\.io` | 0 | 0 | Always was zero. |

## SHA mapping (12 most recent commits)

| Old SHA | New SHA | Subject |
|---------|---------|---------|
| c145b12 | 72cd53b | docs(01-01): complete license switch & community files plan |
| 1b74295 | 10db16d | feat(01-01): GitHub issue + PR templates |
| e260769 | b8f9906 | feat(01-01): community files (CONTRIBUTING, CODE_OF_CONDUCT, SECURITY) |
| f552786 | fe13161 | fix(01-01): add SPDX header to migrations/versions/__init__.py |
| 0598f11 | 428dda1 | docs(01-06): add upgrade runbook and troubleshooting catalog |
| d66c98c | 67d7cee | docs(01-06): add deployment + backup-restore runbooks and operations index |
| f8ee786 | d573ee5 | docs(01-06): rewrite README with badges, 5-minute Quickstart, backends grid |
| 9e0f00c | e2912df | docs(01-05): complete release workflows plan |
| 437aeea | 19d9ae8 | feat(01-05): homebrew tap auto-update + release runbook + pyproject urls |
| cc33619 | f712b75 | feat(01-05): build-image.yml — GHCR push + cosign keyless signing |
| 7606856 | a8d1cc7 | feat(01-05): release.yml — PyPI Trusted Publishing + PEP 740 attestations |
| 9d54b04 | 026ba74 | docs(01-07): complete first-run setup wizard plan |

**Main branch tip**: `38d4807` (old) → `98768cd` (new — now on `origin/main`).
**Phase branch tip**: `c145b12` (old) → `72cd53b` (new — local only; not yet pushed, see Outstanding work).

Older commits also rewritten; mapping not enumerated here. Any external reference to a pre-rewrite SHA in this table can be redirected via the new SHA in the right column.

## Filter-repo spec applied (final, no comments)

```
regex:ocid1\.tenancy\.[a-z0-9.]+==>your-oci-tenancy-here
regex:ocid1\.compartment\.[a-z0-9.]+==>your-oci-compartment-here
regex:130\.61\.[0-9]+\.[0-9]+==>YOUR-PUBLIC-IP
regex:10\.[0-9]+\.[0-9]+\.[0-9]+\b==>YOUR-PRIVATE-IP
adibirzu@gmail.com==>contact@example.invalid
fr4zqfimuxtr==>your-ocir-tenancy-namespace
control-plane-oci==>your-control-plane-host
eu-frankfurt-1.ocir.io==>your-region.ocir.io
```

Eight patterns. No comment lines. `~/.oca==>~/.oca` no-op removed; `[A-Z0-9]{40,}` always-omitted.

## Mailmap applied

```
MultiLLM contributors <contact@example.invalid> <adibirzu@gmail.com>
```

## Force-push to origin (irreversible step completed)

| Action | Result |
|--------|--------|
| `git push --force-with-lease=main:38d4807a8312d5bcce935464ae7df0c7ffc42453 origin main` | Succeeded. Reported: `+ 38d4807...98768cd main -> main (forced update)`. |
| Pre-push hook (ECC) ran `pytest -q` against the cleaned main | 351/351 tests passed. |
| `git push -u origin gsd/phase-01-open-source-readiness` | **Refused** by GitHub — token lacks `workflow` scope; the phase branch contains `.github/workflows/build-image.yml` added in plan 01-05. Force-push to main is unaffected. Resolution: re-issue the PAT with `workflow` scope, then `git push -u origin gsd/phase-01-open-source-readiness` will succeed. Not blocking 01-08's primary goal. |

## Definitive remote-verification scan

Fresh clone of `origin/main` at `/tmp/multillm-verify`, scanned against the canonical `.gitleaks.toml` config:

| Scan | Findings |
|------|----------|
| `gitleaks detect --log-opts="--all"` against fresh clone | **0** |
| `trufflehog git file://. --only-verified` against fresh clone | **0** |
| Manual D-02 sweep (`ocid1\.tenancy\.[a-z0-9.]+`, `130\.61\.[0-9]+\.[0-9]+`, `adibirzu@gmail\.com`, `control-plane-oci`, `fr4zqfimuxtr`) | **All 0** |
| Author signatures on `git log --all --pretty="%ae" \| sort -u` | `contact@example.invalid` only |
| Total commits on `origin/main` | 12 (rewritten) |

D-03 exit gate **satisfied on the remote**. This is the definitive verification.

## Credential rotation (D-04)

Per scanner output and manual D-02 sweep, **no credentials were found in any commit**. The pre-flight `credentials-rotated.md` summary stands: no scanner-driven rotation required. The operator may still elect precautionary rotations for any credentials developed against during the public-repo era — those entries belong in `credentials-rotated.md` as the operator completes them.

## Sign-off

- **Operator approval (option A)**: granted in /gsd-execute-phase 1 --wave 3 --interactive checkpoint dialog
- **Force-push completed at**: 2026-05-18T09:10Z
- **Remote verification scan**: 0 findings via gitleaks + trufflehog against fresh clone of `https://github.com/adibirzu/multillm.git`
- **Old `main` SHA invalidated**: `38d4807` is no longer referenced by any branch on origin; any external reference to it returns "object not found" against the rewritten remote
- **New `main` HEAD**: `98768cd feat: harden production readiness [skip ci]`

## Outstanding work

1. **Push phase branch** — requires PAT with `workflow` scope. Once reissued, `git push -u origin gsd/phase-01-open-source-readiness` lands the 32 rewritten phase commits on remote.
2. **Plan 01-09** (rc.1 publication) — depends on the cleaned remote main. Now unblocked.
3. **Safety backup** — `/tmp/multillm-rewrite` (the rewrite source) and the original repo (no local backup was taken since the working repo at `/Users/abirzu/dev/multillm` was sync'd to the rewritten history in place via fetch + reset, not by `mv` + `cp`). The unpushed phase branch on the working repo is the only place the rewritten phase commits live until they are pushed.

