---
name: Backend request
about: Propose adding support for a new LLM backend
title: "[backend] add support for "
labels: ["backend", "enhancement", "triage"]
assignees: []
---

<!--
A new backend is a significant integration. Please fill out as much as you can — incomplete requests will be
moved to "needs more info" until the surface is clear.
-->

## Backend identity

| Field | Value |
| ----- | ----- |
| Backend name |  |
| Vendor / project URL |  |
| Maintainer status | (commercial vendor / OSS project / personal project) |
| Pricing model | (per-token / flat / free / self-hosted) |

## API surface

| Field | Value |
| ----- | ----- |
| API style | (OpenAI-compatible / Anthropic-compatible / custom / GraphQL / gRPC) |
| Auth mode | (API key / OAuth / OIDC / mTLS / cloud-IAM / none) |
| Streaming support | (yes / no / partial) |
| Function-calling / tool use | (yes / no / partial) |
| Vision / multimodal | (yes / no / partial) |
| Cost-per-token publicly documented | (yes / no) |
| Public API reference URL |  |

## Why this backend matters

<!--
Concrete use cases: who would route traffic to this backend, and why this gateway is the right
place for that integration (vs. calling the backend directly).
-->

## Implementation pointers

<!--
Link to:
- Official client SDK (if any)
- Curl example for a chat-completion call
- Streaming protocol notes
- Known quirks (rate limits, header conventions, error-code semantics)
-->

## Willingness to contribute

- [ ] I am willing to open a PR implementing this backend
- [ ] I can help test once an implementation lands
- [ ] I have access to credentials needed to run integration tests
- [ ] I am requesting that a maintainer implement this
