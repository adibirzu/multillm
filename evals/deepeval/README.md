# Gateway model comparison with DeepEval

This is an opt-in live end-to-end evaluation outside the OCI Skills project. It
sends the same command to configured, live MultiLLM aliases and evaluates every
response with a real DeepEval GEval metric. MoA is always the final target and receives the
individual aliases that actually succeeded.

The default target labels cover the configured Codex profiles, Gemini CLI,
Claude Sonnet CLI, and Antigravity. Labels are not
availability claims: each alias is refreshed against `/api/models/catalog` and
skipped unless live discovery confirms it. Override an alias with its documented
`MULTILLM_EVAL_*_ALIAS` environment variable after discovery; add a target to
`models.json` to compare another model.

Install the optional dependency, start the host-permission gateway, choose an
evaluation judge that is not in the tested MoA panel, and opt into live calls:

```bash
uv sync --extra eval --extra langfuse
export MULTILLM_EVAL_JUDGE_ALIAS=<live-non-fusion-alias>
export MULTILLM_EVAL_MOA_AGGREGATOR=<live-aggregator-alias>
DEEPEVAL_E2E=1 deepeval test run evals/deepeval/test_gateway_model_comparison.py
```

The test does not write prompts or responses to the repository. It requires a
local host gateway, credentials for selected providers, and explicit opt-in because
it can incur provider cost. See the [DeepEval project](https://github.com/confident-ai/deepeval)
for metric and reporting capabilities.
