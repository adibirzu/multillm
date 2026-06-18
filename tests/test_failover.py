# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for quota-aware failover policy."""

import httpx
import pytest
from fastapi import HTTPException

from multillm import failover


# --- is_quota_error -----------------------------------------------------------

def test_429_http_exception_is_quota():
    assert failover.is_quota_error(HTTPException(status_code=429, detail="Too Many Requests"))


def test_402_payment_required_is_quota():
    assert failover.is_quota_error(HTTPException(status_code=402, detail="Payment required"))


def test_insufficient_quota_body_with_generic_status_is_quota():
    # Some providers return 400/403 with an insufficient_quota body.
    assert failover.is_quota_error(HTTPException(status_code=403, detail="insufficient_quota: add credits"))


def test_httpx_status_error_429_is_quota():
    request = httpx.Request("POST", "https://api.example.com/v1/messages")
    response = httpx.Response(429, text="rate limit exceeded", request=request)
    err = httpx.HTTPStatusError("429", request=request, response=response)
    assert failover.is_quota_error(err)


def test_400_bad_request_is_not_quota():
    assert not failover.is_quota_error(HTTPException(status_code=400, detail="invalid model parameter"))


def test_connection_error_is_not_quota():
    assert not failover.is_quota_error(httpx.ConnectError("connection refused"))


# --- build_failover_candidates ------------------------------------------------

_ROUTES = {
    "openai/gpt-4o": {"backend": "openai", "model": "gpt-4o"},
    "anthropic/sonnet": {"backend": "anthropic", "model": "claude-sonnet-4-6"},
    "deepseek/chat": {"backend": "deepseek", "model": "deepseek-chat"},
    "ollama/llama3": {"backend": "ollama", "model": "llama3"},
}


def test_candidates_skip_failed_backend():
    chain = ["openai/gpt-4o", "anthropic/sonnet", "ollama/llama3"]
    out = failover.build_failover_candidates(routes=_ROUTES, chain=chain, failed_backend="openai")
    backends = [r["backend"] for _, r in out]
    assert "openai" not in backends
    assert backends == ["anthropic", "ollama"]


def test_candidates_dedupe_by_backend():
    chain = ["openai/gpt-4o", "anthropic/sonnet", "anthropic/sonnet"]
    out = failover.build_failover_candidates(routes=_ROUTES, chain=chain, failed_backend="openai")
    assert len(out) == 1
    assert out[0][1]["backend"] == "anthropic"


def test_candidates_respect_exclude_aliases():
    chain = ["anthropic/sonnet", "deepseek/chat"]
    out = failover.build_failover_candidates(
        routes=_ROUTES, chain=chain, failed_backend="openai",
        exclude_aliases={"anthropic/sonnet"},
    )
    aliases = [a for a, _ in out]
    assert "anthropic/sonnet" not in aliases
    assert aliases == ["deepseek/chat"]


def test_candidates_drop_unknown_aliases():
    chain = ["nonexistent/model", "deepseek/chat"]
    out = failover.build_failover_candidates(routes=_ROUTES, chain=chain, failed_backend="openai")
    assert [a for a, _ in out] == ["deepseek/chat"]


def test_candidates_preserve_chain_order():
    chain = ["deepseek/chat", "anthropic/sonnet", "ollama/llama3"]
    out = failover.build_failover_candidates(routes=_ROUTES, chain=chain, failed_backend="openai")
    assert [a for a, _ in out] == ["deepseek/chat", "anthropic/sonnet", "ollama/llama3"]
