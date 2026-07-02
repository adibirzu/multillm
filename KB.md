# MultiLLM Gateway — Knowledge Base

Troubleshooting reference. Consult **only when an error occurs**. Add a new
entry (next KB number) after fixing any new error.

---

## KB-001 — OCI APM OTLP export returns 404 on every batch

**Component:** `multillm/tracking.py` (OpenTelemetry → OCI APM exporter)

**Symptom:** Gateway log spams every 30s:
```
[ERROR] opentelemetry.exporter.otlp.proto.http.metric_exporter — Failed to export metrics batch code: 404, reason: Not Found
[ERROR] opentelemetry.exporter.otlp.proto.http.trace_exporter — Failed to export span batch code: 404, reason: Not Found
```

**Root cause (two bugs):**
1. **Wrong host.** The endpoint was derived as the *generic* regional host
   `https://apm-trace.<region>.oci.oraclecloud.com/...`. OCI APM ingests OTLP
   only on the **domain-specific data upload endpoint**, which has a unique
   prefix and uses `apm-agt`, e.g.
   `https://<unique-prefix>.apm-agt.<region>.oci.oraclecloud.com`. The generic
   host resolves but has no per-domain ingestion paths → 404.
2. **Wrong signal paths.** Traces were posted to the bare `/opentelemetry/`
   base, and metrics were rewritten to a non-existent `/opentelemetry/metrics/`.
   The correct OTLP paths are:
   - traces:  `/20200101/opentelemetry/{private|public}/v1/traces`
   - metrics: `/20200101/opentelemetry/v1/metrics`

**Fix:**
- Set `OCI_APM_DATA_UPLOAD_ENDPOINT` to the domain's data upload endpoint. Get it
  with:
  ```
  oci apm-control-plane apm-domain get --apm-domain-id <APM_DOMAIN_OCID> \
      --query 'data."data-upload-endpoint"' --raw-output
  ```
- `config._derive_apm_otlp_base()` builds `<upload>/20200101/opentelemetry/`.
- `tracking._oci_apm_signal_endpoint()` appends the correct per-signal path
  (`private/v1/traces`, `v1/metrics`); `OCI_APM_DATA_KEY_TYPE` selects
  private/public.
- `OCI_APM_METRICS_ENABLED` defaults **false**: many APM domains accept OTLP
  traces but not metrics. Traces (spans with token/cost attributes) always flow.

**Prevention:** Never assume the regional `apm-trace.<region>` host works — always
configure the per-domain data upload endpoint. Auth header is
`Authorization: dataKey <key>`.

**Verified:** A real `/v1/messages` call produced a tracked span (rid logged with
token counts) and **0 export 404s** afterward.

---

## KB-002 — Podman machine reports started but immediately remains stopped

**Component:** Local Podman runtime on macOS (Apple Hypervisor)

**Symptom:** `podman machine start podman-machine-default` reports success, but
`podman machine inspect` still reports `State: "stopped"` and a zero `LastUp`.
Commands that require the Podman API (for example `podman ps` or Compose) then
hang rather than connecting.

**Causes observed:**

1. Podman's Go SSH parser rejected two legacy two-field `known_hosts` entries:
   each retained a hostname and Ed25519 key blob but omitted the required
   `ssh-ed25519` algorithm field.
2. A sandboxed `podman machine start` could not create the VM lock under
   `~/.config/containers` (`operation not permitted`).
3. An approved debug startup proved the VM itself boots, reaches systemd,
   starts SSH and the Podman socket, and receives its readiness acknowledgement.
   The managed command runner then reaps the Apple `vfkit` child when the
   command scope closes, so a later state read reports `stopped`.

**Repair performed:** Backed up the original file as
`~/.ssh/known_hosts.multillm-backup-20260702`, then inserted the missing
algorithm token on only lines 423 and 425. `podman system connection list` now
parses successfully. No hostnames, key material, VM disks, images, or volumes
were removed.

**Safe recovery:** Start the Podman VM from a persistent host terminal or a
macOS launch service, not a short-lived sandbox command. Confirm
`podman machine inspect podman-machine-default --format '{{.State}}'` returns
`running` before Compose. Do not recreate the machine without inventorying its
images and volumes. Live local-CLI model evaluation still belongs in a normal
host gateway because the container does not automatically inherit authenticated
Claude/Codex/Gemini/Antigravity runtimes.

---

## KB-003 — Tenant-scoped scan reporting, audit export, and management data

**Component:** `multillm/orchestration_store.py` and gateway scan-report APIs

**Need:** Other projects must be able to submit scanner findings, drill into
their own reports, and export audit-ready data without exposing another
tenant's reports or raw LLM prompts.

**Implementation:** Added append-oriented scan reports and findings to the
existing tenant-scoped SQLite store. The gateway exposes:

- `POST /api/scan-reports` for bounded report ingestion (up to 1,000 findings)
- `GET /api/scan-reports`, `/summary`, and `/{report_id}` for management views
- `GET /api/scan-reports/export?format=json|csv` for audit/reporting exports

All reads and writes derive the tenant from `X-MultiLLM-Tenant`, validate the
report structure at the boundary, and apply tenant predicates in every query.
The report model intentionally stores structured scanner findings only; it does
not add raw prompt or answer persistence.

**Verified:** `uv run pytest tests/test_scan_reports.py tests/test_scan_report_api.py -q`
passes (3 tests), including cross-tenant lookup/export isolation.

---

## KB-004 — Codex Fusion requires inline prompts and a host-permission gateway

**Component:** `multillm/adapters/codex_cli.py`

**Finding:** Direct `codex exec` with GPT-5.6 succeeds, but the gateway adapter
failed when it used the stdin sentinel (`-`) and forwarded unsupported verbosity
labels. The adapter now passes the prompt as Codex's inline positional argument
and maps `concise`/`balanced`/`detailed` to `low`/`medium`/`high`.

**Verified:** Focused Codex adapter tests pass. A gateway started inside the
sandbox still cannot initialize Codex's local app-server (`Operation not
permitted`); run the gateway itself with host permission for GPT-5.6 Fusion.

**Observability:** `langfuse==4.12.0` is installed in `.venv` and included in
the runtime image build through the `langfuse` project extra. Evaluation-run
telemetry is aggregate-only and excludes prompts, answers, cases, rationales,
reviewer identifiers, and clear-text tenant names.

---

## KB-005 — `uv run` stalls on a stale synchronization cache lock

**Component:** Local test execution

**Symptom:** `uv run pytest ...` stalls before pytest emits any collection or
test output, including with `--no-sync`.

**Cause:** A stale lock in the temporary `uv` cache was left by cancelled
dependency/synchronization commands. The application tests were not deadlocked.

**Recovery:** Invoke the project virtual environment's test runner directly:
`./.venv/bin/pytest <targets> -q`. This bypasses `uv` synchronization entirely.

**Verified:** The Codex execution-control suite passes (15 tests), and the
MoA/Fusion gateway suite passes (86 passed; one explicit live-DeepEval skip).

---

## KB-006 — Team-usage tests must not use an expired fixed reporting day

**Component:** `tests/test_team_usage.py`

**Symptom:** Six team-usage aggregation and API tests reported zero totals when
run after the fixed fixture date aged out of the 720-hour query window.

**Cause:** The tests used `2026-05-30` as an input day while production correctly
filters reports to the requested recent window.

**Fix:** Recent-window fixtures now use the current UTC day. Historical-date
parsing tests keep their fixed dates because they do not query a rolling window.

**Verified:** `./.venv/bin/pytest -q` → 644 passed, 2 intentional skips.

---

## KB-007 — DeepEval MoA comparison entry point restored

**Component:** `evals/deepeval/test_gateway_model_comparison.py`

**Issue:** The DeepEval README referenced a comparison test that was absent.

**Fix:** Added an opt-in live test that discovers live aliases, sends one
identical case prompt to each, retains only usable responses, and invokes the
Mixture of Agents synthesis last with those aliases. It skips unless
`DEEPEVAL_E2E=1`, preventing unapproved model calls during normal CI.

**Verified:** The new test imports and skips correctly without live opt-in.

---

## KB-008 — Durable same-prompt model and layered MoA evaluation

**Component:** `multillm/evaluation/`, `/api/evaluations/*`, and `/evaluations`

**Need:** Compare identical prompts across every configured standalone model
and canonical layered MoA, prove any advantage statistically, retain evidence
for audit/management reporting, and let other projects consume it over APIs.

**Implementation:** Added immutable tenant-scoped suites, a 40-case owned
FinOps suite, durable leased jobs, explicit fixture/replay/live-host contracts,
target-bound execution preflight, encrypted outputs/judgments/reviews, dual
independent A/B+B/A judges, blinded human calibration, tie-aware bootstrap
intervals, exact sign tests with Holm correction, repeat reliability, and
JSON/CSV/HTML exports. The D3 workspace provides five linked views, an
accessible table, case details, URL state, and a blinded review widget.

**MoA correction:** `moa/*`, `/api/moa`, and MCP `llm_moa` are canonical
layered MoA. `auto` is cheap-first adaptive routing, while Fusion names remain
compatibility surfaces. MoA participants are carried into evaluation responses
so a proposer/refiner/aggregator cannot judge its own output. MoA also records
actual end-to-end critical-path milliseconds and per-stage input/output tokens
for the visualization and audit bundle.

**Runbook:** See `docs/evaluations.md`. Live evaluation is disabled until the
operator sets `MULTILLM_EVAL_ALLOW_LIVE_HOST=true`, and every live target must
then pass the exact marker probe. There is no fixture or sandbox fallback.

**Verified:** Evaluation/MoA focused suite: 91 passed. New-surface coverage:
85.93%. Full repository suite after all repairs: 717 passed, 2 intentional
skips (live DeepEval opt-in and absent optional OCI SDK).
A running smoke gateway completed a 40-case/3-target fixture matrix (120
outputs); health, workspace, paginated result API, and JSON/CSV/HTML exports all
returned HTTP 200.

---

## KB-009 — Repeated metrics were overwritten by attempt one

**Component:** `evaluation_metrics` storage and migration

**Symptom:** A run with `repeats > 1` retained only the last deterministic
metric for each case/target/metric, making attempt reliability unauditable.

**Cause:** The metric identity and unique constraint omitted `attempt`.

**Fix:** Migration `0006_evaluation_metric_attempts` adds the attempt dimension
and replaces the uniqueness key with
`(tenant, run, case, target, attempt, metric)`. Direct-store initialization
also upgrades pre-migration local tables without dropping retained metrics.
Run summaries now report attempt pass rate, `pass@k`, and `pass^k`.

---

## KB-010 — Optional dependency install appeared stalled

**Component:** `uv pip install` through the managed command runner

**Symptom:** Langfuse installation emitted no output for multiple 10-second
windows and appeared hung.

**Cause:** The outer tool yielded before `uv`'s non-verbose progress was
available; an offline diagnostic confirmed the wheel was not cached. This was
not a resolver deadlock.

**Recovery:** Use a bounded verbose, no-progress/no-cache invocation so network
and install phases are visible:

```bash
UV_NO_PROGRESS=1 uv pip install --python .venv/bin/python --no-cache -v 'langfuse==4.12.0'
```

**Verified:** `uv pip show --python .venv/bin/python --no-cache langfuse`
reports version 4.12.0.

---

## KB-011 — APFS reserve prevents tests despite reported free bytes

**Component:** Local development filesystem

**Symptom:** `df` reports roughly 100–200 MiB available, but `mktemp` and pytest
fail with `No space left on device` / `No usable temporary directory`.

**Cause:** The APFS data volume is at 100% and has exhausted its practical
metadata/working reserve. Podman first boot and dependency extraction expose the
failure. Open processes also retain several gigabytes of already-deleted files;
do not kill unrelated user services merely to reclaim them.

**Safe recovery:** Remove regenerable project caches first (`.mypy_cache`,
`.pytest_cache`, `.ruff_cache`), then clear the regenerable global uv cache with
`uv cache clean` if approved. Do not delete virtual environments, Podman VM
disks, source, or user data without an explicit inventory. Re-run `mktemp`
before tests; a successful temp file is the actual readiness check.

---

## KB-012 — D3 evaluation workspace must work offline

**Component:** `multillm/static/evaluations.html` and static vendor assets

**Issue:** A CDN-loaded visualization breaks air-gapped audit deployments and
weakens content-security policy.

**Fix:** Vendored D3 7.9.0 with ISC license/provenance and a pinned SHA-256 in
`multillm/static/vendor/README.md`. Static routing uses a strict asset
allowlist; the page contains no runtime CDN URLs. Unknown cost/latency remains
unknown rather than being plotted as zero, and select labels are created with
DOM `textContent` rather than interpolated HTML.

---

## KB-013 — Gateway teardown stalls after Langfuse is installed

**Component:** `multillm/langfuse_integration.py`

**Symptom:** Repeated TestClient lifecycles or gateway shutdown wait forever in
Langfuse `resource_manager.flush()`. The full suite stops around 90% even though
individual request assertions pass.

**Cause:** MultiLLM called SDK `flush()` and then `shutdown()` synchronously.
Langfuse `shutdown()` already flushes; when the collector is unavailable, the
first queue join can block and prevent the application lifespan from closing.

**Fix:** Detach the global client immediately and invoke the SDK's single
`shutdown()` call in a daemon thread. Wait at most
`MULTILLM_LANGFUSE_SHUTDOWN_TIMEOUT_SECONDS` (default 2, bounded 0–30), log a
warning, and continue gateway exit. Telemetry failure cannot hold the service
or test harness hostage.

**Verified:** The isolated lifecycle/SQL-injection group completes (32 passed),
and the complete suite finishes: 717 passed, 2 intentional skips.
