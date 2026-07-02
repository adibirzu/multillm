# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""
MultiLLM Gateway — Anthropic-compatible proxy with streaming and tool support.

Routes requests to 16+ backends: Ollama, LM Studio, OpenAI, Anthropic,
OpenRouter, Google Gemini, Groq, DeepSeek, Mistral, Together, xAI, Fireworks,
Azure OpenAI, AWS Bedrock, Codex CLI, Gemini CLI.

Features: SSE streaming, tool_use passthrough, token tracking, cache token
tracking, adaptive routing, circuit breakers, health probes, OpenTelemetry.

Usage:
  python -m multillm          # starts on :8080
  GATEWAY_PORT=9000 python -m multillm
"""

import asyncio
import hashlib
import logging
import math
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .config import (
    DATA_DIR,
    GATEWAY_CORS_ORIGINS,
    GATEWAY_HOST,
    GATEWAY_PORT,
    GATEWAY_RELOAD,
    MULTILLM_ALLOW_UNAUTHENTICATED_REMOTE,
    OLLAMA_URL,
    LMSTUDIO_URL,
    OPENROUTER_KEY,
    OPENAI_KEY,
    ANTHROPIC_KEY,
    GEMINI_KEY,
    GROQ_KEY,
    DEEPSEEK_KEY,
    MISTRAL_KEY,
    TOGETHER_KEY,
    XAI_KEY,
    FIREWORKS_KEY,
    AZURE_OPENAI_KEY,
    AZURE_OPENAI_ENDPOINT,
    AWS_BEDROCK_REGION,
    load_routes,
    detect_project,
)
from . import __version__
from .cli_tools import resolve_cli_binary
from .runtime_security import (
    build_security_headers,
    is_loopback_host,
    parse_cors_origins,
    validate_gateway_exposure,
)
from .converters import (
    extract_text_from_anthropic,
    count_tokens,
    make_anthropic_response,
    StreamState,
    make_message_start_event,
    make_content_block_start_event,
    make_text_delta_event,
    make_content_block_stop_event,
    make_message_delta_event,
    make_message_stop_event,
)
from .stream_utils import StreamTokenCounter
from .adapters import get_adapter, list_adapters

# Plan 02a-02 Task 20: setup.py:register_all_adapters() retired —
# adapters are now discovered exclusively via entry_points (Plan 02a-01
# Task 1). The first call to get_adapter()/list_adapters() triggers
# discovery automatically.
from .setup.middleware import SetupRedirectMiddleware
from .setup.routes import mount_static as mount_setup_static
from .setup.routes import router as setup_router
from .tracking import (
    record_usage,
    get_usage_summary,
    get_project_summary,
    get_sessions,
    get_session_detail,
    get_dashboard_stats,
    get_active_sessions,
    init_otel,
    trace_llm_call,
    finalize_llm_span,
    record_otel_metrics,
    get_recent_backend_latency,
    _estimate_cost,
    get_model_routing_stats,
    COST_TABLE,
)
from .langfuse_integration import (
    init_langfuse,
    shutdown_langfuse,
    trace_llm_generation,
    trace_fusion_run,
    trace_evaluation_run,
    get_langfuse_status,
)
from .llm_observability import build_llm_observability_summary
from .discovery import (
    discover_all_models,
    discovered_to_routes,
    get_discovered_local_models,
    LOCAL_DISCOVERABLE_BACKENDS,
    resolve_local_target,
)
from .local_launch import (
    ensure_any_local_backend,
    ensure_local_backend,
    installed_local_backends,
    is_backend_installed,
)
from .caching import cache_search, cache_store, get_cache_stats, LANGCACHE_ENABLED
from .claude_stats import get_claude_code_stats
from .codex_stats import get_codex_stats
from .gemini_stats import get_gemini_stats
from . import bundle_cache
from . import cost_forecast
from .usage_reports import build_usage_report
from . import failover
from . import budgets
from . import fusion
from . import moa
from . import complexity
from . import router as query_router
from . import result_cache
from .private_credit_overlay import (
    get_private_credit_overlay,
    save_private_credit_overlay,
)
from .codex_identity import get_codex_login_identity
from .adaptive_orchestration import AdaptiveOrchestrator, classify_task
from .evidence import EvidenceSource, build_evidence_pack, validate_public_url
from .model_registry import ModelRegistry
from .orchestration_contracts import ModelScorecard, OrchestrationPolicy
from .orchestration_store import OrchestrationStore
from .evaluation.api import (
    get_evaluation_store,
    router as evaluation_router,
)
from .evaluation.contracts import EvaluationCase, EvaluationRunRequest, ExecutionMode
from .evaluation.runner import (
    EvaluationResponse,
    EvaluationRunner,
    EvaluationStageUsage,
    deduplicate_targets,
)
from .stats_cache import ttl_cache
from .http_pool import close_all as close_http_pools
from .auth import AuthMiddleware, auth_enabled
from .resilience import (
    with_retry,
    BackendUnavailableError,
    all_breaker_status,
    get_breaker,
    calculate_backend_score,
)
from .rate_limit import (
    check_rate_limit,
    acquire_concurrent,
    release_concurrent,
    get_client_id,
    is_rate_limiting_enabled,
    rate_limit_status,
)
from .health import (
    start_health_checks,
    stop_health_checks,
    check_all_backends,
    all_health_status,
    is_backend_healthy,
    get_health,
)

# Default fusion panel/judge — Codex CLI + OCI GenAI (Meta Llama + Google
# Gemini), three reliable, diverse model families. OCI GenAI replaces the
# gemini-cli backend (which depends on a separately-authenticated CLI tier).
# Unavailable members degrade gracefully; override via the fusion_panel /
# fusion_judge settings.
_DEFAULT_FUSION_PANEL = ["codex/gpt-5-5", "oci/llama-3.3-70b", "antigravity/flash"]
_DEFAULT_FUSION_JUDGE = "oci/llama-3.3-70b"

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
            for key in (
                "request_id",
                "model",
                "backend",
                "project",
                "latency_ms",
                "input_tokens",
                "output_tokens",
                "status",
                "fallback",
            ):
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        for key, value in build_security_headers().items():
            response.headers.setdefault(key, value)
        return response


# ── FastAPI app ──────────────────────────────────────────────────────────────
ROUTES = load_routes()
PROJECT = detect_project()
_EVALUATION_PREFLIGHTS: dict[str, dict] = {}


def _extract_usage_metrics(payload: dict) -> dict:
    """Normalize usage payloads from Anthropic-compatible and OpenAI-compatible responses."""
    usage = payload.get("usage", {}) or {}
    prompt_details = usage.get("prompt_tokens_details", {}) or {}
    return {
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0,
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0))
        or 0,
        "cache_read_input_tokens": (
            usage.get(
                "cache_read_input_tokens",
                usage.get(
                    "cacheReadInputTokens", prompt_details.get("cached_tokens", 0)
                ),
            )
            or 0
        ),
        "cache_creation_input_tokens": (
            usage.get(
                "cache_creation_input_tokens", usage.get("cacheCreationInputTokens", 0)
            )
            or 0
        ),
        "reasoning_tokens": usage.get("reasoning_tokens", 0) or 0,
        "service_tier": usage.get("service_tier"),
        "provider_model": usage.get("provider_model", payload.get("model")),
    }


def _discovered_openai_model_ids() -> set[str]:
    return {
        str(route.get("model"))
        for route in ROUTES.values()
        if route.get("backend") == "openai"
        and route.get("discovered")
        and route.get("model")
    }


def _effective_model_registry() -> ModelRegistry:
    return ModelRegistry.from_routes(
        ROUTES, discovered_model_ids=_discovered_openai_model_ids()
    )


async def _gateway_evaluation_execute(
    target: str, case: EvaluationCase, request: EvaluationRunRequest
) -> EvaluationResponse:
    """Execute one evaluation target without ever downgrading live runs to fixtures."""
    if request.execution_mode is ExecutionMode.FIXTURE:
        text = case.reference_answer or " ".join(case.required_terms)
        if not text:
            text = f"Fixture response for {case.id}"
        return EvaluationResponse(
            text=text,
            input_tokens=max(1, len(case.prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            total_ms=0,
            ttft_unavailable_reason="fixture",
            resolved_model=f"fixture:{target}",
        )
    if request.execution_mode is ExecutionMode.REPLAY:
        raise RuntimeError("replay execution requires a recorded response source")

    receipt = _EVALUATION_PREFLIGHTS.get(str(request.preflight_receipt))
    if not receipt or float(receipt.get("expiresAt", 0)) <= time.time():
        raise RuntimeError("live evaluation preflight receipt is missing or expired")

    if target == "moa" or target.startswith("moa/"):
        candidates = list(request.candidates) or list(_DEFAULT_FUSION_PANEL)
        panel = request.metadata.get("moa_panel") or candidates
        if not set(panel).issubset(set(receipt.get("targets") or ())):
            raise RuntimeError("MoA panel does not match the live preflight receipt")
        aggregator = request.metadata.get("moa_aggregator") or (
            candidates[-1] if candidates else _DEFAULT_FUSION_JUDGE
        )
        result = await _run_moa_request(
            {
                "prompt": case.prompt,
                "models": panel,
                "aggregator": aggregator,
                "preset": target.split("/", 1)[1] if "/" in target else "quality",
                "max_tokens": int(request.metadata.get("max_tokens", 4096)),
            }
        )
        if not result.get("finalAnswer"):
            raise RuntimeError(
                f"MoA execution failed: {result.get('degradedReason') or result.get('status')}"
            )
        totals = result.get("totals") or {}
        stage_usage = tuple(
            EvaluationStageUsage(
                stage=str(stage.get("stage") or "unknown"),
                input_tokens=sum(
                    int(model.get("inputTokens", 0) or 0)
                    for model in stage.get("models") or ()
                ),
                output_tokens=sum(
                    int(model.get("outputTokens", 0) or 0)
                    for model in stage.get("models") or ()
                ),
            )
            for stage in result.get("stages") or ()
        )
        return EvaluationResponse(
            text=str(result["finalAnswer"]),
            input_tokens=int(totals.get("inputTokens", 0) or 0),
            output_tokens=int(totals.get("outputTokens", 0) or 0),
            reasoning_tokens=int(totals.get("reasoningTokens", 0) or 0),
            total_ms=float(totals.get("criticalPathMs", 0) or 0),
            ttft_unavailable_reason="moa_requires_complete_synthesis",
            actual_cost_usd=None,
            normalized_cost_usd=float(totals.get("actualCostUSD", 0) or 0),
            resolved_model=target,
            participant_models=tuple(dict.fromkeys([*panel, aggregator])),
            stage_usage=stage_usage,
        )

    if target not in set(receipt.get("targets") or ()):
        raise RuntimeError(f"{target} is not covered by the live preflight receipt")
    result = await _council_query_one(
        target,
        case.prompt,
        int(request.metadata.get("max_tokens", 4096)),
        float(request.metadata.get("temperature", 0.2)),
        controls={
            "reasoning_effort": str(request.metadata.get("reasoning_effort", "medium")),
            "execution_mode": "standard",
            "verbosity": "balanced",
        },
    )
    if result.get("error") or not str(result.get("text", "")).strip():
        raise RuntimeError(
            f"{target} execution failed: {result.get('error') or 'empty output'}"
        )
    return EvaluationResponse(
        text=str(result["text"]),
        input_tokens=int(result.get("inputTokens", 0) or 0),
        output_tokens=int(result.get("outputTokens", 0) or 0),
        reasoning_tokens=int(result.get("reasoningTokens", 0) or 0),
        cache_read_tokens=int(result.get("cacheReadInputTokens", 0) or 0),
        cache_write_tokens=int(result.get("cacheWriteInputTokens", 0) or 0),
        total_ms=float(result.get("latencyMs", 0) or 0),
        ttft_unavailable_reason="non_streaming_evaluation_call",
        actual_cost_usd=None,
        normalized_cost_usd=float(result.get("actualCostUSD", 0) or 0),
        pricing_version="model-registry-current",
        resolved_model=str(result.get("providerModel") or target),
    )


async def _gateway_evaluation_judge(
    judge_alias: str, prompt: str, request: EvaluationRunRequest
) -> str:
    """Run an independent judge covered by the same live-host preflight."""
    if request.execution_mode is not ExecutionMode.LIVE_HOST:
        raise RuntimeError("model judging requires live_host execution or a test judge")
    receipt = _EVALUATION_PREFLIGHTS.get(str(request.preflight_receipt))
    if (
        not receipt
        or float(receipt.get("expiresAt", 0)) <= time.time()
        or judge_alias not in set(receipt.get("targets") or ())
    ):
        raise RuntimeError("judge is not covered by the live preflight receipt")
    result = await _council_query_one(
        judge_alias,
        prompt,
        2_048,
        0,
        controls={
            "reasoning_effort": "medium",
            "execution_mode": "standard",
            "verbosity": "concise",
        },
    )
    if result.get("error") or not str(result.get("text", "")).strip():
        raise RuntimeError(
            f"judge execution failed: {result.get('error') or 'empty output'}"
        )
    return str(result["text"])


async def _evaluation_worker_loop() -> None:
    """Lease and execute queued runs; expired leases are safely reclaimed."""
    try:
        store = get_evaluation_store()
    except (RuntimeError, ValueError) as exc:
        log.info("Evaluation worker disabled: %s", exc)
        return

    def trace_completed_run(run: dict) -> None:
        request = run.get("request") or {}
        trace_evaluation_run(
            run_id=str(run["id"]),
            suite_id=str(run["suiteId"]),
            tenant_id=str(run["tenantId"]),
            status=str(run["status"]),
            profile=str(request.get("profile") or ""),
            execution_mode=str(request.get("execution_mode") or ""),
            candidates=tuple(request.get("candidates") or ()),
            moa_variants=tuple(request.get("moa_variants") or ()),
            judge_pool=tuple(request.get("judge_pool") or ()),
            summary=run.get("summary") or {},
        )

    runner = EvaluationRunner(
        store=store,
        execute=_gateway_evaluation_execute,
        judge=_gateway_evaluation_judge,
        worker_id=f"gateway-{uuid.uuid4().hex[:10]}",
        on_complete=trace_completed_run,
    )
    while True:
        try:
            run_id = await runner.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Evaluation worker iteration failed; leased run can be reclaimed"
            )
            await asyncio.sleep(2.0)
            continue
        await asyncio.sleep(0.25 if run_id else 2.0)


@lru_cache(maxsize=1)
def _orchestration_store() -> OrchestrationStore:
    return OrchestrationStore(DATA_DIR / "multillm.db")


def _resolve_orchestration_policy(
    body: dict, *, preset: str | None = None
) -> OrchestrationPolicy:
    metadata = body.get("metadata") or {}
    raw = metadata.get("multillm") or {}
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400, detail="metadata.multillm must be an object"
        )
    values = dict(raw)
    for field in OrchestrationPolicy.model_fields:
        if field in body:
            values[field] = body[field]
    if preset:
        values["preset"] = preset
    try:
        requested = OrchestrationPolicy.model_validate(values)
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise HTTPException(
            status_code=400, detail=f"Invalid metadata.multillm policy: {messages}"
        ) from exc

    from .memory import get_setting

    limits = get_setting("orchestration_policy_limits", {}) or {}
    if not isinstance(limits, dict):
        return requested
    effective = requested.model_dump(mode="json")
    try:
        if "max_cost_usd" in limits:
            effective["max_cost_usd"] = min(
                requested.max_cost_usd, float(limits["max_cost_usd"])
            )
        if "max_latency_ms" in limits:
            effective["max_latency_ms"] = min(
                requested.max_latency_ms, int(limits["max_latency_ms"])
            )
    except (TypeError, ValueError):
        log.warning("Ignoring invalid numeric orchestration_policy_limits")
    effort_order = ("none", "low", "medium", "high", "xhigh", "max")
    server_effort = str(limits.get("reasoning_ceiling") or "")
    if server_effort in effort_order:
        effective["reasoning_ceiling"] = effort_order[
            min(
                effort_order.index(requested.reasoning_ceiling.value),
                effort_order.index(server_effort),
            )
        ]
    server_providers = limits.get("allowed_providers") or []
    if isinstance(server_providers, (list, tuple)) and server_providers:
        normalized_server = {
            str(provider).strip().lower() for provider in server_providers
        }
        effective["allowed_providers"] = [
            provider
            for provider in requested.allowed_providers
            if provider in normalized_server
        ] or (list(normalized_server) if not requested.allowed_providers else [])
    if limits.get("require_sources"):
        effective["require_sources"] = True
    if limits.get("require_vendor_diversity"):
        effective["require_vendor_diversity"] = True
    try:
        return OrchestrationPolicy.model_validate(effective)
    except ValidationError as exc:
        raise HTTPException(
            status_code=500, detail="Server orchestration policy limits are invalid"
        ) from exc


def _adaptive_auto_enabled(prompt: str) -> bool:
    """Deterministic rollout bucket with a one-setting rollback."""
    from .memory import get_setting

    if not bool(get_setting("adaptive_auto_enabled", True)):
        return False
    try:
        percentage = int(get_setting("adaptive_auto_rollout_percent", 100))
    except (TypeError, ValueError):
        percentage = 100
    percentage = max(0, min(100, percentage))
    bucket = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < percentage


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
            preview_alias = {
                "gpt-5.6-luna": "openai/luna",
                "gpt-5.6-terra": "openai/terra",
                "gpt-5.6-sol": "openai/sol",
            }.get(str(route.get("model")))
            if route.get("backend") == "openai" and preview_alias:
                ROUTES[preview_alias] = {**route, "discovered": True}
        if added:
            log.info("Discovery added %d new routes (total: %d)", added, len(ROUTES))
    except Exception as e:
        log.warning("Model discovery failed: %s", e)


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_otel(application)
    init_langfuse()
    log.info(
        "Loaded %d static routes, %d adapters for project '%s'",
        len(ROUTES),
        len(list_adapters()),
        PROJECT,
    )
    if auth_enabled():
        log.info("API key authentication ENABLED")
    else:
        log.info("API key authentication disabled (set MULTILLM_API_KEY to enable)")
    await _run_discovery()
    start_health_checks()
    # Warm the dashboard bundle from disk so the first page load is instant even
    # right after a restart, then prime the default range in the background so
    # the persisted copy refreshes without anyone waiting on a cold scan.
    await asyncio.to_thread(bundle_cache.warm_load)
    asyncio.create_task(_prime_dashboard_bundle())
    evaluation_task = None
    if os.getenv("MULTILLM_EVAL_WORKER_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }:
        evaluation_task = asyncio.create_task(_evaluation_worker_loop())
    yield
    if evaluation_task is not None:
        evaluation_task.cancel()
        try:
            await evaluation_task
        except asyncio.CancelledError:
            pass
    stop_health_checks()
    shutdown_langfuse()
    await close_http_pools()


app = FastAPI(title="MultiLLM Gateway", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(GATEWAY_CORS_ORIGINS, port=GATEWAY_PORT),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# SetupRedirectMiddleware is added LAST so it wraps the outermost layer
# of the Starlette middleware stack (last-added = first-evaluated). This
# ensures the wizard is reachable before AuthMiddleware can demand a key.
app.add_middleware(SetupRedirectMiddleware)
app.include_router(setup_router, prefix="/setup")
app.include_router(evaluation_router)
mount_setup_static(app)

# Register multi-tenant team usage monitoring (per-user / per-account).
# See multillm/team_usage_api.py — adds /api/usage/ingest, /api/team-usage, /team.
from .team_usage_api import register as _register_team_usage  # noqa: E402

_register_team_usage(app)


# Plan 02a-02 Task 20: the 10 inline _call_<backend> functions and the
# OpenAI-compat dispatch dict that lived between this comment and
# the fallback-logic block have been retired. Backend dispatch is now
# entirely registry-based via _dispatch_with_resilience() and
# _dispatch_streaming_with_resilience() (see helpers above route_streaming).


# ── Fallback logic ──────────────────────────────────────────────────────────

# Backends that require internet connectivity
CLOUD_BACKENDS = {
    "openrouter",
    "openai",
    "anthropic",
    "gemini",
    "groq",
    "deepseek",
    "mistral",
    "together",
    "xai",
    "fireworks",
    "azure_openai",
    "bedrock",
    "oci_genai",
}
# Backends that work offline
LOCAL_BACKENDS = {
    "ollama",
    "lmstudio",
    "codex_cli",
    "gemini_cli",
    "antigravity",
    "claude_cli",
}

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


def _healthy_local_backends() -> set[str]:
    """Local discoverable backends currently passing their health gate."""
    return {b for b in LOCAL_DISCOVERABLE_BACKENDS if is_backend_healthy(b)}


def _installed_local_models() -> set[tuple[str, str]]:
    """(backend, real_model) pairs that discovery currently reports as installed.

    Empty when no discovery pass has populated the cache yet, which the caller
    treats as "cannot verify" rather than "nothing installed".
    """
    return {
        (m.get("backend", ""), m.get("model", ""))
        for m in get_discovered_local_models()
    }


def _get_fallback_model() -> tuple[str, dict]:
    """Get the best available local fallback model.

    Resolution order:
    1. Configured ``fallback_chain`` entries — but a *local* chain entry is only
       honoured when discovery confirms that model is actually installed (the
       static ROUTES always contain default Ollama aliases the user may never
       have pulled). Non-local entries are trusted as-is.
    2. Best installed + reachable local model from live discovery.
    3. Any Ollama route, then a hardcoded last resort.
    """
    from .memory import get_setting

    chain = get_setting("fallback_chain", ["ollama/qwen3-30b", "ollama/llama3"])
    installed = _installed_local_models()
    for alias in chain:
        route = ROUTES.get(alias)
        if route is None:
            continue
        backend = route.get("backend", "")
        if backend not in LOCAL_DISCOVERABLE_BACKENDS:
            return alias, route  # cloud fallback entry — trust it
        # Local entry: require an installed match when we have discovery data.
        # If the cache is empty we cannot verify, so fall through to discovery.
        if installed and (backend, route.get("model", "")) in installed:
            return alias, route

    # 2. Installed-aware: best discovered local model that is reachable now.
    resolved = resolve_local_target(reachable_backends=_healthy_local_backends())
    if resolved is not None:
        return resolved

    # 3. Last resort: first Ollama route, else a hardcoded default.
    for alias, route in ROUTES.items():
        if route["backend"] == "ollama":
            return alias, route
    return "ollama/llama3", {"backend": "ollama", "model": "llama3"}


async def _check_local_backend_available(backend: str) -> bool:
    """Probe whether a local backend is reachable for fallback dispatch."""
    if backend == "ollama":
        return await _check_ollama_available()
    if backend == "lmstudio":
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                r = await client.get(f"{LMSTUDIO_URL}/v1/models")
                return r.status_code == 200
        except Exception:
            return False
    return is_backend_healthy(backend)


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
    viable = [
        (a, r, info) for a, r, info in candidates if info["score"] >= _SCORE_MIN_VIABLE
    ]
    if not viable:
        # All scores are low — fall back to best of all candidates
        viable = candidates

    if len(viable) == 1:
        return viable[0]

    scores = [info["score"] for _, _, info in viable]
    # Pick two candidates weighted by score, then take the better one
    chosen_pair = random.choices(viable, weights=scores, k=min(2, len(viable)))
    return max(
        chosen_pair,
        key=lambda item: (
            item[2]["score"],
            item[0] == original_alias,
            item[1].get("backend") == original_route.get("backend"),
        ),
    )


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
        # local_first: degrade an unknown alias to the best installed local model
        # rather than failing outright, so requests still get served on-device.
        from .memory import get_setting

        if get_setting("local_first", True):
            resolved = resolve_local_target(
                reachable_backends=_healthy_local_backends()
            )
            if resolved is not None:
                log.info(
                    "Unknown alias '%s' resolved to local target '%s' (local_first)",
                    model_alias,
                    resolved[0],
                )
                return resolved
        raise HTTPException(
            status_code=400, detail=f"Unknown model alias: {model_alias}"
        )

    if "/" in model_alias:
        return model_alias, route

    family = _route_family(model_alias, route)
    candidates: list[tuple[str, dict, dict]] = []
    for alias, candidate_route in ROUTES.items():
        if _route_family(alias, candidate_route) != family:
            continue
        candidates.append(
            (alias, candidate_route, score_backend(candidate_route["backend"]))
        )

    if not candidates:
        return model_alias, route

    selected_alias, selected_route, selected_info = _weighted_random_select(
        candidates,
        model_alias,
        route,
    )

    if len(candidates) > 1:
        candidate_summary = ", ".join(
            f"{alias}={info['score']:.3f}"
            for alias, _, info in sorted(
                candidates,
                key=lambda item: item[2]["score"],
                reverse=True,
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


# ── Dispatch helpers (Plan 02a-02 Task 18) ─────────────────────────────────
#
# Extracted so route_request and route_streaming can have literal ≤ 3 AST
# top-level statements per ROADMAP success criterion #1 (AST-enforced gate
# in tests/test_route_function_shape.py).


def _resolve_route(
    body: dict,
    model_alias: Optional[str],
    route: Optional[dict],
) -> tuple[str, dict]:
    """Resolve (model_alias, route) for a request, including the claude-* fallback.

    Returns the resolved tuple. Raises HTTPException 400 for unknown aliases.
    """
    requested_alias = body.get("model", "ollama/llama3")
    if route is None or model_alias is None:
        model_alias, route = _select_route(requested_alias)
    if route is None:
        if requested_alias.startswith("claude-"):
            log.info(
                "Routing requested=%s selected=%s backend=anthropic (claude-* fallback)",
                requested_alias,
                requested_alias,
            )
            return requested_alias, {
                "backend": "anthropic",
                "model": body.get("model", ""),
            }
        raise HTTPException(
            status_code=400, detail=f"Unknown model alias: {requested_alias}"
        )
    log.info(
        "Routing requested=%s selected=%s backend=%s model=%s",
        requested_alias,
        model_alias,
        route["backend"],
        route["model"],
    )
    return model_alias, route


async def _check_health(backend: str) -> None:
    """Raise BackendUnavailableError if the backend's health gate is failing."""
    if not is_backend_healthy(backend):
        raise BackendUnavailableError(f"Backend '{backend}' is unhealthy")


async def _dispatch_with_resilience(
    backend: str, body: dict, model: str, model_alias: str
) -> dict:
    """Resolve the adapter and call send(), wrapping in retry+breaker except for subprocess CLIs."""
    adapter = get_adapter(backend)
    if adapter is None:
        raise HTTPException(status_code=500, detail=f"Unknown backend: {backend}")
    if backend in ("codex_cli", "gemini_cli", "antigravity", "claude_cli"):
        return await adapter.send(body, model, model_alias)
    return await with_retry(
        lambda: adapter.send(body, model, model_alias),
        backend=backend,
        max_retries=2,
    )


async def _dispatch_streaming_with_resilience(
    backend: str, body: dict, model: str, model_alias: str
):
    """Resolve the adapter and call stream()."""
    adapter = get_adapter(backend)
    if adapter is None:
        raise HTTPException(
            status_code=500, detail=f"Streaming not supported for backend: {backend}"
        )
    return await adapter.stream(body, model, model_alias)


# ── Streaming routing ──────────────────────────────────────────────────────


async def route_streaming(body: dict, route: dict, model_alias: str):
    """Route a streaming request to the appropriate backend (Plan 02a-02 SC#1: ≤3 statements)."""
    backend, real_model = route.get("backend", ""), route.get("model", "")
    await _check_health(backend)
    return await _dispatch_streaming_with_resilience(
        backend, body, real_model, model_alias
    )


# ── Non-streaming routing ──────────────────────────────────────────────────


async def route_request(
    body: dict, model_alias: Optional[str] = None, route: Optional[dict] = None
) -> dict:
    """Route a non-streaming request to the appropriate backend (Plan 02a-02 SC#1: ≤3 statements)."""
    model_alias, route = _resolve_route(body, model_alias, route)
    await _check_health(route["backend"])
    return await _dispatch_with_resilience(
        route["backend"], body, route["model"], model_alias
    )


@ttl_cache(seconds=30)
def _gateway_spend_snapshot(project: Optional[str] = None) -> dict:
    """Cached rolling-window gateway-metered spend (day + month).

    TTL-cached so the per-request budget gate doesn't hit SQLite every call.
    """
    today = get_dashboard_stats(hours=24, project=project)
    month = get_dashboard_stats(hours=720, project=project)
    return {
        "today": float(today.get("totals", {}).get("total_cost", 0) or 0),
        "month": float(month.get("totals", {}).get("total_cost", 0) or 0),
    }


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
                content={
                    "type": "error",
                    "error": {
                        "type": "rate_limit_error",
                        "message": "Rate limit exceeded",
                    },
                },
                headers=rl_headers,
            )
        if not acquire_concurrent(client_id):
            return JSONResponse(
                status_code=429,
                content={
                    "type": "error",
                    "error": {
                        "type": "rate_limit_error",
                        "message": "Too many concurrent requests",
                    },
                },
            )
    else:
        client_id = None

    body = await request.json()
    requested_alias = body.get("model", "ollama/llama3")
    is_streaming = body.get("stream", False)

    # ── Canonical layered Mixture of Agents interception ─────────
    if requested_alias == "moa" or requested_alias.startswith("moa/"):
        try:
            preset = (
                requested_alias.split("/", 1)[1]
                if "/" in requested_alias
                else "quality"
            )
            adapted = {
                **body,
                "prompt": extract_text_from_anthropic(body),
                "preset": preset,
                "models": body.get("models")
                or body.get("moa_panel")
                or list(moa.DEFAULT_PROPOSER_MODELS),
                "aggregator": body.get("aggregator")
                or body.get("moa_aggregator")
                or moa.DEFAULT_AGGREGATOR_MODEL,
            }
            if len(adapted["models"]) < 2 or not adapted["aggregator"]:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "MoA requires moa_panel with at least two models and "
                        "moa_aggregator"
                    ),
                )
            result = await _run_moa_request(adapted)
            return _fusion_response(result, requested_alias, is_streaming)
        finally:
            if client_id:
                release_concurrent(client_id)

    # ── Fusion / auto model-slug interception ────────────────────
    # Explicit `fusion` keeps the legacy fixed-panel contract. Presets and
    # `auto` use the shared progressive cheap-first engine.
    if requested_alias == "fusion" or requested_alias.startswith("fusion/"):
        try:
            explicit_panel = bool(
                body.get("fusion_panel")
                or (body.get("metadata") or {}).get("fusion_panel")
            )
            if requested_alias.startswith("fusion/") and not explicit_panel:
                preset = requested_alias.split("/", 1)[1]
                result = await _run_adaptive(
                    body, preset=preset, force_deliberation=True
                )
                return _fusion_response(result, requested_alias, is_streaming)
            return _fusion_response(
                await _run_fusion(body), requested_alias, is_streaming
            )
        finally:
            if client_id:
                release_concurrent(client_id)

    if requested_alias == "auto" or requested_alias.startswith("auto/"):
        prompt = extract_text_from_anthropic(body)
        preset = requested_alias.split("/", 1)[1] if "/" in requested_alias else None
        try:
            _resolve_orchestration_policy(body, preset=preset)
        except HTTPException:
            if client_id:
                release_concurrent(client_id)
            raise
        if _adaptive_auto_enabled(prompt):
            try:
                result = await _run_adaptive(body, preset=preset)
                return _fusion_response(result, "auto", is_streaming)
            finally:
                if client_id:
                    release_concurrent(client_id)

        # Rollback/holdout path retains the pre-v2 binary auto behavior.
        from .memory import get_setting

        comp = complexity.estimate_complexity(prompt)
        threshold = float(get_setting("fusion_auto_threshold", 0.6))
        if comp["score"] >= threshold:
            try:
                return _fusion_response(await _run_fusion(body), "auto", is_streaming)
            finally:
                if client_id:
                    release_concurrent(client_id)
        decision = _route_decision(prompt)
        requested_alias = decision.get("model") or get_setting(
            "fusion_judge", _DEFAULT_FUSION_JUDGE
        )
        body = {**body, "model": requested_alias}

    effective_alias, route = _select_route(requested_alias)
    backend = route.get("backend", "unknown")
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    # ── Budget enforcement (opt-in) ──────────────────────────────
    # Only metered cloud backends count against budgets; local backends are
    # free, so they are never blocked. Zero overhead unless budgets.enabled.
    from .memory import get_setting as _get_setting

    budget_cfg = _get_setting("budgets", {}) or {}
    if budget_cfg.get("enabled") and backend in CLOUD_BACKENDS:
        try:
            try:
                expected_output_tokens = max(0, int(body.get("max_tokens", 4096)))
            except (TypeError, ValueError):
                expected_output_tokens = 4096
            estimate = cost_forecast.estimate_prompt_cost(
                prompt=extract_text_from_anthropic(body),
                routes=ROUTES,
                candidates=[effective_alias],
                expected_output_tokens=expected_output_tokens,
            )
            anticipated_cost = float(
                (estimate.get("cheapest") or {}).get("estimatedCostUSD", 0.0) or 0.0
            )
            snap = _gateway_spend_snapshot(None)
            proj_spend = {PROJECT: _gateway_spend_snapshot(PROJECT)} if PROJECT else {}
            allowed, reason = budgets.check_request_allowed(
                config=budget_cfg,
                project=PROJECT,
                spent_today=snap["today"],
                spent_month=snap["month"],
                project_spend=proj_spend,
                anticipated_cost=anticipated_cost,
            )
        except Exception:
            # A spend-snapshot failure must not leak the concurrency slot. The
            # finally: release_concurrent guard only covers the dispatch block
            # below, which we have not entered yet.
            if client_id:
                release_concurrent(client_id)
            raise
        if not allowed:
            if client_id:
                release_concurrent(client_id)
            raise HTTPException(status_code=402, detail=f"Budget exceeded: {reason}")

    log.info(
        "Request rid=%s requested=%s selected=%s backend=%s stream=%s project=%s",
        request_id,
        requested_alias,
        effective_alias,
        backend,
        is_streaming,
        PROJECT,
    )
    t0 = time.time()
    used_fallback = False
    effective_backend = backend
    effective_route = route

    with trace_llm_call(effective_alias, backend, PROJECT) as span:
        try:
            # ── Cache lookup (non-streaming only) ────────────────────
            if not is_streaming and LANGCACHE_ENABLED:
                cached = await cache_search(body, effective_alias, backend, PROJECT)
                if cached:
                    elapsed_ms = (time.time() - t0) * 1000
                    log.info("CACHE HIT model=%s ms=%.0f", effective_alias, elapsed_ms)
                    record_usage(
                        project=PROJECT,
                        model_alias=effective_alias,
                        backend=backend,
                        real_model=route.get("model", effective_alias),
                        input_tokens=0,
                        output_tokens=0,
                        latency_ms=elapsed_ms,
                        status="cache_hit",
                    )
                    return JSONResponse(cached)

            if is_streaming:
                # Calculate initial input tokens before starting the stream
                prompt_text = extract_text_from_anthropic(body)
                initial_input_tokens = count_tokens(prompt_text, effective_alias)

                # Get the StreamingResponse from the backend
                streaming_response = await route_streaming(body, route, effective_alias)

                # Define the completion callback for token tracking
                def on_stream_complete(
                    input_tokens: int,
                    output_tokens: int,
                    elapsed_ms: float,
                ):
                    log.info(
                        "rid=%s model=%s backend=%s ms=%.0f in=%d out=%d (streaming complete)",
                        request_id,
                        effective_alias,
                        effective_backend,
                        elapsed_ms,
                        input_tokens,
                        output_tokens,
                    )
                    record_usage(
                        project=PROJECT,
                        model_alias=effective_alias,
                        backend=effective_backend,
                        real_model=effective_route.get("model", effective_alias),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=elapsed_ms,
                        status="streaming",
                    )
                    record_otel_metrics(
                        effective_alias,
                        effective_backend,
                        PROJECT,
                        input_tokens,
                        output_tokens,
                        elapsed_ms,
                    )

                    # Langfuse: record streaming LLM generation
                    trace_llm_generation(
                        model_alias=effective_alias,
                        backend=effective_backend,
                        real_model=effective_route.get("model", effective_alias),
                        project=PROJECT,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=elapsed_ms,
                        is_streaming=True,
                        request_id=request_id,
                    )

                # Wrap the original streaming generator with our token counter
                token_counted_generator = StreamTokenCounter(
                    original_generator=streaming_response.body_iterator,
                    completion_callback=on_stream_complete,
                    input_tokens=initial_input_tokens,
                    model_alias=effective_alias,
                )

                # Create a new StreamingResponse with the wrapped generator
                return StreamingResponse(
                    token_counted_generator,
                    media_type=streaming_response.media_type,
                    headers=streaming_response.headers,
                )

            result = await route_request(body, model_alias=effective_alias, route=route)
            elapsed_ms = (time.time() - t0) * 1000
            usage = _extract_usage_metrics(result)
            in_tok = usage["input_tokens"]
            out_tok = usage["output_tokens"]
            cache_read_tok = usage["cache_read_input_tokens"]
            cache_create_tok = usage["cache_creation_input_tokens"]

            log.info(
                "rid=%s model=%s backend=%s ms=%.0f in=%d out=%d cache_read=%d cache_write=%d",
                request_id,
                effective_alias,
                backend,
                elapsed_ms,
                in_tok,
                out_tok,
                cache_read_tok,
                cache_create_tok,
            )

            record_usage(
                project=PROJECT,
                model_alias=effective_alias,
                backend=backend,
                real_model=usage.get("provider_model")
                or route.get("model", effective_alias),
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_input_tokens=cache_read_tok,
                cache_creation_input_tokens=cache_create_tok,
                reasoning_tokens=usage["reasoning_tokens"],
                service_tier=usage["service_tier"],
                latency_ms=elapsed_ms,
            )

            # ── Cache store (async, non-blocking) ────────────────────
            if LANGCACHE_ENABLED:
                asyncio.create_task(
                    cache_store(body, result, effective_alias, backend, PROJECT)
                )
            record_otel_metrics(
                effective_alias, backend, PROJECT, in_tok, out_tok, elapsed_ms
            )

            # ── Finalize OTel span with GenAI token attributes ────────
            finalize_llm_span(
                span,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_read_tok,
                cache_create_tokens=cache_create_tok,
                model_alias=effective_alias,
            )

            # ── Langfuse LLM observability ────────────────────────────
            trace_llm_generation(
                model_alias=effective_alias,
                backend=backend,
                real_model=route.get("model", effective_alias),
                project=PROJECT,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_read_tok,
                cache_create_tokens=cache_create_tok,
                reasoning_tokens=usage["reasoning_tokens"],
                latency_ms=elapsed_ms,
                is_streaming=False,
                request_id=request_id,
                prompt_text=extract_text_from_anthropic(body),
                response_text=extract_text_from_anthropic(result),
            )

            return JSONResponse(result)

        except (
            HTTPException,
            *FALLBACK_ERRORS,
            httpx.HTTPStatusError,
        ) as primary_error:
            # Quota / credit / rate-limit exhaustion ("out of tokens") is the
            # signal to transparently continue on the next provider rather than
            # surface an error — even though it is a 4xx status.
            quota_err = failover.is_quota_error(primary_error)

            # Determine if we should try fallback
            should_fallback = (
                backend in CLOUD_BACKENDS
                and not used_fallback
                and isinstance(
                    primary_error,
                    (*FALLBACK_ERRORS, httpx.HTTPStatusError, HTTPException),
                )
            )

            # Don't fallback on 400-level client errors (bad request, not cloud
            # issues) — but auth errors (401/403) and quota errors (429/402) DO
            # fall over so the user keeps working.
            if (
                isinstance(primary_error, HTTPException)
                and 400 <= primary_error.status_code < 500
            ):
                if primary_error.status_code not in (401, 403) and not quota_err:
                    should_fallback = False

            # Build an ordered list of failover candidates. For quota/credit
            # exhaustion we walk the whole configured chain (cloud + local) so
            # the user keeps working on another provider; for connection errors
            # we fall back to the best installed local model as before. The
            # local model is always appended as a last resort.
            candidates: list[tuple[str, dict]] = []
            if should_fallback:
                from .memory import get_setting

                chain = get_setting(
                    "fallback_chain", ["ollama/qwen3-30b", "ollama/llama3"]
                )
                if quota_err:
                    candidates = failover.build_failover_candidates(
                        routes=ROUTES,
                        chain=chain,
                        failed_backend=backend,
                        exclude_aliases={requested_alias, effective_alias},
                    )

                # Start an installed-but-stopped local daemon on demand so a
                # local fallback has a target even when it isn't running.
                if get_setting("local_autostart", True):
                    started = await ensure_any_local_backend()
                    if started:
                        # The background health probe still has the daemon marked
                        # unhealthy; reflect the reality we just confirmed.
                        get_health(started).mark_healthy(0.0)
                        await _run_discovery()

                fb_local = _get_fallback_model()
                if fb_local and all(
                    fb_local[1].get("backend") != r.get("backend")
                    for _, r in candidates
                ):
                    candidates.append(fb_local)

            for cand_alias, cand_route in candidates:
                cand_backend = cand_route.get("backend", "")
                # Local candidates must be reachable; cloud candidates are tried
                # directly (a fresh provider has no local daemon to probe).
                if (
                    cand_backend in LOCAL_DISCOVERABLE_BACKENDS
                    and not await _check_local_backend_available(cand_backend)
                ):
                    continue

                log.warning(
                    "rid=%s backend '%s' failed (%s%s), failing over to '%s'",
                    request_id,
                    backend,
                    type(primary_error).__name__,
                    ", quota" if quota_err else "",
                    cand_alias,
                )
                used_fallback = True
                effective_alias = cand_alias
                effective_backend = cand_backend
                effective_route = cand_route

                try:
                    fallback_body = {**body, "model": cand_alias}
                    if is_streaming:
                        response = await route_streaming(
                            fallback_body, cand_route, cand_alias
                        )
                        elapsed_ms = (time.time() - t0) * 1000
                        record_usage(
                            project=PROJECT,
                            model_alias=cand_alias,
                            backend=cand_backend,
                            real_model=cand_route.get("model", cand_alias),
                            input_tokens=0,
                            output_tokens=0,
                            latency_ms=elapsed_ms,
                            status="fallback_streaming",
                        )
                        return response

                    result = await route_request(fallback_body)
                    elapsed_ms = (time.time() - t0) * 1000
                    usage = _extract_usage_metrics(result)

                    # Add fallback notice to response
                    content = result.get("content", [])
                    if content and content[0].get("type") == "text":
                        notice = f"\n\n---\n*[Failover: {requested_alias} unavailable, used {cand_alias}]*"
                        content[0]["text"] += notice

                    log.info(
                        "rid=%s failover model=%s backend=%s ms=%.0f",
                        request_id,
                        cand_alias,
                        cand_backend,
                        elapsed_ms,
                    )
                    record_usage(
                        project=PROJECT,
                        model_alias=cand_alias,
                        backend=cand_backend,
                        real_model=usage.get("provider_model")
                        or cand_route.get("model", cand_alias),
                        input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"],
                        cache_read_input_tokens=usage["cache_read_input_tokens"],
                        cache_creation_input_tokens=usage[
                            "cache_creation_input_tokens"
                        ],
                        reasoning_tokens=usage["reasoning_tokens"],
                        service_tier=usage["service_tier"],
                        latency_ms=elapsed_ms,
                        status="fallback",
                    )
                    return JSONResponse(result)

                except Exception as fallback_error:
                    # This candidate also failed — keep walking the chain.
                    log.warning(
                        "rid=%s failover candidate '%s' failed: %s",
                        request_id,
                        cand_alias,
                        fallback_error,
                    )
                    continue

            # No fallback possible or all candidates exhausted — raise the error
            elapsed_ms = (time.time() - t0) * 1000
            record_usage(
                project=PROJECT,
                model_alias=effective_alias,
                backend=backend,
                real_model=route.get("model", ""),
                input_tokens=0,
                output_tokens=0,
                latency_ms=elapsed_ms,
                status="error",
            )
            if isinstance(primary_error, HTTPException):
                raise
            elif isinstance(primary_error, httpx.HTTPStatusError):
                log.error(
                    "Backend HTTP error: %s — %s",
                    primary_error.response.status_code,
                    primary_error.response.text[:500],
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"Backend error: {primary_error.response.status_code}",
                )
            else:
                log.error("Backend connection failed: %s", primary_error)
                raise HTTPException(
                    status_code=503, detail=f"Cannot reach backend: {primary_error}"
                )

        except Exception as e:
            log.exception("Unexpected error")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if client_id:
                release_concurrent(client_id)


@app.get("/v1/models")
async def list_models():
    models = [
        {
            "id": alias,
            "object": "model",
            "created": 1700000000,
            "owned_by": cfg["backend"],
        }
        for alias, cfg in ROUTES.items()
    ]
    return {"object": "list", "data": models}


@app.get("/health")
async def health():
    backends: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=3) as client:
        for name, url in [
            ("ollama", f"{OLLAMA_URL}/api/tags"),
            ("lmstudio", f"{LMSTUDIO_URL}/v1/models"),
        ]:
            try:
                r = await client.get(url)
                backends[name] = (
                    "ok" if r.status_code == 200 else f"http {r.status_code}"
                )
            except Exception as e:
                backends[name] = f"unreachable ({type(e).__name__})"

    backends["gemini"] = "configured" if GEMINI_KEY else "not set"
    backends["openai"] = "configured" if OPENAI_KEY else "not set"
    backends["anthropic"] = "configured" if ANTHROPIC_KEY else "not set"
    backends["openrouter"] = "configured" if OPENROUTER_KEY else "not set"
    try:
        from .adapters.oci_genai import OCIGenAIAdapter

        backends["oci_genai"] = (
            "configured" if OCIGenAIAdapter().is_configured() else "not set"
        )
    except Exception:
        backends["oci_genai"] = "not set"

    # Check codex CLI
    try:
        proc = await asyncio.create_subprocess_exec(
            "which",
            "codex",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        backends["codex_cli"] = "available" if stdout.strip() else "not found"
    except Exception:
        backends["codex_cli"] = "not found"

    # Gemini CLI
    try:
        proc = await asyncio.create_subprocess_exec(
            "which",
            "gemini",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        backends["gemini_cli"] = "available" if stdout.strip() else "not found"
    except Exception:
        backends["gemini_cli"] = "not found"

    # Antigravity CLI (agy)
    try:
        from .cli_tools import resolve_cli_binary

        backends["antigravity"] = (
            "available"
            if resolve_cli_binary("agy", env_var="ANTIGRAVITY_CLI_PATH")
            else "not found"
        )
    except Exception:
        backends["antigravity"] = "not found"

    return {
        "status": "ok",
        "backends": backends,
        "routes": len(ROUTES),
        "project": PROJECT,
    }


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


@app.get("/api/private-credit")
async def private_credit_api(request: Request):
    """Return the operator's private credit overlay to local browsers only."""
    client_host = request.client.host if request.client else ""
    if not is_loopback_host(client_host):
        raise HTTPException(status_code=404, detail="Not found")
    overlay = get_private_credit_overlay()
    required_domain = overlay.get("requiredEmailDomain")
    identity = get_codex_login_identity()
    if required_domain and identity.get("emailDomain") != required_domain:
        return {"configured": False}
    return overlay


@app.put("/api/private-credit")
async def update_private_credit_api(request: Request):
    """Update the owner-only local credit overlay from a local browser."""
    client_host = request.client.host if request.client else ""
    if not is_loopback_host(client_host):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        payload = await request.json()
        return save_private_credit_overlay(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/settings")
async def update_settings(request: Request):
    from .memory import update_settings as _update_settings

    data = await request.json()
    _update_settings(data)
    return {"status": "ok", "settings": data}


@app.get("/memory/search")
async def memory_search_endpoint(
    q: str,
    project: Optional[str] = None,
    limit: int = 10,
    x_tenant: Optional[str] = Header(None, alias="X-MultiLLM-Tenant"),
):
    from .memory import search_memory

    return search_memory(query=q, project=project, limit=limit, tenant_id=x_tenant)


# ── Memory & Context API (replaces MCP for direct HTTP access) ───────────────
# Tenant scoping: callers identify their ownership boundary with the
# X-MultiLLM-Tenant header (typically the UNIX user). When present, reads are
# restricted to that tenant and writes are tagged with it.


@app.get("/api/memory")
async def list_memories_api(
    project: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    x_tenant: Optional[str] = Header(None, alias="X-MultiLLM-Tenant"),
):
    """List recent shared memories."""
    from .memory import list_memories

    return list_memories(
        project=project, category=category, limit=limit, tenant_id=x_tenant
    )


@app.post("/api/memory")
async def store_memory_api(request: Request):
    """Store a new shared memory entry."""
    from .memory import store_memory

    data = await request.json()
    title = data.get("title")
    content = data.get("content")
    if not title or not content:
        raise HTTPException(status_code=400, detail="title and content are required")
    tenant_id = (
        request.headers.get("x-multillm-tenant") or data.get("tenant_id") or "default"
    )
    mem_id = store_memory(
        title=title,
        content=content,
        project=data.get("project", "global"),
        source_llm=data.get("source_llm", "claude"),
        category=data.get("category", "general"),
        metadata=data.get("metadata"),
        tenant_id=tenant_id,
    )
    return {"status": "ok", "id": mem_id, "title": title, "tenant_id": tenant_id}


@app.get("/api/memory/search")
async def search_memory_api(
    q: str,
    project: Optional[str] = None,
    limit: int = 10,
    x_tenant: Optional[str] = Header(None, alias="X-MultiLLM-Tenant"),
):
    """Search shared memories using FTS5 full-text search."""
    from .memory import search_memory

    return search_memory(query=q, project=project, limit=limit, tenant_id=x_tenant)


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
        raise HTTPException(
            status_code=400, detail="session_id and content are required"
        )
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


def _scan_tenant(value: Optional[str]) -> str:
    tenant_id = (value or "default").strip()
    if not tenant_id or len(tenant_id) > 120:
        raise HTTPException(status_code=422, detail="Invalid tenant identifier")
    return tenant_id


def _validate_scan_report_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Scan report must be an object")
    findings = payload.get("findings")
    if not isinstance(findings, list) or len(findings) > 1_000:
        raise HTTPException(
            status_code=422, detail="findings must contain at most 1000 items"
        )
    for field, maximum in (("source", 80), ("project", 160), ("title", 240)):
        if (
            not isinstance(payload.get(field), str)
            or not payload[field].strip()
            or len(payload[field]) > maximum
        ):
            raise HTTPException(
                status_code=422, detail=f"{field} is required and too long"
            )
    if not isinstance(payload.get("metadata", {}), dict):
        raise HTTPException(status_code=422, detail="metadata must be an object")
    for finding in findings:
        if not isinstance(finding, dict):
            raise HTTPException(
                status_code=422, detail="each finding must be an object"
            )
        if not isinstance(finding.get("metadata", {}), dict):
            raise HTTPException(
                status_code=422, detail="finding metadata must be an object"
            )
    return payload


@app.post("/api/scan-reports", status_code=201)
async def create_scan_report_api(
    payload: dict,
    x_multillm_tenant: Optional[str] = Header(None),
):
    """Ingest a bounded, prompt-free scan report for the authenticated tenant."""
    tenant_id = _scan_tenant(x_multillm_tenant)
    try:
        report_id = _orchestration_store().create_scan_report(
            tenant_id, _validate_scan_report_payload(payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"data": {"id": report_id}}


@app.get("/api/scan-reports")
async def list_scan_reports_api(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_multillm_tenant: Optional[str] = Header(None),
):
    tenant_id = _scan_tenant(x_multillm_tenant)
    reports = _orchestration_store().list_scan_reports(
        tenant_id, limit=limit, offset=offset
    )
    return {
        "data": reports,
        "meta": {"limit": limit, "offset": offset, "count": len(reports)},
    }


@app.get("/api/scan-reports/summary")
async def scan_report_summary_api(x_multillm_tenant: Optional[str] = Header(None)):
    return {
        "data": _orchestration_store().get_scan_summary(_scan_tenant(x_multillm_tenant))
    }


@app.get("/api/scan-reports/export")
async def export_scan_reports_api(
    format: str = Query("json", pattern="^(json|csv)$"),
    x_multillm_tenant: Optional[str] = Header(None),
):
    tenant_id = _scan_tenant(x_multillm_tenant)
    store = _orchestration_store()
    if format == "csv":
        return Response(
            content=store.scan_findings_csv(tenant_id),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=scan-findings.csv"},
        )
    return {"data": store.export_scan_findings(tenant_id)}


@app.get("/api/scan-reports/{report_id}")
async def scan_report_detail_api(
    report_id: str, x_multillm_tenant: Optional[str] = Header(None)
):
    report = _orchestration_store().get_scan_report(
        _scan_tenant(x_multillm_tenant), report_id
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Scan report not found")
    return {"data": report}


@app.get("/api/status")
async def status_api():
    """Operational status summary for dashboards, slash commands, and MCP clients."""
    from .claude_stats import HISTORY_FILE, PROJECTS_DIR, STATS_FILE
    from .codex_stats import STATE_DB
    from .gemini_stats import PROJECTS_FILE, SESSIONS_DIR

    health = all_health_status()
    healthy_backends = sum(1 for item in health.values() if item.get("healthy"))
    unsafe_open_mode = not auth_enabled() and not is_loopback_host(GATEWAY_HOST)
    exposure = validate_gateway_exposure(
        host=GATEWAY_HOST,
        api_key="configured" if auth_enabled() else "",
        allow_unauthenticated_remote=MULTILLM_ALLOW_UNAUTHENTICATED_REMOTE,
    )

    return {
        "status": "ok",
        "version": __version__,
        "project": PROJECT,
        "gateway": {
            "host": GATEWAY_HOST,
            "port": GATEWAY_PORT,
            "reload": GATEWAY_RELOAD,
            "dashboard_url": f"http://localhost:{GATEWAY_PORT}/dashboard",
            "auth_enabled": auth_enabled(),
            "unsafe_open_mode": unsafe_open_mode,
            "exposure": exposure.to_dict(),
            "cors_origins": parse_cors_origins(GATEWAY_CORS_ORIGINS, port=GATEWAY_PORT),
        },
        "runtime": {
            "routes": len(ROUTES),
            "adapters": len(list_adapters()),
            "data_dir": str(DATA_DIR),
            "log_file": str(DATA_DIR / "gateway.log"),
        },
        "tools": {
            "codex_cli": bool(resolve_cli_binary("codex", env_var="CODEX_CLI_PATH")),
            "gemini_cli": bool(resolve_cli_binary("gemini", env_var="GEMINI_CLI_PATH")),
        },
        "direct_clients": {
            "claude_code": {
                "available": STATS_FILE.exists()
                or HISTORY_FILE.exists()
                or PROJECTS_DIR.exists(),
                "source": str(STATS_FILE.parent),
            },
            "codex_cli": {
                "available": STATE_DB.exists(),
                "source": str(STATE_DB),
            },
            "gemini_cli": {
                "available": SESSIONS_DIR.exists() or PROJECTS_FILE.exists(),
                "source": str(PROJECTS_FILE.parent),
            },
        },
        "health": {
            "healthy_backends": healthy_backends,
            "total_backends": len(health),
            "circuit_breakers": all_breaker_status(),
        },
    }


@app.get("/api/sessions")
async def sessions_api(
    hours: int = 168, project: Optional[str] = None, limit: int = 50
):
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

    def _backend_auth_metadata(backend: str) -> dict:
        auth_backends = {
            "openai": ("OPENAI_API_KEY", bool(OPENAI_KEY)),
            "openrouter": ("OPENROUTER_API_KEY", bool(OPENROUTER_KEY)),
            "gemini": ("GEMINI_API_KEY or GOOGLE_API_KEY", bool(GEMINI_KEY)),
            "groq": ("GROQ_API_KEY", bool(GROQ_KEY)),
            "deepseek": ("DEEPSEEK_API_KEY", bool(DEEPSEEK_KEY)),
            "mistral": ("MISTRAL_API_KEY", bool(MISTRAL_KEY)),
            "together": ("TOGETHER_API_KEY", bool(TOGETHER_KEY)),
            "xai": ("XAI_API_KEY", bool(XAI_KEY)),
            "fireworks": ("FIREWORKS_API_KEY", bool(FIREWORKS_KEY)),
        }
        if backend in auth_backends:
            env_var, authenticated = auth_backends[backend]
            return {
                "requires_auth": True,
                "authenticated": authenticated,
                "status_hint": "configured" if authenticated else "unconfigured",
                "note": None if authenticated else f"Set {env_var}",
            }
        return {
            "requires_auth": False,
            "authenticated": None,
            "status_hint": "local",
            "note": None,
        }

    def _format_catalog_source(value: str) -> str:
        labels = {
            "api": "live API",
            "local_api": "local API",
            "cache": "cache file",
            "fallback": "fallback list",
        }
        return labels.get(value, value or "unknown")

    summary = {}
    for backend, models in discovered.items():
        auth = _backend_auth_metadata(backend)
        catalog_available = len(models) > 0
        catalog_source = (
            models[0].get("catalog_source", "api") if catalog_available else ""
        )

        if auth["requires_auth"]:
            runnable = bool(auth["authenticated"]) and catalog_available
            if runnable:
                status = "available"
                note = None
            elif catalog_available:
                status = "catalog_only"
                note = (
                    f"Catalog loaded from {_format_catalog_source(catalog_source)}; "
                    f"{auth['note'] or 'authenticate to use this backend'}"
                )
            else:
                status = auth["status_hint"]
                note = auth["note"]
        else:
            runnable = catalog_available
            status = "available" if runnable else "offline"
            note = None if runnable else "Local service not reachable"

        summary[backend] = {
            "available": runnable,
            "catalog_available": catalog_available,
            "catalog_source": catalog_source or None,
            "status": status,
            "requires_auth": auth["requires_auth"],
            "authenticated": auth["authenticated"],
            "note": note,
            "model_count": len(models),
            "models": [
                {
                    "id": m["id"],
                    "name": m.get("name", ""),
                    "model": m["model"],
                    "catalog_source": m.get("catalog_source"),
                }
                for m in models
            ],
        }

    # Merge in the local CLI-agent backends (claude / codex / gemini / agy). These
    # are subprocess tools, not HTTP endpoints, so discover_all_models() never
    # probes them — detection is a cheap PATH lookup done here on each call.
    from .cli_discovery import discover_cli_agents, fusion_capability, moa_capability

    summary.update(discover_cli_agents(ROUTES))
    summary["fusion"] = fusion_capability(summary)
    summary["moa"] = moa_capability(summary)
    return {
        "backends": summary,
        "total_routes": len(ROUTES),
        "discovery": {
            "refreshed": bool(refresh),
            "observed_at": time.time(),
            "eligibility": "live discovery and a healthy configured provider are required; catalog entries alone are not availability proof",
        },
    }


@app.post("/api/routes")
async def add_route(request: Request):
    """Dynamically add or update a route."""
    data = await request.json()
    alias = data.get("alias")
    backend = data.get("backend")
    model = data.get("model")
    if not alias or not backend or not model:
        raise HTTPException(
            status_code=400, detail="alias, backend, and model are required"
        )
    ROUTES[alias] = {"backend": backend, "model": model, "dynamic": True}
    return {
        "status": "ok",
        "alias": alias,
        "route": ROUTES[alias],
        "total_routes": len(ROUTES),
    }


@app.delete("/api/routes/{alias:path}")
async def delete_route(alias: str):
    """Remove a dynamically added route."""
    if alias not in ROUTES:
        raise HTTPException(status_code=404, detail=f"Route not found: {alias}")
    removed = ROUTES.pop(alias)
    return {"status": "ok", "removed": alias, "route": removed}


@app.get("/api/claude-stats")
async def claude_stats_api(hours: Optional[int] = None, project: Optional[str] = None):
    """Get Claude Code token usage, costs, and session history."""
    return get_claude_code_stats(hours=hours, project=project)


@app.get("/api/codex-stats")
async def codex_stats_api(hours: int = 168, project: Optional[str] = None):
    """Get Codex CLI token usage, costs, and session history."""
    return get_codex_stats(hours=hours, project=project)


@app.get("/api/gemini-stats")
async def gemini_stats_api(hours: int = 168, project: Optional[str] = None):
    """Get Gemini CLI token usage, costs, and session history."""
    return get_gemini_stats(hours=hours, project=project)


def _build_all_llm_usage(
    *,
    hours: int,
    project: Optional[str],
    gw: Optional[dict] = None,
    claude: Optional[dict] = None,
    codex: Optional[dict] = None,
    gemini: Optional[dict] = None,
) -> dict:
    """Unified cross-LLM usage ledger — merges gateway, Claude Code, Codex CLI, and Gemini CLI."""
    from datetime import date as _date

    today_str = _date.today().isoformat()

    gw = gw if gw is not None else get_dashboard_stats(hours=hours, project=project)
    claude = (
        claude
        if claude is not None
        else get_claude_code_stats(hours=hours, project=project)
    )
    codex = (
        codex if codex is not None else get_codex_stats(hours=hours, project=project)
    )
    gemini = (
        gemini if gemini is not None else get_gemini_stats(hours=hours, project=project)
    )

    observability = build_llm_observability_summary(
        hours=hours,
        gateway_stats=gw,
        claude_stats=claude,
        codex_stats=codex,
        gemini_stats=gemini,
    )

    # Build unified summary
    sources = []
    grand_tokens = 0
    grand_cost = 0.0
    grand_list_price = 0.0
    all_models = []

    # --- Gateway ---
    gw_tokens = (gw.get("totals", {}).get("total_input", 0) or 0) + (
        gw.get("totals", {}).get("total_output", 0) or 0
    )
    gw_cost = gw.get("totals", {}).get("total_cost", 0) or 0.0
    sources.append(
        {
            "source": "gateway",
            "available": True,
            "tokens": gw_tokens,
            "costUSD": round(gw_cost, 4),
            "requests": gw.get("totals", {}).get("total_requests", 0),
            "sessions": gw.get("session_count", 0),
        }
    )
    grand_tokens += gw_tokens
    grand_cost += gw_cost
    grand_list_price += gw_cost
    for m in gw.get("by_model", []):
        mcost = round(m.get("cost_usd", 0) or 0, 4)
        all_models.append(
            {
                "model": m.get("model_alias", ""),
                "backend": m.get("backend", "gateway"),
                "source": "gateway",
                "tokens": (m.get("input_tokens", 0) or 0)
                + (m.get("output_tokens", 0) or 0),
                "requests": m.get("requests", 0),
                "actualCostUSD": mcost,
                "listPriceUSD": mcost,
            }
        )

    # --- Claude Code ---
    claude_tokens = 0
    claude_cost = 0.0
    if claude.get("available"):
        for model, u in claude.get("modelUsage", {}).items():
            mtok = (
                (u.get("inputTokens", 0) or 0)
                + (u.get("outputTokens", 0) or 0)
                + (u.get("cacheReadInputTokens", 0) or 0)
                + (u.get("cacheCreationInputTokens", 0) or 0)
            )
            mcost = u.get("estimatedCostUSD", 0) or 0.0
            claude_tokens += mtok
            claude_cost += mcost
            all_models.append(
                {
                    "model": model,
                    "backend": "claude_code",
                    "source": "claude_code",
                    "tokens": mtok,
                    "requests": 0,
                    "actualCostUSD": round(mcost, 2),
                    "listPriceUSD": round(mcost, 2),
                }
            )

    sources.append(
        {
            "source": "claude_code",
            "available": claude.get("available", False),
            "tokens": claude_tokens,
            "actualCostUSD": round(claude_cost, 2),
            "listPriceUSD": round(claude_cost, 2),
            "sessions": claude.get("totalSessions", 0),
            "messages": claude.get("totalMessages", 0),
            "dataAsOf": claude.get("latestDate", ""),
        }
    )
    grand_tokens += claude_tokens
    grand_cost += claude_cost
    grand_list_price += claude_cost

    # --- Codex CLI ---
    codex_tokens = codex.get("totalTokens", 0) if codex.get("available") else 0
    codex_actual = codex.get("totalActualCostUSD", 0) if codex.get("available") else 0.0
    codex_list = codex.get("totalListPriceUSD", 0) if codex.get("available") else 0.0
    if codex.get("available"):
        for model, agg in codex.get("byModel", {}).items():
            all_models.append(
                {
                    "model": model,
                    "backend": "codex_cli",
                    "source": "codex_cli",
                    "tokens": agg.get("tokens", 0),
                    "requests": agg.get("sessions", 0),
                    "actualCostUSD": round(agg.get("actualCostUSD", 0), 4),
                    "listPriceUSD": round(agg.get("listPriceUSD", 0), 4),
                }
            )

    sources.append(
        {
            "source": "codex_cli",
            "available": codex.get("available", False),
            "tokens": codex_tokens,
            "actualCostUSD": round(codex_actual, 4),
            "listPriceUSD": round(codex_list, 4),
            "sessions": codex.get("totalSessions", 0),
            "byProvider": codex.get("byProvider", {}),
        }
    )
    grand_tokens += codex_tokens
    grand_cost += codex_actual
    grand_list_price += codex_list

    # --- Gemini CLI ---
    gemini_tokens = gemini.get("totalTokens", 0) if gemini.get("available") else 0
    gemini_cost = (
        gemini.get("totalEstimatedCostUSD", 0) if gemini.get("available") else 0.0
    )
    if gemini.get("available"):
        all_models.append(
            {
                "model": gemini.get("model", "gemini-2.5-pro"),
                "backend": "gemini_cli",
                "source": "gemini_cli",
                "tokens": gemini_tokens,
                "requests": gemini.get("totalSessions", 0),
                "actualCostUSD": round(gemini_cost, 4),
                "listPriceUSD": round(gemini_cost, 4),
            }
        )

    sources.append(
        {
            "source": "gemini_cli",
            "available": gemini.get("available", False),
            "tokens": gemini_tokens,
            "actualCostUSD": round(gemini_cost, 4),
            "listPriceUSD": round(gemini_cost, 4),
            "sessions": gemini.get("totalSessions", 0),
            "byProject": gemini.get("byProject", {}),
        }
    )
    grand_tokens += gemini_tokens
    grand_cost += gemini_cost
    grand_list_price += gemini_cost

    # Sort models by tokens descending
    all_models.sort(key=lambda x: x["tokens"], reverse=True)

    # Today's breakdown
    today = {"date": today_str, "sources": {}}

    # Claude today
    if claude.get("available"):
        for entry in claude.get("dailyActivity", []):
            if entry.get("date") == today_str:
                today["sources"]["claude_code"] = {
                    "messages": entry.get("messageCount", 0),
                    "sessions": entry.get("sessionCount", 0),
                }

    # Codex today
    if codex.get("available"):
        for d in codex.get("daily", []):
            if d.get("date") == today_str:
                today["sources"]["codex_cli"] = {
                    "tokens": d.get("tokens", 0),
                    "sessions": d.get("sessions", 0),
                    "actualCostUSD": d.get("actualCostUSD", 0),
                    "listPriceUSD": d.get("listPriceUSD", 0),
                    "models": d.get("models", []),
                }

    # Gemini today
    if gemini.get("available"):
        for d in gemini.get("daily", []):
            if d.get("date") == today_str:
                today["sources"]["gemini_cli"] = {
                    "tokens": d.get("totalTokens", 0),
                    "sessions": d.get("sessions", 0),
                    "costUSD": d.get("costUSD", 0),
                }

    return {
        "hours": hours,
        "project": project,
        "grandTotalTokens": grand_tokens,
        "grandTotalCostUSD": round(grand_cost, 2),
        "grandTotalListPriceUSD": round(grand_list_price, 2),
        "sources": sources,
        "byModel": all_models,
        "today": today,
        "statusBySource": observability["statusBySource"],
        "limits": observability["limits"],
    }


@app.get("/api/all-llm-usage")
async def all_llm_usage_api(hours: int = 168, project: Optional[str] = None):
    """Unified cross-LLM usage ledger — merges gateway, Claude Code, Codex CLI, and Gemini CLI."""
    return _build_all_llm_usage(hours=hours, project=project)


def _limit_direct_sessions(payload: dict, *, limit: int) -> dict:
    sessions = payload.get("sessions")
    if isinstance(sessions, list) and len(sessions) > limit:
        payload["sessionCountBeforeLimit"] = len(sessions)
        payload["sessions"] = sessions[:limit]
        payload["sessionsTruncated"] = True
    else:
        payload["sessionsTruncated"] = False
    return payload


def _compute_dashboard_bundle(
    *, hours: int, project: Optional[str], session_limit: int, direct_session_limit: int
) -> dict:
    """Blocking single-pass bundle compute (gateway SQL + direct-history scans).

    Runs off the event loop via ``asyncio.to_thread`` — every call here is
    synchronous, CPU/IO-bound, and safe to execute in a worker thread because
    each stats function opens and closes its own SQLite connection.
    """
    started = time.perf_counter()
    gw = get_dashboard_stats(hours=hours, project=project)
    sessions = get_sessions(hours=hours, project=project, limit=session_limit)
    claude = get_claude_code_stats(hours=hours, project=project)
    codex = _limit_direct_sessions(
        get_codex_stats(hours=hours, project=project), limit=direct_session_limit
    )
    gemini = _limit_direct_sessions(
        get_gemini_stats(hours=hours, project=project), limit=direct_session_limit
    )
    unified = _build_all_llm_usage(
        hours=hours,
        project=project,
        gw=gw,
        claude=claude,
        codex=codex,
        gemini=gemini,
    )
    return {
        "stats": gw,
        "sessions": sessions,
        "claudeStats": claude,
        "codexStats": codex,
        "geminiStats": gemini,
        "unified": unified,
        "performance": {
            "elapsedMs": round((time.perf_counter() - started) * 1000, 2),
            "strategy": "swr_bundled_single_pass",
            "cacheTtlSeconds": bundle_cache.FRESH_TTL_SECONDS,
            "costCalculation": "gateway_sql_plus_cached_direct_client_scans",
            "gatewaySessionLimit": session_limit,
            "directSessionLimit": direct_session_limit,
            "longRange": hours >= 8760,
        },
    }


async def _prime_dashboard_bundle() -> None:
    """Background-compute the dashboard's default range (Last 7 days) at startup.

    Keeps the persisted cache fresh so the very first page load after a restart
    renders current data without a cold 20s scan. Failures are non-fatal.
    """
    hours, session_limit, direct_session_limit = 168, 50, 100
    key = bundle_cache.make_key(
        hours=hours,
        project=None,
        session_limit=session_limit,
        direct_session_limit=direct_session_limit,
    )

    async def _compute() -> dict:
        return await asyncio.to_thread(
            _compute_dashboard_bundle,
            hours=hours,
            project=None,
            session_limit=session_limit,
            direct_session_limit=direct_session_limit,
        )

    try:
        await bundle_cache.get_bundle(key, _compute, force=True)
        log.info("dashboard bundle primed for default range (last 7 days)")
    except Exception as exc:
        log.warning("dashboard bundle prime failed: %s", exc)


@app.get("/api/dashboard-bundle")
async def dashboard_bundle_api(
    hours: int = Query(720, ge=1, le=43800),
    project: Optional[str] = None,
    session_limit: int = Query(50, ge=1, le=500),
    direct_session_limit: int = Query(100, ge=1, le=1000),
    refresh: bool = False,
):
    """Dashboard payload served with stale-while-revalidate caching.

    The cold compute scans Claude/Codex/Gemini history and can take 20s+. With
    SWR the page gets an instant response from the persisted cache and the slow
    recompute happens in the background. ``refresh=true`` forces a fresh compute
    (the dashboard "Refresh" button) and waits for it.
    """
    key = bundle_cache.make_key(
        hours=hours,
        project=project,
        session_limit=session_limit,
        direct_session_limit=direct_session_limit,
    )

    async def _compute() -> dict:
        return await asyncio.to_thread(
            _compute_dashboard_bundle,
            hours=hours,
            project=project,
            session_limit=session_limit,
            direct_session_limit=direct_session_limit,
        )

    return await bundle_cache.get_bundle(key, _compute, force=refresh)


@app.get("/api/usage-report")
async def usage_report_api(
    kind: str = Query("daily", pattern="^(daily|weekly|monthly|session|blocks)$"),
    hours: int = Query(720, ge=1, le=43800),
    project: Optional[str] = None,
    session_limit: int = Query(100, ge=1, le=1000),
    direct_session_limit: int = Query(250, ge=1, le=2000),
    refresh: bool = False,
):
    """Calendar/session usage reports over gateway + direct CLI usage."""
    key = bundle_cache.make_key(
        hours=hours,
        project=project,
        session_limit=session_limit,
        direct_session_limit=direct_session_limit,
    )

    async def _compute() -> dict:
        return await asyncio.to_thread(
            _compute_dashboard_bundle,
            hours=hours,
            project=project,
            session_limit=session_limit,
            direct_session_limit=direct_session_limit,
        )

    bundle = await bundle_cache.get_bundle(key, _compute, force=refresh)
    return build_usage_report(bundle, kind=kind)


async def _cached_unified(hours: int, project: Optional[str]) -> dict:
    """Fetch the unified usage payload via the SWR bundle cache (no extra scan)."""
    key = bundle_cache.make_key(
        hours=hours, project=project, session_limit=50, direct_session_limit=100
    )

    async def _compute() -> dict:
        return await asyncio.to_thread(
            _compute_dashboard_bundle,
            hours=hours,
            project=project,
            session_limit=50,
            direct_session_limit=100,
        )

    bundle = await bundle_cache.get_bundle(key, _compute)
    return bundle.get("unified") or {}


@app.get("/api/cost/forecast")
async def cost_forecast_api(
    hours: int = Query(168, ge=1, le=43800), project: Optional[str] = None
):
    """Burn-rate, projected spend (today/month), and quota-exhaustion ETAs.

    Reuses the cached unified payload and cheap gateway SQL windows, so it does
    not trigger a fresh history scan.
    """
    unified = await _cached_unified(hours, project)
    gw_recent_1h = await asyncio.to_thread(
        get_dashboard_stats, hours=1, project=project
    )
    gw_recent_24h = await asyncio.to_thread(
        get_dashboard_stats, hours=24, project=project
    )

    return cost_forecast.build_cost_forecast(
        unified=unified,
        gw_recent_1h=gw_recent_1h,
        gw_recent_24h=gw_recent_24h,
        window_hours=hours,
    )


@app.post("/api/cost/estimate")
async def cost_estimate_api(body: dict | None = None):
    """Pre-flight cost estimate of a prompt across candidate model aliases.

    Body: ``{"prompt": "...", "models": ["openai/gpt-4o", ...],
    "expected_output_tokens": 500}``. ``models`` is optional — omit to price
    every known route. Returns estimates sorted cheapest-first.
    """
    body = body or {}
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing 'prompt'")
    candidates = body.get("models") or None
    expected_output = body.get(
        "expected_output_tokens", cost_forecast.DEFAULT_EXPECTED_OUTPUT_TOKENS
    )
    return cost_forecast.estimate_prompt_cost(
        prompt=prompt,
        routes=ROUTES,
        candidates=candidates,
        expected_output_tokens=expected_output,
    )


async def _council_query_one(
    alias: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    controls: dict[str, str] | None = None,
) -> dict:
    """Query one model for the council; never raises — errors are captured."""
    body = {
        "model": alias,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if controls:
        body["metadata"] = {"multillm_execution": dict(controls)}
        if controls.get("structured_output") == "verifier":
            body["output_schema"] = {
                "name": "multillm_verifier_verdict",
                "schema": {
                    "type": "object",
                    "properties": {
                        "correctness": {"type": "number", "minimum": 0, "maximum": 1},
                        "completeness": {"type": "number", "minimum": 0, "maximum": 1},
                        "evidence_support": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "uncertainty": {"type": "number", "minimum": 0, "maximum": 1},
                        "defects": {"type": "array", "items": {"type": "string"}},
                        "accepted": {"type": "boolean"},
                    },
                    "required": [
                        "correctness",
                        "completeness",
                        "evidence_support",
                        "uncertainty",
                        "defects",
                        "accepted",
                    ],
                    "additionalProperties": False,
                },
            }
        elif controls.get("structured_output") == "comparison":
            properties = {
                key: {"type": "array", "items": {"type": "string"}}
                for key in (
                    "consensus",
                    "contradictions",
                    "unsupported_claims",
                    "partial_coverage",
                    "unique_insights",
                    "blind_spots",
                )
            }
            properties["best_response_index"] = {"type": "integer", "minimum": 0}
            body["output_schema"] = {
                "name": "multillm_comparison",
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            }
    t0 = time.time()
    try:
        result = await route_request(body)
        usage = _extract_usage_metrics(result)
        content = result.get("content", []) or []
        text = next((b.get("text", "") for b in content if b.get("type") == "text"), "")
        route = ROUTES.get(alias, {})
        backend = route.get("backend", "")
        profile = _effective_model_registry().get(alias)
        if profile is not None:
            billable_input = usage["input_tokens"]
            if profile.provider in {"openai", "azure_openai"}:
                billable_input = max(
                    0,
                    usage["input_tokens"]
                    - usage["cache_read_input_tokens"]
                    - usage["cache_creation_input_tokens"],
                )
            cost = profile.pricing.estimate(
                input_tokens=billable_input,
                output_tokens=usage["output_tokens"],
                cached_read_tokens=usage["cache_read_input_tokens"],
                cache_write_tokens=usage["cache_creation_input_tokens"],
                reasoning_tokens=usage["reasoning_tokens"],
            )
        else:
            cost = _estimate_cost(
                backend,
                usage["input_tokens"],
                usage["output_tokens"],
                usage["cache_read_input_tokens"],
                usage["cache_creation_input_tokens"],
            )
        return {
            "alias": alias,
            "backend": backend,
            "text": text,
            "inputTokens": usage["input_tokens"],
            "outputTokens": usage["output_tokens"],
            "cacheReadInputTokens": usage["cache_read_input_tokens"],
            "cacheWriteInputTokens": usage["cache_creation_input_tokens"],
            "reasoningTokens": usage["reasoning_tokens"],
            "serviceTier": usage["service_tier"],
            "providerModel": usage["provider_model"],
            "actualCostUSD": round(cost, 6),
            "latencyMs": round((time.time() - t0) * 1000, 1),
            "error": None,
        }
    except HTTPException as e:
        return {
            "alias": alias,
            "text": "",
            "error": f"{e.status_code}: {e.detail}",
            "actualCostUSD": 0.0,
            "latencyMs": round((time.time() - t0) * 1000, 1),
        }
    except Exception as e:  # noqa: BLE001 — one model failing must not sink the council
        return {
            "alias": alias,
            "text": "",
            "error": str(e),
            "actualCostUSD": 0.0,
            "latencyMs": round((time.time() - t0) * 1000, 1),
        }


@app.post("/api/council")
async def council_api(body: dict | None = None):
    """Query several models in parallel, with pre-flight cost estimates.

    Body: ``{"prompt": "...", "models": ["ollama/qwen3-30b", "openai/gpt-4o"],
    "max_tokens": 2048, "temperature": 0.7}``. Returns a cheapest-first cost
    estimate for the panel *before* spending, then each model's response with its
    actual token cost, plus combined totals — so multi-model opinions are
    cost-aware (the user can see projected vs actual spend).
    """
    body = body or {}
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing 'prompt'")

    mode = str(body.get("mode", "raw")).strip().lower()
    if mode not in {"raw", "adaptive", "synthesized"}:
        raise HTTPException(
            status_code=400, detail="mode must be raw, adaptive, or synthesized"
        )

    if mode != "raw":
        result = await _run_adaptive(
            body,
            preset=body.get("preset"),
            candidates=body.get("models") or None,
            force_deliberation=mode == "synthesized",
        )
        if mode == "adaptive":
            return {
                **result,
                "mode": mode,
                "responses": result.get("panel", []),
                "finalAnswer": None,
            }
        return {**result, "mode": mode, "responses": result.get("panel", [])}

    from .memory import get_setting

    models = body.get("models") or get_setting(
        "auto_council_models", ["ollama/qwen3-30b", "codex/gpt-5-5", "gemini/flash"]
    )
    max_tokens = int(body.get("max_tokens", 2048))
    temperature = float(body.get("temperature", 0.7))

    # Pre-flight: what will this cost across the chosen models?
    estimate = cost_forecast.estimate_prompt_cost(
        prompt=prompt,
        routes=ROUTES,
        candidates=models,
        expected_output_tokens=max_tokens,
    )

    # Serve an identical repeat from the result cache (skip re-querying the panel).
    cache_enabled, cache_ttl = _cache_enabled()
    cache_key = result_cache.make_key(
        kind="council", prompt=prompt, models=models, judge=None, max_tokens=max_tokens
    )
    if cache_enabled:
        cached = result_cache.get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

    # Query all models concurrently; one failure does not sink the rest.
    responses = await asyncio.gather(
        *[_council_query_one(m, prompt, max_tokens, temperature) for m in models]
    )

    succeeded = [r for r in responses if not r.get("error")]
    total_actual = round(sum(r["actualCostUSD"] for r in succeeded), 6)
    result = {
        "prompt": prompt,
        "models": models,
        "preflightEstimate": estimate,
        "responses": responses,
        "totals": {
            "estimatedCostUSD": round(
                sum(e["estimatedCostUSD"] for e in estimate["estimates"]), 6
            ),
            "actualCostUSD": total_actual,
            "modelsQueried": len(responses),
            "modelsSucceeded": len(succeeded),
        },
    }
    # Cache only when at least one model answered (don't cache total failures).
    if cache_enabled and succeeded:
        result_cache.set(cache_key, result, cache_ttl)
    return result


async def _fusion_query_fn(
    alias: str, prompt: str, max_tokens: int, temperature: float
) -> dict:
    """Query one model for fusion and record its usage (per-backend accuracy).

    Sub-calls don't flow through the main ``messages`` handler, so they are not
    otherwise recorded; logging each here keeps cost tracking, budgets, and the
    forecast accurate for fusion runs.
    """
    is_judge = "judge of a multi-model panel" in prompt.lower()
    controls = {
        "reasoning_effort": "medium" if is_judge else "low",
        "execution_mode": "standard",
        "verbosity": "balanced" if is_judge else "concise",
    }
    r = await _council_query_one(
        alias, prompt, max_tokens, temperature, controls=controls
    )
    if not r.get("error"):
        route = ROUTES.get(alias, {})
        record_usage(
            project=PROJECT,
            model_alias=alias,
            backend=r.get("backend", ""),
            real_model=r.get("providerModel") or route.get("model", alias),
            input_tokens=r.get("inputTokens", 0),
            output_tokens=r.get("outputTokens", 0),
            cache_read_input_tokens=r.get("cacheReadInputTokens", 0),
            cache_creation_input_tokens=r.get("cacheWriteInputTokens", 0),
            reasoning_tokens=r.get("reasoningTokens", 0),
            service_tier=r.get("serviceTier"),
            latency_ms=r.get("latencyMs", 0),
            status="fusion",
        )
        trace_llm_generation(
            model_alias=alias,
            backend=r.get("backend", ""),
            real_model=r.get("providerModel") or route.get("model", alias),
            project=PROJECT,
            input_tokens=r.get("inputTokens", 0),
            output_tokens=r.get("outputTokens", 0),
            reasoning_tokens=r.get("reasoningTokens", 0),
            cache_read_tokens=r.get("cacheReadInputTokens", 0),
            cache_create_tokens=r.get("cacheWriteInputTokens", 0),
            latency_ms=r.get("latencyMs", 0),
            cost_usd=r.get("actualCostUSD", 0),
            status="orchestration",
            prompt_text=prompt,
            response_text=r.get("text", ""),
        )
    return r


async def _adaptive_query_fn(
    alias: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    controls: dict[str, str],
) -> dict:
    result = await _council_query_one(
        alias, prompt, max_tokens, temperature, controls=controls
    )
    if not result.get("error"):
        route = ROUTES.get(alias, {})
        record_usage(
            project=PROJECT,
            model_alias=alias,
            backend=result.get("backend", ""),
            real_model=result.get("providerModel") or route.get("model", alias),
            input_tokens=result.get("inputTokens", 0),
            output_tokens=result.get("outputTokens", 0),
            cache_read_input_tokens=result.get("cacheReadInputTokens", 0),
            cache_creation_input_tokens=result.get("cacheWriteInputTokens", 0),
            reasoning_tokens=result.get("reasoningTokens", 0),
            service_tier=result.get("serviceTier"),
            latency_ms=result.get("latencyMs", 0),
            status="orchestration",
        )
    return result


def _resolve_fusion_config(body: dict) -> tuple[list, str, int, float]:
    """Resolve (panel, judge, max_tokens, temperature) from body + settings."""
    from .memory import get_setting

    md = body.get("metadata") or {}
    panel = (
        body.get("fusion_panel")
        or md.get("fusion_panel")
        or get_setting("fusion_panel", _DEFAULT_FUSION_PANEL)
    )
    judge = (
        body.get("fusion_judge")
        or md.get("fusion_judge")
        or get_setting("fusion_judge", _DEFAULT_FUSION_JUDGE)
    )
    max_tokens = int(body.get("max_tokens", 1024))
    temperature = float(body.get("temperature", 0.7))
    return panel, judge, max_tokens, temperature


def _cache_enabled() -> tuple[bool, float]:
    """(enabled, ttl) for the council/fusion result cache, from settings."""
    from .memory import get_setting

    enabled = bool(get_setting("council_fusion_cache_enabled", True))
    ttl = float(
        get_setting("council_fusion_cache_ttl", result_cache.DEFAULT_TTL_SECONDS)
    )
    return enabled, ttl


async def _run_fusion(body: dict) -> dict:
    """Run the fusion pipeline for a request body and return the full result.

    Identical repeat requests are served from the result cache (exact prompt +
    panel + judge), avoiding a full re-query of the panel.
    """
    prompt = extract_text_from_anthropic(body)
    panel, judge, max_tokens, temperature = _resolve_fusion_config(body)

    enabled, ttl = _cache_enabled()
    key = result_cache.make_key(
        kind="fusion", prompt=prompt, models=panel, judge=judge, max_tokens=max_tokens
    )
    if enabled:
        cached = result_cache.get(key)
        if cached is not None:
            return {**cached, "cached": True}

    result = await fusion.run_fusion(
        prompt=prompt,
        panel=panel,
        judge=judge,
        query_fn=_fusion_query_fn,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    # Langfuse: one parent span per fusion run with child generations (panel + judge).
    trace_fusion_run(
        kind="fusion",
        prompt=prompt,
        panel_results=result.get("panel", []),
        judge=result.get("judge"),
        analysis=result.get("analysis", ""),
        final_answer=result.get("finalAnswer", ""),
        status=result.get("status", ""),
        project=PROJECT,
    )
    # Only cache real syntheses, not degraded/no-panel outcomes.
    if enabled and result.get("status") in ("fused", "single"):
        result_cache.set(key, result, ttl)
    return result


async def _run_moa_request(body: dict) -> dict:
    """Run the canonical layered Mixture of Agents pipeline."""
    prompt = str(body.get("prompt") or "").strip()
    models = (
        body.get("models") or body.get("moa_panel") or list(moa.DEFAULT_PROPOSER_MODELS)
    )
    aggregator = str(
        body.get("aggregator")
        or body.get("moa_aggregator")
        or moa.DEFAULT_AGGREGATOR_MODEL
    ).strip()
    preset = str(body.get("preset") or "quality").strip().lower()
    refiners = body.get("refiner_layers")
    if refiners is None:
        refiners = [models] if preset in {"quality", "critical"} else []
        if preset == "critical":
            refiners = [models, models]
    try:
        config = moa.MoAConfig(
            proposer_models=tuple(models),
            refiner_layers=tuple(tuple(layer) for layer in refiners),
            aggregator_model=aggregator,
            max_tokens=int(body.get("max_tokens", 4096)),
            temperature=float(body.get("temperature", 0.2)),
            max_context_chars=int(body.get("max_context_chars", 48_000)),
            per_call_timeout_seconds=float(body.get("timeout_seconds", 180)),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid MoA configuration: {exc}"
        ) from exc
    result = await moa.run_moa(prompt=prompt, config=config, query_fn=_fusion_query_fn)
    trace_fusion_run(
        kind="moa",
        prompt=prompt,
        panel_results=[],
        judge=aggregator,
        analysis=str(result.get("analysis", {})),
        final_answer=result.get("finalAnswer", ""),
        status=result.get("status", ""),
        project=PROJECT,
    )
    return result


async def _run_adaptive(
    body: dict,
    *,
    preset: str | None = None,
    candidates: list[str] | None = None,
    force_deliberation: bool = False,
) -> dict:
    """Run adaptive fusion, persist a sanitized trace, and preserve legacy fields."""
    prompt = extract_text_from_anthropic(body)
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing prompt content")
    policy = _resolve_orchestration_policy(body, preset=preset)
    try:
        max_tokens = int(body.get("max_tokens", 1024))
        temperature = float(body.get("temperature", 0.2))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="max_tokens and temperature must be numeric"
        ) from exc
    if not 1 <= max_tokens <= 131_072:
        raise HTTPException(
            status_code=400, detail="max_tokens must be from 1 to 131072"
        )
    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise HTTPException(status_code=400, detail="temperature must be from 0 to 2")
    has_images = any(
        isinstance(message.get("content"), list)
        and any(block.get("type") == "image" for block in message["content"])
        for message in body.get("messages", [])
    )
    evidence_pack = None
    raw_evidence = body.get("evidence") or []
    if raw_evidence:
        if not isinstance(raw_evidence, list) or len(raw_evidence) > 20:
            raise HTTPException(
                status_code=400,
                detail="evidence must be an array of at most 20 sources",
            )
        try:
            evidence_sources = [
                EvidenceSource.model_validate(item) for item in raw_evidence
            ]
            validated_sources = []
            for source in evidence_sources:
                validated_url = await validate_public_url(source.url)
                validated_sources.append(
                    source.model_copy(update={"url": validated_url})
                )
            evidence_pack = build_evidence_pack(validated_sources, max_sources=6)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid evidence: {exc}"
            ) from exc
    try:
        raw_scorecards = _orchestration_store().get_scorecards("default")
    except Exception as exc:
        log.warning("Could not load orchestration scorecards: %s", exc)
        raw_scorecards = []
    scorecards = {
        (item["model"], item["task_type"]): ModelScorecard.model_validate(
            {
                "model": item["model"],
                "task_type": item["task_type"],
                "quality_mean": item["quality_mean"],
                "reliability_mean": item["reliability_mean"],
                "avg_cost_usd": item["avg_cost_usd"],
                "sample_count": item["sample_count"],
                "confidence_lower": item["confidence_lower"],
            }
        )
        for item in raw_scorecards
    }
    providers = {profile.provider for profile in _effective_model_registry().profiles}
    health_scores = {}
    for provider in providers:
        try:
            health_scores[provider] = float(score_backend(provider).get("score", 0.5))
        except Exception:
            health_scores[provider] = 0.5
    engine = AdaptiveOrchestrator(
        registry=_effective_model_registry(),
        query_fn=_adaptive_query_fn,
        scorecards=scorecards,
        health_scores=health_scores,
    )
    result = await engine.run(
        prompt=prompt,
        policy=policy,
        candidates=candidates,
        max_tokens=max_tokens,
        temperature=temperature,
        has_images=has_images,
        has_tools=bool(body.get("tools")),
        force_deliberation=force_deliberation,
        evidence_pack=evidence_pack,
    )
    if evidence_pack is not None:
        result = {
            **result,
            "evidence": {
                "sourceCount": len(evidence_pack.sources),
                "sources": [
                    {
                        "url": source.url,
                        "title": source.title,
                        "publishedAt": source.published_at,
                        "contentHash": source.content_hash,
                    }
                    for source in evidence_pack.sources
                ],
            },
        }

    # Prompt text and answer content are intentionally absent from persistence.
    task_features = result.get("decision", {}).get("task", {})
    try:
        store = _orchestration_store()
        persisted_id = store.create_run(
            "default",
            prompt,
            policy.model_dump(mode="json"),
            task_features,
        )
        for stage in result.get("stages", []):
            if not stage.get("model"):
                continue
            store.record_call(
                tenant_id="default",
                run_id=persisted_id,
                stage=stage.get("stage", "unknown"),
                model=stage["model"],
                effort=stage.get("effort", "none"),
                usage={
                    "input_tokens": stage.get("input_tokens", 0),
                    "output_tokens": stage.get("output_tokens", 0),
                    "cache_read_tokens": stage.get("cache_read_tokens", 0),
                    "cache_write_tokens": stage.get("cache_write_tokens", 0),
                    "reasoning_tokens": stage.get("reasoning_tokens", 0),
                },
                cost_usd=stage.get("actual_cost_usd", 0),
                latency_ms=stage.get("latency_ms", 0),
                status=stage.get("status", "unknown"),
            )
        store.complete_run(
            "default",
            persisted_id,
            decision=result.get("decision", {}),
            totals=result.get("totals", {}),
            outcome=result.get("status", "unknown"),
        )
        result = {**result, "runId": persisted_id}
    except Exception as exc:  # trace failure must not lose a completed answer
        log.warning("Could not persist orchestration trace: %s", exc)
    return result


def _fusion_usage(result: dict) -> tuple[int, int]:
    """Aggregate (input, output) tokens across the panel + judge."""
    if result.get("kind") == "moa":
        totals = result.get("totals") or {}
        return int(totals.get("inputTokens", 0) or 0), int(
            totals.get("outputTokens", 0) or 0
        )
    in_tok = sum(
        r.get("inputTokens", 0) for r in result.get("panel", []) if not r.get("error")
    )
    out_tok = sum(
        r.get("outputTokens", 0) for r in result.get("panel", []) if not r.get("error")
    )
    ju = result.get("judgeUsage") or {}
    return in_tok + ju.get("inputTokens", 0), out_tok + ju.get("outputTokens", 0)


def _fusion_to_anthropic(result: dict, model_label: str) -> JSONResponse:
    """Abstract a fusion result into a single Anthropic-format JSON response."""
    text = result.get("finalAnswer") or "[fusion produced no answer]"
    if fusion.FINAL_ANSWER_MARKER in text:
        _, text = fusion.split_judge_output(text)
    in_tok, out_tok = _fusion_usage(result)
    resp = make_anthropic_response(
        text,
        model=model_label,
        input_tokens=in_tok,
        output_tokens=out_tok,
        usage_extras={
            "fusion_status": result.get("status"),
            "fusion_cost_usd": result.get("totals", {}).get("costUSD"),
        },
    )
    return JSONResponse(resp)


def _fusion_to_anthropic_stream(result: dict, model_label: str) -> StreamingResponse:
    """Stream a fusion result as Anthropic SSE (final answer in one delta).

    Fusion must synthesize the whole answer before it can stream, so the answer
    is delivered as a single content delta — valid Anthropic SSE that any client
    expecting a stream (e.g. Claude Code) can consume.
    """
    text = result.get("finalAnswer") or "[fusion produced no answer]"
    if fusion.FINAL_ANSWER_MARKER in text:
        _, text = fusion.split_judge_output(text)
    in_tok, out_tok = _fusion_usage(result)

    async def gen():
        state = StreamState(model_label, input_tokens=in_tok)
        yield make_message_start_event(state)
        yield make_content_block_start_event(0)
        yield make_text_delta_event(0, text)
        yield make_content_block_stop_event(0)
        yield make_message_delta_event("end_turn", out_tok)
        yield make_message_stop_event()

    return StreamingResponse(gen(), media_type="text/event-stream")


def _fusion_response(result: dict, model_label: str, is_streaming: bool):
    """Return the fusion result as JSON or SSE depending on the request."""
    return (_fusion_to_anthropic_stream if is_streaming else _fusion_to_anthropic)(
        result, model_label
    )


@app.post("/api/fusion")
async def fusion_api(body: dict | None = None):
    """Run fusion explicitly and return the full result (panel + analysis + answer).

    Body: ``{"prompt": "...", "fusion_panel": [...], "fusion_judge": "...",
    "max_tokens": 1024, "temperature": 0.7}``. Panel/judge default to the
    fusion_panel/fusion_judge settings. Use the ``fusion`` model slug on
    ``/v1/messages`` instead to get a single abstracted response.
    """
    body = body or {}
    if not body.get("prompt"):
        raise HTTPException(status_code=400, detail="Missing 'prompt'")
    # /api/fusion takes prompt directly; adapt it to the messages body shape.
    adapted = dict(body)
    adapted["messages"] = [{"role": "user", "content": body["prompt"]}]
    explicit = bool(body.get("fusion_panel") or body.get("fusion_judge"))
    if body.get("preset") and not explicit:
        return await _run_adaptive(
            adapted,
            preset=str(body["preset"]),
            candidates=body.get("models") or None,
            force_deliberation=True,
        )
    return await _run_fusion(adapted)


@app.post("/api/moa")
async def moa_api(body: dict | None = None):
    """Run canonical layered Mixture of Agents orchestration.

    ``/api/fusion`` remains available as a backwards-compatible one-layer or
    adaptive entry point; new integrations should use this endpoint.
    """
    body = body or {}
    if not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
        raise HTTPException(status_code=400, detail="Missing 'prompt'")
    models = (
        body.get("models") or body.get("moa_panel") or list(moa.DEFAULT_PROPOSER_MODELS)
    )
    aggregator = (
        body.get("aggregator")
        or body.get("moa_aggregator")
        or moa.DEFAULT_AGGREGATOR_MODEL
    )
    if not isinstance(models, list) or len(models) < 2:
        raise HTTPException(status_code=422, detail="MoA requires at least two models")
    if not isinstance(aggregator, str) or not aggregator.strip():
        raise HTTPException(status_code=422, detail="MoA aggregator is required")
    return await _run_moa_request(
        {**body, "models": list(models), "aggregator": aggregator}
    )


@app.post("/api/adaptive")
async def adaptive_api(body: dict | None = None):
    """Run cheap-first orchestration without requiring forced deliberation."""
    body = body or {}
    if not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
        raise HTTPException(status_code=400, detail="Missing 'prompt'")
    adapted = dict(body)
    adapted["messages"] = [{"role": "user", "content": body["prompt"]}]
    return await _run_adaptive(
        adapted,
        preset=str(body.get("preset") or "balanced"),
        candidates=body.get("models") or None,
        force_deliberation=False,
    )


@app.get("/api/budgets")
async def budgets_status_api():
    """Current budget status: caps, spend, %used, alert states, and enforcement.

    Spend is the gateway-metered cloud cost (rolling 24h / 30d), not flat-rate
    subscriptions — so budgets reflect what the gateway can actually control.
    """
    from .memory import get_setting

    config = get_setting("budgets", {}) or {}
    snap = await asyncio.to_thread(_gateway_spend_snapshot, None)
    project_spend = {}
    for name in config.get("per_project") or {}:
        project_spend[name] = await asyncio.to_thread(_gateway_spend_snapshot, name)

    return budgets.evaluate_budgets(
        config=config,
        spent_today=snap["today"],
        spent_month=snap["month"],
        project_spend=project_spend,
    )


@app.put("/api/budgets")
async def set_budgets_api(body: dict | None = None):
    """Update budget configuration (merged into the persisted ``budgets`` setting).

    Body example::

        {"enabled": true, "daily_usd": 10, "monthly_usd": 200,
         "alert_thresholds": [0.8, 1.0],
         "per_project": {"multillm": {"daily_usd": 5}}}
    """
    from .memory import get_setting, set_setting

    current = get_setting("budgets", {}) or {}
    current.update(body or {})
    set_setting("budgets", current)
    _gateway_spend_snapshot.cache_clear()  # reflect new caps immediately
    return {"status": "ok", "budgets": current}


@app.get("/api/cache")
async def cache_stats_api():
    """Get cache statistics."""
    return get_cache_stats()


@app.get("/api/otel")
async def otel_status_api():
    """Get OpenTelemetry / OCI APM configuration status."""
    from .config import (
        OTEL_ENABLED,
        OTEL_SERVICE_NAME,
        OCI_APM_DOMAIN_ID,
        OCI_APM_ENDPOINT,
        OCI_APM_REGION,
    )
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
        "langfuse": get_langfuse_status(),
        "has_metrics": _meter is not None,
    }


@app.get("/api/rate-limit")
async def rate_limit_api():
    """Get rate limit status and active client info."""
    return rate_limit_status()


@app.get("/api/local/status")
async def local_status_api():
    """Report which local backends are installed (CLI on PATH)."""
    from .memory import get_setting

    return {
        "installed": installed_local_backends(),
        "autostart": get_setting("local_autostart", True),
    }


@app.post("/api/local/start")
async def local_start_api(body: dict | None = None):
    """Start an installed-but-stopped local backend on demand.

    Body: ``{"backend": "ollama"}`` to target one, or omit to start the first
    installed local backend. Triggers a discovery refresh on success.
    """
    backend = (body or {}).get("backend")
    if backend:
        if not is_backend_installed(backend):
            raise HTTPException(
                status_code=404, detail=f"Local backend not installed: {backend}"
            )
        started = await ensure_local_backend(backend)
        ready_backend = backend if started else None
    else:
        ready_backend = await ensure_any_local_backend()

    if ready_backend:
        await _run_discovery()
    return {
        "started": bool(ready_backend),
        "backend": ready_backend,
        "installed": installed_local_backends(),
    }


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


def _routing_pool() -> list[str]:
    """The candidate models the router chooses among (configurable)."""
    from .memory import get_setting

    return get_setting("routing_pool", None) or get_setting(
        "fusion_panel", _DEFAULT_FUSION_PANEL
    )


def _route_decision(
    prompt: str, *, bias: Optional[float] = None, project: Optional[str] = None
) -> dict:
    """Run the log-driven router for a prompt and return its decision."""
    from .memory import get_setting

    if bias is None:
        bias = float(get_setting("routing_quality_bias", 0.5))
    pool = _routing_pool()
    stats = get_model_routing_stats(hours=168, project=project)
    comp = complexity.estimate_complexity(prompt)

    def health_fn(backend: str) -> float:
        try:
            return float(score_backend(backend).get("score", 0.5))
        except Exception:
            return 0.5

    def cost_fn(alias: str) -> float:
        backend = (ROUTES.get(alias, {}) or {}).get("backend", "")
        return float((COST_TABLE.get(backend, {}) or {}).get("input", 0.0))

    decision = query_router.choose_model(
        prompt_complexity=comp["score"],
        pool=pool,
        stats=stats,
        health_fn=health_fn,
        cost_fn=cost_fn,
        bias=float(bias),
        routes=ROUTES,
    )
    decision["complexityReasons"] = comp["reasons"]
    task = classify_task(prompt)
    registry = _effective_model_registry()
    selected = registry.get(decision.get("model") or "")
    decision["taskType"] = task.task_type.value
    decision["risk"] = task.risk.value
    decision["capabilityFilters"] = list(task.required_capabilities)
    decision["predictedQuality"] = (
        round(selected.task_score(task.task_type), 3) if selected else None
    )
    decision["selectedEffort"] = "low" if task.complexity < 0.35 else "medium"
    decision["expectedCostUSD"] = (
        round(
            selected.pricing.estimate(
                input_tokens=max(1, len(prompt) // 4), output_tokens=500
            ),
            6,
        )
        if selected
        else None
    )
    tier_order = ("local", "economy", "balanced", "frontier")
    available_tiers = {profile.tier.value for profile in registry.profiles}
    decision["escalationPath"] = [
        tier for tier in tier_order if tier in available_tiers
    ]
    return decision


@app.get("/api/routing/decision")
async def routing_decision_api(
    prompt: str, bias: Optional[float] = None, project: Optional[str] = None
):
    """Show which model the log-driven router would pick for ``prompt``.

    Blends historical performance (from usage logs), live health, latency, and
    cost, tuned by ``bias`` (0=cheapest/fastest … 1=highest quality). Read-only —
    does not send the prompt anywhere.
    """
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing 'prompt'")
    return _route_decision(prompt, bias=bias, project=project)


@app.get("/api/models/capabilities")
async def model_capabilities_api():
    registry = _effective_model_registry()
    return {
        "profiles": registry.public_profiles(),
        "pricingFreshness": "static_or_discovered",
        "previewModelsRequireDiscovery": True,
    }


@app.get("/api/models/scorecards")
async def model_scorecards_api(min_samples: int = 20, task_type: str | None = None):
    if not 1 <= min_samples <= 1_000_000:
        raise HTTPException(
            status_code=400, detail="min_samples must be from 1 to 1000000"
        )
    if task_type is not None and (not task_type.strip() or len(task_type) > 100):
        raise HTTPException(
            status_code=400,
            detail="task_type must be a non-empty value of at most 100 characters",
        )
    return {
        "scorecards": _orchestration_store().get_scorecards(
            "default", min_samples=min_samples, task_type=task_type
        )
    }


@app.get("/api/models/catalog")
async def model_catalog_api(refresh: bool = False):
    """Return discovery-backed capability, pricing, and scorecard data.

    A configured route is not treated as evidence that a provider is currently
    usable: consumers must use `available` and `classificationSource`.
    """
    discovered = await discover_all_models(force=refresh)
    live = {
        (str(model.get("backend")), str(model.get("model")))
        for models in discovered.values()
        for model in models
    }
    from .cli_discovery import discover_cli_agents

    cli_backends = discover_cli_agents(ROUTES)
    live_aliases = {
        str(model.get("id"))
        for backend in cli_backends.values()
        if backend.get("available")
        for model in backend.get("models", [])
    }
    scorecards = _orchestration_store().get_scorecards("default", min_samples=1)
    by_model = {item["model"]: item for item in scorecards}
    models = []
    for profile in _effective_model_registry().public_profiles():
        observed = (
            profile["provider"],
            profile["provider_model_id"],
        ) in live or profile["alias"] in live_aliases
        try:
            health = float(score_backend(profile["provider"]).get("score", 0.0))
        except Exception:
            health = 0.0
        models.append(
            {
                **profile,
                "available": observed,
                "health": health,
                "classificationSource": (
                    "cli_discovery"
                    if profile["alias"] in live_aliases
                    else "live_discovery"
                    if observed
                    else "route_configuration"
                ),
                "scorecard": by_model.get(profile["alias"]),
            }
        )
    return {"models": models, "refreshed": bool(refresh), "observed_at": time.time()}


@app.get("/api/orchestration/{run_id}")
async def orchestration_trace_api(run_id: str):
    trace = _orchestration_store().get_trace("default", run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    return trace


@app.post("/api/orchestration/{run_id}/feedback")
async def orchestration_feedback_api(run_id: str, body: dict | None = None):
    body = body or {}
    allowed = {"rating", "issue_categories", "preferred_model"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown feedback field(s): {', '.join(sorted(unknown))}",
        )
    issues = body.get("issue_categories") or []
    if not isinstance(issues, list) or len(issues) > 20:
        raise HTTPException(
            status_code=400,
            detail="issue_categories must be an array of at most 20 values",
        )
    if any(not isinstance(issue, str) or len(issue) > 100 for issue in issues):
        raise HTTPException(
            status_code=400,
            detail="issue categories must be strings of at most 100 characters",
        )
    preferred_model = body.get("preferred_model")
    if preferred_model is not None and (
        not isinstance(preferred_model, str) or len(preferred_model) > 200
    ):
        raise HTTPException(
            status_code=400, detail="preferred_model must be at most 200 characters"
        )
    try:
        created = _orchestration_store().add_feedback(
            "default",
            run_id,
            rating=body.get("rating"),
            issue_categories=tuple(issues),
            preferred_model=preferred_model,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not created:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    trace = _orchestration_store().get_trace("default", run_id) or {}
    decision = trace.get("decision") or {}
    selected = decision.get("selectedModels") or []
    observed_model = body.get("preferred_model") or (selected[-1] if selected else None)
    task_type = (trace.get("taskFeatures") or {}).get("task_type") or "general"
    if observed_model:
        _orchestration_store().record_scorecard_observation(
            "default",
            model=observed_model,
            task_type=task_type,
            quality=float(body["rating"]) / 5.0,
            reliable=True,
            cost_usd=float((trace.get("totals") or {}).get("actualCostUSD", 0) or 0),
        )
    return {"status": "accepted", "runId": run_id}


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
        "action": None
        if (AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT)
        else "export AZURE_OPENAI_API_KEY=<key> AZURE_OPENAI_ENDPOINT=<url>",
    }

    # AWS Bedrock
    backends["bedrock"] = {
        "authenticated": bool(AWS_BEDROCK_REGION),
        "method": "aws_profile",
        "env_var": "AWS_BEDROCK_REGION + AWS_PROFILE or AWS_BEDROCK_PROFILE",
        "action": None
        if AWS_BEDROCK_REGION
        else "export AWS_BEDROCK_REGION=us-east-1 AWS_PROFILE=<profile>",
    }

    # CLI-based backends
    for cli_name, cli_bin in [("codex_cli", "codex"), ("gemini_cli", "gemini")]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "which",
                cli_bin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            available = bool(stdout.strip())
        except Exception:
            available = False
        backends[cli_name] = {
            "authenticated": available,
            "method": "cli_binary",
            "action": None
            if available
            else f"Install: npm i -g @{'openai' if cli_name == 'codex_cli' else 'google'}/{cli_bin}",
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


@app.post("/api/evaluations/preflight")
async def evaluation_live_preflight(body: dict | None = None):
    """Execute bounded probes and issue a target-bound live-host receipt."""
    if os.getenv("MULTILLM_EVAL_ALLOW_LIVE_HOST", "false").lower() not in {
        "1",
        "true",
        "yes",
    }:
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "data": None,
                "error": {
                    "message": (
                        "Live evaluation is disabled; set "
                        "MULTILLM_EVAL_ALLOW_LIVE_HOST=true on the host gateway"
                    )
                },
                "meta": {},
            },
        )
    body = body or {}
    targets = body.get("targets")
    if (
        not isinstance(targets, list)
        or not 1 <= len(targets) <= 20
        or any(not isinstance(item, str) or not item.strip() for item in targets)
    ):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "data": None,
                "error": {"message": "targets must contain 1 to 20 aliases"},
                "meta": {},
            },
        )
    normalized = tuple(dict.fromkeys(item.strip() for item in targets))
    unknown = [target for target in normalized if target not in ROUTES]
    if unknown:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "data": None,
                "error": {"message": f"Unknown evaluation aliases: {unknown}"},
                "meta": {},
            },
        )

    marker = "MULTILLM_EVAL_PROBE_OK"
    prompt = (
        f"Reply with exactly {marker} and nothing else. "
        "Do not call tools, read files, or modify any external state."
    )

    async def probe(target: str) -> dict:
        result = await _council_query_one(
            target,
            prompt,
            64,
            0,
            controls={
                "reasoning_effort": "low",
                "execution_mode": "standard",
                "verbosity": "concise",
            },
        )
        verified = (
            not result.get("error") and str(result.get("text", "")).strip() == marker
        )
        return {
            "alias": target,
            "executionVerified": verified,
            "resolvedModel": result.get("providerModel") if verified else None,
            "latencyMs": result.get("latencyMs"),
            "error": None if verified else "probe_failed",
        }

    results = list(await asyncio.gather(*(probe(target) for target in normalized)))
    verified = all(item["executionVerified"] for item in results)
    receipt = None
    expires_at = None
    if verified:
        receipt = f"evalpf_{uuid.uuid4().hex}"
        expires_at = time.time() + 1_800
        _EVALUATION_PREFLIGHTS[receipt] = {
            "targets": set(normalized),
            "expiresAt": expires_at,
        }
    data = {
        "receipt": receipt,
        "expiresAt": expires_at,
        "executionMode": "live_host",
        "sandboxFallback": False,
        "targets": results,
    }
    return JSONResponse(
        status_code=200 if verified else 409,
        content={
            "success": verified,
            "data": data,
            "error": None
            if verified
            else {"message": "One or more execution probes failed"},
            "meta": {},
        },
    )


@app.get("/api/evaluations/live-targets")
async def evaluation_live_targets():
    """List configured host CLI candidates; execution proof remains separate."""
    from .cli_discovery import discover_cli_agents

    catalog: list[dict] = []
    for backend, capability in discover_cli_agents(ROUTES).items():
        if not capability.get("available"):
            continue
        for model in capability.get("models") or []:
            alias = str(model.get("id") or "").strip()
            if not alias:
                continue
            route = ROUTES.get(alias) or {}
            catalog.append(
                {
                    "alias": alias,
                    "provider": backend,
                    "providerModel": str(model.get("model") or alias),
                    "reasoning": str(route.get("reasoning_effort") or "default"),
                }
            )
    targets = [
        {
            "alias": item["alias"],
            "backend": item["provider"],
            "providerModel": item["providerModel"],
            "equivalentAliases": item["equivalentAliases"],
        }
        for item in deduplicate_targets(catalog)
    ]
    targets.sort(key=lambda item: item["alias"])
    return {
        "success": True,
        "data": {"targets": targets},
        "error": None,
        "meta": {
            "count": len(targets),
            "discoveryVerified": True,
            "executionVerified": False,
            "next": "POST /api/evaluations/preflight",
        },
    }


@app.get("/dashboard")
async def dashboard_page():
    static_dir = Path(__file__).parent / "static"
    html_file = static_dir / "dashboard.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return HTMLResponse(html_file.read_text())


@app.get("/evaluations")
async def evaluations_page():
    static_dir = Path(__file__).parent / "static"
    html_file = static_dir / "evaluations.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Evaluation workspace not found")
    return HTMLResponse(html_file.read_text(encoding="utf-8"))


@app.get("/evaluations/assets/{asset_name}")
async def evaluation_asset(asset_name: str):
    if asset_name not in {"evaluations.js", "d3.v7.min.js"}:
        raise HTTPException(status_code=404, detail="Evaluation asset not found")
    asset = (
        Path(__file__).parent
        / "static"
        / (f"vendor/{asset_name}" if asset_name == "d3.v7.min.js" else asset_name)
    )
    if not asset.exists():
        raise HTTPException(status_code=404, detail="Evaluation asset not built")
    return Response(content=asset.read_bytes(), media_type="text/javascript")


@app.get("/")
async def root_page():
    return RedirectResponse(url="/dashboard", status_code=307)


# ── Entry point ──────────────────────────────────────────────────────────────
def main():
    import uvicorn

    exposure = validate_gateway_exposure(
        host=GATEWAY_HOST,
        api_key="configured" if auth_enabled() else "",
        allow_unauthenticated_remote=MULTILLM_ALLOW_UNAUTHENTICATED_REMOTE,
    )
    if not exposure.ok:
        raise SystemExit(exposure.message)

    log.info("MultiLLM Gateway starting on %s:%d", GATEWAY_HOST, GATEWAY_PORT)
    log.info("  Project:    %s", PROJECT)
    log.info("  Dashboard:  http://localhost:%d/dashboard", GATEWAY_PORT)
    log.info("  Data dir:   %s", DATA_DIR)
    log.info(
        "  Auth:       %s",
        "enabled" if auth_enabled() else "disabled (localhost only recommended)",
    )
    log.info("  Exposure:   %s — %s", exposure.severity, exposure.message)
    log.info(
        "  CORS:       %s",
        ", ".join(parse_cors_origins(GATEWAY_CORS_ORIGINS, port=GATEWAY_PORT)),
    )
    log.info("  Ollama:     %s", OLLAMA_URL)
    log.info("  LM Studio:  %s", LMSTUDIO_URL)
    log.info("  OpenRouter:  %s", "configured" if OPENROUTER_KEY else "not set")
    log.info("  OpenAI:     %s", "configured" if OPENAI_KEY else "not set")
    log.info("  Anthropic:  %s", "configured" if ANTHROPIC_KEY else "not set")
    log.info("  Gemini:     %s", "configured" if GEMINI_KEY else "not set")
    log.info("  Routes:     %d total", len(ROUTES))
    uvicorn.run(
        "multillm.gateway:app",
        host=GATEWAY_HOST,
        port=GATEWAY_PORT,
        reload=GATEWAY_RELOAD,
    )


if __name__ == "__main__":
    main()
