# SPDX-License-Identifier: Apache-2.0

"""Private, local-only credit-to-cost overlay for an individual operator.

The overlay is intentionally generic and disabled without a local configuration
file. It is not a product entitlement, billing integration, or shared tenant
setting: the only supported source is a mode-0600 file outside the repository.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
import tempfile
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("multillm.private_credit_overlay")

# Deliberately not environment-configurable: this is a private, per-operator
# overlay, not a deployable product setting. Tests may monkeypatch this path.
PRIVATE_CREDIT_OVERLAY_FILE = DATA_DIR / "private-credit-overlay.json"
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _finite_nonnegative(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > 1_000_000_000_000:
        return None
    return number


def _is_private_file(path: Path) -> bool:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    return path.is_file() and not (mode & (stat.S_IRWXG | stat.S_IRWXO))


def _validated_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("private credit overlay must be an object")
    allowed = {
        "enabled",
        "period",
        "credits_used",
        "credit_to_usd",
        "required_email_domain",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            f"unsupported private credit field(s): {', '.join(sorted(unknown))}"
        )
    if payload.get("enabled") is not True:
        raise ValueError("enabled must be true")
    period = str(payload.get("period") or "")
    credits_used = _finite_nonnegative(payload.get("credits_used"))
    if not _PERIOD_RE.fullmatch(period) or credits_used is None:
        raise ValueError("period and non-negative credits_used are required")
    credit_to_usd = None
    if "credit_to_usd" in payload:
        credit_to_usd = _finite_nonnegative(payload["credit_to_usd"])
        if credit_to_usd is None:
            raise ValueError("credit_to_usd must be a non-negative finite number")
    result = {"enabled": True, "period": period, "credits_used": credits_used}
    if credit_to_usd is not None:
        result["credit_to_usd"] = credit_to_usd
    required_email_domain = str(payload.get("required_email_domain") or "").lower()
    if required_email_domain:
        if not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)+",
            required_email_domain,
        ):
            raise ValueError("required_email_domain must be a domain name")
        result["required_email_domain"] = required_email_domain
    return result


def save_private_credit_overlay(payload: object) -> dict:
    """Atomically save a local-only overlay with owner-only permissions."""
    validated = _validated_payload(payload)
    PRIVATE_CREDIT_OVERLAY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=PRIVATE_CREDIT_OVERLAY_FILE.parent,
            prefix=".private-credit-",
            delete=False,
        ) as handle:
            temp_name = handle.name
            os.chmod(temp_name, 0o600)
            json.dump(validated, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, PRIVATE_CREDIT_OVERLAY_FILE)
        os.chmod(PRIVATE_CREDIT_OVERLAY_FILE, 0o600)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
    return get_private_credit_overlay()


def get_private_credit_overlay() -> dict:
    """Return a validated local overlay, without ever persisting its values."""
    if not _is_private_file(PRIVATE_CREDIT_OVERLAY_FILE):
        return {"configured": False}
    try:
        with open(PRIVATE_CREDIT_OVERLAY_FILE) as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("Private credit overlay is unavailable: %s", exc)
        return {"configured": False}

    if not isinstance(payload, dict) or payload.get("enabled") is not True:
        return {"configured": False}
    period = str(payload.get("period") or "")
    credits_used = _finite_nonnegative(payload.get("credits_used"))
    if not _PERIOD_RE.fullmatch(period) or credits_used is None:
        return {"configured": False}

    credit_to_usd = _finite_nonnegative(payload.get("credit_to_usd"))
    required_email_domain = str(payload.get("required_email_domain") or "").lower()
    if required_email_domain and not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)+",
        required_email_domain,
    ):
        return {"configured": False}
    mapped_cost = (
        round(credits_used * credit_to_usd, 6) if credit_to_usd is not None else None
    )
    return {
        "configured": True,
        "period": period,
        "creditsUsed": credits_used,
        "creditToUsd": credit_to_usd,
        "mappedCostUSD": mapped_cost,
        "requiredEmailDomain": required_email_domain or None,
    }
