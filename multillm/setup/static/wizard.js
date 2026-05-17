// SPDX-License-Identifier: Apache-2.0
// MultiLLM first-run wizard — progressive enhancement, no frameworks.

(function () {
  "use strict";

  const panes = Array.from(document.querySelectorAll(".pane"));
  const steppers = Array.from(document.querySelectorAll(".stepper li"));

  function showPane(n) {
    panes.forEach((p) => p.classList.remove("pane--active"));
    const target = panes.find((p) => p.dataset.pane === String(n));
    if (target) target.classList.add("pane--active");
    steppers.forEach((s) => s.removeAttribute("aria-current"));
    const step = steppers.find((s) => s.dataset.step === String(n));
    if (step) step.setAttribute("aria-current", "step");
  }

  function setStatus(name, text, level) {
    const el = document.querySelector('.status[data-status="' + name + '"]');
    if (!el) return;
    el.textContent = text || "";
    if (level) el.dataset.level = level;
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await r.json(); } catch (_e) { /* non-JSON response */ }
    return { ok: r.ok, status: r.status, data: data };
  }

  // ── Pane 1: admin ──────────────────────────────────────────────────────────
  const formAdmin = document.getElementById("form-admin");
  if (formAdmin) {
    formAdmin.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(formAdmin);
      const email = (fd.get("email") || "").toString();
      const password = (fd.get("password") || "").toString();
      setStatus("admin", "Creating admin...", "info");
      const r = await postJSON("/setup/admin", { email, password });
      if (r.ok && r.data && r.data.state === "admin_created") {
        setStatus("admin", "Admin created.", "ok");
        showPane(2);
      } else {
        const msg = r.data && r.data.error ? r.data.error : "Request failed (" + r.status + ")";
        setStatus("admin", msg, "error");
      }
    });
  }

  // ── Pane 2: backends ───────────────────────────────────────────────────────
  const formBackends = document.getElementById("form-backends");
  if (formBackends) {
    formBackends.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(formBackends);
      const body = {};
      fd.forEach((v, k) => { body[k] = (v || "").toString(); });
      setStatus("backends", "Saving...", "info");
      const r = await postJSON("/setup/backends", body);
      if (r.ok) {
        const count = (r.data && r.data.configured ? r.data.configured.length : 0);
        setStatus("backends", count + " backend(s) saved.", "ok");
        showPane(3);
        runProbe();
      } else {
        setStatus("backends", "Save failed (" + r.status + ")", "error");
      }
    });
  }

  // ── Pane 3: local probe ────────────────────────────────────────────────────
  async function runProbe() {
    const results = document.getElementById("probe-results");
    if (!results) return;
    results.textContent = "Probing...";
    let data = {};
    try {
      const r = await fetch("/setup/probe-local");
      data = await r.json();
    } catch (_e) {
      results.textContent = "Probe failed.";
      return;
    }
    const cards = Object.keys(data).map((name) => {
      const entry = data[name] || {};
      const reachable = !!entry.reachable;
      const models = (entry.models || []).slice(0, 3).join(", ");
      const label = reachable ? "available" : "not found";
      const detail = models ? "<small>" + escapeHtml(models) + "</small>" : "";
      return (
        '<article class="probe-card">' +
          '<h3>' + escapeHtml(name) + '</h3>' +
          '<span class="badge" data-reachable="' + reachable + '">' + label + '</span>' +
          detail +
        '</article>'
      );
    }).join("");
    results.innerHTML = cards || "<p>No backends detected.</p>";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  const probeRerun = document.getElementById("probe-rerun");
  if (probeRerun) probeRerun.addEventListener("click", runProbe);

  const probeContinue = document.getElementById("probe-continue");
  if (probeContinue) probeContinue.addEventListener("click", () => showPane(4));

  // ── Pane 4: observability + complete ───────────────────────────────────────
  const formObs = document.getElementById("form-observability");
  if (formObs) {
    formObs.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(formObs);
      const body = {
        prometheus_enabled: fd.get("prometheus_enabled") === "on",
        otel_endpoint: (fd.get("otel_endpoint") || "").toString(),
      };
      setStatus("observability", "Saving...", "info");
      const rObs = await postJSON("/setup/observability", body);
      if (!rObs.ok) {
        setStatus("observability", "Save failed (" + rObs.status + ")", "error");
        return;
      }
      const rComplete = await postJSON("/setup/complete", {});
      if (rComplete.ok && rComplete.data && rComplete.data.status === "complete") {
        setStatus("observability", "Setup complete. Redirecting...", "ok");
        showPane(5);
        if (rComplete.data.redirect) {
          window.setTimeout(() => { window.location.href = rComplete.data.redirect; }, 1500);
        }
      } else {
        setStatus("observability", "Complete failed (" + rComplete.status + ")", "error");
      }
    });
  }

  // ── Initial pane from server-derived state ─────────────────────────────────
  const initial = document.body.dataset.state || "pending";
  const initialPane = ({
    "pending": 1,
    "admin_created": 2,
    "backends_configured": 3,
    "local_probed": 4,
    "observability_set": 4,
    "complete": 5,
  })[initial] || 1;
  showPane(initialPane);
})();
