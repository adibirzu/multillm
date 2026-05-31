# Troubleshooting

Symptom → diagnosis command → fix, for the failure modes a self-hosting operator hits in practice. Skim the symptom column first; the order isn't priority, it's just the order issues tend to surface.

Before pasting any log excerpt in a public issue, **scrub backend API keys, OAuth tokens, and any internal hostnames**. Anything secret-bearing should go through [SECURITY.md](../../SECURITY.md)'s private-disclosure channel instead.

---

## 1. `docker compose up` succeeds but `/health` returns connection-refused

**Symptom.** `docker compose ps` shows the container running, but `curl http://localhost:8080/health` returns `connection refused` or `empty reply from server`. The container's HEALTHCHECK reports `unhealthy` after 20–30 seconds.

**Diagnosis.**

```bash
docker compose logs gateway --tail=80
```

Look for:
- `ModuleNotFoundError` / `ImportError` → broken install (rare on a published image; common on a hand-built one).
- `KeyError` / `ValueError` raised during startup → missing required env var.
- Alembic stack trace → migration failed (see case 5 below).
- Bound to wrong interface (`Uvicorn running on http://127.0.0.1:8080`) → host inside the container, not reachable from the host network. `GATEWAY_HOST` must be `0.0.0.0` inside the container; the Dockerfile sets this but `.env` can override.

**Fix.**

Most often a missing env var. Diff against the canonical inventory:

```bash
diff <(grep -oE '^[A-Z_]+=' .env | sort -u) \
     <(grep -oE '^[A-Z_]+=' .env.example | sort -u)
```

Set whatever is missing in `.env`, then `docker compose up -d` to recreate.

If the gateway is healthy from inside the container (`docker compose exec gateway curl -fsS http://localhost:8080/health` succeeds) but not from the host, the published port mapping is wrong; check `ports:` in `docker-compose.yml`.

---

## 2. Wizard redirect loop (`/setup` keeps redirecting to `/setup`)

**Symptom.** The browser shows "redirecting too many times" (ERR_TOO_MANY_REDIRECTS) when opening `http://localhost:8080/setup`. Or every other route also redirects to `/setup` even after you finished the wizard.

**Diagnosis.**

```bash
sqlite3 $MULTILLM_HOME/multillm.db \
  "SELECT key, value FROM system WHERE key IN ('setup_complete');
   SELECT count(*) AS admin_count FROM admin_users;"
```

The state machine is:
- `setup_complete='0'` and `admin_count=0` → wizard must run (expected on fresh install).
- `setup_complete='1'` and `admin_count>=1` → setup done, no redirect (expected after wizard).
- Anything else → inconsistent state. The middleware will redirect-loop.

The middleware logic is in `multillm/setup/middleware.py` — only `/setup`, `/health`, and the wizard's static assets are excluded from the redirect.

**Fix.**

Inconsistent state usually means the DB was partially written from a previous gateway crash. Reset and redo:

```bash
docker compose down
multillm reset --confirm        # blanks setup_state, system.setup_complete, admin_users
docker compose up -d
open http://localhost:8080/setup
```

If you cannot run `multillm reset` (e.g., the gateway will not start at all), patch the row directly. Stop the gateway first to avoid racing the live connection:

```bash
sqlite3 $MULTILLM_HOME/multillm.db \
  "UPDATE system SET value='0' WHERE key='setup_complete';"
```

Then restart and redo the wizard.

---

## 3. Backend appears unreachable in `/api/health`

**Symptom.** `curl http://localhost:8080/api/health` reports a backend as `unreachable` or `degraded`. Requests to that backend return 502 / 504 / "no healthy backend".

**Diagnosis.** Probe the backend URL from *inside* the container — the gateway's network namespace, not the host's:

```bash
docker compose exec gateway curl -sSf <BACKEND_URL>/v1/models
```

For local backends (Ollama, LM Studio), the most common cause is the `localhost` trap: inside the container `localhost` is the container itself, not the host. The OS-specific fixes:

| Host OS                | Use this URL in `.env`                         |
| ---------------------- | ---------------------------------------------- |
| macOS / Windows        | `http://host.docker.internal:11434`            |
| Linux                  | `http://172.17.0.1:11434` (docker0 bridge), or run `--add-host=host.docker.internal:host-gateway` |

For cloud backends, check:
- API key is set in `.env` (not just `.env.example`).
- The key has not been revoked / expired (test it with `curl` directly against the vendor's API from outside the container).
- The vendor's status page (the gateway can't help if OpenAI / Anthropic itself is down).

**Fix.**

Update `.env` with the corrected URL or rotated key, then either `docker compose restart gateway` or — for a faster check — force a re-probe:

```bash
curl -X POST http://localhost:8080/api/health/check
```

---

## 4. Circuit breaker stuck open after the backend recovered

**Symptom.** The backend is healthy when probed directly, but the gateway still returns 503 / "circuit breaker open" for requests routed to it. `/api/health` shows `breaker_state: open`.

**Diagnosis.**

```bash
curl http://localhost:8080/api/health | jq '.backends[] | select(.breaker_state != "closed")'
```

The breaker transitions are: `closed → open` after 5 consecutive failures, `open → half-open` after 60 seconds, `half-open → closed` on the next successful probe (or back to `open` on failure). It is **designed** to lag the actual recovery — that's the point of a circuit breaker.

**Fix.**

Force an immediate health probe:

```bash
curl -X POST http://localhost:8080/api/health/check
```

If the backend is healthy, the breaker will transition through `half-open` to `closed` within the next probe cycle. If it does not, restart the gateway as a last resort:

```bash
docker compose restart gateway
```

If the breaker keeps re-opening, the backend is failing more than it appears; check the gateway logs for the specific upstream error (rate limit? auth? body-too-large?).

---

## 5. `multillm migrate up` fails midway

**Symptom.** Container startup logs (or `multillm migrate up` invocation) show an alembic exception. The container restarts in a loop, or the bare-metal service is in `failed` state.

**Diagnosis.**

```bash
# What revision is the DB stamped at?
multillm migrate status

# What pre-migration backup exists?
ls -lt $MULTILLM_HOME/backups/pre-*.db | head
```

The `pre-<rev>-<ts>.db` files are the auto-snapshots `multillm migrate up` takes *before* applying each revision. The most recent one is the safe restore point.

**Fix.**

1. Restore the pre-migration backup (full procedure in [backup-restore.md](backup-restore.md), section "Migration-time recovery").
2. Pin to the previous MultiLLM version so the restored DB matches the running image:

   ```bash
   # docker-compose.yml: image: ghcr.io/adibirzu/multillm:v1.0.0-rc.1
   docker compose up -d
   ```

3. File an issue with the alembic stack trace AND the failing revision ID. Migration failures are bugs we want to fix.

---

## 6. FTS5 search returns empty for known content

**Symptom.** You stored a memory via `/api/memory`, and `curl 'http://localhost:8080/api/memory/search?q=keyword'` returns an empty array — but `sqlite3 memory.db "SELECT * FROM memory LIMIT 5"` clearly shows the content.

**Diagnosis.**

```bash
sqlite3 $MULTILLM_HOME/memory.db \
  "SELECT 'base:' || count(*) FROM memory;
   SELECT 'fts:'  || count(*) FROM memory_fts;"
```

If `base:` and `fts:` disagree, the FTS5 virtual table is out of sync with the content table.

**Fix.**

Rebuild the FTS5 index. Full procedure in [backup-restore.md](backup-restore.md), section "FTS5 corruption recovery":

```bash
# Stop the gateway first.
sqlite3 $MULTILLM_HOME/memory.db "INSERT INTO memory_fts(memory_fts) VALUES('rebuild');"
# Restart the gateway.
```

---

## 7. Coverage gate fails on a PR

**Symptom.** GitHub Actions CI fails on the `test` job with `Coverage failure: total of XX.X is less than fail-under=80.0`.

**Diagnosis.**

```bash
pytest --cov=multillm --cov-report=term-missing | tail -40
```

The output lists each uncovered line. The gate is enforced per `OSS-08`; CI rejects PRs below 80%.

**Fix.**

Add tests for the lines reported as uncovered. Prefer behavioral tests against the public function — not line-coverage-padding mocks that exercise branches without checking behavior. If you genuinely cannot cover a branch (e.g., a defensive `except` for a `MemoryError` that the test environment can't reproduce), add `# pragma: no cover` on that specific line, with a comment explaining why.

---

## Reporting issues

For **security-sensitive** reports (auth bypass, secret exposure, RCE, privilege escalation): use [SECURITY.md](../../SECURITY.md)'s private-disclosure channel. Do **not** open a public issue.

For **non-security bugs**: use the bug-report issue template. Include:

- MultiLLM version (`multillm --version`)
- Deployment recipe (Compose / systemd / Kubernetes)
- Host OS and Docker version (`docker version | head`)
- Relevant log excerpt (scrubbed of backend keys, tokens, and any internal hostnames)
- The exact `curl` request that reproduces, if applicable

For **feature requests**: open a discussion before a PR. We want to talk through the design with you before you spend a weekend on it.

See also: [deployment.md](deployment.md), [backup-restore.md](backup-restore.md), [upgrade.md](upgrade.md).
