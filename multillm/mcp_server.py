"""
MultiLLM MCP Server — multi-LLM routing + memory + tracking + settings as MCP tools.

Tools:
  LLM Routing:
    - llm_ask_model         Ask any routed model
    - llm_second_opinion    Code/plan review by another LLM
    - llm_council           Query 2-5 models in parallel
    - llm_summarize_cheap   Compress text via local model
    - llm_list_models       Show available aliases

  Usage Tracking:
    - llm_usage             Token usage dashboard

  Shared Memory:
    - llm_memory_store      Store shared memory
    - llm_memory_search     Search shared memory (local RAG)
    - llm_memory_list       List recent memories
    - llm_memory_delete     Delete a memory entry

  Context Sharing:
    - llm_share_context     Share context between LLMs
    - llm_get_context       Get shared context for current session

  Settings:
    - llm_settings_get      Get gateway settings
    - llm_settings_set      Update gateway settings

Usage (stdio, for Claude Code):
  python -m multillm.mcp_server
"""

import asyncio
import json
import logging
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

from .memory import (
    store_memory, search_memory, list_memories, get_memory, delete_memory,
    share_context, get_shared_context,
    get_settings, get_setting, set_setting, update_settings,
)
from .tracking import get_usage_summary, get_project_summary, get_sessions, get_dashboard_stats
from .config import detect_project

log = logging.getLogger("multillm.mcp")

GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://localhost:8080")

# Shared HTTP client for gateway calls — avoids creating a new client per tool invocation.
# Lazily initialized on first use, reused across all MCP tool calls.
_gateway_client: Optional[httpx.AsyncClient] = None


def _get_gateway_client() -> httpx.AsyncClient:
    """Get or create a shared AsyncClient for gateway communication."""
    global _gateway_client
    if _gateway_client is None:
        _gateway_client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            http2=True,
        )
    return _gateway_client


mcp = FastMCP("multillm")

# ── Input models ─────────────────────────────────────────────────────────────

class AskModelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., description="Model alias (e.g. 'ollama/llama3', 'oca/gpt5', 'gemini/flash', 'codex/cli')")
    prompt: str = Field(..., description="The question or task", min_length=1)
    system: Optional[str] = Field(default=None, description="Optional system prompt")
    max_tokens: int = Field(default=2048, ge=64, le=16000)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)


class SecondOpinionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    reviewer_model: str = Field(..., description="Model alias for reviewer")
    artifact: str = Field(..., description="Code, plan, or text to review", min_length=10)
    review_focus: str = Field(default="correctness, security, and clarity")


class CouncilInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    models: list[str] = Field(..., description="2-5 model aliases to query", min_length=2, max_length=5)
    prompt: str = Field(..., min_length=1)
    system: Optional[str] = Field(default=None)


class SummarizeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(default="ollama/llama3")
    text: str = Field(..., min_length=50)
    max_words: int = Field(default=150, ge=20, le=500)


class MemoryStoreInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(..., description="Short title for the memory")
    content: str = Field(..., description="Content to remember", min_length=5)
    project: str = Field(default="global", description="Project scope")
    category: str = Field(default="general", description="Category: decision, finding, context, todo")
    source_llm: str = Field(default="claude", description="Which LLM stored this")


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., description="Search query (supports FTS5 syntax)", min_length=1)
    project: Optional[str] = Field(default=None, description="Filter by project")
    limit: int = Field(default=10, ge=1, le=50)


class ContextShareInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session identifier for context sharing")
    content: str = Field(..., description="Context to share", min_length=1)
    source_llm: str = Field(default="claude", description="Source LLM")
    target_llm: str = Field(default="*", description="Target LLM ('*' for all)")
    context_type: str = Field(default="info", description="Type: info, decision, finding, error")


class UsageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project: Optional[str] = Field(default=None, description="Filter by project")
    hours: int = Field(default=24, ge=1, le=720, description="Lookback window in hours")


class SettingsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    settings: dict = Field(..., description="Key-value pairs to update")


# ── Helper ───────────────────────────────────────────────────────────────────

async def _call_gateway(
    model: str, prompt: str, system: Optional[str] = None,
    max_tokens: int = 2048, temperature: float = 0.7,
) -> str:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system:
        payload["system"] = system

    client = _get_gateway_client()
    r = await client.post(f"{GATEWAY_URL}/v1/messages", json=payload)
    r.raise_for_status()
    data = r.json()

    content = data.get("content", [])
    if content and isinstance(content, list):
        return content[0].get("text", "")
    return str(data)


# ── LLM Tools ────────────────────────────────────────────────────────────────

@mcp.tool(name="llm_ask_model", annotations={"title": "Ask a specific LLM model", "readOnlyHint": True})
async def llm_ask_model(params: AskModelInput) -> str:
    """Send a prompt to any LLM via the gateway (Ollama, OCA, Gemini, Codex, OpenAI, etc.)."""
    try:
        response = await _call_gateway(
            model=params.model, prompt=params.prompt, system=params.system,
            max_tokens=params.max_tokens, temperature=params.temperature,
        )
        return f"[{params.model}]\n\n{response}"
    except httpx.HTTPStatusError as e:
        return f"Error ({e.response.status_code}): {e.response.text[:300]}"
    except httpx.ConnectError:
        return f"Cannot reach gateway at {GATEWAY_URL}. Is it running?"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


@mcp.tool(name="llm_second_opinion", annotations={"title": "Get review from another LLM", "readOnlyHint": True})
async def llm_second_opinion(params: SecondOpinionInput) -> str:
    """Ask another LLM (OCA, GPT-4o, Gemini, Llama, etc.) to review code or a plan."""
    system = (
        f"You are a rigorous technical reviewer. Focus on: {params.review_focus}. "
        "Structure: VERDICT: PASS|WARN|FAIL, ISSUES: (list), SUGGESTIONS: (list), SUMMARY: (2-3 sentences)"
    )
    prompt = f"Review this artifact:\n\n```\n{params.artifact}\n```"
    try:
        response = await _call_gateway(model=params.reviewer_model, prompt=prompt, system=system, temperature=0.3)
        return f"[Second Opinion from {params.reviewer_model}]\n\n{response}"
    except Exception as e:
        return f"Review failed: {e}"


@mcp.tool(name="llm_council", annotations={"title": "Query multiple LLMs in parallel", "readOnlyHint": True})
async def llm_council(params: CouncilInput) -> str:
    """Query 2-5 LLMs simultaneously (e.g. OCA + Gemini + Ollama + Claude for diverse perspectives)."""
    async def ask_one(model: str) -> tuple[str, str]:
        try:
            resp = await _call_gateway(model=model, prompt=params.prompt, system=params.system, max_tokens=1024)
            return model, resp
        except Exception as e:
            return model, f"ERROR: {e}"

    results = await asyncio.gather(*[ask_one(m) for m in params.models])
    parts = [f"# Council Query\n\n**Prompt:** {params.prompt}\n"]
    for model, response in results:
        parts.append(f"\n---\n## [{model}]\n\n{response}")
    return "\n".join(parts)


@mcp.tool(name="llm_summarize_cheap", annotations={"title": "Summarize with cheap local model", "readOnlyHint": True})
async def llm_summarize_cheap(params: SummarizeInput) -> str:
    """Summarize text using a free local model (Ollama) to save tokens."""
    system = f"Summarize in at most {params.max_words} words. Preserve key facts and decisions. Plain text only."
    try:
        response = await _call_gateway(
            model=params.model, prompt=f"Summarize:\n\n{params.text}",
            system=system, max_tokens=min(params.max_words * 3, 1024), temperature=0.3,
        )
        return f"[Summary via {params.model}]\n\n{response}"
    except Exception as e:
        return f"Summarization failed: {e}"


@mcp.tool(name="llm_list_models", annotations={"title": "List available models", "readOnlyHint": True, "idempotentHint": True})
async def llm_list_models() -> str:
    """List all model aliases available in the gateway (Ollama, OCA, Gemini, Codex, OpenAI, etc.)."""
    try:
        client = _get_gateway_client()
        r = await client.get(f"{GATEWAY_URL}/routes")
        r.raise_for_status()
        routes = r.json()

        by_backend: dict[str, list] = {}
        for alias, cfg in routes.items():
            by_backend.setdefault(cfg.get("backend", "?"), []).append(alias)

        lines = ["Available models:\n"]
        for backend, aliases in sorted(by_backend.items()):
            lines.append(f"**{backend.upper()}**")
            for alias in sorted(aliases):
                lines.append(f"  - {alias}")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"Could not fetch routes: {e}"


@mcp.tool(name="llm_discover_models", annotations={"title": "Discover models from backends", "readOnlyHint": True})
async def llm_discover_models(backend: Optional[str] = None, refresh: bool = True) -> str:
    """Discover available models from all backends (Ollama, LM Studio, OpenAI, OCA, Gemini, OpenRouter).
    Use refresh=True to force re-query. Optionally filter by backend name."""
    try:
        client = _get_gateway_client()
        r = await client.get(f"{GATEWAY_URL}/api/backends", params={"refresh": str(refresh).lower()})
        r.raise_for_status()
        data = r.json()

        backends = data.get("backends", {})
        lines = [f"# Model Discovery (total routes: {data.get('total_routes', '?')})\n"]

        for name, info in sorted(backends.items()):
            if backend and name != backend:
                continue
            status = "available" if info["available"] else "offline"
            lines.append(f"## {name.upper()} ({status}, {info['model_count']} models)")
            for m in info.get("models", []):
                lines.append(f"  - `{m['id']}` — {m.get('name', m['model'])}")
            if not info.get("models"):
                lines.append("  (no models found)")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Discovery failed: {e}"


@mcp.tool(name="llm_add_route", annotations={"title": "Add/configure a model route", "readOnlyHint": False})
async def llm_add_route(alias: str, backend: str, model: str) -> str:
    """Add or update a model route. Example: alias='my-gpt4', backend='openai', model='gpt-4o-2024-08-06'.
    Backends: ollama, lmstudio, openai, openrouter, anthropic, oca, gemini, codex_cli."""
    valid_backends = {"ollama", "lmstudio", "openai", "openrouter", "anthropic", "oca", "gemini", "codex_cli"}
    if backend not in valid_backends:
        return f"Invalid backend '{backend}'. Valid: {', '.join(sorted(valid_backends))}"
    try:
        client = _get_gateway_client()
        r = await client.post(
            f"{GATEWAY_URL}/api/routes",
            json={"alias": alias, "backend": backend, "model": model},
        )
        r.raise_for_status()
        data = r.json()
        return f"Route added: `{alias}` -> {backend}/{model} (total routes: {data.get('total_routes', '?')})"
    except Exception as e:
        return f"Failed to add route: {e}"


@mcp.tool(name="llm_remove_route", annotations={"title": "Remove a model route", "readOnlyHint": False})
async def llm_remove_route(alias: str) -> str:
    """Remove a model route by alias."""
    try:
        client = _get_gateway_client()
        r = await client.delete(f"{GATEWAY_URL}/api/routes/{alias}")
        r.raise_for_status()
        data = r.json()
        return f"Route removed: `{alias}`"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Route not found: `{alias}`"
        return f"Failed: {e}"
    except Exception as e:
        return f"Failed to remove route: {e}"


# ── Usage/Tracking Tools ────────────────────────────────────────────────────

@mcp.tool(name="llm_usage", annotations={"title": "Token usage dashboard", "readOnlyHint": True, "idempotentHint": True})
async def llm_usage(params: UsageInput) -> str:
    """Show token usage, costs, and latency across all LLMs and projects."""
    by_model = get_usage_summary(project=params.project, hours=params.hours)
    by_project = get_project_summary(hours=params.hours)
    dashboard = get_dashboard_stats(hours=params.hours, project=params.project)
    derived = dashboard.get("derived", {})

    lines = [f"# LLM Usage (last {params.hours}h)\n"]

    lines.append(
        "## Summary\n"
        f"- Requests/session: {derived.get('avg_requests_per_session', 0):.2f}\n"
        f"- Tokens/request: {derived.get('avg_tokens_per_request', 0):.1f}\n"
        f"- Cost/request: ${derived.get('avg_cost_per_request', 0):.6f}\n"
        f"- Cost/1K tokens: ${derived.get('avg_cost_per_1k_tokens', 0):.6f}\n"
        f"- Requests/hour: {derived.get('requests_per_hour', 0):.2f}\n"
        f"- Tokens/hour: {derived.get('tokens_per_hour', 0):.1f}"
    )

    if by_project:
        lines.append("## By Project")
        total_cost = 0
        for p in by_project:
            cost = p.get("cost_usd", 0) or 0
            total_cost += cost
            lines.append(
                f"- **{p['project']}**: {p['requests']} requests, "
                f"{p.get('input_tokens',0):,}+{p.get('output_tokens',0):,} tokens"
                + (f" (+{p.get('cache_read_input_tokens',0):,} cache read, +{p.get('cache_creation_input_tokens',0):,} cache write)"
                   if p.get('cache_read_input_tokens',0) or p.get('cache_creation_input_tokens',0) else "")
                + f", ${cost:.4f}"
            )
        lines.append(f"\n**Total cost: ${total_cost:.4f}**\n")

    if by_model:
        lines.append("## By Model")
        for m in by_model:
            lines.append(
                f"- **{m['model_alias']}** ({m['backend']}): "
                f"{m['request_count']} reqs, {m.get('total_input',0):,}+{m.get('total_output',0):,} tokens"
                + (f" (+{m.get('total_cache_read_input',0):,} cache read, +{m.get('total_cache_creation_input',0):,} cache write)"
                   if m.get('total_cache_read_input',0) or m.get('total_cache_creation_input',0) else "")
                + ", "
                f"avg {m.get('avg_latency_ms',0):.0f}ms, ${m.get('total_cost_usd',0):.4f}"
                + (f", {m.get('error_count',0)} errors" if m.get('error_count') else "")
            )
    else:
        lines.append("No usage data yet.")

    return "\n".join(lines)


@mcp.tool(name="llm_cache_stats", annotations={"title": "Semantic cache stats", "readOnlyHint": True, "idempotentHint": True})
async def llm_cache_stats() -> str:
    """Show Redis LangCache statistics (hit rate, stored entries, cost savings)."""
    try:
        client = _get_gateway_client()
        r = await client.get(f"{GATEWAY_URL}/api/cache")
        r.raise_for_status()
        stats = r.json()

        if not stats.get("enabled"):
            return ("LangCache is disabled. Enable with env vars:\n"
                    "  LANGCACHE_ENABLED=true\n"
                    "  LANGCACHE_HOST=your-host\n"
                    "  LANGCACHE_CACHE_ID=your-cache-id\n"
                    "  LANGCACHE_API_KEY=your-key")

        return (
            f"# LangCache Stats\n\n"
            f"- **Status**: {'connected' if stats.get('connected') else 'disconnected'}\n"
            f"- **Hits**: {stats.get('hits', 0)}\n"
            f"- **Misses**: {stats.get('misses', 0)}\n"
            f"- **Stored**: {stats.get('stores', 0)}\n"
            f"- **Hit Rate**: {stats.get('hit_rate_pct', 0)}%\n"
            f"- **Errors**: {stats.get('errors', 0)}"
        )
    except Exception as e:
        return f"Could not fetch cache stats: {e}"


@mcp.tool(name="llm_sessions", annotations={"title": "List LLM sessions", "readOnlyHint": True, "idempotentHint": True})
async def llm_sessions(hours: int = 168, project: Optional[str] = None) -> str:
    """List recent LLM sessions with models used, tokens, and costs. Dashboard at http://localhost:8080/dashboard"""
    sessions = get_sessions(hours=hours, project=project, limit=20)
    if not sessions:
        return "No sessions found. Dashboard: http://localhost:8080/dashboard"

    from datetime import datetime
    label = f"{hours}h" if hours < 24 else f"{hours // 24}d"
    lines = [f"# LLM Sessions (last {label})\n"]
    lines.append(f"Dashboard: http://localhost:8080/dashboard\n")
    for s in sessions:
        started = datetime.fromtimestamp(s["started_at"]).strftime("%b %d %H:%M")
        duration_s = int(s["last_active_at"] - s["started_at"])
        duration = f"{duration_s}s" if duration_s < 60 else f"{duration_s // 60}m"
        models = ", ".join(s.get("models_used", []))
        total_tok = (s.get("total_input_tokens", 0) or 0) + (s.get("total_output_tokens", 0) or 0)
        lines.append(
            f"- **{started}** ({duration}) [{s['project']}] "
            f"{s.get('total_requests', 0)} reqs, {total_tok:,} tokens, "
            f"${s.get('total_cost_usd', 0):.4f} — {models}"
        )
    return "\n".join(lines)


# ── Memory Tools ─────────────────────────────────────────────────────────────

@mcp.tool(name="llm_memory_store", annotations={"title": "Store shared memory", "readOnlyHint": False})
async def llm_memory_store(params: MemoryStoreInput) -> str:
    """Store a memory entry that can be searched by any LLM (local RAG via FTS5)."""
    mem_id = store_memory(
        title=params.title, content=params.content, project=params.project,
        source_llm=params.source_llm, category=params.category,
    )
    return f"Memory stored: {mem_id} (title='{params.title}', project='{params.project}')"


@mcp.tool(name="llm_memory_search", annotations={"title": "Search shared memory (RAG)", "readOnlyHint": True})
async def llm_memory_search(params: MemorySearchInput) -> str:
    """Search shared memories using full-text search. Works as local RAG for all LLMs."""
    results = search_memory(query=params.query, project=params.project, limit=params.limit)
    if not results:
        return f"No memories found for query: '{params.query}'"

    lines = [f"# Memory Search: '{params.query}' ({len(results)} results)\n"]
    for r in results:
        lines.append(
            f"### {r['title']} [{r['id']}]\n"
            f"- Project: {r['project']} | Source: {r.get('source_llm','?')} | Category: {r.get('category','?')}\n"
            f"{r['content'][:500]}\n"
        )
    return "\n".join(lines)


@mcp.tool(name="llm_memory_list", annotations={"title": "List recent memories", "readOnlyHint": True})
async def llm_memory_list(project: Optional[str] = None, category: Optional[str] = None) -> str:
    """List recent shared memories, optionally filtered by project or category."""
    results = list_memories(project=project, category=category, limit=30)
    if not results:
        return "No memories stored yet."

    lines = ["# Shared Memories\n"]
    for r in results:
        lines.append(f"- **{r['title']}** [{r['id']}] ({r['project']}/{r.get('category','')}) by {r.get('source_llm','?')}")
    return "\n".join(lines)


@mcp.tool(name="llm_memory_delete", annotations={"title": "Delete a memory entry", "readOnlyHint": False})
async def llm_memory_delete(memory_id: str) -> str:
    """Delete a memory entry by ID."""
    if delete_memory(memory_id):
        return f"Memory deleted: {memory_id}"
    return f"Memory not found: {memory_id}"


# ── Context Sharing Tools ───────────────────────────────────────────────────

@mcp.tool(name="llm_share_context", annotations={"title": "Share context between LLMs", "readOnlyHint": False})
async def llm_share_context(params: ContextShareInput) -> str:
    """Share context from one LLM to others within a session (cross-LLM communication)."""
    ctx_id = share_context(
        session_id=params.session_id, source_llm=params.source_llm,
        content=params.content, context_type=params.context_type,
        target_llm=params.target_llm,
    )
    return f"Context shared: {ctx_id} (session={params.session_id}, type={params.context_type})"


@mcp.tool(name="llm_get_context", annotations={"title": "Get shared context for session", "readOnlyHint": True})
async def llm_get_context(session_id: str, target_llm: Optional[str] = None) -> str:
    """Get all shared context entries for a session (what other LLMs have shared)."""
    entries = get_shared_context(session_id=session_id, target_llm=target_llm)
    if not entries:
        return f"No shared context for session '{session_id}'"

    lines = [f"# Shared Context (session: {session_id})\n"]
    for e in entries:
        lines.append(
            f"### [{e.get('context_type','info')}] from {e.get('source_llm','?')}\n"
            f"{e['content']}\n"
        )
    return "\n".join(lines)


# ── Settings Tools ──────────────────────────────────────────────────────────

@mcp.tool(name="llm_settings_get", annotations={"title": "Get gateway settings", "readOnlyHint": True, "idempotentHint": True})
async def llm_settings_get() -> str:
    """Get all MultiLLM gateway settings (defaults merged with overrides)."""
    settings = get_settings()
    lines = ["# MultiLLM Settings\n"]
    for key, value in sorted(settings.items()):
        lines.append(f"- **{key}**: `{json.dumps(value)}`")
    return "\n".join(lines)


@mcp.tool(name="llm_settings_set", annotations={"title": "Update gateway settings", "readOnlyHint": False})
async def llm_settings_set(params: SettingsInput) -> str:
    """Update MultiLLM gateway settings (persisted to ~/.multillm/memory.db)."""
    update_settings(params.settings)
    updated = ", ".join(f"{k}={v}" for k, v in params.settings.items())
    return f"Settings updated: {updated}"


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    mcp.run()


if __name__ == "__main__":
    main()
