# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""
Per-user LLM usage collector.

Runs *as a single developer* (typically via a ``systemd`` template timer
``multillm-collector@<user>.timer`` on the shared dev VM), reads that
developer's local CLI stats, and POSTs a daily usage snapshot to the gateway's
``/api/usage/ingest`` endpoint.

Standalone HTTP via ``urllib`` (no third-party deps beyond the multillm package
itself) so it stays cheap to schedule.

Usage::

    python -m multillm.team_collector \\
        --gateway http://10.200.200.1:8080 \\
        --user "$USER" --hours 168 --token "$MULTILLM_API_KEY"

Identity resolution order for the tenant label:
``--user`` flag → ``$MULTILLM_TENANT`` → ``$USER`` → ``getpass.getuser()``.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import socket
import sys
import urllib.error
import urllib.request

log = logging.getLogger("multillm.team_collector")


def _resolve_tenant(cli_user: str | None) -> str:
    return (
        (cli_user or "").strip()
        or os.environ.get("MULTILLM_TENANT", "").strip()
        or os.environ.get("USER", "").strip()
        or getpass.getuser()
    )


def _parse_accounts(raw: str | None) -> dict[str, str]:
    """Parse ``backend=account,backend=account`` overrides from --accounts."""
    out: dict[str, str] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def post_ingest(gateway: str, token: str, payload: dict, timeout: float = 15.0) -> dict:
    """POST a snapshot batch to the gateway ingest endpoint."""
    url = gateway.rstrip("/") + "/api/usage/ingest"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-API-Key", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — operator-supplied gateway URL
        body = resp.read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def build_payload(tenant: str, host: str, hours: int, accounts: dict[str, str]) -> dict:
    from .team_usage import collect_local_usage

    records = collect_local_usage(tenant, host=host, hours=hours, accounts=accounts)
    return {
        "tenant_id": tenant,
        "source_host": host,
        "records": [r.to_payload() for r in records],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="multillm-collect", description=__doc__)
    parser.add_argument("--gateway", default=os.environ.get("MULTILLM_GATEWAY", "http://localhost:8080"))
    parser.add_argument("--user", default=None, help="tenant label (default: $USER)")
    parser.add_argument("--token", default=os.environ.get("MULTILLM_API_KEY", ""))
    parser.add_argument("--host", default=os.environ.get("MULTILLM_HOST", socket.gethostname()))
    parser.add_argument("--hours", type=int, default=int(os.environ.get("MULTILLM_COLLECT_HOURS", "168")))
    parser.add_argument("--accounts", default=os.environ.get("MULTILLM_ACCOUNTS", ""),
                        help="override account labels, e.g. claude=me@x.com,codex=team")
    parser.add_argument("--dry-run", action="store_true", help="print payload, do not POST")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    tenant = _resolve_tenant(args.user)
    payload = build_payload(tenant, args.host, args.hours, _parse_accounts(args.accounts))
    n = len(payload["records"])

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        log.info("dry-run: %d record(s) for tenant=%s host=%s", n, tenant, args.host)
        return 0

    if n == 0:
        log.info("no local usage found for tenant=%s — nothing to push", tenant)
        return 0

    try:
        result = post_ingest(args.gateway, args.token, payload)
    except urllib.error.HTTPError as e:
        log.error("ingest failed: HTTP %s %s", e.code, e.read().decode("utf-8", "ignore")[:300])
        return 1
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        log.error("ingest failed: %s", e)
        return 1

    log.info("pushed %d record(s) for tenant=%s → %s", n, tenant, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
