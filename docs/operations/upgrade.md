# Upgrading MultiLLM

The canonical upgrade workflow for moving from one MultiLLM version to the next, on any of the three deployment recipes ([Compose, systemd, Kubernetes](deployment.md)).

The flow is the same in all three: **backup → dry-run → upgrade → verify → rollback (if needed)**. The runtime-specific bits are the `pull` / `install` step in the middle.

---

## Step-by-step

### 1. Read the release notes

Open the GitHub Releases page for the target version. Look for:

- **BREAKING changes** (major version bump) — these always require operator action.
- **Schema changes** — listed under "Migrations" in the release notes. Each entry maps to one alembic revision under [`multillm/migrations/versions/`](../../multillm/migrations/versions/). Read the docstring at the top of each revision before proceeding if you are upgrading across more than one minor version.
- **Config changes** — new env vars added to `.env.example`. CI keeps the example in sync with `os.getenv()` lookups, so this is the authoritative diff source.

### 2. Backup

`multillm migrate up` writes an automatic pre-migration snapshot. That covers schema-change accidents but does **not** cover application-level data drift (e.g., a settings-table column repurposed across versions). For any upgrade, also take an explicit named snapshot:

Adaptive Fusion v2 adds revision `0004_adaptive_orchestration`, which creates
tenant-scoped run, call, feedback, and model-scorecard tables. Only prompt
hashes and derived features are retained by default; raw prompts, answers,
evidence, and reasoning summaries are not migrated or stored.

```bash
# Docker Compose
docker compose exec gateway sh -c '
  sqlite3 /data/multillm.db ".backup /data/backups/pre-upgrade-$(date +%F-%H%M).db"
  sqlite3 /data/memory.db   ".backup /data/backups/memory-pre-upgrade-$(date +%F-%H%M).db"
'

# Bare-metal
sudo -u multillm sqlite3 /var/lib/multillm/multillm.db \
  ".backup '/var/backups/multillm/pre-upgrade-$(date +%F-%H%M).db'"
sudo -u multillm sqlite3 /var/lib/multillm/memory.db \
  ".backup '/var/backups/multillm/memory-pre-upgrade-$(date +%F-%H%M).db'"
```

See [backup-restore.md](backup-restore.md) for the full procedure (and why `cp` on a WAL-mode DB is unsafe).

### 3. Dry-run the migration

Before pulling the new image, check what schema changes the target version will apply:

```bash
multillm migrate --dry-run
```

- **Empty output** → no pending revisions. Safe to upgrade with no schema concerns.
- **One or more revision IDs** → schema change incoming. Read each revision's docstring under `multillm/migrations/versions/<rev>_*.py` before continuing. The docstring lists the up/down operations and any operator-visible side effects.

For Docker Compose where the CLI lives inside the container, run the dry-run against the *new* image first by pulling it but NOT yet recreating:

```bash
docker compose pull
docker run --rm --env-file .env -v "${MULTILLM_HOME:-./.multillm}:/data" \
  multillm:local multillm migrate --dry-run
```

### 4. Pull / install the new version

```bash
# Docker Compose
docker compose pull
docker compose up -d            # Recreate with the new image. Volume persists.
                                # Entrypoint runs `multillm migrate up` automatically.

# Bare-metal pipx
sudo -u multillm pipx upgrade multillm
sudo systemctl restart multillm

# Kubernetes
kubectl set image deploy/multillm gateway=ghcr.io/${OWNER}/multillm:vX.Y.Z
kubectl rollout status deploy/multillm
```

For installations created by `install.sh`, update the checkout and repeat the
same component selection. Inspect the mutation-free plan first:

```bash
./install.sh --dry-run --component codex-mcp --component codex-skills
./install.sh --component codex-mcp --component codex-skills
```

The installer preserves `.env`, updates MCP registrations by name, and refreshes
skills in place. Start a fresh Codex thread after an MCP or skills upgrade. See
[Selective installation](../installation.md) for all components and removal.

### 5. Verify

After the new version starts, confirm three things:

```bash
# 1. Migration landed on the expected head
multillm migrate status
#    → expected: head revision matches the target

# 2. Liveness probe passes
curl -fsS http://localhost:8080/health
#    → expected: HTTP 200 with {"status":"ok"}

# 3. The gateway is processing real requests
curl -s http://localhost:8080/api/dashboard | jq '.totals.requests_last_hour'
#    → expected: >0 within a few minutes of opening traffic
```

If any of those three fail, treat it as a failed upgrade and proceed to rollback.

### 6. Rollback (if needed)

```bash
# 1. Stop the gateway
docker compose down                 # or systemctl stop multillm / kubectl scale --replicas=0

# 2. Restore from the pre-upgrade backup (see backup-restore.md for full steps)
cd $MULTILLM_HOME
rm -f multillm.db-wal multillm.db-shm multillm.db-journal
cp backups/pre-upgrade-2026-05-17-1432.db multillm.db

# 3. Pin to the old version
# Compose: edit docker-compose.yml `image:` to the old tag
# Bare-metal: pipx install 'multillm==1.0.0rc2'
# K8s: kubectl set image ... ghcr.io/${OWNER}/multillm:v1.0.0-rc.1

# 4. Restart and verify (same step 5 as above)
```

File a bug report with:
- The version you were upgrading from and to
- The migration revision that failed (from the alembic stack trace)
- The relevant log excerpt (scrub any backend API keys before pasting)

---

## Version-pinning policy

MultiLLM follows **Semantic Versioning** (SemVer).

| Bump  | Compatibility                                              | Migration required? |
| ----- | ---------------------------------------------------------- | ------------------- |
| Patch (1.0.x) | Bug fixes only. No schema changes. No config changes. | No                  |
| Minor (1.x.0) | Backward-compatible additions. Schema may add tables/columns; never remove or rename. New env vars are opt-in with safe defaults. | Yes (additive only) |
| Major (x.0.0) | Breaking changes allowed. Schema may rename or remove. Env vars may be removed. Release notes call out every break. | Yes (read notes)    |

Phase 1's first public release is **`v1.0.0-rc.1`**. Future v1.x.x patch releases do not require migrations. Phase 2b's tenant-auth schema lands as **v1.1.0** to signal the schema additions (`tenants`, `api_keys`, `quotas` tables). Phase 3 dashboard polish ships as **v1.2.0**.

Pre-release versions (`-rc.1`, `-rc.2`, …) may break compatibility without a major bump. Treat the `-rc` series as feedback-driven; pin to a specific RC if you want stability, and read the upgrade notes between RCs.

---

## See also

- [backup-restore.md](backup-restore.md) — the safety net under every step above
- [deployment.md](deployment.md) — the runtime-specific recipes
- [troubleshooting.md](troubleshooting.md) — what to do when verify fails
- [release.md](release.md) — the maintainer-side counterpart (how releases are cut)
