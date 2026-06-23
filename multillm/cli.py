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
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, TypeVar

import click
from alembic.util.exc import CommandError

from multillm.migrations.runner import (
    current_revision,
    migrate_down,
    migrate_dry_run,
    migrate_up,
)

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


@app.command(name="usage", help="Show daily, weekly, monthly, session, or block usage reports.")
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
