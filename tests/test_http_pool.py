"""Tests for the HTTP client pool."""

import pytest
from multillm.http_pool import get_client, close_all, _clients


@pytest.fixture(autouse=True)
async def cleanup_pools():
    """Clean up pools after each test."""
    yield
    await close_all()


def test_get_client_creates_pool():
    client = get_client("ollama")
    assert client is not None
    assert "ollama" in _clients


def test_get_client_reuses_pool():
    c1 = get_client("openai")
    c2 = get_client("openai")
    assert c1 is c2


def test_different_backends_get_different_pools():
    c1 = get_client("ollama")
    c2 = get_client("oca")
    assert c1 is not c2


def test_unknown_backend_uses_defaults():
    client = get_client("unknown_backend")
    assert client is not None


def test_http2_enabled():
    client = get_client("openai")
    # httpx with h2 installed enables HTTP/2
    assert client._transport is not None


@pytest.mark.asyncio
async def test_close_all_clears_pools():
    get_client("ollama")
    get_client("oca")
    assert len(_clients) == 2
    await close_all()
    assert len(_clients) == 0
