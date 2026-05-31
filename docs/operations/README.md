# Operations Documentation

Operator-facing runbooks for self-hosting MultiLLM. The audience is whoever runs the gateway in production: a solo developer on their laptop, a team's infra engineer, or a SRE wiring it into an existing cluster.

| Document                                  | When to read it                                                                |
| ----------------------------------------- | ------------------------------------------------------------------------------ |
| [deployment.md](deployment.md)            | First time bringing the gateway up. Three recipes: Compose, systemd, K8s.      |
| [backup-restore.md](backup-restore.md)    | Before exposing to real traffic. SQLite `.backup`, FTS5 rebuild, restore.      |
| [upgrade.md](upgrade.md)                  | Every time you move from one MultiLLM version to the next.                     |
| [troubleshooting.md](troubleshooting.md)  | When something is broken. Symptom → diagnosis command → fix, for ≥7 cases.     |
| [release.md](release.md)                  | Maintainers cutting a new MultiLLM release. PyPI + GHCR + Homebrew workflow.   |

Phase 10 of the roadmap publishes these as a MkDocs Material site. Today they live as plain Markdown in the repo so they're greppable from any operator's shell.

For project-level context (vision, quick start, architecture), see the [README](../../README.md). For the developer-facing contributing guide, see [CONTRIBUTING.md](../../CONTRIBUTING.md).
