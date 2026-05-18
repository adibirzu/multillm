# Release Operations Runbook

> Operator-facing procedure for cutting a MultiLLM release.
> Audience: maintainers with push access to `${OWNER}/multillm` and the
> Homebrew tap repo.

This runbook covers the **three-channel publish** wired in Phase 1:

1. **PyPI** — via Trusted Publishing + PEP 740 Sigstore attestations.
2. **GHCR** — multi-arch image (linux/amd64 + linux/arm64), cosign-signed
   keyless against the runner's GitHub OIDC token.
3. **Homebrew tap** — formula auto-patched from the PyPI sdist sha256.

All three channels are triggered by a single tag push (`v[0-9]+.[0-9]+.[0-9]+`
or `v[0-9]+.[0-9]+.[0-9]+-rc.[0-9]+`).

---

## Prerequisites

These are **one-time** setup steps. Do **not** attempt to push a release tag
until all four are confirmed green.

### 1. PyPI Trusted Publishing (D-10)

Operator must configure Trusted Publishing in the PyPI project settings
**before the first tag push** — without it, the `publish-pypi` job fails
with `403 invalid-publisher`.

1. Sign in to https://pypi.org as the project owner.
2. Either (a) for an existing `multillm` project: open *Manage → Publishing*
   → *Add a new publisher*, or (b) for a brand-new project: register
   `multillm` first under *Your projects → Register*.
3. Add a **GitHub** publisher with:

   | Field | Value |
   |---|---|
   | Owner | `${OWNER}` (e.g. `adibirzu`) |
   | Repository name | `multillm` |
   | Workflow filename | `release.yml` |
   | Environment name | `pypi-publish` |

4. The corresponding GitHub Environment also needs to exist on the repo side:
   *Settings → Environments → New environment* → name `pypi-publish`.
   No required reviewers, no protection rules for v1.0 — Trusted Publishing
   is already the security boundary. (Optionally add a "tag-only" deployment
   branch rule later.)

PEP 740 attestations are automatically produced by
`pypa/gh-action-pypi-publish` v1.10+ when `attestations: true` is set
(default) **and** the workflow has `id-token: write` **and**
`attestations: write` — both already declared on the `publish-pypi` job in
`.github/workflows/release.yml`.

### 2. GHCR package visibility

GHCR packages default to *Private*. After the first successful image push
(see `build-image.yml`), flip the package to *Public*:

1. GitHub → your-org-or-user → *Packages* → `multillm`.
2. *Package settings → Change visibility → Public*.
3. *Manage Actions access* → ensure the source repo has `Write` role.

### 3. Homebrew tap repo (D-11)

Create the tap repo. Default name is `${OWNER}/homebrew-multillm`
(D-11 / D-16). The repo only needs a `Formula/` directory at root and a
minimal README; `homebrew.yml` populates `Formula/multillm.rb`.

After the tap repo exists, mint a **fine-grained Personal Access Token**
scoped to **only the tap repo**, with `contents:write` + `pull-requests:write`.
Save it as repo secret `HOMEBREW_TAP_TOKEN` on the `multillm` repo
(*Settings → Secrets and variables → Actions → New repository secret*).

This is the **only long-lived secret** in the release pipeline. Rotate
quarterly or on any contributor turnover — see [Secret rotation](#secret-rotation) below.

### 4. SHA-pin resolution (OSS-13)

Before pushing the first real tag, Plan 9 Task 1 resolves every
`PIN_*_SHA` placeholder in `.github/workflows/release.yml`,
`.github/workflows/build-image.yml`, and `.github/workflows/homebrew.yml`
to a concrete 40-char SHA. CI will refuse to load the workflows while
placeholders remain — by design.

---

## Tag-and-release procedure

Once the prerequisites are confirmed:

```bash
# 1. Lock in the version in pyproject.toml on main (PR + merge).
#    Example: bump from 0.6.x → 1.0.0-rc.1.

# 2. From a clean main checkout:
git fetch --tags
git checkout main
git pull --ff-only

# 3. Cut and push the tag (signed; gpg or ssh signing per local config).
git tag -as v1.0.0-rc.1 -m "Release v1.0.0-rc.1"
git push origin v1.0.0-rc.1
```

The tag push fires the three workflows in this order:

```
release.yml      ───► build + publish-pypi + github-release
                          │
                          ▼ (workflow_run completion event)
homebrew.yml     ───► poll PyPI → patch formula → PR to tap repo

build-image.yml  ───► build + push GHCR (parallel to release.yml; both
                       triggered by the same tag push)
```

Watch the runs:

```bash
# All three workflows for this tag:
gh run list --workflow=release.yml      --branch v1.0.0-rc.1
gh run list --workflow=build-image.yml  --branch v1.0.0-rc.1
gh run list --workflow=homebrew.yml

# Stream a specific run:
gh run watch <run-id>
```

---

## Verification

Run all three checks before announcing the release.

### Verify PyPI artifact + PEP 740 attestations

```bash
# Download and inspect the artifacts:
pip download multillm-gateway==1.0.0rc1 --no-deps -d /tmp/multillm-verify
ls /tmp/multillm-verify

# List PEP 740 Sigstore attestations attached to the artifact:
python -m pip install pypi-attestations
pypi-attestations inspect /tmp/multillm-verify/multillm_gateway-1.0.0rc1.tar.gz

# OR, with the official `pypi attest` plugin once available on the
# end-user machine:
#   pypi attest verify multillm-1.0.0rc1.tar.gz
```

Expected: at least one Sigstore-backed attestation tied to
`https://github.com/${OWNER}/multillm/.github/workflows/release.yml@refs/tags/v1.0.0-rc.1`.

### Verify GHCR image signature

```bash
# Pull (optional, to inspect locally):
docker pull ghcr.io/${OWNER}/multillm:v1.0.0-rc.1

# Verify cosign signature came from THIS workflow on THIS repo:
cosign verify \
  ghcr.io/${OWNER}/multillm:v1.0.0-rc.1 \
  --certificate-identity-regexp "https://github.com/${OWNER}/multillm/\.github/workflows/build-image\.yml@.+" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Expected: the verifier prints the cert chain, the Rekor log entry URL, and
exits 0. **Do not announce the release if this check fails** — the image
may be impersonated.

The same command, with `${OWNER}` substituted, ships in the public README
so any end user can replicate this check.

### Verify Homebrew formula install

The `homebrew.yml` workflow opens a PR on the tap repo (PR mode is the
default — direct push is not used). Review and merge the PR, then:

```bash
brew tap ${OWNER}/multillm
brew install multillm
multillm --help
```

Expected: `multillm` CLI prints its help banner. The formula installs
via the PyPI wheel as the source of truth (D-11).

---

## Rollback playbook

### PyPI

PyPI does **not** support deletion of a released version. Use the *yank*
mechanism, which leaves the artifact downloadable for pinned installs but
hides it from `pip install multillm-gateway` (resolves to the next-newest version):

1. https://pypi.org/project/multillm-gateway/ → *Manage → Releases →
   ${VERSION} → Options → Yank*.
2. Provide a reason (free-text). Yanks are reversible.

### GHCR

Image tags are mutable; the underlying digest is immutable. To remove a
broken release:

1. *GitHub → Packages → multillm → versions → ${TAG} → Delete*.
2. **Important:** the cosign signature for the deleted digest remains in
   the Rekor transparency log — that is by design and auditable forever.
3. Re-cut the release from a fixed commit with a new tag (e.g.
   `v1.0.0-rc.1.post1`); never reuse a yanked / deleted version number.

### Homebrew tap

Revert the merged PR on the tap repo:

```bash
gh pr revert <merged-pr-number> --repo ${OWNER}/homebrew-multillm
```

Or, manually, restore `Formula/multillm.rb` to the previous version's content
and commit.

---

## Re-publishing under a different owner (D-16 transition)

When `${OWNER}` flips from `adibirzu` to an org (`multillm`-the-org), follow
this sequence to avoid orphaning the existing PyPI project:

1. Transfer the GitHub repo to the new org.
2. On PyPI: *Manage → Publishing*, **add** a new publisher row pointing
   at the new owner. Keep the old row for one release as a fallback.
3. Update `.github/workflows/release.yml` environment URL if needed
   (`https://pypi.org/p/multillm` is owner-agnostic — no change required).
4. Create a new Homebrew tap `${NEW_OWNER}/homebrew-multillm`, mint a new
   `HOMEBREW_TAP_TOKEN`, deprecate the old tap.
5. Cut a fresh release under the new owner. The cosign verify command
   end-users run changes (new `--certificate-identity-regexp`); document
   that change in the release notes.

---

## Secret rotation

The only long-lived secret in this pipeline is `HOMEBREW_TAP_TOKEN`.
Rotate on any of these events:

- Quarterly schedule (recurring calendar reminder).
- Maintainer departs the project.
- Suspicion of token compromise (see SECURITY.md for the disclosure flow).
- The tap repo changes ownership.

Procedure:

1. Generate a fresh fine-grained PAT scoped to the tap repo
   (`contents:write` + `pull-requests:write`).
2. Update *Settings → Secrets → Actions → HOMEBREW_TAP_TOKEN* on the
   `multillm` repo.
3. Delete the old PAT from your GitHub account's *Settings → Developer
   settings → Personal access tokens*.
4. Trigger the homebrew workflow once manually (`gh workflow run
   homebrew.yml -f version=$LATEST`) to confirm the new PAT works.

---

## References

- D-07 / D-08 / D-09 / D-10 / D-11: `.planning/phases/01-open-source-readiness/01-CONTEXT.md`
- PEP 740 — release-artifact attestations: https://peps.python.org/pep-0740/
- Sigstore: https://www.sigstore.dev/
- PyPI Trusted Publishing: https://docs.pypi.org/trusted-publishers/
- Cosign keyless: https://docs.sigstore.dev/cosign/signing/overview/
