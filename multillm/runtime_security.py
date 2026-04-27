"""Runtime safety helpers for production gateway operation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from ipaddress import ip_address


@dataclass(frozen=True)
class ExposureValidation:
    ok: bool
    severity: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def is_loopback_host(host: str) -> bool:
    value = (host or "").strip().lower()
    if value in {"localhost", "::1"}:
        return True
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def validate_gateway_exposure(
    *,
    host: str,
    api_key: str,
    allow_unauthenticated_remote: bool,
) -> ExposureValidation:
    if is_loopback_host(host):
        return ExposureValidation(True, "ok", "Gateway is bound to a loopback interface.")

    if api_key:
        return ExposureValidation(True, "ok", "Remote gateway binding is protected by MULTILLM_API_KEY.")

    if allow_unauthenticated_remote:
        return ExposureValidation(
            True,
            "warning",
            "Remote gateway binding is unauthenticated because MULTILLM_ALLOW_UNAUTHENTICATED_REMOTE is enabled.",
        )

    return ExposureValidation(
        False,
        "critical",
        "Refusing unauthenticated remote gateway binding. Set MULTILLM_API_KEY or bind GATEWAY_HOST to 127.0.0.1.",
    )


def parse_cors_origins(raw: str, *, port: int) -> list[str]:
    configured = [item.strip() for item in (raw or "").split(",") if item.strip()]
    if configured:
        return configured
    return [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    ]


def build_security_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self' http://localhost:* http://127.0.0.1:*; "
            "frame-ancestors 'none'"
        ),
    }
