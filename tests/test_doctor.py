"""Tests for the production readiness doctor."""

from multillm.doctor import assess_doctor_report, collect_doctor_report, format_doctor_report


def test_assess_doctor_report_flags_unsafe_remote_gateway():
    report = {
        "gateway": {
            "reachable": True,
            "status": {
                "gateway": {
                    "unsafe_open_mode": True,
                }
            },
        },
        "configuration": {
            "gateway_exposure": {"ok": False, "severity": "critical", "message": "bad"},
        },
    }

    result = assess_doctor_report(report)

    assert result["ready"] is False
    assert any("unsafe" in issue.lower() for issue in result["issues"])


def test_format_doctor_report_keeps_secret_values_hidden():
    report = {
        "version": "0.6.2",
        "configuration": {
            "host": "127.0.0.1",
            "port": 8080,
            "auth_enabled": True,
            "cors_origins": ["http://localhost:8080"],
            "configured_backends": {"openai": True, "gemini": False},
            "gateway_exposure": {"ok": True, "severity": "ok", "message": "safe"},
        },
        "tools": {"codex": {"installed": False}, "gemini": {"installed": True}},
        "gateway": {
            "reachable": False,
            "url": "http://localhost:8080",
            "error": "connect failed",
        },
        "assessment": {"ready": False, "issues": ["Gateway is not reachable."]},
    }

    text = format_doctor_report(report)

    assert "OPENAI_API_KEY" not in text
    assert "Auth: enabled" in text
    assert "openai: configured" in text
    assert "connect failed" in text


def test_collect_doctor_report_ignores_proxy_environment(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"gateway": {"unsafe_open_mode": False}}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            return FakeResponse()

    monkeypatch.setattr("multillm.doctor.httpx.Client", FakeClient)

    report = collect_doctor_report(gateway_url="http://127.0.0.1:8080", timeout=1)

    assert report["gateway"]["reachable"] is True
    assert captured["trust_env"] is False
