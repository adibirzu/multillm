# Model and Mixture-of-Agents evaluation

MultiLLM evaluates the exact same prompt against standalone models and one or
more layered Mixture-of-Agents (MoA) variants. The system retains encrypted raw
artifacts for audit, reports quality together with latency/token/cost tradeoffs,
and refuses to label PATH discovery as a successful live test.

## What is implemented

- `finops-v1`: 40 project-owned cases covering FOCUS NLP, anomaly detection,
  allocation, forecasting, management reporting, security, and tool use.
- Immutable, hash-versioned suites and tenant-scoped durable jobs with leases,
  heartbeats, cancellation, retries, and idempotent outputs.
- Fixture, replay-contract, and explicitly authorized `live_host` modes. Live
  mode has no fixture or sandbox fallback.
- Standalone candidates plus `moa/economy`, `moa/balanced`, `moa/quality`, and
  `moa/critical` targets.
- Dual independent LLM judges. Every anonymous pair is graded in A/B and B/A
  order; inconsistent ordering, judge disagreement, unavailable judges, and
  self-judging become abstentions requiring human review.
- Blinded human calibration for every disputed pair and, on release runs, a
  deterministic sample of `max(30, 10%)` comparisons (bounded by the run size).
- Tie-aware prompt-level bootstrap confidence intervals, exact one-sided sign
  tests, and Holm-Bonferroni correction across pairwise release claims.
- Deterministic required/forbidden-term checks plus repeat reliability
  (`pass@k`, `pass^k`, and attempt pass rate).
- AES-256-GCM artifact encryption bound to tenant/run/case/target/attempt
  context. Raw outputs, judge rationales, and reviewer rationale are never
  written in plaintext.
- JSON audit bundles, flat multi-record CSV, and escaped standalone HTML
  management reports. The JSON manifest includes a SHA-256 digest.
- A local D3 workspace at `/evaluations` with a win matrix, Pareto view, skill
  profile, token waterfall, latency view, accessible equivalent table, case
  inspector, and blinded review widget. D3 is vendored; no runtime CDN is used.
- DeepEval 4 `GEval` integration through the gateway and aggregate-only
  Langfuse completion traces. Evaluation traces exclude prompts, answers, case
  IDs, rationales, reviewer IDs, and clear-text tenant names.

## Methodology

Use three profiles rather than treating one score as universal:

| Profile | Purpose | Recommended input |
|---|---|---|
| `ci` | Deterministic regression feedback | Fixture cases, one repeat |
| `nightly` | Drift, reliability, latency, and cost | Live or replay, 3 repeats |
| `release` | A defensible superiority claim | Full suite, live/replay, 3–5 repeats, dual judges, human calibration |

For each case, preserve the prompt bytes and execution controls across all
standalone candidates and MoA variants. Randomize presentation only inside the
judge layer. Compare MoA against every standalone baseline; never compare only
against the weakest model.

The release gate passes only when every MoA-versus-baseline comparison has a
tie-aware 95% lower confidence bound above 50% and a Holm-adjusted one-sided
sign-test p-value at or below 0.05. A CI/nightly run reports
`releaseGate=not_evaluated`; a release run without sufficient evidence reports
`not_demonstrated`. A release run stays `pending_human_review` until every
disagreement and sampled calibration pair has a recorded review; the gate is
recomputed after each submission. This is evidence of performance on the named suite and
execution snapshot, not a universal claim.

Track tradeoffs together:

- Quality: pairwise wins/losses/ties/abstentions, confidence intervals,
  deterministic task checks, DeepEval/FLASK-aligned skills, and human agreement.
- Reliability: successful attempts, failure classes, `pass@k`, and `pass^k`.
- Latency: total wall time and TTFT when a backend exposes it. Missing TTFT is
  named, never converted to zero.
- Tokens: prompt, reasoning, cache-read/write, proposer/refiner/aggregator, and
  final-output usage; watch context expansion and token amplification.
- Cost: actual cost when a provider reports it and separately versioned,
  normalized list-price estimates. Missing actual cost remains unknown.

### Industry benchmark adapters

`GET /api/evaluations/benchmarks` returns source, metric, license, and
download-on-demand metadata for AlpacaEval 2.0, MT-Bench, FLASK, and
Arena-Hard. The original MoA paper reported AlpacaEval 2.0, MT-Bench, and
FLASK; Arena-Hard is a useful supplemental stress set, not represented here as
an original-paper benchmark. MultiLLM does not silently redistribute benchmark
data. Import only data whose license you accept and verify its checksum before
creating an immutable suite.

## One-time setup

Install the locked optional evaluation and observability dependencies:

```bash
uv sync --extra eval --extra langfuse
```

Generate a dedicated 32-byte artifact key and keep it in a secret manager or
local untracked `.env` file:

```bash
openssl rand -base64 32
```

Set the resulting value as `MULTILLM_EVAL_ARTIFACT_KEY`. Losing or rotating
this key without a re-encryption procedure makes existing retained artifacts
unreadable.

For fixture-only evaluation, start the normal gateway. For local Claude,
Codex, Gemini, or Antigravity CLI calls, start it from a regular host terminal,
outside a parent coding-agent sandbox:

```bash
export MULTILLM_HOME="$PWD/.multillm"
export MULTILLM_EVAL_ARTIFACT_KEY='<BASE64_32_BYTE_KEY>'
export MULTILLM_EVAL_ALLOW_LIVE_HOST=true
export MULTILLM_EVAL_WORKER_ENABLED=true
.venv/bin/multillm serve
```

`MULTILLM_EVAL_ALLOW_LIVE_HOST=true` is an operator opt-in, not execution
proof. Each run still needs a short-lived, exact-target receipt from the marker
preflight. A container can serve the UI and fixture runs, but it cannot invoke
host CLI tools unless those binaries and their authenticated runtime are
deliberately made available. Prefer the host gateway for CLI-model evaluation.

## Run from the MultiLLM CLI

Discover every configured host CLI alias, remove aliases reserved as judges,
execute-probe every remaining candidate and judge, then queue a release run:

```bash
.venv/bin/multillm eval run \
  --suite finops-v1 \
  --profile release \
  --all-live \
  --live \
  --repeat 3 \
  --moa moa/quality \
  --judge claude-cli/sonnet \
  --judge gemini-cli/flash
```

Use explicit candidates when you need a pinned comparison:

```bash
.venv/bin/multillm eval run \
  --suite finops-v1 --profile nightly --live --repeat 3 \
  --target codex/gpt-5-5 \
  --target codex/gpt-5-6-sol \
  --target antigravity/pro \
  --moa moa/quality \
  --judge claude-cli/sonnet \
  --judge gemini-cli/flash
```

Check and export the returned run ID:

```bash
.venv/bin/multillm eval status <RUN_ID> --json-output
.venv/bin/multillm eval export <RUN_ID> --format json --output audit.json
.venv/bin/multillm eval export <RUN_ID> --format csv --output audit.csv
.venv/bin/multillm eval export <RUN_ID> --format html --output management.html
```

Open `http://127.0.0.1:8080/evaluations?run=<RUN_ID>`. Human reviewers use the
blinded Response A/Response B widget; the reviewer identifier and rationale are
required and encrypted at rest.

## Use MoA from Codex

The project MCP server exposes canonical `llm_moa`. In Codex:

1. Call `llm_model_catalog` and retain only execution-ready aliases.
2. Call `llm_moa` with the same task prompt, at least two proposer `models`, an
   independent `aggregator`, and optionally up to four `refiner_layers`.
3. Use `preset="quality"` for evaluation. Do not put `moa/*`, `fusion/*`, or
   `auto` inside the proposer/refiner/aggregator lists; recursive orchestration
   is rejected.
4. For a statistically comparable run, use the `multillm eval run` command
   above instead of manually calling each model. It freezes the suite, repeats,
   target list, judges, execution proof, and export manifest.

Example tool arguments:

```json
{
  "prompt": "Explain this FOCUS cost anomaly and propose verification steps.",
  "models": ["codex/gpt-5-5", "antigravity/pro"],
  "aggregator": "claude-cli/sonnet",
  "refiner_layers": [["gemini-cli/flash"]],
  "preset": "quality"
}
```

The canonical HTTP equivalent is `POST /api/moa`. `POST /api/fusion` and
`llm_fusion` remain compatibility/adaptive surfaces and are not renamed to MoA.

## DeepEval

The opt-in live harness sends one identical prompt to each standalone alias and
uses the actual DeepEval 4 `GEval` implementation through
`GatewayDeepEvalModel`:

```bash
DEEPEVAL_E2E=1 \
MULTILLM_GATEWAY_URL=http://127.0.0.1:8080 \
.venv/bin/pytest evals/deepeval/test_gateway_model_comparison.py -q
```

Normal CI skips this test. Live runs can consume subscriptions, tokens, and
provider budget.

## API surface

All evaluation APIs use `{success,data,error,meta}` and accept
`X-MultiLLM-Tenant` for tenant scope.

- `GET /api/evaluations/suites`
- `GET /api/evaluations/benchmarks`
- `GET /api/evaluations/live-targets` (discovery only)
- `POST /api/evaluations/preflight` (live execution proof)
- `GET|POST /api/evaluations/runs`
- `GET /api/evaluations/runs/{id}`
- `GET /api/evaluations/runs/{id}/results`
- `GET /api/evaluations/runs/{id}/comparisons`
- `POST /api/evaluations/runs/{id}/cancel`
- `GET /api/evaluations/runs/{id}/export?format=json|csv|html`
- `GET /api/evaluations/reviews/queue`
- `POST /api/evaluations/reviews/{comparison_id}` with
  `X-MultiLLM-Reviewer`

## Interpreting exports

The JSON export is the canonical audit bundle: suite content/hash, run request,
outputs and hashes, usage/latency/cost, metrics, pairwise comparisons, encrypted
artifact-derived judgments, and completed human reviews. The CSV uses
`recordType` values `output`, `comparison`, `judgment`, and `review`. Cells that
could trigger spreadsheet formulas are prefixed safely. The HTML report escapes
all model-controlled content and is designed for printing or management review.

Parquet is intentionally not advertised by the API until a columnar dependency
can be installed and verified in the runtime image; JSON and CSV remain the
portable audit formats.
