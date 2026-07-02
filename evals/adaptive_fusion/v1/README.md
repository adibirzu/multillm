# Adaptive Fusion evaluation corpus v1

This versioned corpus covers factual lookup, research, coding, debugging,
architecture, summarization, extraction, multimodal input, tool use,
adversarial routing, budget enforcement, and provider degradation.

Run the paid-provider-disabled policy checks with:

```bash
pytest -q tests/test_eval_corpus.py tests/test_adaptive_orchestration.py
```

Production-shaped shadow runs set `metadata.multillm.shadow=true`. Shadow mode
records the proposed candidate and escalation path but issues no model calls.
Baseline exports should include policy/version, accepted-answer cost, latency,
validator outcome, frontier/deep-reasoning call count, and blinded preference.
Raw prompts and answers are excluded unless retention is explicitly enabled.
