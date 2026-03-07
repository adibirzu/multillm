"""Shared helper for OpenAI-compatible API calls."""

from typing import Optional
from ..http_pool import get_client


async def call_openai_compat(
    base_url: str,
    api_key: str,
    payload: dict,
    extra_headers: Optional[dict] = None,
    backend: str = "openai",
) -> dict:
    """Call an OpenAI-compatible chat/completions endpoint."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        **(extra_headers or {}),
    }
    client = get_client(backend)
    r = await client.post(f"{base_url}/v1/chat/completions", json=payload, headers=headers)
    r.raise_for_status()
    return r.json()
