---
description: Run MultiLLM production-readiness checks and show actionable setup issues
allowed-tools: Bash
---

Run the MultiLLM doctor and summarize whether the local installation is production-ready.

```bash
multillm-doctor --strict || true
```

If the result is not ready, list the reported issues first and keep the fix guidance concrete.
