# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Top-level ``multillm`` CLI.

Exposes two subcommand groups:

- ``multillm migrate {up,down,status}`` (plus ``--dry-run`` flag on ``migrate``)
- ``multillm serve`` (delegates to the legacy ``multillm.gateway:main``)

The legacy ``multillm-gateway`` console script is preserved in
``pyproject.toml`` for backward compatibility; ``multillm serve`` is the new
canonical equivalent.

Exit codes follow the plan contract:
- 0 on success
- 1 on Alembic ``CommandError`` (migration logic failure)
- 2 on missing DB / FileNotFoundError surfaced by the runner
"""

from __future__ import annotations

import functools
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click
from alembic.util.exc import CommandError

from multillm.migrations.runner import (
    current_revision,
    migrate_down,
    migrate_dry_run,
    migrate_up,
)
from multillm.evaluation.api import get_evaluation_store
from multillm.evaluation.suites import load_finops_agent_cases

__all__ = ["app", "migrate"]

_F = TypeVar("_F", bound=Callable[..., Any])


def _emit_error(message: str, exit_code: int) -> None:
    click.echo(message, err=True)
    sys.exit(exit_code)


def _handle_migration_errors(func: _F) -> _F:
    """Map runner exceptions to the CLI's exit-code contract.

    - ``alembic.util.exc.CommandError`` -> exit 1 with 'alembic error:' prefix
    - ``FileNotFoundError`` -> exit 2 with 'database not found:' prefix
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except CommandError as exc:
            _emit_error(f"alembic error: {exc}", exit_code=1)
        except FileNotFoundError as exc:
            _emit_error(f"database not found: {exc}", exit_code=2)

    return wrapper  # type: ignore[return-value]


@click.group(
    name="multillm",
    help="MultiLLM gateway control plane — run migrations, start the server, "
    "and (in later phases) manage tenants.",
)
def app() -> None:
    """Top-level command group."""


@app.group(
    name="migrate",
    invoke_without_command=True,
    help="Schema migrations against the MultiLLM SQLite database.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="List pending revisions without applying them.",
)
@click.pass_context
def migrate(ctx: click.Context, dry_run: bool) -> None:
    """Migrate command group with a top-level --dry-run flag."""
    if dry_run:
        if ctx.invoked_subcommand is not None:
            _emit_error(
                "--dry-run is a top-level flag for `multillm migrate` and "
                "cannot be combined with a subcommand.",
                exit_code=1,
            )
        _dry_run_dispatch()
        sys.exit(0)

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@_handle_migration_errors
def _dry_run_dispatch() -> None:
    pending = migrate_dry_run()
    if not pending:
        click.echo("No pending migrations.")
        return
    click.echo("Pending migrations:")
    for rev in pending:
        click.echo(f"  - {rev}")


def _format_revision(rev: str | None) -> str:
    return rev if rev is not None else "<base>"


@migrate.command(name="up", help="Backup the DB and upgrade to TARGET (default: head).")
@click.option(
    "--target",
    default="head",
    show_default=True,
    help="Alembic target revision (default: head).",
)
@_handle_migration_errors
def migrate_up_cmd(target: str) -> None:
    """Run ``migrate_up`` and report the backup file + new revision."""
    from multillm.migrations.backup import BACKUP_DIR

    pre_existing: set[str] = set()
    if BACKUP_DIR.exists():
        pre_existing = {p.name for p in BACKUP_DIR.iterdir()}

    new_rev = migrate_up(target=target)

    if BACKUP_DIR.exists():
        new_files = sorted(
            (p for p in BACKUP_DIR.iterdir() if p.name not in pre_existing),
            key=lambda p: p.stat().st_mtime,
        )
        for backup in new_files:
            click.echo(f"Backup written: {backup}")

    click.echo(f"Migrated to: {_format_revision(new_rev)}")


@migrate.command(
    name="down", help="Downgrade to TARGET (e.g. 'base' or a revision ID)."
)
@click.option(
    "--target",
    required=True,
    help="Alembic target revision (e.g. 'base' or a specific revision id).",
)
@_handle_migration_errors
def migrate_down_cmd(target: str) -> None:
    """Run ``migrate_down`` and report the new revision."""
    new_rev = migrate_down(target=target)
    click.echo(f"Migrated to: {_format_revision(new_rev)}")


@migrate.command(name="status", help="Print the alembic revision currently stamped.")
def migrate_status_cmd() -> None:
    """Print the current revision or 'no migrations applied'."""
    rev = current_revision()
    if rev is None:
        click.echo("no migrations applied")
    else:
        click.echo(f"Current revision: {rev}")


@app.command(name="serve", help="Launch the MultiLLM gateway HTTP server.")
def serve() -> None:
    """Start the gateway — delegates to the legacy ``multillm.gateway:main``."""
    from multillm.gateway import main as gateway_main

    gateway_main()


def _format_usage_table(report: dict[str, Any], *, limit: int) -> str:
    rows = report.get("rows", []) or []
    kind = report.get("kind", "usage")
    if not rows:
        return f"No {kind} usage rows found."

    if kind == "session":
        header = f"{'Started':19} {'Source':13} {'Project':18} {'Tokens':>12} {'Cost':>10} Models"
        lines = [header, "-" * len(header)]
        for row in rows[:limit]:
            started = str(row.get("startedAt", ""))[:19]
            source = str(row.get("source", ""))[:13]
            project = str(row.get("project", ""))[:18]
            models = ", ".join(row.get("models", []) or [])
            lines.append(
                f"{started:19} {source:13} {project:18} "
                f"{int(row.get('tokens', 0) or 0):12,} "
                f"${float(row.get('actualCostUSD', 0) or 0):9.4f} {models}"
            )
        return "\n".join(lines)

    if kind == "blocks":
        header = f"{'Block start':19} {'Block end':19} {'Sessions':>8} {'Tokens':>12} {'Cost':>10}"
        lines = [header, "-" * len(header)]
        for row in rows[:limit]:
            lines.append(
                f"{str(row.get('startsAt', ''))[:19]:19} "
                f"{str(row.get('endsAt', ''))[:19]:19} "
                f"{int(row.get('sessions', 0) or 0):8,} "
                f"{int(row.get('tokens', 0) or 0):12,} "
                f"${float(row.get('actualCostUSD', 0) or 0):9.4f}"
            )
        return "\n".join(lines)

    header = f"{'Period':12} {'Sources':32} {'Sessions':>8} {'Requests':>8} {'Tokens':>12} {'Cost':>10}"
    lines = [header, "-" * len(header)]
    for row in rows[:limit]:
        sources = ",".join(row.get("sources", []) or [])[:32]
        lines.append(
            f"{str(row.get('period', '')):12} {sources:32} "
            f"{int(row.get('sessions', 0) or 0):8,} "
            f"{int(row.get('requests', 0) or 0):8,} "
            f"{int(row.get('tokens', 0) or 0):12,} "
            f"${float(row.get('actualCostUSD', 0) or 0):9.4f}"
        )
    return "\n".join(lines)


@app.command(
    name="usage", help="Show daily, weekly, monthly, session, or block usage reports."
)
@click.option(
    "--kind",
    type=click.Choice(["daily", "weekly", "monthly", "session", "blocks"]),
    default="daily",
    show_default=True,
)
@click.option("--hours", type=int, default=720, show_default=True)
@click.option("--project", default=None, help="Filter to a project name.")
@click.option("--limit", type=int, default=50, show_default=True)
@click.option("--json-output", "as_json", is_flag=True, help="Emit raw JSON.")
@click.option(
    "--gateway",
    default="http://localhost:8080",
    show_default=True,
    help="MultiLLM gateway base URL.",
)
def usage_cmd(
    kind: str,
    hours: int,
    project: str | None,
    limit: int,
    as_json: bool,
    gateway: str,
) -> None:
    """Fetch and print a usage report from the running gateway."""
    query = {"kind": kind, "hours": str(hours), "session_limit": str(limit)}
    if project:
        query["project"] = project
    url = gateway.rstrip("/") + "/api/usage-report?" + urllib.parse.urlencode(query)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        _emit_error(f"usage report failed: {exc}", exit_code=1)
        return

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    click.echo(_format_usage_table(payload, limit=limit))


def _eval_http_json(
    method: str,
    gateway: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call one evaluation API endpoint with the configured gateway key."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("MULTILLM_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(
        gateway.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1_000]
        raise click.ClickException(
            f"evaluation API returned HTTP {exc.code}: {detail}"
        ) from exc
    except OSError as exc:
        raise click.ClickException(f"cannot reach evaluation API: {exc}") from exc
    if result.get("success") is False:
        message = (result.get("error") or {}).get(
            "message"
        ) or "evaluation request failed"
        raise click.ClickException(str(message))
    return result


def _eval_http_bytes(gateway: str, path: str) -> bytes:
    headers: dict[str, str] = {}
    api_key = os.getenv("MULTILLM_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(
        gateway.rstrip("/") + path,
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1_000]
        raise click.ClickException(
            f"evaluation export returned HTTP {exc.code}: {detail}"
        ) from exc
    except OSError as exc:
        raise click.ClickException(
            f"cannot reach evaluation export API: {exc}"
        ) from exc


@app.group(name="eval", help="Run, inspect, review, and export model/MoA evaluations.")
def evaluation() -> None:
    """Evaluation control-plane commands."""


@evaluation.command(
    name="preflight", help="Execution-probe live model and judge aliases."
)
@click.option(
    "--target",
    "targets",
    multiple=True,
    required=True,
    help="Alias to probe; repeatable.",
)
@click.option("--gateway", default="http://localhost:8080", show_default=True)
@click.option("--json-output", "as_json", is_flag=True)
def evaluation_preflight_cmd(
    targets: tuple[str, ...], gateway: str, as_json: bool
) -> None:
    result = _eval_http_json(
        "POST",
        gateway,
        "/api/evaluations/preflight",
        {"targets": list(dict.fromkeys(targets))},
    )["data"]
    if as_json:
        click.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    click.echo(f"Execution mode: {result.get('executionMode', 'unknown')}")
    click.echo(f"Sandbox fallback: {result.get('sandboxFallback', 'unknown')}")
    for item in result.get("targets") or []:
        mark = "PASS" if item.get("executionVerified") else "FAIL"
        click.echo(f"  {mark} {item.get('alias')}")
    click.echo(f"Receipt: {result.get('receipt')}")


@evaluation.command(name="run", help="Queue a same-prompt model and MoA evaluation.")
@click.option("--suite", default="finops-v1", show_default=True)
@click.option(
    "--profile", type=click.Choice(["ci", "nightly", "release"]), default="ci"
)
@click.option(
    "--target", "targets", multiple=True, help="Base model alias; repeatable."
)
@click.option(
    "--all-live",
    is_flag=True,
    help="Discover all configured host CLI models, then execution-probe them.",
)
@click.option(
    "--moa", "moa_variants", multiple=True, default=("moa/quality",), show_default=True
)
@click.option(
    "--judge", "judges", multiple=True, help="Independent judge alias; repeatable."
)
@click.option(
    "--repeat", "repeats", type=click.IntRange(1, 5), default=1, show_default=True
)
@click.option("--live", is_flag=True, help="Probe and use live host model execution.")
@click.option("--gateway", default="http://localhost:8080", show_default=True)
@click.option("--json-output", "as_json", is_flag=True)
def evaluation_run_cmd(
    suite: str,
    profile: str,
    targets: tuple[str, ...],
    all_live: bool,
    moa_variants: tuple[str, ...],
    judges: tuple[str, ...],
    repeats: int,
    live: bool,
    gateway: str,
    as_json: bool,
) -> None:
    unique_targets = list(dict.fromkeys(targets))
    unique_judges = list(dict.fromkeys(judges))
    if unique_judges and len(unique_judges) < 2:
        raise click.ClickException(
            "dual-judge evaluation requires at least two --judge values"
        )
    if all_live and not live:
        raise click.ClickException("--all-live requires --live")
    if all_live:
        discovered = (
            _eval_http_json("GET", gateway, "/api/evaluations/live-targets")[
                "data"
            ].get("targets")
            or []
        )
        discovered_aliases = [
            str(item.get("alias") or "").strip()
            for item in discovered
            if isinstance(item, dict) and str(item.get("alias") or "").strip()
        ]
        unique_targets = list(dict.fromkeys([*unique_targets, *discovered_aliases]))
        unique_targets = [
            target for target in unique_targets if target not in unique_judges
        ]
        if not unique_targets:
            raise click.ClickException(
                "no independent live candidates remain after excluding judge aliases"
            )
    receipt = None
    if live:
        probe_targets = list(dict.fromkeys([*unique_targets, *unique_judges]))
        if not probe_targets:
            raise click.ClickException(
                "live evaluation requires explicit --target aliases"
            )
        receipt = _eval_http_json(
            "POST",
            gateway,
            "/api/evaluations/preflight",
            {"targets": probe_targets},
        )["data"]["receipt"]
    request_payload: dict[str, Any] = {
        "suite_id": suite,
        "profile": profile,
        "candidate_scope": "live"
        if all_live
        else "explicit"
        if unique_targets
        else "core",
        "candidates": unique_targets,
        "moa_variants": list(dict.fromkeys(moa_variants)),
        "judge_pool": unique_judges,
        "execution_mode": "live_host" if live else "fixture",
        "live_authorized": live,
        "preflight_receipt": receipt,
        "repeats": repeats,
    }
    result = _eval_http_json("POST", gateway, "/api/evaluations/runs", request_payload)[
        "data"
    ]
    if as_json:
        click.echo(json.dumps(result, indent=2, sort_keys=True))
    else:
        click.echo(f"Queued evaluation {result['id']} ({result['status']})")


@evaluation.command(name="status", help="Show one evaluation run and its release gate.")
@click.argument("run_id")
@click.option("--gateway", default="http://localhost:8080", show_default=True)
@click.option("--json-output", "as_json", is_flag=True)
def evaluation_status_cmd(run_id: str, gateway: str, as_json: bool) -> None:
    result = _eval_http_json(
        "GET", gateway, f"/api/evaluations/runs/{urllib.parse.quote(run_id, safe='')}"
    )["data"]
    if as_json:
        click.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    click.echo(f"{result['id']}: {result['status']}")
    summary = result.get("summary") or {}
    if summary:
        click.echo(f"Release gate: {summary.get('releaseGate', 'not evaluated')}")
        click.echo(f"Outputs: {summary.get('outputs', 0)}")


@evaluation.command(
    name="suite-import",
    help="Import an oci-finops-agent golden JSON file as an immutable suite.",
)
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--suite-id", required=True)
@click.option("--name", default="Imported FinOps agent suite", show_default=True)
@click.option("--version", default="1.0.0", show_default=True)
@click.option("--tenant", default="default", show_default=True)
def evaluation_suite_import_cmd(
    source: Path, suite_id: str, name: str, version: str, tenant: str
) -> None:
    try:
        cases = load_finops_agent_cases(source)
        suite = get_evaluation_store().upsert_suite(
            tenant,
            suite_id=suite_id,
            name=name,
            version=version,
            source=str(source.resolve()),
            license_id="project-owned",
            cases=cases,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Imported {suite['caseCount']} cases as {suite['id']} "
        f"(sha256 {suite['contentHash']})"
    )


@evaluation.command(name="export", help="Write an audit export for one run.")
@click.argument("run_id")
@click.option(
    "--format",
    "export_format",
    type=click.Choice(["json", "csv", "html"]),
    default="json",
)
@click.option(
    "--output", type=click.Path(dir_okay=False, path_type=Path), required=True
)
@click.option("--gateway", default="http://localhost:8080", show_default=True)
def evaluation_export_cmd(
    run_id: str, export_format: str, output: Path, gateway: str
) -> None:
    safe_run = urllib.parse.quote(run_id, safe="")
    data = _eval_http_bytes(
        gateway,
        f"/api/evaluations/runs/{safe_run}/export?format={export_format}",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    click.echo(f"Wrote {export_format.upper()} evaluation export: {output}")


@app.group(
    name="service",
    help="Install/uninstall the gateway as a per-user service that starts at login.",
)
def service() -> None:
    """Service management command group (launchd on macOS, systemd on Linux)."""


@service.command(
    name="install", help="Install and start the gateway as a login service."
)
def service_install_cmd() -> None:
    """Write the platform service file and load it (RunAtLoad/KeepAlive)."""
    from multillm.config import DATA_DIR, GATEWAY_HOST, GATEWAY_PORT
    from multillm.service import install_service

    try:
        paths = install_service(host=GATEWAY_HOST, port=GATEWAY_PORT, data_dir=DATA_DIR)
    except RuntimeError as exc:
        _emit_error(f"service install failed: {exc}", exit_code=1)
        return
    click.echo(f"Installed {paths.platform} service: {paths.unit_path}")
    click.echo(f"Gateway will start at login at http://{GATEWAY_HOST}:{GATEWAY_PORT}")


@service.command(name="uninstall", help="Stop and remove the gateway boot service.")
def service_uninstall_cmd() -> None:
    """Unload and delete the platform service file."""
    from multillm.service import uninstall_service

    paths = uninstall_service()
    click.echo(f"Removed {paths.platform} service: {paths.unit_path}")


@service.command(
    name="status", help="Show whether the boot service is installed and loaded."
)
def service_status_cmd() -> None:
    """Print installed/loaded state for the host platform."""
    from multillm.service import service_status

    state = service_status()
    mark = "🟢" if state["loaded"] else ("🟡" if state["installed"] else "⚪")
    click.echo(f"{mark} {state['platform']} service — {state['detail']}")
    click.echo(f"   unit: {state['unit_path']}")


@app.command(
    name="reset",
    help=(
        "Reset the first-run wizard. Requires --confirm because this "
        "deletes the admin user and re-enables /setup."
    ),
)
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Required: confirm that you want to wipe the admin user and "
    "re-enable the /setup wizard.",
)
def reset_cmd(confirm: bool) -> None:
    """Wipe the wizard state and admin user so the next start hits /setup."""
    if not confirm:
        click.echo(
            "Refusing to reset without --confirm. This will delete the "
            "admin user and re-enable the /setup wizard.",
            err=True,
        )
        sys.exit(1)

    import sqlite3

    from multillm.migrations.runner import db_path
    from multillm.setup.state import reset_setup

    path = db_path()
    if not path.exists():
        click.echo(f"No database at {path} — nothing to reset.")
        return

    conn = sqlite3.connect(path)
    try:
        reset_setup(conn)
    finally:
        conn.close()

    click.echo("Setup reset. Restart the gateway to re-enter the wizard.")


if __name__ == "__main__":  # pragma: no cover
    app()
