# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""
Langfuse LLM observability integration for MultiLLM Gateway.

Records every LLM call as a Langfuse generation with full token usage,
model metadata, latency, and cost. Provides rich LLM-specific observability
complementing the infrastructure-level traces sent to OCI APM.

Architecture: Direct SDK integration (no collector needed) — the gateway
already proxies every LLM call, so we emit Langfuse events inline.

Uses Langfuse SDK v4 API (start_observation / start_as_current_observation).
"""

import hashlib
import logging
import os
import threading
from typing import Any, Mapping, Optional, Sequence

from .config import (
    LANGFUSE_ENABLED,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_HOST,
)

log = logging.getLogger("multillm.langfuse")

_client = None


def init_langfuse() -> bool:
    """Initialize Langfuse client. Returns True if successful."""
    global _client

    if not LANGFUSE_ENABLED:
        log.info("Langfuse disabled (set LANGFUSE_ENABLED=true)")
        return False

    if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
        log.warning(
            "Langfuse enabled but missing keys (LANGFUSE_PUBLIC_KEY/SECRET_KEY)"
        )
        return False

    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
        # Verify auth
        _client.auth_check()
        log.info("Langfuse v4 initialized: host=%s (auth OK)", LANGFUSE_HOST)
        return True
    except ImportError:
        log.warning("langfuse package not installed (pip install langfuse)")
        return False
    except Exception as e:
        log.error("Langfuse init failed: %s", e)
        return False


def shutdown_langfuse(*, timeout_seconds: float | None = None) -> None:
    """Bound Langfuse shutdown so an unreachable collector cannot stall exit."""
    global _client
    client = _client
    _client = None
    if client is None:
        return

    timeout = (
        float(os.getenv("MULTILLM_LANGFUSE_SHUTDOWN_TIMEOUT_SECONDS", "2"))
        if timeout_seconds is None
        else timeout_seconds
    )
    timeout = max(0.0, min(timeout, 30.0))

    def close() -> None:
        try:
            # Langfuse shutdown already flushes once; a separate flush duplicates
            # the unbounded queue join and was the source of gateway teardown stalls.
            client.shutdown()
        except Exception as exc:
            log.debug("Langfuse shutdown failed: %s", exc)

    worker = threading.Thread(
        target=close,
        name="multillm-langfuse-shutdown",
        daemon=True,
    )
    worker.start()
    worker.join(timeout=timeout)
    if worker.is_alive():
        log.warning("Langfuse shutdown exceeded %.1fs; continuing gateway exit", timeout)


# Backend → provider display name
_PROVIDER_MAP = {
    "ollama": "ollama",
    "lmstudio": "lmstudio",
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "gemini": "google",
    "groq": "groq",
    "deepseek": "deepseek",
    "mistral": "mistral",
    "together": "together",
    "xai": "xai",
    "fireworks": "fireworks",
    "azure_openai": "azure-openai",
    "bedrock": "aws-bedrock",
    "codex_cli": "openai-codex",
    "gemini_cli": "google-gemini",
}


def trace_llm_generation(
    *,
    model_alias: str,
    backend: str,
    real_model: str,
    project: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
    latency_ms: float = 0,
    cost_usd: float = 0,
    status: str = "ok",
    error_message: Optional[str] = None,
    is_streaming: bool = False,
    request_id: Optional[str] = None,
    prompt_text: Optional[str] = None,
    response_text: Optional[str] = None,
) -> None:
    """Record an LLM call as a Langfuse generation (v4 SDK API).

    Uses start_observation(as_type='generation') to create a generation
    span with full token usage, model, latency, and cost metadata.
    """
    if not _client:
        return

    try:
        provider = _PROVIDER_MAP.get(backend, backend)

        # Build usage_details (v4 SDK uses usage_details dict instead of usage)
        usage_details = {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        }
        if cache_read_tokens:
            usage_details["cache_read"] = cache_read_tokens
        if cache_create_tokens:
            usage_details["cache_create"] = cache_create_tokens

        # Create a generation observation using v4 API
        generation = _client.start_observation(
            name=f"{model_alias}",
            as_type="generation",
            model=real_model or model_alias,
            model_parameters={
                "provider": provider,
                "backend": backend,
                "streaming": is_streaming,
            },
            input=prompt_text[:500] if prompt_text else None,
            output=response_text[:500] if response_text else None,
            usage_details=usage_details,
            metadata={
                "project": project,
                "request_id": request_id or "",
                "cache_read_tokens": cache_read_tokens,
                "cache_create_tokens": cache_create_tokens,
                "latency_ms": round(latency_ms, 1),
            },
            level="ERROR" if status != "ok" else "DEFAULT",
            status_message=error_message if error_message else None,
        )

        # End the generation
        generation.end()

    except Exception as e:
        log.debug("Langfuse trace failed: %s", e)


def trace_fusion_run(
    *,
    kind: str,
    prompt: str,
    panel_results: list,
    judge: Optional[str] = None,
    analysis: str = "",
    final_answer: str = "",
    status: str = "",
    project: str = "",
    request_id: Optional[str] = None,
) -> None:
    """Record a fusion/council run as a parent span with child generations.

    Reconstructs the panel→judge tree from the completed result, so the run is
    visible as one trace (parent span) with a child generation per panel model
    and the judge. Post-hoc (no live context threaded through the query fn).
    """
    if not _client:
        return
    try:
        with _client.start_as_current_observation(
            name=f"{kind}",
            as_type="span",
            input=(prompt or "")[:500],
            output=(final_answer or "")[:500],
            metadata={
                "project": project,
                "request_id": request_id or "",
                "panelSize": len(panel_results),
                "status": status,
                "judge": judge or "",
            },
        ):
            for r in panel_results or []:
                gen = _client.start_observation(
                    name=f"panel:{r.get('alias', '?')}",
                    as_type="generation",
                    model=r.get("alias"),
                    input=(prompt or "")[:200],
                    output=(r.get("text") or "")[:500],
                    usage_details={
                        "input": r.get("inputTokens", 0),
                        "output": r.get("outputTokens", 0),
                    },
                    metadata={"role": "panel", "costUSD": r.get("actualCostUSD", 0)},
                    level="ERROR" if r.get("error") else "DEFAULT",
                    status_message=r.get("error") or None,
                )
                gen.end()
            if judge:
                jg = _client.start_observation(
                    name=f"judge:{judge}",
                    as_type="generation",
                    model=judge,
                    input="(panel responses → structured analysis)",
                    output=(final_answer or "")[:500],
                    metadata={"role": "judge", "analysis": (analysis or "")[:500]},
                )
                jg.end()
    except Exception as e:
        log.debug("Langfuse fusion trace failed: %s", e)


def _evaluation_summary_metadata(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return aggregate-only evaluation fields safe for external telemetry."""
    pairwise = []
    for raw in summary.get("pairwise") or []:
        if not isinstance(raw, Mapping):
            continue
        pairwise.append(
            {
                key: raw.get(key)
                for key in (
                    "candidate",
                    "baseline",
                    "winRate",
                    "lower95",
                    "upper95",
                    "sampleCount",
                )
            }
        )
    failures = summary.get("failures") or []
    return {
        "outputCount": int(summary.get("outputs") or 0),
        "failureCount": len(failures) if isinstance(failures, Sequence) else 0,
        "deterministicPassRate": summary.get("deterministicPassRate"),
        "releaseGate": summary.get("releaseGate"),
        "pairwise": pairwise,
    }


def trace_evaluation_run(
    *,
    run_id: str,
    suite_id: str,
    tenant_id: str,
    status: str,
    profile: str,
    execution_mode: str,
    candidates: Sequence[str] = (),
    moa_variants: Sequence[str] = (),
    judge_pool: Sequence[str] = (),
    summary: Mapping[str, Any] | None = None,
) -> None:
    """Emit one privacy-bounded evaluation event.

    Prompts, model outputs, case identifiers, judge rationales, reviewer data,
    and tenant names are deliberately excluded. The tenant is represented by a
    one-way digest so operators can correlate activity without disclosing it.
    """
    if not _client:
        return
    try:
        aggregate = _evaluation_summary_metadata(summary or {})
        targets = list(dict.fromkeys((*candidates, *moa_variants)))
        observation = _client.start_observation(
            name="evaluation-run",
            as_type="span",
            metadata={
                "runId": run_id,
                "suiteId": suite_id,
                "tenantHash": hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:16],
                "status": status,
                "profile": profile,
                "executionMode": execution_mode,
                "targets": targets,
                "judgeModels": list(dict.fromkeys(judge_pool)),
                **aggregate,
            },
            level="ERROR" if status == "failed" else "DEFAULT",
        )
        observation.end()
    except Exception as exc:
        log.debug("Langfuse evaluation trace failed: %s", exc)


def get_langfuse_status() -> dict:
    """Get Langfuse connection status for the dashboard."""
    return {
        "enabled": LANGFUSE_ENABLED,
        "initialized": _client is not None,
        "host": LANGFUSE_HOST if LANGFUSE_ENABLED else None,
        "has_keys": bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY),
    }
