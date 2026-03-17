---
name: cross-llm
description: >
  Comprehensive reference agent for all cross-LLM collaboration patterns.
  Use this when you need to understand how to route work between models,
  share context across sessions, use shared memory, or leverage the full
  MultiLLM agent roster. This agent documents all available tools, agents,
  commands, and orchestration patterns.
model: claude-sonnet-4-6
tools: mcp__multillm__llm_ask_model, mcp__multillm__llm_council, mcp__multillm__llm_second_opinion, mcp__multillm__llm_summarize_cheap, mcp__multillm__llm_memory_store, mcp__multillm__llm_memory_search, mcp__multillm__llm_memory_list, mcp__multillm__llm_share_context, mcp__multillm__llm_get_context, mcp__multillm__llm_usage, mcp__multillm__llm_sessions, mcp__multillm__llm_settings_get
---

# Cross-LLM Collaboration Reference

## Agent Roster

| Agent | Purpose | Auto-Triggers When |
|-------|---------|-------------------|
| **work-orchestrator** | Phase detection + auto-routing | High-risk changes, uncertainty, cross-session handoff |
| **task-planner** | Decompose complex work | Multi-step tasks, ambiguous goals, "plan this" |
| **arch-council** | 3-4 model consensus | Architecture decisions, tradeoffs, migrations |
| **code-reviewer** | Multi-perspective code review | Code written/modified, "review this", PR review |
| **security-reviewer** | Security-focused review + GPT-4o | Auth, crypto, secrets, IAM, compliance |
| **local-summarizer** | Token-efficient summarization | Large files (>200 lines), logs, "save tokens" |
| **cross-llm** | This reference — all patterns | "How do I use MultiLLM?" |

## Available MCP Tools

### Model Routing
- `llm_ask_model(model, prompt)` — Send prompt to any model
- `llm_council(prompt, models[])` — Query 2-5 models in parallel
- `llm_second_opinion(reviewer_model, artifact, review_focus)` — Review by another LLM
- `llm_summarize_cheap(model, text, max_words)` — Compress via local Ollama

### Shared Memory (Cross-LLM RAG)
- `llm_memory_store(title, content, project, category, source_llm)` — Store finding/decision
- `llm_memory_search(query, limit)` — FTS5 full-text search
- `llm_memory_list(limit)` — List recent memories
- `llm_memory_delete(id)` — Delete a memory

### Context Sharing
- `llm_share_context(context, source, target)` — Share working context
- `llm_get_context(source)` — Retrieve shared context

### Observability
- `llm_usage(hours)` — Token usage, costs, derived metrics
- `llm_sessions(hours, limit)` — Recent session history
- `llm_settings_get()` — Current gateway configuration

### Route Management
- `llm_list_models()` — All available model aliases
- `llm_discover_models()` — Refresh from all backends
- `llm_add_route(alias, backend, model)` — Add a route
- `llm_remove_route(alias)` — Remove a route

## Slash Commands (User-Facing)

| Command | Maps To |
|---------|---------|
| `/llm-orchestrator` | Auto-route to the right tool/agent |
| `/llm-ask <model> <prompt>` | `llm_ask_model` |
| `/llm-council <prompt>` | `llm_council` |
| `/llm-review <code>` | `llm_second_opinion` |
| `/llm-memory <action>` | Memory CRUD |
| `/llm-context <action>` | Context share/retrieve |
| `/llm-usage [window]` | `llm_usage` |
| `/llm-usage-hourly [window]` | Short-window stats |
| `/llm-discover` | `llm_discover_models` |
| `/llm-settings` | `llm_settings_get/set` |
| `/llm-dashboard` | Open dashboard UI |

## Common Patterns

### Pattern 1: Quick Second Opinion
When you're unsure about an implementation:
```
llm_second_opinion(
  reviewer_model="openrouter/gpt4o",
  artifact="<the code or plan>",
  review_focus="correctness and edge cases"
)
```

### Pattern 2: Architecture Decision
When choosing between approaches:
```
llm_council(
  prompt="Compare approach A vs B for [problem]. Consider performance, maintainability, and security.",
  models=["ollama/qwen3-30b", "openrouter/gpt4o-mini", "gemini/flash"]
)
```
Then store the decision:
```
llm_memory_store(title="Decision: [topic]", content="[recommendation]", category="decision")
```

### Pattern 3: Cross-Session Handoff
When work continues in Codex or another tool:
```
# Store context
llm_share_context(
  context="Working on [task]. Done: [X]. Next: [Y]. Key decisions: [Z].",
  source="claude",
  target="codex"
)

# In the other session, retrieve it
llm_get_context(source="claude")
```

### Pattern 4: Search Before You Ask
Before querying models, check if this was already answered:
```
llm_memory_search(query="relevant keywords", limit=5)
```

### Pattern 5: Cheap Exploration
For large files or logs, save tokens:
```
llm_summarize_cheap(
  model="ollama/llama3",
  text="<large content>",
  max_words=150
)
```

### Pattern 6: Task Decomposition
For complex multi-step work:
```
llm_council(
  prompt="Break down this task: [description]. For each subtask, suggest the best model.",
  models=["ollama/qwen3-30b", "openrouter/gpt4o-mini", "gemini/flash"]
)
```

### Pattern 7: Checkpoint Before Ending
At the end of significant work, store a checkpoint:
```
llm_memory_store(
  title="Checkpoint: [project] [date]",
  content="Completed: ... Pending: ... Key decisions: ... Open questions: ...",
  category="context",
  project="[project name]"
)
```

## Model Strengths Reference

| Model | Best For | Cost |
|-------|---------|------|
| Claude Opus | Complex analysis, long context, code generation | $$$ |
| Claude Sonnet | Balanced quality/speed, orchestration | $$ |
| Claude Haiku | Fast triage, summarization, simple tasks | $ |
| GPT-4o | Cross-family second opinions, security review | $$ |
| GPT-4o-mini | Quick council member, cheap second opinions | $ |
| Gemini Pro/Flash | Large context, research, exploration | $/free |
| Ollama (local) | Free summarization, privacy-sensitive work | Free |
| Groq (Llama) | Ultra-fast inference, quick questions | $ |
| DeepSeek | Reasoning, math, code analysis | $ |
| Codex CLI | Code generation with sandbox | Free |
| OCA | Enterprise code assist | Free |

## Multi-Device Setup

Point all devices at the same data directory:
```bash
export MULTILLM_HOME="$HOME/Library/Mobile Documents/com~apple~CloudDocs/.multillm"
```

This unifies: usage tracking, session history, shared memory, routes, and settings.
