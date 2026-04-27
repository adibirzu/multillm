"""Tests for production runtime safety helpers."""

from multillm.runtime_security import (
    build_security_headers,
    is_loopback_host,
    parse_cors_origins,
    validate_gateway_exposure,
)
from multillm.cli_tools import build_cli_search_path


def test_loopback_host_detection():
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("192.168.1.10") is False


def test_remote_bind_requires_auth_by_default():
    result = validate_gateway_exposure(host="0.0.0.0", api_key="", allow_unauthenticated_remote=False)

    assert result.ok is False
    assert result.severity == "critical"
    assert "MULTILLM_API_KEY" in result.message


def test_remote_bind_with_auth_is_allowed():
    result = validate_gateway_exposure(host="0.0.0.0", api_key="secret", allow_unauthenticated_remote=False)

    assert result.ok is True
    assert result.severity == "ok"


def test_parse_cors_origins_defaults_to_loopback_origins():
    origins = parse_cors_origins("", port=8080)

    assert "http://localhost:8080" in origins
    assert "http://127.0.0.1:8080" in origins
    assert "*" not in origins


def test_parse_cors_origins_supports_explicit_values():
    origins = parse_cors_origins("https://example.com, http://localhost:3000", port=8080)

    assert origins == ["https://example.com", "http://localhost:3000"]


def test_security_headers_are_strict_by_default():
    headers = build_security_headers()

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in headers["Content-Security-Policy"]


def test_cli_search_path_includes_common_local_install_dirs():
    search_path = build_cli_search_path("/custom/bin")

    assert "/custom/bin" in search_path
    assert "/opt/homebrew/bin" in search_path
    assert "/usr/local/bin" in search_path
