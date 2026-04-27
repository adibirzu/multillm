"""Tests for the authentication middleware."""

import os
import pytest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from multillm.auth import AuthMiddleware, _extract_key, auth_enabled


def _make_app(api_key: str = ""):
    """Create a test FastAPI app with auth middleware."""
    app = FastAPI()
    # Patch the module-level API_KEY for the middleware
    with patch("multillm.auth.API_KEY", api_key):
        app.add_middleware(AuthMiddleware)

    @app.get("/v1/test")
    async def protected():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/dashboard")
    async def dashboard():
        return {"page": "dashboard"}

    @app.get("/api/dashboard")
    async def dashboard_api():
        return {"stats": True}

    @app.get("/api/memory")
    async def list_memory():
        return {"memory": []}

    @app.post("/api/memory")
    async def write_memory():
        return {"ok": True}

    return app


class TestAuthDisabled:
    """When MULTILLM_API_KEY is not set, all endpoints are open."""

    def test_no_key_required(self):
        app = _make_app("")
        client = TestClient(app)
        r = client.get("/v1/test")
        assert r.status_code == 200

    def test_auth_enabled_false(self):
        with patch("multillm.auth.API_KEY", ""):
            assert auth_enabled() is False


class TestAuthEnabled:
    """When MULTILLM_API_KEY is set, protected endpoints require it."""

    def test_missing_key_returns_401(self):
        app = _make_app("secret123")
        with patch("multillm.auth.API_KEY", "secret123"):
            client = TestClient(app)
            r = client.get("/v1/test")
            assert r.status_code == 401

    def test_wrong_key_returns_403(self):
        app = _make_app("secret123")
        with patch("multillm.auth.API_KEY", "secret123"):
            client = TestClient(app)
            r = client.get("/v1/test", headers={"X-API-Key": "wrong"})
            assert r.status_code == 403

    def test_correct_key_via_header(self):
        app = _make_app("secret123")
        with patch("multillm.auth.API_KEY", "secret123"):
            client = TestClient(app)
            r = client.get("/v1/test", headers={"X-API-Key": "secret123"})
            assert r.status_code == 200

    def test_correct_key_via_bearer(self):
        app = _make_app("secret123")
        with patch("multillm.auth.API_KEY", "secret123"):
            client = TestClient(app)
            r = client.get("/v1/test", headers={"Authorization": "Bearer secret123"})
            assert r.status_code == 200

    def test_public_endpoints_skip_auth(self):
        app = _make_app("secret123")
        with patch("multillm.auth.API_KEY", "secret123"):
            client = TestClient(app)
            assert client.get("/health").status_code == 200
            assert client.get("/dashboard").status_code == 200
            assert client.get("/api/dashboard").status_code == 401

    def test_memory_api_requires_auth(self):
        app = _make_app("secret123")
        with patch("multillm.auth.API_KEY", "secret123"):
            client = TestClient(app)
            assert client.get("/api/memory").status_code == 401
            assert client.post("/api/memory", json={"title": "x", "content": "y"}).status_code == 401
            assert client.get("/api/memory", headers={"X-API-Key": "secret123"}).status_code == 200

    def test_public_dashboard_api_can_be_explicitly_enabled(self):
        app = _make_app("secret123")
        with patch("multillm.auth.API_KEY", "secret123"), patch("multillm.auth.PUBLIC_DASHBOARD_API", True):
            client = TestClient(app)
            assert client.get("/api/dashboard").status_code == 200


class TestExtractKey:
    """Test key extraction from various header formats."""

    def test_x_api_key_header(self):
        from starlette.testclient import TestClient
        from starlette.requests import Request

        # Simple mock
        class FakeRequest:
            headers = {"x-api-key": "mykey"}

        assert _extract_key(FakeRequest()) == "mykey"

    def test_bearer_token(self):
        class FakeRequest:
            headers = {"authorization": "Bearer mytoken"}

        assert _extract_key(FakeRequest()) == "mytoken"

    def test_no_key(self):
        class FakeRequest:
            headers = {}

        assert _extract_key(FakeRequest()) == ""

    def test_x_api_key_takes_priority(self):
        class FakeRequest:
            headers = {"x-api-key": "key1", "authorization": "Bearer key2"}

        assert _extract_key(FakeRequest()) == "key1"
