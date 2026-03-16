# Cross-LLM Agent

Use the `mcp__multillm__*` tools to leverage multiple LLM backends from any client (Claude Code, Codex CLI, or other MCP-capable tools).

## Available Tools

- `mcp__multillm__llm_ask_model` — Ask a specific model (e.g., "ollama/qwen3-30b", "gemini/flash", "oca/gpt5")
- `mcp__multillm__llm_council` — Get opinions from multiple models simultaneously
- `mcp__multillm__llm_second_opinion` — Quick second opinion from a different backend
- `mcp__multillm__llm_list_models` — See all available models across backends
- `mcp__multillm__llm_discover_models` — Refresh available models from all backends
- `mcp__multillm__llm_memory_store` — Store context that persists across all LLMs
- `mcp__multillm__llm_memory_search` — Search shared memory from any LLM session
- `mcp__multillm__llm_share_context` — Share context between LLM sessions (e.g., Claude → Codex)
- `mcp__multillm__llm_get_context` — Retrieve context shared by another LLM
- `mcp__multillm__llm_usage` — View token usage and costs across all backends
- `mcp__multillm__llm_sessions` — View recent sessions
- `mcp__multillm__llm_usage` with short windows such as `1`, `6`, `12`, or `24` hours for hourly inspection

## Cross-LLM Patterns

### From Codex: Use Claude's analysis
```
mcp__multillm__llm_ask_model(model="anthropic/claude", prompt="Analyze this architecture...")
```

### From Claude: Use Codex's code generation
```
mcp__multillm__llm_ask_model(model="codex_cli/codex", prompt="Generate a function that...")
```

### Get diverse opinions
```
mcp__multillm__llm_council(prompt="Review this approach", models=["ollama/qwen3-30b", "gemini/flash", "oca/gpt5"])
```

### Share context between sessions
```
# In Claude session:
mcp__multillm__llm_share_context(context="Project uses FastAPI + SQLite...", target="codex")

# In Codex session:
mcp__multillm__llm_get_context(source="claude")
```

### Use a shared memory and usage store across devices
```
export MULTILLM_HOME="$HOME/Library/Mobile Documents/com~apple~CloudDocs/.multillm"
```

Point every device at the same `MULTILLM_HOME` before starting the gateway if you want one consolidated memory, usage, and session history store.
