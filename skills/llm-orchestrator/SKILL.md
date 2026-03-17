---
name: llm-orchestrator
description: Route work through the local MultiLLM gateway and decide when to ask other LLMs or helper agents for support. Use when Codex should leverage Claude, OCA, GPT, local models, or MultiLLM specialist agents for second opinions, architecture review, security review, context handoff, dashboard checks, or multi-device session consolidation.
---

# LLM Orchestrator

Use MultiLLM as the control plane for cross-model work instead of treating other models as ad hoc side conversations.

## Quick Checks

1. Assume the gateway is `http://localhost:8080` unless the environment says otherwise.
2. If the user asks about usage, costs, sessions, or hourly trends, use the dashboard and usage commands first.
3. If the user wants other models involved, prefer the MultiLLM MCP tools instead of manual copy/paste.
4. If work must appear across multiple devices, assume a shared `MULTILLM_HOME` is the intended consolidation mechanism.

## Auto-Detection: When to Invoke Agents

The orchestrator should be invoked **proactively** — don't wait for the user to ask. Detect the task phase and route automatically:

### Planning Phase (use task-planner or arch-council)
- User asks "how should we...", "what's the best approach", "design", "plan"
- Task is ambiguous or has competing approaches
- Multiple components need coordination
- Migration or major refactor is being discussed

### Execution Phase (use work-orchestrator)
- Code touches auth, crypto, secrets, IAM, or compliance → auto-trigger security-reviewer
- Change affects >5 files or crosses module boundaries → call second opinion
- Debugging has failed 2+ times → call second opinion with error context
- Implementation choice is uncertain → call council for quick validation

### QA Phase (use code-reviewer or security-reviewer)
- Code was just written or modified → auto-trigger code-reviewer
- Changes touch security-sensitive areas → auto-trigger security-reviewer
- User asks "is this right?", "review", "check", "validate"

### Token-Saving (use local-summarizer)
- File is >200 lines and needs to be understood, not edited
- Exploring logs, traces, or large outputs
- User says "summarize" or context is getting large

## Decision Rules

Use the narrowest tool that matches the task:

| Need | Tool | Agent |
|------|------|-------|
| Direct question to another model | `llm_ask` | — |
| Moderate-risk implementation | `llm_second_opinion` | work-orchestrator |
| Architecture, migration, tradeoffs | `llm_council` | arch-council |
| Code quality review | `llm_second_opinion` | code-reviewer |
| Security-sensitive changes | `llm_second_opinion` | security-reviewer |
| Complex task decomposition | `llm_council` | task-planner |
| Large file comprehension | `llm_summarize_cheap` | local-summarizer |
| Cross-session handoff | `llm_share_context` | work-orchestrator |
| Usage, costs, dashboard | `llm_usage` | — |
| Settings changes | `llm_settings_get/set` | — |

## Standard Operating Procedures

### SOP: Architecture Decision
```
1. State the question precisely
2. Search shared memory for prior decisions on this topic
3. Call llm_council with 3-4 models
4. Synthesize consensus and diverging views
5. Store the decision to shared memory
6. Present recommendation with confidence level
```

### SOP: Security Review
```
1. Read the changed files
2. Identify security-relevant patterns (auth, crypto, input handling, secrets)
3. Call llm_second_opinion with security focus using GPT-4o
4. Merge both analyses
5. Store findings to shared memory
6. Present PASS/WARN/FAIL verdict
```

### SOP: Code Review
```
1. Read the code under review
2. Analyze correctness, design, performance, error handling
3. Call llm_second_opinion for cross-family perspective
4. Compare findings — flag agreements and disagreements
5. Store significant findings to shared memory
6. Present structured review with Accept/Request Changes verdict
```

### SOP: Task Planning
```
1. Parse the objective and constraints
2. Search memory for related prior work
3. Decompose into 3-7 subtasks with model assignments
4. Call llm_council to validate the plan
5. Store the plan to shared memory
6. Present with execution order and dependencies
```

### SOP: Context Handoff
```
1. Summarize current working context (what was done, what's next, decisions made)
2. Search memory for any related prior context
3. Call llm_share_context with structured summary
4. Confirm the context is retrievable
5. Tell the user how to resume in the other session
```

## Checkpoint Discipline

After every significant orchestration action, store a memory:

```python
llm_memory_store(
    title="[decision|finding|plan]: short description",
    content="Detailed content with model consensus...",
    category="decision",  # or: finding, context, todo
    project="auto-detect from cwd",
    source_llm="claude"
)
```

This ensures continuity across sessions, models, and devices.

## Workflow

1. Determine whether the task needs one model, multiple models, or only observability.
2. Read current orchestration settings before changing behavior.
3. If the task is architectural, ambiguous, or high-impact, bring in a council or second opinion before writing final guidance.
4. If the task involves implementation handoff, store the working context so another session can resume cleanly.
5. After using other models, summarize only the actionable result and which model or agent changed the decision.

## Multi-Device Consolidation

When the user wants one dashboard across machines:

- Prefer a shared `MULTILLM_HOME`.
- Treat that directory as the source of truth for usage DBs, memory DB, routes, PID files, and logs.
- Remind the user that traffic must go through MultiLLM, or come from supported local telemetry, for the dashboard to populate.

## Related Repo Resources

- `agents/work-orchestrator.md` — Auto-routing with phase detection and checkpoint discipline
- `agents/task-planner.md` — Task decomposition with model assignment
- `agents/code-reviewer.md` — Multi-perspective code quality review
- `agents/security-reviewer.md` — Security-focused review with GPT-4o second opinion
- `agents/arch-council.md` — 3-4 model council for architecture decisions
- `agents/local-summarizer.md` — Token-efficient summarization via local models
- `commands/llm-usage.md` and `commands/llm-usage-hourly.md` for dashboard-oriented usage summaries
- `CLAUDE.md` for the runtime architecture, API, and gateway behavior
