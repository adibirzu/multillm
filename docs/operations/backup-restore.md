# Backup & Restore

MultiLLM's only persistent state lives in SQLite under `MULTILLM_HOME` (defaults to `~/.multillm/`, or `/data` inside the Docker image). This page covers what to back up, how to do it safely with WAL-mode databases, how to restore, and how to recover from FTS5 corruption.

---

## What gets backed up

| Path                                  | Contents                                                                    |
| ------------------------------------- | --------------------------------------------------------------------------- |
| `$MULTILLM_HOME/multillm.db`          | Tracking, sessions, settings, admin users, system state                     |
| `$MULTILLM_HOME/memory.db`            | Cross-LLM shared memory + FTS5 virtual table                                |
| `$MULTILLM_HOME/backups/`             | Auto-snapshots written by `multillm migrate up` (see below)                 |
| `$MULTILLM_HOME/routes.json`          | Custom model-alias overrides (if you created any)                           |

Logs are stdout-only by default (`docker compose logs`, `journalctl -u multillm`); the gateway does not write log files. Skip them unless you've configured a log-shipper to disk.

---

## Automated pre-migration backups

`multillm migrate up` writes a snapshot of `multillm.db` to `$MULTILLM_HOME/backups/pre-<rev>-<unix-ms>.db` **before** any DDL runs. The runner aborts the migration if the backup fails (T-01-03-01). The backup directory is created with mode `0o700`.

This is automatic — every container restart and every `multillm migrate up` invocation. No operator action needed.

Retention is **not** managed; the directory grows unbounded. Add a cron / systemd-timer cleanup if you migrate frequently:

```bash
find /var/lib/multillm/backups -type f -name 'pre-*.db' -mtime +30 -delete
```

---

## Manual periodic backups

For disaster-recovery snapshots (not just pre-migration safety nets), use SQLite's `.backup` dotcommand. **Never use `cp`** on a live WAL-mode DB — it captures the main file but misses the WAL, producing a torn snapshot.

### Docker Compose

```bash
docker compose exec gateway sh -c '
  mkdir -p /data/backups
  sqlite3 /data/multillm.db ".backup /data/backups/multillm-$(date +%F-%H%M).db"
  sqlite3 /data/memory.db   ".backup /data/backups/memory-$(date +%F-%H%M).db"
'
```

### Bare-metal (systemd)

```bash
sudo -u multillm sqlite3 /var/lib/multillm/multillm.db \
  ".backup '/var/backups/multillm-$(date +%F).db'"
sudo -u multillm sqlite3 /var/lib/multillm/memory.db \
  ".backup '/var/backups/memory-$(date +%F).db'"
```

### Systemd timer recipe

`/etc/systemd/system/multillm-backup.service`:

```ini
[Unit]
Description=MultiLLM nightly backup
After=multillm.service

[Service]
Type=oneshot
User=multillm
ExecStart=/bin/sh -c 'mkdir -p /var/backups/multillm && sqlite3 /var/lib/multillm/multillm.db ".backup /var/backups/multillm/multillm-$(date +%%F).db" && sqlite3 /var/lib/multillm/memory.db ".backup /var/backups/multillm/memory-$(date +%%F).db"'
```

`/etc/systemd/system/multillm-backup.timer`:

```ini
[Unit]
Description=Nightly MultiLLM backup at 03:00

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable: `sudo systemctl enable --now multillm-backup.timer`.

---

## Restoring from a backup

The gateway holds long-lived SQLite connections. Restoring **requires** the gateway to be stopped first; otherwise the live connection will overwrite your restore.

### Procedure

1. **Stop the gateway.**

   ```bash
   docker compose down                  # Compose
   sudo systemctl stop multillm         # systemd
   kubectl scale deploy/multillm --replicas=0   # Kubernetes
   ```

2. **Remove stale WAL / journal sidecar files.** These belong to the killed process. SQLite recreates them on next open. If you skip this, the recovered DB may roll-back to the in-flight state instead of the backup.

   ```bash
   cd $MULTILLM_HOME
   rm -f multillm.db-wal multillm.db-shm multillm.db-journal
   rm -f memory.db-wal memory.db-shm memory.db-journal
   ```

3. **Copy the backup over the live DB.**

   ```bash
   cp $MULTILLM_HOME/backups/multillm-2026-05-17.db $MULTILLM_HOME/multillm.db
   cp $MULTILLM_HOME/backups/memory-2026-05-17.db   $MULTILLM_HOME/memory.db
   chown multillm:multillm $MULTILLM_HOME/multillm.db $MULTILLM_HOME/memory.db
   chmod 600 $MULTILLM_HOME/multillm.db $MULTILLM_HOME/memory.db
   ```

4. **Restart and verify.**

   ```bash
   docker compose up -d                 # or systemctl start / kubectl scale
   curl http://localhost:8080/health    # expect 200
   multillm migrate status              # confirm the head revision
   ```

---

## FTS5 corruption recovery

`memory.db` contains a virtual table (`memory_fts`) that mirrors the rows in `memory`. If the row count diverges, or FTS5 search returns empty for content you can see in the base table, the index is desynced.

Diagnosis:

```bash
sqlite3 $MULTILLM_HOME/memory.db \
  "SELECT 'memory:' || count(*) FROM memory;
   SELECT 'memory_fts:' || count(*) FROM memory_fts;"
```

Rebuild the FTS5 index:

```bash
# Stop the gateway first (same as restore step 1).
sqlite3 $MULTILLM_HOME/memory.db "INSERT INTO memory_fts(memory_fts) VALUES('rebuild');"
```

The `INSERT INTO ... VALUES('rebuild')` form is FTS5's canonical rebuild trigger — it drops and repopulates the auxiliary tables from the content table. The helper that wraps this lives in `multillm/migrations/fts5.py` for use from future migrations; the SQL above is the operator-facing equivalent.

Restart the gateway. The counts above should now match.

---

## Migration-time recovery

If `multillm migrate up` fails partway through (rare — alembic transactions are atomic per revision in SQLite, but a hostile process kill mid-transaction can still leave inconsistent state), the auto-backup is your safety net.

1. **Identify the pre-migration backup.**

   ```bash
   ls -lt $MULTILLM_HOME/backups/pre-*.db | head
   ```

   The most recent `pre-<rev>-<ts>.db` is the snapshot taken **before** the failing migration. The `<rev>` field tells you the target revision the migration was trying to reach.

2. **Stop the gateway**, **delete the WAL/journal sidecars** (see Restore step 2), **copy the backup over**, restart.

3. **Confirm the rollback.**

   ```bash
   multillm migrate status
   ```

   The head should be one revision behind the failed target.

4. **File an issue** with the alembic stack trace AND the failing revision ID. Migration failures in v1.0 are bugs we want to fix, not a normal operational mode.

---

## See also

- [deployment.md](deployment.md) — for `$MULTILLM_HOME` placement
- [upgrade.md](upgrade.md) — for the full upgrade-time backup workflow
- [troubleshooting.md](troubleshooting.md) — for the broader failure-mode catalog
