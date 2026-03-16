"""
MultiLLM Gateway — Anthropic-compatible proxy with streaming and tool support.

Routes requests to 16+ backends: Ollama, LM Studio, OpenAI, Anthropic,
OpenRouter, Google Gemini, Groq, DeepSeek, Mistral, Together, xAI, Fireworks,
Azure OpenAI, AWS Bedrock, Oracle Code Assist (OCA), Codex CLI, Gemini CLI.

Features: SSE streaming, tool_use passthrough, token tracking, cache token
tracking, adaptive routing, circuit breakers, health probes, OpenTelemetry.

Usage:
  python -m multillm          # starts on :8080
  GATEWAY_PORT=9000 python -m multillm
"""

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    GATEWAY_PORT, OLLAMA_URL, LMSTUDIO_URL,
    OPENROUTER_KEY, OPENAI_KEY, ANTHROPIC_KEY, GEMINI_KEY,
    GROQ_KEY, DEEPSEEK_KEY, MISTRAL_KEY, TOGETHER_KEY,
    XAI_KEY, FIREWORKS_KEY,
    AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION,
    AWS_BEDROCK_REGION, AWS_BEDROCK_PROFILE,
    OCA_ENDPOINT, OCA_API_VERSION,
    load_routes, detect_project,
)
from .converters import (
    build_openai_payload,
    build_ollama_payload,
    openai_response_to_anthropic,
    make_anthropic_response,
    extract_text_from_anthropic,
    anthropic_messages_to_openai,
)
from .oca_auth import get_oca_bearer_token
from .streaming import (
    stream_openai_compat,
    stream_ollama,
    stream_anthropic_passthrough,
    stream_oca,
    stream_gemini,
)
from .adapters import get_adapter, list_adapters
from .adapters.setup import register_all_adapters
from .tracking import (
    record_usage, get_usage_summary, get_project_summary,
    get_sessions, get_session_detail, get_dashboard_stats, get_active_sessions,
    init_otel, trace_llm_call, record_otel_metrics, get_recent_backend_latency,
)
from .discovery import discover_all_models, discovered_to_routes
from .caching import cache_search, cache_store, get_cache_stats, LANGCACHE_ENABLED
from .claude_stats import get_claude_code_stats
from .http_pool import get_client, close_all as close_http_pools
from .auth import AuthMiddleware, auth_enabled
from .resilience import with_retry, BackendUnavailableError, all_breaker_status, get_breaker, calculate_backend_score
from .rate_limit import (
    check_rate_limit, acquire_concurrent, release_concurrent,
    get_client_id, is_rate_limiting_enabled, rate_limit_status,
)
from .health import (
    start_health_checks, stop_health_checks, check_all_backends,
    all_health_status, is_backend_healthy, get_health,
)

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FORMAT = os.getenv("LOG_FORMAT", "text")  # "text" or "json"

if LOG_FORMAT == "json":
    import json as _json

    class _JsonFormatter(logging.Formatter):
        def format(self, record):
            entry = {
                "ts": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info and record.exc_info[0]:
                entry["exception"] = self.formatException(record.exc_info)
            # Include extra fields added via log.info("...", extra={...})
            for key in ("request_id", "model", "backend", "project", "latency_ms",
                        "input_tokens", "output_tokens", "status", "fallback"):
                if hasattr(record, key):
                    entry[key] = getattr(record, key)
            return _json.dumps(entry)

    _handler = logging.StreamHandler()
    _handler.setFormatter(_JsonFormatter())
    logging.root.handlers = [_handler]
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

log = logging.getLogger("multillm.gateway")

# ── FastAPI app ──────────────────────────────────────────────────────────────
ROUTES = load_routes()
PROJECT = detect_project()


def _extract_usage_metrics(payload: dict) -> dict:
    """Normalize usage payloads from Anthropic-compatible and OpenAI-compatible responses."""
    usage = payload.get("usage", {}) or {}
    prompt_details = usage.get("prompt_tokens_details", {}) or {}
    return {
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0,
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0,
        "cache_read_input_tokens": (
            usage.get("cache_read_input_tokens",
                      usage.get("cacheReadInputTokens", prompt_details.get("cached_tokens", 0))) or 0
        ),
        "cache_creation_input_tokens": (
            usage.get("cache_creation_input_tokens", usage.get("cacheCreationInputTokens", 0)) or 0
        ),
    }


async def _run_discovery():
    """Discover models from all backends and merge into ROUTES."""
    try:
        discovered = await discover_all_models(force=True)
        new_routes = discovered_to_routes(discovered)
        # Merge: static routes take priority, discovered fill gaps
        added = 0
        for alias, route in new_routes.items():
            if alias not in ROUTES:
                ROUTES[alias] = route
                added += 1
        if added:
            log.info("Discovery added %d new routes (total: %d)", added, len(ROUTES))
    except Exception as e:
        log.warning("Model discovery failed: %s", e)


@asynccontextmanager
async def lifespan(application: FastAPI):
    register_all_adapters()
    init_otel(application)
    log.info("Loaded %d static routes, %d adapters for project '%s'",
             len(ROUTES), len(list_adapters()), PROJECT)
    if auth_enabled():
        log.info("API key authentication ENABLED")
    else:
        log.info("API key authentication disabled (set MULTILLM_API_KEY to enable)")
    await _run_discovery()
    start_health_checks()
    yield
    stop_health_checks()
    await close_http_pools()


app = FastAPI(title="MultiLLM Gateway", version="0.6.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)


# ── Backend adapters (non-streaming) ─────────────────────────────────────────

async def _call_openai_compat(
    base_url: str,
    api_key: str,
    payload: dict,
    extra_headers: Optional[dict] = None,
    backend: str = "openai",
) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        **(extra_headers or {}),
    }
    client = get_client(backend)
    r = await client.post(f"{base_url}/v1/chat/completions", json=payload, headers=headers)
    r.raise_for_status()
    return r.json()


async def _call_ollama(model: str, body: dict) -> dict:
    payload = build_ollama_payload(body, model)
    payload["stream"] = False

    client = get_client("ollama")
    r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
    r.raise_for_status()
    data = r.json()

    # Ollama non-streaming response format
    message = data.get("message", {})
    content_blocks: list[dict] = []

    text = message.get("content", "")
    if text:
        content_blocks.append({"type": "text", "text": text})

    # Tool calls from Ollama
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        content_blocks.append({
            "type": "tool_use",
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": func.get("name", ""),
            "input": func.get("arguments", {}),
        })

    stop_reason = "tool_use" if tool_calls else "end_turn"
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    return make_anthropic_response(
        text="",
        model=model,
        input_tokens=data.get("prompt_eval_count", 0),
        output_tokens=data.get("eval_count", 0),
        stop_reason=stop_reason,
        content_blocks=content_blocks,
    )


async def _call_anthropic_real(body: dict) -> dict:
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body_copy = {**body, "stream": False}
    client = get_client("anthropic")
    r = await client.post("https://api.anthropic.com/v1/messages", json=body_copy, headers=headers)
    r.raise_for_status()
    return r.json()


async def _call_oca(model: str, body: dict) -> dict:
    if not OCA_ENDPOINT:
        raise HTTPException(status_code=500, detail="OCA_ENDPOINT not configured")
    token = await get_oca_bearer_token()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="OCA not authenticated. Run OAuth flow or check ~/.oca/token.json",
        )

    # OCA uses LiteLLM routing — model names include the oca/ prefix
    payload = build_openai_payload(body, model)
    payload["stream"] = False
    # OCA: only send model + messages (strip everything else)
    payload = {"model": payload["model"], "messages": payload["messages"]}
    log.info("OCA request payload=%s", json.dumps(payload, default=str)[:1000])

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "client": "multillm-gateway",
        "client-version": "0.6.0",
    }

    url = f"{OCA_ENDPOINT}/{OCA_API_VERSION}/app/litellm/chat/completions"
    client = get_client("oca")
    r = await client.post(url, json=payload, headers=headers)
    log.info("OCA response status=%d content_type=%s body_len=%d", r.status_code, r.headers.get("content-type", ""), len(r.content))
    if r.status_code != 200:
        log.error("OCA error %d: %s", r.status_code, r.text[:500])
    r.raise_for_status()
    # OCA may return SSE stream even when stream=false — handle both
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct or r.text.startswith("data:"):
        text_parts = []
        for line in r.text.splitlines():
            if line.startswith("data:"):
                chunk_str = line[5:].strip()
                if chunk_str == "[DONE]":
                    continue
                try:
                    chunk = json.loads(chunk_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if "content" in delta:
                        text_parts.append(delta["content"])
                except json.JSONDecodeError:
                    continue
        data = {
            "id": "oca-response",
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": "".join(text_parts)}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    else:
        data = r.json()

    return openai_response_to_anthropic(data, model if model.startswith("oca/") else f"oca/{model}")


async def _call_gemini(model: str, body: dict) -> dict:
    try:
        from google import genai
    except ImportError:
        raise HTTPException(status_code=500, detail="google-genai package not installed")

    if not GEMINI_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY or GOOGLE_API_KEY not set")

    client = genai.Client(api_key=GEMINI_KEY)
    prompt = extract_text_from_anthropic(body)

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                max_output_tokens=body.get("max_tokens", 4096),
                temperature=body.get("temperature", 0.7),
            ),
        )
        text = response.text or ""
        input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini error: {e}")

    return make_anthropic_response(text, model, input_tokens, output_tokens)


async def _call_codex_cli(body: dict, model_alias: str = "codex/cli") -> dict:
    prompt = extract_text_from_anthropic(body)
    if len(prompt) > 10000:
        prompt = prompt[:10000] + "\n...(truncated)"

    # Determine profile from route model field (e.g. "codex:gpt-5-4" → "-p gpt-5-4")
    route_model = body.get("_route_model", "")
    if route_model.startswith("codex:"):
        profile = route_model.split(":", 1)[1]
    else:
        profile = os.getenv("CODEX_DEFAULT_PROFILE", "gpt-5-4")

    # Per-request sandbox override via metadata, else env var, else default
    metadata = body.get("metadata", {})
    sandbox = metadata.get("sandbox_mode") or os.getenv("CODEX_SANDBOX", "read-only")

    try:
        # Pipe prompt via stdin with "-" to avoid CLI argument length limits
        proc = await asyncio.create_subprocess_exec(
            "codex", "exec", "--full-auto", "-s", sandbox, "-p", profile, "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")), timeout=180
        )
        raw_out = stdout.decode("utf-8", errors="replace").strip()
        raw_err = stderr.decode("utf-8", errors="replace").strip()
        # Codex CLI outputs response to stdout; stderr has session banner + logs
        # On rc=1 with empty stdout, try to extract text from stderr after the banner
        text = raw_out
        if not text and raw_err:
            # Skip the session banner (everything before "--------\nuser\n")
            parts = raw_err.split("--------\nuser\n", 1)
            if len(parts) > 1:
                # After the user prompt echo, look for assistant response
                after_prompt = parts[1]
                lines = after_prompt.split("\n")
                # Skip the echoed prompt lines, take the rest
                text = "\n".join(lines).strip()
            if not text:
                text = f"Codex CLI error (rc={proc.returncode}): {raw_err[:500]}"
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Codex CLI timed out after 180s")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Codex CLI not found. Install: npm i -g @openai/codex")

    return make_anthropic_response(
        text=text, model=model_alias,
        input_tokens=len(prompt) // 4, output_tokens=len(text) // 4,
    )


async def _call_gemini_cli(body: dict, model_alias: str = "gemini-cli/default") -> dict:
    prompt = extract_text_from_anthropic(body)
    if len(prompt) > 10000:
        prompt = prompt[:10000] + "\n...(truncated)"

    route_model = body.get("_route_model", "")
    model_flag = []
    if route_model.startswith("gemini-cli:"):
        gemini_model = route_model.split(":", 1)[1]
        if gemini_model:
            model_flag = ["-m", gemini_model]

    gemini_bin = os.getenv("GEMINI_CLI_PATH", "gemini")

    # Per-request approval mode override via metadata, else env var, else yolo
    metadata = body.get("metadata", {})
    approval = metadata.get("sandbox_mode") or os.getenv("GEMINI_APPROVAL_MODE", "yolo")
    approval_flag = ["--approval-mode", approval] if approval != "yolo" else ["--yolo"]

    try:
        proc = await asyncio.create_subprocess_exec(
            gemini_bin, "-p", prompt, "-o", "json",
            *approval_flag, *model_flag,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        raw = stdout.decode("utf-8", errors="replace").strip()

        json_start = raw.find("{")
        if json_start >= 0:
            try:
                import json as _json
                data = _json.loads(raw[json_start:])
                text = data.get("response", "")
                input_tokens = 0
                output_tokens = 0
                for m_stats in data.get("stats", {}).get("models", {}).values():
                    tokens = m_stats.get("tokens", {})
                    input_tokens += tokens.get("input", 0)
                    output_tokens += tokens.get("candidates", 0)
            except (ValueError, KeyError):
                text = raw
                input_tokens = len(prompt) // 4
                output_tokens = len(text) // 4
        else:
            text = raw
            input_tokens = len(prompt) // 4
            output_tokens = len(text) // 4

        if proc.returncode != 0 and not text:
            text = f"Gemini CLI error (rc={proc.returncode}): {stderr.decode('utf-8', errors='replace')[:500]}"
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Gemini CLI timed out after 180s")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Gemini CLI not found. Install: npm i -g @google/gemini-cli")

    return make_anthropic_response(
        text=text, model=model_alias,
        input_tokens=input_tokens, output_tokens=output_tokens,
    )


# ── Cline-compatible backend adapters ────────────────────────────────────────

# OpenAI-compatible endpoints (Groq, DeepSeek, Mistral, Together, xAI, Fireworks)
OPENAI_COMPAT_BACKENDS = {
    "groq":       {"url": "https://api.groq.com/openai",     "key_fn": lambda: GROQ_KEY},
    "deepseek":   {"url": "https://api.deepseek.com",        "key_fn": lambda: DEEPSEEK_KEY},
    "mistral":    {"url": "https://api.mistral.ai",          "key_fn": lambda: MISTRAL_KEY},
    "together":   {"url": "https://api.together.xyz",        "key_fn": lambda: TOGETHER_KEY},
    "xai":        {"url": "https://api.x.ai",                "key_fn": lambda: XAI_KEY},
    "fireworks":  {"url": "https://api.fireworks.ai/inference", "key_fn": lambda: FIREWORKS_KEY},
}


async def _call_openai_compat_backend(backend: str, model: str, body: dict) -> dict:
    """Generic adapter for any OpenAI-compatible backend (Groq, DeepSeek, etc.)."""
    cfg = OPENAI_COMPAT_BACKENDS.get(backend)
    if not cfg:
        raise HTTPException(status_code=500, detail=f"Unknown OpenAI-compat backend: {backend}")
    key = cfg["key_fn"]()
    if not key:
        raise HTTPException(status_code=500, detail=f"{backend.upper()}_API_KEY not set")
    payload = build_openai_payload(body, model)
    payload["stream"] = False
    oai = await _call_openai_compat(cfg["url"], key, payload, backend=backend)
    return openai_response_to_anthropic(oai, f"{backend}/{model.split('/')[-1]}")


async def _call_azure_openai(model: str, body: dict) -> dict:
    """Azure OpenAI adapter — uses deployment-based URL pattern."""
    if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
        raise HTTPException(status_code=500, detail="AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT required")
    payload = build_openai_payload(body, model)
    payload["stream"] = False
    # Azure uses deployment name as model, URL pattern differs
    url = f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/{model}/chat/completions?api-version={AZURE_OPENAI_API_VERSION}"
    headers = {"Content-Type": "application/json", "api-key": AZURE_OPENAI_KEY}
    client = get_client("azure_openai")
    r = await client.post(url, json=payload, headers=headers)
    r.raise_for_status()
    return openai_response_to_anthropic(r.json(), f"azure/{model}")


async def _call_bedrock(model: str, body: dict) -> dict:
    """AWS Bedrock adapter — uses boto3 bedrock-runtime client."""
    try:
        import boto3
    except ImportError:
        raise HTTPException(status_code=500, detail="boto3 not installed. Run: pip install boto3")

    prompt = extract_text_from_anthropic(body)
    max_tokens = body.get("max_tokens", 4096)

    session_kwargs = {"region_name": AWS_BEDROCK_REGION}
    if AWS_BEDROCK_PROFILE:
        session_kwargs["profile_name"] = AWS_BEDROCK_PROFILE
    session = boto3.Session(**session_kwargs)
    bedrock = session.client("bedrock-runtime")

    # Use the Converse API for broad model support
    messages = [{"role": "user", "content": [{"text": prompt}]}]
    system_text = body.get("system")
    system_param = [{"text": system_text}] if system_text else []

    try:
        kwargs = {
            "modelId": model,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": body.get("temperature", 0.7)},
        }
        if system_param:
            kwargs["system"] = system_param
        response = bedrock.converse(**kwargs)
        text = response["output"]["message"]["content"][0]["text"]
        usage = response.get("usage", {})
        return make_anthropic_response(
            text=text, model=f"bedrock/{model.split('.')[-1]}",
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Bedrock error: {e}")


# ── Fallback logic ──────────────────────────────────────────────────────────

# Backends that require internet connectivity
CLOUD_BACKENDS = {
    "openrouter", "openai", "anthropic", "oca", "gemini",
    "groq", "deepseek", "mistral", "together", "xai", "fireworks",
    "azure_openai", "bedrock",
}
# Backends that work offline
LOCAL_BACKENDS = {"ollama", "lmstudio", "codex_cli", "gemini_cli"}

# Errors that should trigger fallback to local
FALLBACK_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
    ConnectionRefusedError,
    OSError,
    BackendUnavailableError,
)


def _get_fallback_model() -> tuple[str, dict]:
    """Get the best available local fallback model."""
    # Prefer configured fallback chain from settings
    from .memory import get_setting
    chain = get_setting("fallback_chain", ["ollama/qwen3-30b", "ollama/llama3"])
    for alias in chain:
        if alias in ROUTES:
            return alias, ROUTES[alias]
    # Last resort: first Ollama route
    for alias, route in ROUTES.items():
        if route["backend"] == "ollama":
            return alias, route
    return "ollama/llama3", {"backend": "ollama", "model": "llama3"}


def _normalize_family_name(value: str) -> str:
    """Normalize model family names so cross-backend aliases can be compared."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _route_family(model_alias: str, route: Optional[dict] = None) -> str:
    """Return a backend-agnostic family key for a route alias."""
    if route and route.get("family"):
        return _normalize_family_name(str(route["family"]))

    alias_family = model_alias.split("/", 1)[1] if "/" in model_alias else model_alias
    return _normalize_family_name(alias_family)


def score_backend(backend: str) -> dict:
    """Score a backend from 0.0-1.0 using latency, breaker failures, and health.

    Returns a decomposed score dict from ``calculate_backend_score`` that
    includes the overall score *and* the per-component breakdown so callers
    can inspect or log the decision.
    """
    breaker = get_breaker(backend)
    health = get_health(backend)
    breaker_status = breaker.status()

    return calculate_backend_score(
        health_status=health.status,
        breaker_available=breaker.is_available,
        breaker_state=breaker.state,
        breaker_failures=breaker_status["failures"],
        breaker_threshold=breaker.failure_threshold,
        recent_latency_ms=get_recent_backend_latency(backend),
    )


_SCORE_MIN_VIABLE = 0.1  # backends scoring below this are excluded


def _weighted_random_select(
    candidates: list[tuple[str, dict, dict]],
    original_alias: str,
    original_route: dict,
) -> tuple[str, dict, dict]:
    """Power-of-two-choices weighted random selection among viable backends.

    Filters out backends below ``_SCORE_MIN_VIABLE``, then picks two at random
    (weighted by score) and returns the better one.  This prevents thundering
    herd when multiple backends have near-identical scores.

    Falls back to deterministic max when there is only one viable candidate.
    """
    viable = [(a, r, info) for a, r, info in candidates if info["score"] >= _SCORE_MIN_VIABLE]
    if not viable:
        # All scores are low — fall back to best of all candidates
        viable = candidates

    if len(viable) == 1:
        return viable[0]

    scores = [info["score"] for _, _, info in viable]
    # Pick two candidates weighted by score, then take the better one
    chosen_pair = random.choices(viable, weights=scores, k=min(2, len(viable)))
    return max(chosen_pair, key=lambda item: (
        item[2]["score"],
        item[0] == original_alias,
        item[1].get("backend") == original_route.get("backend"),
    ))


def _select_route(model_alias: str) -> tuple[str, dict]:
    """Resolve the requested model alias to the best route.

    Provider-qualified aliases such as `openai/gpt-4o` are respected as-is.
    Unqualified family aliases such as `claude-sonnet` can adapt across all
    matching backends using weighted random selection (power of two choices).
    """
    route = ROUTES.get(model_alias)

    if not route:
        if model_alias.startswith("claude-"):
            return model_alias, {"backend": "anthropic", "model": model_alias}
        raise HTTPException(status_code=400, detail=f"Unknown model alias: {model_alias}")

    if "/" in model_alias:
        return model_alias, route

    family = _route_family(model_alias, route)
    candidates: list[tuple[str, dict, dict]] = []
    for alias, candidate_route in ROUTES.items():
        if _route_family(alias, candidate_route) != family:
            continue
        candidates.append((alias, candidate_route, score_backend(candidate_route["backend"])))

    if not candidates:
        return model_alias, route

    selected_alias, selected_route, selected_info = _weighted_random_select(
        candidates, model_alias, route,
    )

    if len(candidates) > 1:
        candidate_summary = ", ".join(
            f"{alias}={info['score']:.3f}" for alias, _, info in sorted(
                candidates, key=lambda item: item[2]["score"], reverse=True,
            )
        )
        log.info(
            "Adaptive route [%s] family=%s selected=%s backend=%s score=%.3f "
            "decomposition=health:%.2f/latency:%.2f/error:%.2f candidates=[%s]",
            model_alias,
            family,
            selected_alias,
            selected_route["backend"],
            selected_info["score"],
            selected_info["health_score"],
            selected_info["latency_score"],
            selected_info["error_score"],
            candidate_summary,
        )

    return selected_alias, selected_route


async def _check_ollama_available() -> bool:
    """Quick check if Ollama is reachable."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


# ── Streaming routing ──────────────────────────────────────────────────────

async def route_streaming(body: dict, route: dict, model_alias: str):
    """Route a streaming request to the appropriate backend."""
    backend = route.get("backend", "")
    real_model = route.get("model", "")

    # Health gate — let caller's fallback handler catch this
    if not is_backend_healthy(backend):
        raise BackendUnavailableError(f"Backend '{backend}' is unhealthy")

    if backend == "ollama":
        return await stream_ollama(OLLAMA_URL, body, real_model, model_alias)

    elif backend == "lmstudio":
        return await stream_openai_compat(LMSTUDIO_URL, "", body, real_model, model_alias, backend="lmstudio")

    elif backend == "openrouter":
        if not OPENROUTER_KEY:
            raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not set")
        return await stream_openai_compat(
            "https://openrouter.ai/api", OPENROUTER_KEY, body, real_model, model_alias,
            extra_headers={"HTTP-Referer": "https://multillm-gateway", "X-Title": "MultiLLM Gateway"},
            backend="openrouter",
        )

    elif backend == "openai":
        if not OPENAI_KEY:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
        return await stream_openai_compat("https://api.openai.com", OPENAI_KEY, body, real_model, model_alias, backend="openai")

    elif backend == "anthropic":
        if not ANTHROPIC_KEY:
            raise HTTPException(status_code=500, detail="ANTHROPIC_REAL_KEY not set")
        return await stream_anthropic_passthrough(ANTHROPIC_KEY, {**body, "model": real_model})

    elif backend == "oca":
        token = await get_oca_bearer_token()
        if not token:
            raise HTTPException(status_code=401, detail="OCA not authenticated")
        return await stream_oca(OCA_ENDPOINT, OCA_API_VERSION, token, body, real_model, model_alias)

    elif backend == "gemini":
        if not GEMINI_KEY:
            raise HTTPException(status_code=500, detail="GEMINI_API_KEY not set")
        return await stream_gemini(GEMINI_KEY, body, real_model, model_alias)

    elif backend == "codex_cli":
        # Codex CLI doesn't support streaming — fall back to non-streaming
        body["_route_model"] = real_model
        result = await _call_codex_cli(body, model_alias)
        return JSONResponse(result)

    elif backend == "gemini_cli":
        # Gemini CLI doesn't support streaming — fall back to non-streaming
        body["_route_model"] = real_model
        result = await _call_gemini_cli(body, model_alias)
        return JSONResponse(result)

    elif backend in OPENAI_COMPAT_BACKENDS:
        cfg = OPENAI_COMPAT_BACKENDS[backend]
        key = cfg["key_fn"]()
        if not key:
            raise HTTPException(status_code=500, detail=f"{backend.upper()}_API_KEY not set")
        return await stream_openai_compat(cfg["url"], key, body, real_model, model_alias, backend=backend)

    elif backend == "azure_openai":
        if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
            raise HTTPException(status_code=500, detail="AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT required")
        # Azure uses different URL pattern — fall back to non-streaming for now
        result = await _call_azure_openai(real_model, body)
        return JSONResponse(result)

    elif backend == "bedrock":
        # Bedrock uses boto3, no HTTP streaming — fall back to non-streaming
        result = await _call_bedrock(real_model, body)
        return JSONResponse(result)

    raise HTTPException(status_code=500, detail=f"Streaming not supported for backend: {backend}")


# ── Non-streaming routing ──────────────────────────────────────────────────

async def _route_single_request(body: dict, backend: str, real_model: str, model_alias: str) -> dict:
    """Dispatch a non-streaming request to the appropriate backend adapter."""
    if backend == "ollama":
        return await _call_ollama(real_model, body)
    elif backend == "lmstudio":
        payload = build_openai_payload(body, real_model)
        payload["stream"] = False
        oai = await _call_openai_compat(LMSTUDIO_URL, "", payload, backend="lmstudio")
        return openai_response_to_anthropic(oai, model_alias)
    elif backend == "openrouter":
        if not OPENROUTER_KEY:
            raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not set")
        payload = build_openai_payload(body, real_model)
        payload["stream"] = False
        oai = await _call_openai_compat(
            "https://openrouter.ai/api", OPENROUTER_KEY, payload,
            extra_headers={"HTTP-Referer": "https://multillm-gateway", "X-Title": "MultiLLM Gateway"},
            backend="openrouter",
        )
        return openai_response_to_anthropic(oai, model_alias)
    elif backend == "openai":
        if not OPENAI_KEY:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
        payload = build_openai_payload(body, real_model)
        payload["stream"] = False
        oai = await _call_openai_compat("https://api.openai.com", OPENAI_KEY, payload)
        return openai_response_to_anthropic(oai, model_alias)
    elif backend == "anthropic":
        if not ANTHROPIC_KEY:
            raise HTTPException(status_code=500, detail="ANTHROPIC_REAL_KEY not set")
        return await _call_anthropic_real({**body, "model": real_model})
    elif backend == "oca":
        return await _call_oca(real_model, body)
    elif backend == "gemini":
        return await _call_gemini(real_model, body)
    elif backend == "codex_cli":
        body["_route_model"] = real_model
        return await _call_codex_cli(body, model_alias)
    elif backend == "gemini_cli":
        body["_route_model"] = real_model
        return await _call_gemini_cli(body, model_alias)
    elif backend in OPENAI_COMPAT_BACKENDS:
        return await _call_openai_compat_backend(backend, real_model, body)
    elif backend == "azure_openai":
        return await _call_azure_openai(real_model, body)
    elif backend == "bedrock":
        return await _call_bedrock(real_model, body)

    raise HTTPException(status_code=500, detail=f"Unknown backend: {backend}")


async def route_request(body: dict, model_alias: Optional[str] = None, route: Optional[dict] = None) -> dict:
    requested_alias = body.get("model", "ollama/llama3")
    if route is None or model_alias is None:
        model_alias, route = _select_route(requested_alias)

    if route is None:
        if requested_alias.startswith("claude-"):
            return await _call_anthropic_real(body)
        raise HTTPException(status_code=400, detail=f"Unknown model alias: {requested_alias}")

    backend = route["backend"]
    real_model = route["model"]
    log.info(
        "Routing requested=%s selected=%s backend=%s model=%s",
        requested_alias,
        model_alias,
        backend,
        real_model,
    )

    # Skip unhealthy backends early — raise so fallback handler catches it
    if not is_backend_healthy(backend):
        raise BackendUnavailableError(f"Backend '{backend}' is unhealthy")

    # Wrap in retry + circuit breaker (skip for CLI-based backends — subprocess-based)
    if backend in ("codex_cli", "gemini_cli"):
        return await _route_single_request(body, backend, real_model, model_alias)

    return await with_retry(
        lambda: _route_single_request(body, backend, real_model, model_alias),
        backend=backend,
        max_retries=2,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/v1/messages")
async def messages(request: Request):
    # ── Rate limiting ────────────────────────────────────────────
    if is_rate_limiting_enabled():
        client_id = get_client_id(request)
        allowed, rl_headers = check_rate_limit(client_id)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}},
                headers=rl_headers,
            )
        if not acquire_concurrent(client_id):
            return JSONResponse(
                status_code=429,
                content={"type": "error", "error": {"type": "rate_limit_error", "message": "Too many concurrent requests"}},
            )
    else:
        client_id = None

    body = await request.json()
    requested_alias = body.get("model", "ollama/llama3")
    is_streaming = body.get("stream", False)
    effective_alias, route = _select_route(requested_alias)
    backend = route.get("backend", "unknown")
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    log.info(
        "Request rid=%s requested=%s selected=%s backend=%s stream=%s project=%s",
        request_id, requested_alias, effective_alias, backend, is_streaming, PROJECT,
    )
    t0 = time.time()
    used_fallback = False
    effective_backend = backend
    effective_route = route

    with trace_llm_call(effective_alias, backend, PROJECT):
        try:
            # ── Cache lookup (non-streaming only) ────────────────────
            if not is_streaming and LANGCACHE_ENABLED:
                cached = await cache_search(body, effective_alias, backend, PROJECT)
                if cached:
                    elapsed_ms = (time.time() - t0) * 1000
                    log.info("CACHE HIT model=%s ms=%.0f", effective_alias, elapsed_ms)
                    record_usage(
                        project=PROJECT, model_alias=effective_alias, backend=backend,
                        real_model=route.get("model", effective_alias),
                        input_tokens=0, output_tokens=0,
                        latency_ms=elapsed_ms, status="cache_hit",
                    )
                    return JSONResponse(cached)

            if is_streaming:
                response = await route_streaming(body, route, effective_alias)
                elapsed_ms = (time.time() - t0) * 1000
                record_usage(
                    project=PROJECT, model_alias=effective_alias, backend=effective_backend,
                    real_model=effective_route.get("model", effective_alias),
                    input_tokens=0, output_tokens=0,
                    latency_ms=elapsed_ms, status="streaming",
                )
                return response

            result = await route_request(body, model_alias=effective_alias, route=route)
            elapsed_ms = (time.time() - t0) * 1000
            usage = _extract_usage_metrics(result)
            in_tok = usage["input_tokens"]
            out_tok = usage["output_tokens"]
            cache_read_tok = usage["cache_read_input_tokens"]
            cache_create_tok = usage["cache_creation_input_tokens"]

            log.info(
                "rid=%s model=%s backend=%s ms=%.0f in=%d out=%d cache_read=%d cache_write=%d",
                request_id, effective_alias, backend, elapsed_ms, in_tok, out_tok, cache_read_tok, cache_create_tok,
            )

            record_usage(
                project=PROJECT, model_alias=effective_alias, backend=backend,
                real_model=route.get("model", effective_alias),
                input_tokens=in_tok, output_tokens=out_tok,
                cache_read_input_tokens=cache_read_tok,
                cache_creation_input_tokens=cache_create_tok,
                latency_ms=elapsed_ms,
            )

            # ── Cache store (async, non-blocking) ────────────────────
            if LANGCACHE_ENABLED:
                asyncio.create_task(cache_store(body, result, effective_alias, backend, PROJECT))
            record_otel_metrics(effective_alias, backend, PROJECT, in_tok, out_tok, elapsed_ms)

            return JSONResponse(result)

        except (HTTPException, *FALLBACK_ERRORS, httpx.HTTPStatusError) as primary_error:
            # Determine if we should try fallback to local LLM
            should_fallback = (
                backend in CLOUD_BACKENDS
                and not used_fallback
                and isinstance(primary_error, (*FALLBACK_ERRORS, httpx.HTTPStatusError, HTTPException))
            )

            # Don't fallback on 400-level client errors (bad request, not cloud issues)
            if isinstance(primary_error, HTTPException) and 400 <= primary_error.status_code < 500:
                if primary_error.status_code not in (401, 403):  # Auth errors DO fallback
                    should_fallback = False

            if should_fallback and await _check_ollama_available():
                fb_alias, fb_route = _get_fallback_model()
                log.warning(
                    "rid=%s backend '%s' failed (%s), falling back to '%s'",
                    request_id, backend, type(primary_error).__name__, fb_alias,
                )
                used_fallback = True
                effective_alias = fb_alias
                effective_backend = fb_route["backend"]
                effective_route = fb_route

                try:
                    fallback_body = {**body, "model": fb_alias}
                    if is_streaming:
                        response = await route_streaming(fallback_body, fb_route, fb_alias)
                        elapsed_ms = (time.time() - t0) * 1000
                        record_usage(
                            project=PROJECT, model_alias=fb_alias, backend=effective_backend,
                            real_model=fb_route["model"],
                            input_tokens=0, output_tokens=0,
                            latency_ms=elapsed_ms, status="fallback_streaming",
                        )
                        return response

                    result = await route_request(fallback_body)
                    elapsed_ms = (time.time() - t0) * 1000
                    usage = _extract_usage_metrics(result)

                    # Add fallback notice to response
                    content = result.get("content", [])
                    if content and content[0].get("type") == "text":
                        notice = f"\n\n---\n*[Fallback: {requested_alias} unavailable, used {fb_alias}]*"
                        content[0]["text"] += notice

                    log.info(
                        "rid=%s fallback model=%s backend=%s ms=%.0f",
                        request_id, fb_alias, effective_backend, elapsed_ms,
                    )
                    record_usage(
                        project=PROJECT, model_alias=fb_alias, backend=effective_backend,
                        real_model=fb_route["model"],
                        input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"],
                        cache_read_input_tokens=usage["cache_read_input_tokens"],
                        cache_creation_input_tokens=usage["cache_creation_input_tokens"],
                        latency_ms=elapsed_ms, status="fallback",
                    )
                    return JSONResponse(result)

                except Exception as fallback_error:
                    log.error("Fallback also failed: %s", fallback_error)
                    # Fall through to original error handling

            # No fallback possible — raise the original error
            elapsed_ms = (time.time() - t0) * 1000
            record_usage(
                project=PROJECT, model_alias=effective_alias, backend=backend,
                real_model=route.get("model", ""), input_tokens=0, output_tokens=0,
                latency_ms=elapsed_ms, status="error",
            )
            if isinstance(primary_error, HTTPException):
                raise
            elif isinstance(primary_error, httpx.HTTPStatusError):
                log.error("Backend HTTP error: %s — %s", primary_error.response.status_code, primary_error.response.text[:500])
                raise HTTPException(status_code=502, detail=f"Backend error: {primary_error.response.status_code}")
            else:
                log.error("Backend connection failed: %s", primary_error)
                raise HTTPException(status_code=503, detail=f"Cannot reach backend: {primary_error}")

        except Exception as e:
            log.exception("Unexpected error")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if client_id:
                release_concurrent(client_id)


@app.get("/v1/models")
async def list_models():
    models = [
        {"id": alias, "object": "model", "created": 1700000000, "owned_by": cfg["backend"]}
        for alias, cfg in ROUTES.items()
    ]
    return {"object": "list", "data": models}


@app.get("/health")
async def health():
    backends: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=3) as client:
        for name, url in [("ollama", f"{OLLAMA_URL}/api/tags"), ("lmstudio", f"{LMSTUDIO_URL}/v1/models")]:
            try:
                r = await client.get(url)
                backends[name] = "ok" if r.status_code == 200 else f"http {r.status_code}"
            except Exception as e:
                backends[name] = f"unreachable ({type(e).__name__})"

    token = await get_oca_bearer_token()
    backends["oca"] = "authenticated" if token else "not authenticated"
    backends["gemini"] = "configured" if GEMINI_KEY else "not set"
    backends["openai"] = "configured" if OPENAI_KEY else "not set"
    backends["anthropic"] = "configured" if ANTHROPIC_KEY else "not set"
    backends["openrouter"] = "configured" if OPENROUTER_KEY else "not set"

    # Check codex CLI
    try:
        proc = await asyncio.create_subprocess_exec(
            "which", "codex",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        backends["codex_cli"] = "available" if stdout.strip() else "not found"
    except Exception:
        backends["codex_cli"] = "not found"

    # Gemini CLI
    try:
        proc = await asyncio.create_subprocess_exec(
            "which", "gemini",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        backends["gemini_cli"] = "available" if stdout.strip() else "not found"
    except Exception:
        backends["gemini_cli"] = "not found"

    return {"status": "ok", "backends": backends, "routes": len(ROUTES), "project": PROJECT}


@app.get("/routes")
async def show_routes():
    return ROUTES


@app.get("/usage")
async def usage_endpoint(project: Optional[str] = None, hours: int = 24):
    return {
        "by_model": get_usage_summary(project=project, hours=hours),
        "by_project": get_project_summary(hours=hours),
    }


# ── Settings endpoints ──────────────────────────────────────────────────────

@app.get("/settings")
async def get_settings():
    from .memory import get_settings as _get_settings
    return _get_settings()


@app.put("/settings")
async def update_settings(request: Request):
    from .memory import update_settings as _update_settings
    data = await request.json()
    _update_settings(data)
    return {"status": "ok", "settings": data}


@app.get("/memory/search")
async def memory_search_endpoint(q: str, project: Optional[str] = None, limit: int = 10):
    from .memory import search_memory
    return search_memory(query=q, project=project, limit=limit)


# ── Memory & Context API (replaces MCP for direct HTTP access) ───────────────

@app.get("/api/memory")
async def list_memories_api(project: Optional[str] = None, category: Optional[str] = None, limit: int = 50):
    """List recent shared memories."""
    from .memory import list_memories
    return list_memories(project=project, category=category, limit=limit)


@app.post("/api/memory")
async def store_memory_api(request: Request):
    """Store a new shared memory entry."""
    from .memory import store_memory
    data = await request.json()
    title = data.get("title")
    content = data.get("content")
    if not title or not content:
        raise HTTPException(status_code=400, detail="title and content are required")
    mem_id = store_memory(
        title=title,
        content=content,
        project=data.get("project", "global"),
        source_llm=data.get("source_llm", "claude"),
        category=data.get("category", "general"),
        metadata=data.get("metadata"),
    )
    return {"status": "ok", "id": mem_id, "title": title}


@app.get("/api/memory/search")
async def search_memory_api(q: str, project: Optional[str] = None, limit: int = 10):
    """Search shared memories using FTS5 full-text search."""
    from .memory import search_memory
    return search_memory(query=q, project=project, limit=limit)


@app.get("/api/memory/{memory_id}")
async def get_memory_api(memory_id: str):
    """Get a single memory entry by ID."""
    from .memory import get_memory
    mem = get_memory(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return mem


@app.delete("/api/memory/{memory_id}")
async def delete_memory_api(memory_id: str):
    """Delete a memory entry."""
    from .memory import delete_memory
    if delete_memory(memory_id):
        return {"status": "ok", "deleted": memory_id}
    raise HTTPException(status_code=404, detail="Memory not found")


@app.post("/api/context")
async def share_context_api(request: Request):
    """Share context between LLMs within a session."""
    from .memory import share_context
    data = await request.json()
    session_id = data.get("session_id")
    content = data.get("content")
    if not session_id or not content:
        raise HTTPException(status_code=400, detail="session_id and content are required")
    ctx_id = share_context(
        session_id=session_id,
        source_llm=data.get("source_llm", "claude"),
        content=content,
        context_type=data.get("context_type", "info"),
        target_llm=data.get("target_llm", "*"),
        ttl_seconds=data.get("ttl_seconds", 3600),
    )
    return {"status": "ok", "id": ctx_id, "session_id": session_id}


@app.get("/api/context/{session_id}")
async def get_context_api(session_id: str, target_llm: Optional[str] = None):
    """Get shared context entries for a session."""
    from .memory import get_shared_context
    return get_shared_context(session_id=session_id, target_llm=target_llm)


# ── Dashboard API ────────────────────────────────────────────────────────────

@app.get("/api/dashboard")
async def dashboard_api(hours: int = 720, project: Optional[str] = None):
    return get_dashboard_stats(hours=hours, project=project)


@app.get("/api/sessions")
async def sessions_api(hours: int = 168, project: Optional[str] = None, limit: int = 50):
    return get_sessions(hours=hours, project=project, limit=limit)


@app.get("/api/active-sessions")
async def active_sessions_api():
    return get_active_sessions()


@app.get("/api/sessions/{session_id}")
async def session_detail_api(session_id: str):
    detail = get_session_detail(session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@app.get("/api/backends")
async def backends_api(refresh: bool = False):
    """List all backends with their discovered models."""
    discovered = await discover_all_models(force=refresh)
    summary = {}
    for backend, models in discovered.items():
        summary[backend] = {
            "available": len(models) > 0,
            "model_count": len(models),
            "models": [{"id": m["id"], "name": m.get("name", ""), "model": m["model"]} for m in models],
        }
    return {"backends": summary, "total_routes": len(ROUTES)}


@app.post("/api/routes")
async def add_route(request: Request):
    """Dynamically add or update a route."""
    data = await request.json()
    alias = data.get("alias")
    backend = data.get("backend")
    model = data.get("model")
    if not alias or not backend or not model:
        raise HTTPException(status_code=400, detail="alias, backend, and model are required")
    ROUTES[alias] = {"backend": backend, "model": model, "dynamic": True}
    return {"status": "ok", "alias": alias, "route": ROUTES[alias], "total_routes": len(ROUTES)}


@app.delete("/api/routes/{alias:path}")
async def delete_route(alias: str):
    """Remove a dynamically added route."""
    if alias not in ROUTES:
        raise HTTPException(status_code=404, detail=f"Route not found: {alias}")
    removed = ROUTES.pop(alias)
    return {"status": "ok", "removed": alias, "route": removed}


@app.get("/api/claude-stats")
async def claude_stats_api():
    """Get Claude Code token usage, costs, and session history."""
    return get_claude_code_stats()


@app.get("/api/cache")
async def cache_stats_api():
    """Get cache statistics."""
    return get_cache_stats()


@app.get("/api/otel")
async def otel_status_api():
    """Get OpenTelemetry / OCI APM configuration status."""
    from .config import OTEL_ENABLED, OTEL_SERVICE_NAME, OCI_APM_DOMAIN_ID, OCI_APM_ENDPOINT, OCI_APM_REGION
    from .tracking import _tracer, _meter
    return {
        "enabled": OTEL_ENABLED,
        "initialized": _tracer is not None,
        "service_name": OTEL_SERVICE_NAME,
        "oci_apm": {
            "configured": bool(OCI_APM_DOMAIN_ID),
            "region": OCI_APM_REGION if OCI_APM_DOMAIN_ID else None,
            "endpoint": OCI_APM_ENDPOINT if OCI_APM_DOMAIN_ID else None,
        },
        "has_metrics": _meter is not None,
    }


@app.get("/api/rate-limit")
async def rate_limit_api():
    """Get rate limit status and active client info."""
    return rate_limit_status()


@app.get("/api/health")
async def health_status_api():
    """Get active health check results for all backends."""
    return {
        "backends": all_health_status(),
        "circuit_breakers": all_breaker_status(),
    }


@app.post("/api/health/check")
async def trigger_health_check():
    """Force an immediate health check of all backends."""
    await check_all_backends()
    return {"status": "ok", "backends": all_health_status()}


@app.get("/api/routing/scores")
async def routing_scores():
    """Return current adaptive routing scores for all backends with decomposition."""
    seen_backends: set[str] = set()
    scores: dict[str, dict] = {}
    for alias, route in ROUTES.items():
        backend = route.get("backend", "unknown")
        if backend in seen_backends:
            continue
        seen_backends.add(backend)
        scores[backend] = score_backend(backend)
    return {"backends": scores}


@app.get("/api/auth")
async def auth_status():
    """Return auth status for each backend with login instructions."""
    backends = {}

    # API-key backends
    api_key_backends = {
        "openai": ("OPENAI_API_KEY", OPENAI_KEY),
        "anthropic": ("ANTHROPIC_REAL_KEY", ANTHROPIC_KEY),
        "openrouter": ("OPENROUTER_API_KEY", OPENROUTER_KEY),
        "gemini": ("GEMINI_API_KEY or GOOGLE_API_KEY", GEMINI_KEY),
        "groq": ("GROQ_API_KEY", GROQ_KEY),
        "deepseek": ("DEEPSEEK_API_KEY", DEEPSEEK_KEY),
        "mistral": ("MISTRAL_API_KEY", MISTRAL_KEY),
        "together": ("TOGETHER_API_KEY", TOGETHER_KEY),
        "xai": ("XAI_API_KEY", XAI_KEY),
        "fireworks": ("FIREWORKS_API_KEY", FIREWORKS_KEY),
    }
    for name, (env_var, key_val) in api_key_backends.items():
        backends[name] = {
            "authenticated": bool(key_val),
            "method": "api_key",
            "env_var": env_var,
            "action": None if key_val else f"export {env_var}=<your-key>",
        }

    # Azure OpenAI
    backends["azure_openai"] = {
        "authenticated": bool(AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT),
        "method": "api_key",
        "env_var": "AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT",
        "action": None if (AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT) else
            "export AZURE_OPENAI_API_KEY=<key> AZURE_OPENAI_ENDPOINT=<url>",
    }

    # AWS Bedrock
    backends["bedrock"] = {
        "authenticated": bool(AWS_BEDROCK_REGION),
        "method": "aws_profile",
        "env_var": "AWS_BEDROCK_REGION + AWS_PROFILE or AWS_BEDROCK_PROFILE",
        "action": None if AWS_BEDROCK_REGION else
            "export AWS_BEDROCK_REGION=us-east-1 AWS_PROFILE=<profile>",
    }

    # OCA (OAuth PKCE)
    token = await get_oca_bearer_token()
    backends["oca"] = {
        "authenticated": bool(token),
        "method": "oauth_pkce",
        "action": None if token else "Run: oca login",
        "token_status": "valid" if token else "expired_or_missing",
    }

    # CLI-based backends
    for cli_name, cli_bin in [("codex_cli", "codex"), ("gemini_cli", "gemini")]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "which", cli_bin,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            available = bool(stdout.strip())
        except Exception:
            available = False
        backends[cli_name] = {
            "authenticated": available,
            "method": "cli_binary",
            "action": None if available else f"Install: npm i -g @{'openai' if cli_name == 'codex_cli' else 'google'}/{cli_bin}",
        }

    # Local backends (no auth needed)
    for local in ["ollama", "lmstudio"]:
        backends[local] = {
            "authenticated": True,
            "method": "local",
            "action": None,
            "note": "No authentication required — connect to local service",
        }

    return {"backends": backends}


@app.post("/api/discover")
async def trigger_discovery():
    """Force re-discovery of all backend models."""
    await _run_discovery()
    discovered = await discover_all_models()
    total = sum(len(v) for v in discovered.values())
    return {"status": "ok", "discovered": total, "total_routes": len(ROUTES)}


@app.get("/dashboard")
async def dashboard_page():
    static_dir = Path(__file__).parent / "static"
    html_file = static_dir / "dashboard.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return HTMLResponse(html_file.read_text())


# ── Entry point ──────────────────────────────────────────────────────────────
def main():
    import uvicorn

    log.info("MultiLLM Gateway starting on port %d", GATEWAY_PORT)
    log.info("  Project:    %s", PROJECT)
    log.info("  Ollama:     %s", OLLAMA_URL)
    log.info("  LM Studio:  %s", LMSTUDIO_URL)
    log.info("  OpenRouter:  %s", "configured" if OPENROUTER_KEY else "not set")
    log.info("  OpenAI:     %s", "configured" if OPENAI_KEY else "not set")
    log.info("  Anthropic:  %s", "configured" if ANTHROPIC_KEY else "not set")
    log.info("  OCA:        %s", OCA_ENDPOINT)
    log.info("  Gemini:     %s", "configured" if GEMINI_KEY else "not set")
    log.info("  Routes:     %d total", len(ROUTES))
    uvicorn.run("multillm.gateway:app", host="0.0.0.0", port=GATEWAY_PORT, reload=True)


if __name__ == "__main__":
    main()
