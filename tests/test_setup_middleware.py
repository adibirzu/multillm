# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for ``multillm.setup.middleware.SetupRedirectMiddleware``.

Each test builds a minimal FastAPI app, mounts the middleware, and uses
``fastapi.testclient.TestClient`` to assert the redirect contract.

Hermetic: ``MULTILLM_HOME`` is moved to ``tmp_path`` and the DB is
migrated to head so the ``system`` table exists.
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


def _set_complete(db_path: Path, complete: bool) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE system SET value=? WHERE key='setup_complete'",
            ("1" if complete else "0",),
        )
        conn.commit()
    finally:
        conn.close()


def _build_app() -> FastAPI:
    from multillm.setup.middleware import SetupRedirectMiddleware
    from multillm.setup.routes import router as setup_router

    app = FastAPI()
    app.include_router(setup_router, prefix="/setup")

    @app.get("/")
    def root():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/messages")
    def messages():
        return {"messages": []}

    # Mount the redirect middleware LAST so it wraps the outermost layer
    # (Starlette evaluates the last-added middleware first per request).
    app.add_middleware(SetupRedirectMiddleware)
    return app


# ── setup_complete = false ──────────────────────────────────────────────────


def test_root_redirects_to_setup_when_incomplete(migrated_db: Path) -> None:
    _set_complete(migrated_db, False)
    client = TestClient(_build_app(), follow_redirects=False)

    r = client.get("/")

    assert r.status_code == 302
    assert r.headers["location"].endswith("/setup")


def test_v1_messages_redirects_to_setup_when_incomplete(migrated_db: Path) -> None:
    _set_complete(migrated_db, False)
    client = TestClient(_build_app(), follow_redirects=False)

    r = client.get("/v1/messages")

    assert r.status_code == 302
    assert r.headers["location"].endswith("/setup")


def test_health_passes_through_when_incomplete(migrated_db: Path) -> None:
    _set_complete(migrated_db, False)
    client = TestClient(_build_app(), follow_redirects=False)

    r = client.get("/health")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_setup_root_passes_through_when_incomplete(migrated_db: Path) -> None:
    _set_complete(migrated_db, False)
    client = TestClient(_build_app(), follow_redirects=False)

    r = client.get("/setup")

    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_setup_static_passes_through_when_incomplete(migrated_db: Path) -> None:
    _set_complete(migrated_db, False)
    client = TestClient(_build_app(), follow_redirects=False)

    r = client.get("/setup/static/wizard.css")

    # File served by StaticFiles → 200; never a redirect.
    assert r.status_code == 200
    assert "text/css" in r.headers.get("content-type", "")


# ── setup_complete = true ───────────────────────────────────────────────────


def test_root_passes_through_when_complete(migrated_db: Path) -> None:
    _set_complete(migrated_db, True)
    client = TestClient(_build_app(), follow_redirects=False)

    r = client.get("/")

    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_setup_returns_410_when_complete(migrated_db: Path) -> None:
    _set_complete(migrated_db, True)
    client = TestClient(_build_app(), follow_redirects=False)

    r = client.get("/setup")

    assert r.status_code == 410
