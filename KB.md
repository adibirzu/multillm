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
