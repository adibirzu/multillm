const d3 = window.d3;
if (!d3) throw new Error("Local D3 bundle failed to load");

const state = { runs: [], run: null, outputs: [], reviews: [], reviewError: "", target: "all", query: "", selected: null };
const $ = (selector) => document.querySelector(selector);
const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

function captureApiKey() {
  const params = new URLSearchParams(location.search);
  const supplied = params.get("api_key") || params.get("token");
  if (supplied) {
    sessionStorage.setItem("multillm_api_key", supplied);
    params.delete("api_key");
    params.delete("token");
    const query = params.toString();
    history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}`);
  }
  return sessionStorage.getItem("multillm_api_key") || localStorage.getItem("multillm_api_key") || "";
}

const apiKey = captureApiKey();

function finiteNumber(value) {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function replaceOptions(select, options) {
  select.replaceChildren();
  for (const { value, label } of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.append(option);
  }
}

function apiHeaders() {
  return apiKey ? { "X-API-Key": apiKey } : {};
}

async function fetchEnvelope(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...apiHeaders(), ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok || payload.success === false) throw new Error(payload?.error?.message || `HTTP ${response.status}`);
  return payload;
}

function setStatus(kind, text) {
  $("#statusDot").className = `dot ${kind}`;
  $("#statusText").textContent = text;
  $("#lastUpdated").textContent = ` · ${new Date().toLocaleTimeString()}`;
}

function syncURL() {
  const params = new URLSearchParams(location.search);
  state.run?.id ? params.set("run", state.run.id) : params.delete("run");
  state.target !== "all" ? params.set("target", state.target) : params.delete("target");
  state.query ? params.set("q", state.query) : params.delete("q");
  history.replaceState(null, "", `${location.pathname}?${params}`);
}

function filteredOutputs() {
  const needle = state.query.toLocaleLowerCase();
  return state.outputs.filter((item) =>
    (state.target === "all" || item.target === state.target) &&
    (!needle || `${item.caseId} ${item.target}`.toLocaleLowerCase().includes(needle))
  );
}

function targetSummaries(outputs) {
  return Array.from(d3.group(outputs, (item) => item.target), ([target, rows]) => {
    const metrics = (state.run?.metrics || []).filter((metric) => metric.target === target && typeof metric.value === "number");
    const latencies = rows.map((item) => finiteNumber(item.latency?.total_ms)).filter((value) => value != null);
    const costs = rows.map((item) => finiteNumber(item.cost?.normalized_usd)).filter((value) => value != null);
    return {
      target,
      outputs: rows.length,
      quality: metrics.length ? d3.mean(metrics, (metric) => metric.value) : null,
      latency: latencies.length ? d3.mean(latencies) : null,
      cost: costs.length ? d3.sum(costs) : null,
      tokens: d3.sum(rows, (item) => Number(item.usage?.input_tokens || 0) + Number(item.usage?.output_tokens || 0)),
    };
  });
}

function frame(selector, height = 300) {
  const svg = d3.select(selector);
  const width = Math.max(320, svg.node()?.clientWidth || 640);
  svg.attr("viewBox", `0 0 ${width} ${height}`).selectAll("*").remove();
  return { svg, width, height, margin: { top: 24, right: 28, bottom: 48, left: 70 } };
}

function empty(frameState, message) {
  frameState.svg.append("text").attr("class", "empty").attr("x", frameState.width / 2).attr("y", frameState.height / 2).attr("text-anchor", "middle").text(message);
}

function selectDatum(value) {
  state.selected = value;
  $("#inspectorSummary").textContent = value ? `${value.caseId || value.target || "Selection"}` : "No selection";
  $("#inspectorContent").textContent = value ? JSON.stringify(value, null, 2) : "";
  document.querySelectorAll(".selected-mark").forEach((node) => node.classList.remove("selected-mark"));
  if (value?.id) document.querySelectorAll(`[data-id="${CSS.escape(value.id)}"]`).forEach((node) => node.classList.add("selected-mark"));
}

export function renderWinMatrix() {
  const f = frame("#winMatrix", 300);
  const pairwise = state.run?.summary?.pairwise || [];
  if (!pairwise.length) return empty(f, "Pairwise judgments will appear after judging completes");
  const labels = Array.from(new Set(pairwise.flatMap((item) => [item.candidate, item.baseline])));
  const x = d3.scaleBand(labels, [f.margin.left, f.width - f.margin.right]).padding(.08);
  const y = d3.scaleBand(labels, [f.margin.top, f.height - f.margin.bottom]).padding(.08);
  const color = d3.scaleDiverging([0, .5, 1], d3.interpolateRdYlGn);
  f.svg.append("g").attr("transform", `translate(0,${f.height - f.margin.bottom})`).call(d3.axisBottom(x)).selectAll("text").attr("transform", "rotate(-25)").attr("text-anchor", "end");
  f.svg.append("g").attr("transform", `translate(${f.margin.left},0)`).call(d3.axisLeft(y));
  const cells = f.svg.append("g").selectAll("g").data(pairwise, (d) => `${d.candidate}:${d.baseline}`).join("g");
  cells.append("rect").attr("x", (d) => x(d.baseline)).attr("y", (d) => y(d.candidate)).attr("width", x.bandwidth()).attr("height", y.bandwidth()).attr("fill", (d) => color(d.winRate)).attr("stroke", "#09111b");
  cells.append("text").attr("x", (d) => x(d.baseline) + x.bandwidth() / 2).attr("y", (d) => y(d.candidate) + y.bandwidth() / 2).attr("dy", ".35em").attr("text-anchor", "middle").attr("fill", "#071018").text((d) => `${fmt.format(d.winRate * 100)}%`);
  $("#winMatrixDescription").textContent = `${pairwise.length} pairwise comparisons. Cells show tie-aware win rate; inspect the table/export for intervals and sample counts.`;
}

export function renderPareto() {
  const f = frame("#paretoChart", 300);
  const summaries = targetSummaries(filteredOutputs());
  const data = summaries.filter((item) => item.outputs && item.quality != null && item.cost != null && item.latency != null);
  if (!data.length) return empty(f, "Quality, cost, or latency is unavailable for this filter");
  const x = d3.scaleLinear().domain([0, d3.max(data, (d) => d.cost) || 1]).nice().range([f.margin.left, f.width - f.margin.right]);
  const y = d3.scaleLinear().domain([0, 1]).range([f.height - f.margin.bottom, f.margin.top]);
  const radius = d3.scaleSqrt().domain([0, d3.max(data, (d) => d.latency) || 1]).range([6, 18]);
  f.svg.append("g").attr("transform", `translate(0,${f.height - f.margin.bottom})`).call(d3.axisBottom(x).ticks(5)).append("text").attr("x", f.width - f.margin.right).attr("y", 38).attr("fill", "currentColor").attr("text-anchor", "end").text("Normalized cost (USD)");
  f.svg.append("g").attr("transform", `translate(${f.margin.left},0)`).call(d3.axisLeft(y).ticks(5)).append("text").attr("x", 0).attr("y", -10).attr("fill", "currentColor").attr("text-anchor", "start").text("Quality");
  const marks = f.svg.append("g").selectAll("g").data(data, (d) => d.target).join("g").attr("tabindex", 0).attr("role", "button").attr("aria-label", (d) => `${d.target}: quality ${fmt.format(d.quality ?? 0)}, cost ${fmt.format(d.cost)}, latency ${fmt.format(d.latency)} milliseconds`).on("click keydown", (event, d) => { if (event.type === "click" || event.key === "Enter") selectDatum(d); });
  marks.append("circle").attr("cx", (d) => x(d.cost)).attr("cy", (d) => y(d.quality ?? 0)).attr("r", (d) => radius(d.latency)).attr("fill", "#55d6be").attr("fill-opacity", .78).attr("stroke", "#09111b");
  marks.append("text").attr("x", (d) => x(d.cost)).attr("y", (d) => y(d.quality ?? 0) - radius(d.latency) - 4).attr("text-anchor", "middle").text((d) => d.target.replace(/^.*\//, ""));
}

export function renderSkillProfile() {
  const f = frame("#skillChart", 300);
  const metrics = (state.run?.metrics || []).filter((metric) => typeof metric.value === "number" && (state.target === "all" || metric.target === state.target));
  const data = Array.from(d3.rollup(metrics, (rows) => d3.mean(rows, (row) => row.value), (row) => row.metric), ([skill, value]) => ({ skill, value })).sort((a, b) => d3.descending(a.value, b.value)).slice(0, 8);
  if (!data.length) return empty(f, "Skill metrics will appear after grading completes");
  const y = d3.scaleBand(data.map((d) => d.skill), [f.margin.top, f.height - f.margin.bottom]).padding(.25);
  const x = d3.scaleLinear([0, 1], [f.margin.left, f.width - f.margin.right]);
  f.svg.append("g").attr("transform", `translate(${f.margin.left},0)`).call(d3.axisLeft(y));
  f.svg.append("g").attr("transform", `translate(0,${f.height - f.margin.bottom})`).call(d3.axisBottom(x).ticks(5, "%"));
  f.svg.append("g").selectAll("rect").data(data).join("rect").attr("x", x(0)).attr("y", (d) => y(d.skill)).attr("width", (d) => x(d.value) - x(0)).attr("height", y.bandwidth()).attr("fill", "#68a9ff");
  f.svg.append("g").selectAll("text").data(data).join("text").attr("x", (d) => x(d.value) + 5).attr("y", (d) => y(d.skill) + y.bandwidth() / 2).attr("dy", ".35em").text((d) => fmt.format(d.value));
}

export function renderTokenWaterfall() {
  const f = frame("#tokenWaterfall", 300);
  const outputs = filteredOutputs();
  const stages = outputs.flatMap((output) => output.usage?.stages || []);
  const stageSum = (name, field) => d3.sum(stages.filter((stage) => name(stage.stage || "")), (stage) => Number(stage[field] || 0));
  const categories = stages.length ? [
    { label: "Proposer input", value: stageSum((stage) => stage === "proposer", "input_tokens") },
    { label: "Proposer output", value: stageSum((stage) => stage === "proposer", "output_tokens") },
    { label: "Refinement input", value: stageSum((stage) => stage.startsWith("refiner_"), "input_tokens") },
    { label: "Refinement output", value: stageSum((stage) => stage.startsWith("refiner_"), "output_tokens") },
    { label: "Aggregator input", value: stageSum((stage) => stage === "aggregator", "input_tokens") },
    { label: "Final output", value: stageSum((stage) => stage === "aggregator", "output_tokens") },
  ] : [
    { label: "Input", value: d3.sum(outputs, (d) => Number(d.usage?.input_tokens || 0)) },
    { label: "Reasoning", value: d3.sum(outputs, (d) => Number(d.usage?.reasoning_tokens || 0)) },
    { label: "Cache read", value: d3.sum(outputs, (d) => Number(d.usage?.cache_read_tokens || 0)) },
    { label: "Output", value: d3.sum(outputs, (d) => Number(d.usage?.output_tokens || 0)) },
  ];
  if (!d3.sum(categories, (d) => d.value)) return empty(f, "No token measurements in this filter");
  let running = 0;
  const data = categories.map((item) => { const start = running; running += item.value; return { ...item, start, end: running }; });
  const x = d3.scaleBand(data.map((d) => d.label), [f.margin.left, f.width - f.margin.right]).padding(.25);
  const y = d3.scaleLinear([0, running], [f.height - f.margin.bottom, f.margin.top]);
  f.svg.append("g").attr("transform", `translate(0,${f.height - f.margin.bottom})`).call(d3.axisBottom(x));
  f.svg.append("g").attr("transform", `translate(${f.margin.left},0)`).call(d3.axisLeft(y).ticks(5));
  f.svg.selectAll("rect").data(data).join("rect").attr("x", (d) => x(d.label)).attr("y", (d) => y(d.end)).attr("width", x.bandwidth()).attr("height", (d) => y(d.start) - y(d.end)).attr("fill", (d, i) => ["#68a9ff", "#f2be5c", "#9dafc2", "#55d6be"][i]);
  f.svg.selectAll(".value").data(data).join("text").attr("class", "value").attr("x", (d) => x(d.label) + x.bandwidth() / 2).attr("y", (d) => y(d.end) - 5).attr("text-anchor", "middle").text((d) => fmt.format(d.value));
}

export function renderLatency() {
  const f = frame("#latencyChart", 300);
  const data = targetSummaries(filteredOutputs()).filter((item) => item.latency != null).sort((a, b) => d3.ascending(a.latency, b.latency));
  if (!data.length) return empty(f, "No latency measurements in this filter");
  const y = d3.scaleBand(data.map((d) => d.target), [f.margin.top, f.height - f.margin.bottom]).padding(.25);
  const x = d3.scaleLinear([0, d3.max(data, (d) => d.latency) || 1], [f.margin.left, f.width - f.margin.right]);
  f.svg.append("g").attr("transform", `translate(${f.margin.left},0)`).call(d3.axisLeft(y).tickFormat((d) => d.replace(/^.*\//, "")));
  f.svg.append("g").attr("transform", `translate(0,${f.height - f.margin.bottom})`).call(d3.axisBottom(x).ticks(5)).append("text").attr("x", f.width - f.margin.right).attr("y", 38).attr("fill", "currentColor").attr("text-anchor", "end").text("Mean total latency (ms)");
  f.svg.selectAll("rect").data(data).join("rect").attr("x", x(0)).attr("y", (d) => y(d.target)).attr("width", (d) => x(d.latency) - x(0)).attr("height", y.bandwidth()).attr("fill", "#f2be5c");
}

function renderTable() {
  const body = d3.select("#resultsTable tbody");
  const rows = body.selectAll("tr").data(filteredOutputs(), (d) => d.id).join("tr").attr("tabindex", 0).attr("data-id", (d) => d.id).on("click keydown", (event, d) => { if (event.type === "click" || event.key === "Enter") selectDatum(d); });
  rows.html("");
  rows.each(function(d) {
    const totalLatency = finiteNumber(d.latency?.total_ms);
    const values = [d.caseId, d.target, d.status, Number(d.usage?.input_tokens || 0) + Number(d.usage?.output_tokens || 0), totalLatency == null ? "Unavailable" : `${fmt.format(totalLatency)} ms`, d.latency?.ttft_ms == null ? (d.latency?.ttft_unavailable_reason || "Unavailable") : `${fmt.format(d.latency.ttft_ms)} ms`, d.cost?.normalized_usd == null ? "Unknown" : `$${fmt.format(d.cost.normalized_usd)}`];
    d3.select(this).selectAll("td").data(values).join("td").text((value) => value);
  });
}

export function renderReviewQueue() {
  const item = state.reviews[0];
  const card = $("#reviewCard");
  if (!item) {
    card.hidden = true;
    $("#reviewQueueStatus").textContent = state.reviewError
      ? `Review queue unavailable: ${state.reviewError}`
      : "No blinded comparisons require review for this run.";
    return;
  }
  card.hidden = false;
  $("#reviewQueueStatus").textContent = `${state.reviews.length} comparison${state.reviews.length === 1 ? "" : "s"} awaiting review · case ${item.caseId}`;
  $("#reviewResponseA").textContent = item.responseA || "(empty response)";
  $("#reviewResponseB").textContent = item.responseB || "(empty response)";
  $("#reviewSubmitStatus").textContent = "Model identities remain hidden until the decision is recorded.";
}

async function submitReview(decision) {
  const item = state.reviews[0];
  if (!item) return;
  const reviewer = $("#reviewerId").value.trim();
  const rationale = $("#reviewRationale").value.trim();
  if (!reviewer || !rationale) {
    $("#reviewSubmitStatus").textContent = "Reviewer ID and evidence-based rationale are required.";
    return;
  }
  const buttons = document.querySelectorAll("[data-review-decision]");
  buttons.forEach((button) => { button.disabled = true; });
  $("#reviewSubmitStatus").textContent = "Recording review…";
  try {
    await fetchEnvelope(`/api/evaluations/reviews/${encodeURIComponent(item.id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-MultiLLM-Reviewer": reviewer },
      body: JSON.stringify({ decision, rationale }),
    });
    state.reviews = state.reviews.slice(1);
    $("#reviewRationale").value = "";
    renderReviewQueue();
  } catch (error) {
    $("#reviewSubmitStatus").textContent = `Review was not saved: ${error.message}`;
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function renderKpis() {
  const outputs = filteredOutputs();
  const summary = state.run?.summary || {};
  const latencies = outputs.map((d) => finiteNumber(d.latency?.total_ms)).filter((value) => value != null).sort(d3.ascending);
  const costs = outputs.map((d) => finiteNumber(d.cost?.normalized_usd)).filter((value) => value != null);
  const winRates = (summary.pairwise || []).map((item) => finiteNumber(item.winRate)).filter((value) => value != null);
  $("#qualityKpi").textContent = winRates.length ? `${fmt.format(d3.mean(winRates) * 100)}%` : "—";
  $("#latencyKpi").textContent = latencies.length ? `${fmt.format(d3.quantileSorted(latencies, .95))} ms` : "—";
  $("#costKpi").textContent = costs.length ? `$${fmt.format(d3.sum(costs))} Known cost` : "Unknown";
  $("#reliabilityKpi").textContent = outputs.length ? `${outputs.filter((d) => d.status === "succeeded").length}/${outputs.length}` : "—";
  $("#gateKpi").textContent = summary.releaseGate || "Not evaluated";
}

function renderAll() {
  renderKpis(); renderWinMatrix(); renderPareto(); renderSkillProfile(); renderTokenWaterfall(); renderLatency(); renderTable(); renderReviewQueue();
}

async function loadRun(runId) {
  if (!runId) return;
  setStatus("", "Loading evaluation evidence…");
  const [runEnvelope, outputEnvelope, reviewEnvelope] = await Promise.all([
    fetchEnvelope(`/api/evaluations/runs/${encodeURIComponent(runId)}`),
    fetchEnvelope(`/api/evaluations/runs/${encodeURIComponent(runId)}/results`),
    fetchEnvelope(`/api/evaluations/reviews/queue?run_id=${encodeURIComponent(runId)}&limit=100`).catch((error) => ({ data: [], reviewError: error.message })),
  ]);
  state.run = runEnvelope.data;
  state.outputs = outputEnvelope.data;
  state.reviews = reviewEnvelope.data || [];
  state.reviewError = reviewEnvelope.reviewError || "";
  const targets = Array.from(new Set(state.outputs.map((item) => item.target))).sort();
  replaceOptions($("#targetFilter"), [
    { value: "all", label: "All targets" },
    ...targets.map((target) => ({ value: target, label: target })),
  ]);
  $("#targetFilter").value = targets.includes(state.target) ? state.target : "all";
  $("#exportButton").href = `/api/evaluations/runs/${encodeURIComponent(runId)}/export?format=html`;
  setStatus(state.run.status === "completed" ? "live" : "", `${state.run.status} · ${state.outputs.length} outputs · suite ${state.run.suiteId}`);
  syncURL(); renderAll();
}

async function loadRuns() {
  try {
    const envelope = await fetchEnvelope("/api/evaluations/runs?limit=100");
    state.runs = envelope.data;
    const select = $("#runSelect");
    replaceOptions(select, [
      { value: "", label: "Select a run" },
      ...state.runs.map((run) => ({ value: run.id, label: `${run.id} · ${run.status} · ${run.suiteId}` })),
    ]);
    const requested = new URLSearchParams(location.search).get("run");
    const runId = requested && state.runs.some((run) => run.id === requested) ? requested : state.runs[0]?.id;
    if (runId) { select.value = runId; await loadRun(runId); } else { setStatus("", "No evaluation runs yet. Create one through the API or CLI."); renderAll(); }
  } catch (error) { setStatus("error", `Evaluation data unavailable: ${error.message}`); renderAll(); }
}

function wireControls() {
  const params = new URLSearchParams(location.search);
  state.target = params.get("target") || "all"; state.query = params.get("q") || ""; $("#caseSearch").value = state.query;
  $("#runSelect").addEventListener("change", (event) => loadRun(event.target.value).catch((error) => setStatus("error", error.message)));
  $("#targetFilter").addEventListener("change", (event) => { state.target = event.target.value; syncURL(); renderAll(); });
  $("#caseSearch").addEventListener("input", (event) => { state.query = event.target.value.trim(); syncURL(); renderAll(); });
  $("#refreshButton").addEventListener("click", loadRuns);
  document.querySelectorAll("[data-review-decision]").forEach((button) => button.addEventListener("click", () => submitReview(button.dataset.reviewDecision)));
  $("#mobileFilters").addEventListener("click", () => { const controls = $("#evaluationControls"); const open = controls.classList.toggle("open"); $("#mobileFilters").setAttribute("aria-expanded", String(open)); });
  window.addEventListener("popstate", () => location.reload());
  new ResizeObserver(() => { if (state.run) renderAll(); }).observe(document.querySelector(".evidence"));
}

wireControls();
loadRuns();
