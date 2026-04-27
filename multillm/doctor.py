"""Production readiness checks for MultiLLM installations."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from . import __version__
from .auth import auth_enabled
from .cli_tools import resolve_cli_binary
from .config import (
    ANTHROPIC_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_KEY,
    DEEPSEEK_KEY,
    FIREWORKS_KEY,
    GATEWAY_CORS_ORIGINS,
    GATEWAY_HOST,
    GATEWAY_PORT,
    GEMINI_KEY,
    GROQ_KEY,
    MISTRAL_KEY,
    OCA_CLIENT_ID,
    OCA_ENDPOINT,
    OCA_IDCS_URL,
    OPENAI_KEY,
    OPENROUTER_KEY,
    TOGETHER_KEY,
    XAI_KEY,
)
from .runtime_security import parse_cors_origins, validate_gateway_exposure


def _tool_status(binary: str) -> dict[str, Any]:
    env_var = f"{binary.upper()}_CLI_PATH" if binary in {"codex", "gemini"} else None
    path = resolve_cli_binary(binary, env_var=env_var)
    return {"installed": bool(path), "path": path}


def _configured_backends() -> dict[str, bool]:
    return {
        "openai": bool(OPENAI_KEY),
        "anthropic": bool(ANTHROPIC_KEY),
        "openrouter": bool(OPENROUTER_KEY),
        "gemini": bool(GEMINI_KEY),
        "groq": bool(GROQ_KEY),
        "deepseek": bool(DEEPSEEK_KEY),
        "mistral": bool(MISTRAL_KEY),
        "together": bool(TOGETHER_KEY),
        "xai": bool(XAI_KEY),
        "fireworks": bool(FIREWORKS_KEY),
        "azure_openai": bool(AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT),
        "oca": bool(OCA_ENDPOINT and OCA_IDCS_URL and OCA_CLIENT_ID),
    }


def assess_doctor_report(report: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    gateway = report.get("gateway", {})
    gateway_status = gateway.get("status") or {}
    gateway_config = gateway_status.get("gateway") or {}
    exposure = (report.get("configuration") or {}).get("gateway_exposure") or {}

    if not gateway.get("reachable"):
        issues.append("Gateway is not reachable.")
    if exposure.get("ok") is False:
        issues.append(exposure.get("message") or "Gateway exposure configuration is unsafe.")
    if gateway_config.get("unsafe_open_mode"):
        issues.append("Gateway is in unsafe open mode on a non-loopback interface.")

    return {
        "ready": not issues,
        "issues": issues,
    }


def collect_doctor_report(
    *,
    gateway_url: str | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    url = (gateway_url or f"http://127.0.0.1:{GATEWAY_PORT}").rstrip("/")
    exposure = validate_gateway_exposure(
        host=GATEWAY_HOST,
        api_key="configured" if auth_enabled() else "",
        allow_unauthenticated_remote=False,
    )
    report: dict[str, Any] = {
        "version": __version__,
        "configuration": {
            "host": GATEWAY_HOST,
            "port": GATEWAY_PORT,
            "auth_enabled": auth_enabled(),
            "cors_origins": parse_cors_origins(GATEWAY_CORS_ORIGINS, port=GATEWAY_PORT),
            "configured_backends": _configured_backends(),
            "gateway_exposure": exposure.to_dict(),
        },
        "tools": {
            "codex": _tool_status("codex"),
            "gemini": _tool_status("gemini"),
            "ollama": _tool_status("ollama"),
        },
        "gateway": {
            "reachable": False,
            "url": url,
        },
    }

    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(f"{url}/api/status")
            response.raise_for_status()
            report["gateway"] = {
                "reachable": True,
                "url": url,
                "status": response.json(),
            }
    except Exception as exc:
        report["gateway"]["error"] = str(exc)

    report["assessment"] = assess_doctor_report(report)
    return report


def format_doctor_report(report: dict[str, Any]) -> str:
    config = report.get("configuration", {})
    exposure = config.get("gateway_exposure", {})
    gateway = report.get("gateway", {})
    assessment = report.get("assessment", {})
    tools = report.get("tools", {})
    configured = config.get("configured_backends", {})

    lines = [
        "=== MultiLLM Doctor ===",
        f"Version: {report.get('version', '?')}",
        f"Gateway config: {config.get('host', '?')}:{config.get('port', '?')}",
        f"Auth: {'enabled' if config.get('auth_enabled') else 'disabled'}",
        f"Exposure: {exposure.get('severity', '?')} - {exposure.get('message', '')}",
        f"CORS origins: {', '.join(config.get('cors_origins') or [])}",
        "",
        "--- Gateway ---",
        f"URL: {gateway.get('url', '?')}",
        f"Reachable: {'yes' if gateway.get('reachable') else 'no'}",
    ]
    if gateway.get("error"):
        lines.append(f"Error: {gateway['error']}")

    lines.extend(["", "--- Local Tools ---"])
    for name in sorted(tools):
        info = tools[name]
        lines.append(f"{name}: {'installed' if info.get('installed') else 'not found'}")

    lines.extend(["", "--- Configured Backends ---"])
    for name in sorted(configured):
        lines.append(f"{name}: {'configured' if configured[name] else 'not configured'}")

    lines.extend(["", "--- Assessment ---"])
    lines.append("Ready: yes" if assessment.get("ready") else "Ready: no")
    for issue in assessment.get("issues", []):
        lines.append(f"- {issue}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check MultiLLM production readiness.")
    parser.add_argument("--gateway-url", default=None, help="Gateway base URL, default http://127.0.0.1:<GATEWAY_PORT>")
    parser.add_argument("--timeout", type=float, default=2.0, help="Gateway request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when readiness checks fail")
    args = parser.parse_args(argv)

    report = collect_doctor_report(gateway_url=args.gateway_url, timeout=args.timeout)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_doctor_report(report))

    if args.strict and not report["assessment"]["ready"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
