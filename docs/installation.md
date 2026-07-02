# Selective installation

MultiLLM's installer can install the gateway and editor integrations together
or independently. Run it from a checkout:

```bash
./install.sh --list-components
```

For a remote install, pass installer options after `bash -s --`:

```bash
curl -sSL https://raw.githubusercontent.com/adibirzu/multillm/main/install.sh \
  | bash -s -- --component codex-mcp --component codex-skills
```

Review a plan without checking prerequisites, cloning, installing packages, or
writing to the home directory:

```bash
./install.sh --dry-run --component claude
```

## Components and dependencies

| Component | Installs | Dependency |
| --- | --- | --- |
| `gateway` | Isolated Python runtime, gateway package, `.env` template, runtime data directory, and `multillm-gateway` | None |
| `codex-mcp` | `multillm-mcp`, `codex-multillm`, and one Codex MCP registration named `multillm` | `gateway` |
| `codex-skills` | Reusable skills under `${CODEX_HOME:-~/.codex}/skills` | None |
| `claude` | Session-start hook, Claude MCP entry, `multillm-mcp`, and `claude-multillm` | `gateway` |
| `all` | Every component above | Expands to all components |

Dependencies are explicit: selecting `codex-mcp` or `claude` also selects
`gateway`. Selecting `codex-skills` alone does not install Python packages,
create gateway data, register MCP tools, or write Claude files.

With no arguments, `./install.sh` selects `all` for backward compatibility.
Component flags are repeatable and duplicate selections are collapsed:

```bash
# Gateway only
./install.sh --component gateway

# Codex MCP and reusable workflows
./install.sh --component codex-mcp --component codex-skills

# Claude integration (gateway dependency included)
./install.sh --component claude

# Complete installation, equivalent to no arguments
./install.sh --component all
```

The MCP integrations use the generated `~/.local/bin/multillm-mcp`
executable. It points to the installation's isolated runtime, so Codex and
Claude do not depend on a particular working directory or whichever `python3`
appears on their `PATH`. Provider keys remain in the installation's `.env` and
are never placed in MCP command arguments.

Run the same command to upgrade or repair an installation. A remote install
fast-forwards its managed checkout; a checkout installation uses the current
working tree. Existing `.env` files are preserved, MCP entries are updated by
name instead of duplicated, and skill files are refreshed in place.

After installing or upgrading `codex-mcp` or `codex-skills`, start a **fresh
Codex thread** so Codex discovers the new server and skills.

## Removal

Removal is component-scoped and manual so runtime data is never deleted by an
upgrade:

```bash
# Codex MCP registration and launcher
codex mcp remove multillm
rm -f ~/.local/bin/codex-multillm

# Reusable Codex skills installed by this repository
rm -rf ~/.codex/skills/llm-dashboard ~/.codex/skills/llm-orchestrator

# Claude launcher (also remove the `multillm` entry from ~/.claude/.mcp.json
# and the MultiLLM SessionStart entry from ~/.claude/hooks.json)
rm -f ~/.local/bin/claude-multillm

# Shared MCP executable; remove after both Codex MCP and Claude are removed
rm -f ~/.local/bin/multillm-mcp

# Gateway executable and managed source/runtime
rm -f ~/.local/bin/multillm-gateway
rm -rf ~/.local/share/multillm
```

Gateway data is intentionally retained in `${MULTILLM_HOME:-~/.multillm}`.
Delete that directory only when its databases and history are no longer
needed. If `MULTILLM_INSTALL_DIR`, `MULTILLM_BIN_DIR`, `CODEX_HOME`,
`CLAUDE_CONFIG_DIR`, or `MULTILLM_HOME` was customized, remove files from those
locations instead.
