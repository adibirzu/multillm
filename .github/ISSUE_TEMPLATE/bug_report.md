---
name: Bug report
about: Report a defect in the gateway, an adapter, the dashboard, or the CLI
title: "[bug] "
labels: ["bug", "triage"]
assignees: []
---

<!--
Before filing:
- Please search existing issues to avoid duplicates.
- DO NOT report security vulnerabilities here. See SECURITY.md for the private disclosure process.
- Redact any credentials, API keys, public IPs, or other sensitive data from logs you paste below.
-->

## Gateway version

<!-- Output of `multillm --version` -->

```
```

## Backends affected

<!-- List the backend route(s) involved, e.g. ollama/llama3.3, openai/gpt-4o, codex/cli -->

## Reproduction steps

1.
2.
3.

## Expected behavior

<!-- What you expected to happen -->

## Actual behavior

<!-- What actually happened, including any error messages -->

## Gateway log excerpt

<!--
Paste the relevant gateway log lines around the failure.
Redact tokens, API keys, and any host/IP addresses you do not want public.
-->

```
```

## Environment

| Field | Value |
| ----- | ----- |
| OS / version |  |
| Python version |  |
| Install method | (pip / docker compose / homebrew / source) |
| Local backends running | (ollama / lmstudio / codex / gemini-cli / none) |

## Additional context

<!-- Screenshots, related issues, anything else that helps triage -->
