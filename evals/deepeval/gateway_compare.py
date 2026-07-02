"""Helpers for the opt-in DeepEval suite."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent


def read_json(name: str) -> dict[str, Any]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def configured_alias(target: dict[str, Any]) -> str:
    return os.getenv(target["alias_env"], target["default_alias"])


def live_aliases(catalog: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live = {item.get("alias") for item in catalog.get("models", []) if item.get("available")}
    return [target for target in targets if configured_alias(target) in live]


def _headers() -> dict[str, str]:
    return {"X-API-Key": os.environ["MULTILLM_API_KEY"]} if os.getenv("MULTILLM_API_KEY") else {}


def message(client: httpx.Client, url: str, alias: str, command: str, effort: str) -> str:
    response = client.post(f"{url}/v1/messages", headers=_headers(), json={"model": alias, "messages": [{"role": "user", "content": command}], "metadata": {"multillm": {"reasoning_ceiling": effort}}})
    response.raise_for_status()
    content = response.json().get("content") or []
    return str(content[0].get("text") if content else "")


def moa(client: httpx.Client, url: str, command: str, aliases: list[str], aggregator: str, preset: str) -> str:
    response = client.post(
        f"{url}/api/moa",
        headers=_headers(),
        json={
            "prompt": command,
            "models": aliases,
            "aggregator": aggregator,
            "preset": preset,
        },
    )
    response.raise_for_status()
    return str(response.json().get("finalAnswer") or "")
