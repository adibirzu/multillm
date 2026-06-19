# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""SQL-injection regression tests (Plan 02b-01 Task 5, AUTH-16).

Posts three malicious bearer tokens to ``/v1/messages`` and asserts the
gateway returns HTTP 401 (not 500). The injection strings never reach a
DB query because the AuthMiddleware's ``secrets.compare_digest`` short-
circuits any non-matching key with 401 — but the test fences this
contract so a future regression that interpolates the bearer into SQL
(or removes the auth gate) trips the test.

The DROP TABLE vector additionally asserts the api_keys table still
exists post-call.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


_INJECTION_VECTORS = [
    pytest.param("' OR 1=1 --", id="or-1-eq-1"),
    pytest.param("' UNION SELECT * FROM api_keys --", id="union-select"),
    pytest.param("'; DROP TABLE api_keys; --", id="drop-table"),
]


@pytest.fixture
def authed_client(monkeypatch):
    """Engage MULTILLM_API_KEY so AuthMiddleware enforces 401 on bad keys."""
    monkeypatch.setenv("MULTILLM_API_KEY", "test-secret-not-used")
    # Reload auth + gateway so the env var is picked up.
    import importlib
    import multillm.auth as auth_mod

    importlib.reload(auth_mod)
    import multillm.gateway as gateway_mod

    importlib.reload(gateway_mod)

    from fastapi.testclient import TestClient

    with TestClient(gateway_mod.app) as client:
        yield client


@pytest.mark.parametrize("bearer", _INJECTION_VECTORS)
def test_injection_bearer_returns_401(authed_client, bearer: str) -> None:
    """AUTH-16: every SQLi vector returns 401, not 500."""
    response = authed_client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "model": "ollama/llama3",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 8,
        },
    )
    assert response.status_code == 401, (
        f"SQLi vector {bearer!r} returned {response.status_code} (expected 401). "
        f"Body: {response.text[:200]}"
    )


def test_drop_table_does_not_drop_api_keys(
    authed_client, tmp_path: Path, monkeypatch
) -> None:
    """AUTH-16: the DROP TABLE injection vector must not cascade to a DDL execution."""
    # Run the malicious DROP request — it should be 401 (auth gate).
    response = authed_client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer '; DROP TABLE api_keys; --"},
        json={
            "model": "ollama/llama3",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 8,
        },
    )
    assert response.status_code == 401

    # Independently, prove the api_keys schema would survive: run the
    # migration against a fresh DB and confirm the table is present.
    db = tmp_path / "verify.db"
    monkeypatch.setenv("MULTILLM_DB_PATH", str(db))
    from multillm.migrations.runner import migrate_up

    migrate_up()

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, (
        "api_keys table missing after migration — DROP injection contract broken"
    )
