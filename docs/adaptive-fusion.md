# Adaptive Fusion v2 and layered MoA

MultiLLM exposes three distinct orchestration contracts:

- `auto` / `POST /api/adaptive`: cheap-first progressive routing that may stop
  after one answer.
- `fusion/*`, `POST /api/fusion`, and `llm_fusion`: compatibility/adaptive
  Fusion surfaces.
- `moa/*`, `POST /api/moa`, and `llm_moa`: canonical layered
  Mixture-of-Agents with parallel proposers, optional refiner layers, and one
  structured final aggregator.

MoA is unrelated to Oracle Fusion. Recursive `moa/*`, `fusion/*`, and `auto`
aliases are rejected from all MoA roles.

Adaptive Fusion is the default implementation behind `model: "auto"`. It
starts with the least expensive capable model, runs deterministic checks and an
independent verifier when risk warrants it, and progressively adds balanced or
frontier specialists. Structured comparison and synthesis run only after more
than one usable answer exists.

Explicit `model: "fusion"` remains the fixed panel → judge compatibility path.
Use `fusion/economy`, `fusion/balanced`, `fusion/quality`, or
`fusion/critical` to force progressive deliberation. Request-level
`fusion_panel` and `fusion_judge` values override presets.

## Layered MoA

```json
{
  "prompt": "Review this FinOps anomaly explanation.",
  "models": ["codex/gpt-5-5", "antigravity/pro"],
  "refiner_layers": [["gemini-cli/flash"]],
  "aggregator": "claude-cli/sonnet",
  "preset": "quality",
  "max_tokens": 4096
}
```

Proposers receive the exact user prompt in parallel. Refiners and the
aggregator receive bounded, anonymously labeled response blocks; model aliases
are removed from copied response text. The aggregator must return structured
JSON with `analysis`, `final_answer`, and `confidence`. If aggregation fails,
MoA degrades to the highest explicit quality score rather than choosing the
longest answer. Per-call timeouts and maximum context characters bound each
stage.

For model/MoA comparisons, confidence intervals, independent judging, and audit
exports, use [Model and MoA evaluation](evaluations.md).

## Policy controls

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Review this design"}],
  "metadata": {
    "multillm": {
      "preset": "balanced",
      "max_cost_usd": 0.25,
      "max_latency_ms": 30000,
      "reasoning_ceiling": "high",
      "require_sources": false,
      "allowed_providers": ["openai", "anthropic", "gemini", "ollama"],
      "require_vendor_diversity": true
    }
  }
}
```

Unknown fields and unsupported values return 400. `max` reasoning and `ultra`
execution require the `critical` preset. Prompt content is never interpreted as
permission to alter cost, provider, retention, or reasoning limits.

## Council and traces

`POST /api/council` defaults to legacy `raw` mode. `adaptive` returns the
progressively selected individual answers; `synthesized` also returns the final
answer, stage timeline, confidence, and costs. The dashboard and
`/llm-council` request `synthesized` explicitly.

`GET /api/orchestration/{run_id}` returns a tenant-scoped sanitized trace.
`POST /api/orchestration/{run_id}/feedback` accepts a 1–5 `rating`, optional
`issue_categories`, and an optional `preferred_model`. Production ranking uses
feedback-derived scorecards only after 20 samples and a conservative confidence
bound.

Raw prompts, answers, evidence excerpts, and reasoning summaries are not stored
by default. The trace contains a prompt hash, task features, decisions, usage,
latency, cache metrics, and cost.

## GPT-5.6 preview behavior

MultiLLM does not install guessed GPT-5.6 routes. Stable aliases
`openai/luna`, `openai/terra`, and `openai/sol` appear only when OpenAI model
discovery confirms the corresponding provider IDs. Sol is excluded from
ordinary automatic routing; `max` and `ultra` remain critical-only controls.
Preview pricing and cache economics follow the
[OpenAI preview announcement](https://openai.com/index/previewing-gpt-5-6-sol/).

## Rollout and rollback

`adaptive_auto_rollout_percent` deterministically assigns traffic to adaptive
auto at 0–100 percent. Roll out at 5, 25, then 100 after evaluation gates pass.
Set `adaptive_auto_enabled=false` for the one-setting rollback to the previous
binary auto path. Shadow requests set `metadata.multillm.shadow=true`; they
return the proposed model and escalation path without issuing model calls.

For cost surprises, inspect `/api/models/capabilities`, confirm the provider
model identity in the stage trace, and compare cached-read/write and reasoning
tokens. For unexpected escalation, inspect task/risk features, validator
defects, and the early-exit reason.
