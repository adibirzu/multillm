# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for ``multillm.setup.routes`` (wizard HTTP endpoints).

Covers behaviour required by Plan 01-07 Task 2:

- GET /setup renders wizard HTML
- POST /setup/admin validates email + password length, persists hashed user
- POST /setup/backends accepts optional dict, filters empty values
- GET /setup/probe-local enumerates local backends and never 500s on probe error
- POST /setup/observability persists user-toggled flags
- POST /setup/complete flips setup_complete and subsequent GET returns 410

Also includes the Task 3 end-to-end integration test (full happy path
+ multillm reset --confirm round trip).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MULTILLM_HOME", str(tmp_path))
    monkeypatch.delenv("MULTILLM_DB_PATH", raising=False)
    return tmp_path


@pytest.fixture
def migrated_db(isolated_home: Path) -> Path:
    from multillm.migrations.runner import db_path, migrate_up

    path = db_path()
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system "
            "(key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()
    migrate_up()
    return path


def _build_app() -> FastAPI:
    from multillm.setup.middleware import SetupRedirectMiddleware
    from multillm.setup.routes import mount_static, router as setup_router

    app = FastAPI()
    app.include_router(setup_router, prefix="/setup")
    mount_static(app)

    @app.get("/")
    def root():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.add_middleware(SetupRedirectMiddleware)
    return app


@pytest.fixture
def client(migrated_db: Path) -> TestClient:
    return TestClient(_build_app(), follow_redirects=False)


# ── GET /setup renders wizard ────────────────────────────────────────────────


def test_get_setup_renders_wizard_html(client: TestClient) -> None:
    r = client.get("/setup")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Create admin account" in r.text


# ── POST /setup/admin ────────────────────────────────────────────────────────


def test_post_admin_creates_admin_with_valid_input(
    client: TestClient, migrated_db: Path
) -> None:
    r = client.post(
        "/setup/admin",
        json={"email": "a@example.test", "password": "supersecret123"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "admin_created"

    conn = sqlite3.connect(migrated_db)
    row = conn.execute(
        "SELECT email, password_hash FROM admin_users WHERE id=1"
    ).fetchone()
    conn.close()
    assert row is not None
    email, password_hash = row
    assert email == "a@example.test"
    assert password_hash.startswith("$argon2id$")


def test_post_admin_rejects_short_password(client: TestClient) -> None:
    r = client.post(
        "/setup/admin",
        json={"email": "a@example.test", "password": "short"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "password_too_short"
    assert body["min_length"] == 12


def test_post_admin_rejects_invalid_email(client: TestClient) -> None:
    r = client.post(
        "/setup/admin",
        json={"email": "not-an-email", "password": "supersecret123"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_email"


# ── POST /setup/backends ─────────────────────────────────────────────────────


def test_post_backends_filters_empty_values(
    client: TestClient, migrated_db: Path
) -> None:
    # Must complete admin first (panes 2-4 require admin).
    client.post(
        "/setup/admin",
        json={"email": "a@example.test", "password": "supersecret123"},
    )

    r = client.post(
        "/setup/backends",
        json={"openai": "", "anthropic": "sk-test-anthropic"},
    )
    assert r.status_code == 200, r.text

    import json

    conn = sqlite3.connect(migrated_db)
    row = conn.execute(
        "SELECT payload_json FROM setup_state WHERE pane='backends'"
    ).fetchone()
    conn.close()
    payload = json.loads(row[0])
    assert payload == {"anthropic": "sk-test-anthropic"}


def test_post_backends_accepts_empty_dict(client: TestClient) -> None:
    client.post(
        "/setup/admin",
        json={"email": "a@example.test", "password": "supersecret123"},
    )
    r = client.post("/setup/backends", json={})
    assert r.status_code == 200


# ── GET /setup/probe-local ───────────────────────────────────────────────────


def test_probe_local_returns_four_backends_never_500(client: TestClient) -> None:
    r = client.get("/setup/probe-local")
    assert r.status_code == 200
    body = r.json()
    for backend in ("ollama", "lmstudio", "codex_cli", "gemini_cli"):
        assert backend in body
        entry = body[backend]
        assert "reachable" in entry
        assert isinstance(entry["reachable"], bool)
        assert "models" in entry
        assert isinstance(entry["models"], list)


# ── POST /setup/observability ────────────────────────────────────────────────


def test_post_observability_persists_flags(
    client: TestClient, migrated_db: Path
) -> None:
    client.post(
        "/setup/admin",
        json={"email": "a@example.test", "password": "supersecret123"},
    )
    r = client.post(
        "/setup/observability",
        json={"prometheus_enabled": True, "otel_endpoint": "http://otel.local"},
    )
    assert r.status_code == 200

    import json

    conn = sqlite3.connect(migrated_db)
    row = conn.execute(
        "SELECT payload_json FROM setup_state WHERE pane='observability'"
    ).fetchone()
    conn.close()
    payload = json.loads(row[0])
    assert payload["prometheus_enabled"] is True
    assert payload["otel_endpoint"] == "http://otel.local"


# ── POST /setup/complete ─────────────────────────────────────────────────────


def test_post_complete_flips_flag_and_subsequent_get_returns_410(
    client: TestClient,
) -> None:
    client.post(
        "/setup/admin",
        json={"email": "a@example.test", "password": "supersecret123"},
    )
    r = client.post("/setup/complete")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"
    assert body["redirect"] == "/dashboard"

    r2 = client.get("/setup")
    assert r2.status_code == 410


# ── End-to-end integration (Task 3) ──────────────────────────────────────────


def test_full_first_run_flow_then_reset_re_enables_wizard(
    client: TestClient, migrated_db: Path
) -> None:
    # 1. Fresh DB → wizard reachable
    assert client.get("/setup").status_code == 200

    # 2. Admin → 200
    assert (
        client.post(
            "/setup/admin",
            json={"email": "a@example.test", "password": "supersecret123"},
        ).status_code
        == 200
    )

    # 3. Backends (optional, may be empty)
    assert client.post("/setup/backends", json={"openai": "sk-x"}).status_code == 200

    # 4. Probe local
    probe = client.get("/setup/probe-local")
    assert probe.status_code == 200

    # 5. Observability
    assert (
        client.post(
            "/setup/observability",
            json={"prometheus_enabled": False},
        ).status_code
        == 200
    )

    # 6. Complete
    assert client.post("/setup/complete").status_code == 200

    # 7. /setup is now 410, root passes through (no redirect)
    assert client.get("/setup").status_code == 410
    r_root = client.get("/")
    assert r_root.status_code == 200
    assert r_root.json() == {"ok": True}

    # 8. `multillm reset --confirm` via the CLI re-enables the wizard
    from click.testing import CliRunner

    from multillm.cli import app as cli_app

    runner = CliRunner()
    result = runner.invoke(cli_app, ["reset", "--confirm"])
    assert result.exit_code == 0, result.output
    assert "reset" in result.output.lower()

    # 9. /setup is reachable again, admin_users is empty
    assert client.get("/setup").status_code == 200
    conn = sqlite3.connect(migrated_db)
    n = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    conn.close()
    assert n == 0
