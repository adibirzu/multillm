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

import sys

import click
from alembic.util.exc import CommandError

from multillm.migrations.runner import (
    current_revision,
    migrate_down,
    migrate_dry_run,
    migrate_up,
)

__all__ = ["app", "migrate"]


def _emit_error(message: str, exit_code: int) -> None:
    click.echo(message, err=True)
    sys.exit(exit_code)


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
        try:
            pending = migrate_dry_run()
        except CommandError as exc:
            _emit_error(f"alembic error: {exc}", exit_code=1)
        except FileNotFoundError as exc:
            _emit_error(f"database not found: {exc}", exit_code=2)
        if not pending:
            click.echo("No pending migrations.")
        else:
            click.echo("Pending migrations:")
            for rev in pending:
                click.echo(f"  - {rev}")
        sys.exit(0)

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@migrate.command(name="up", help="Backup the DB and upgrade to TARGET (default: head).")
@click.option(
    "--target",
    default="head",
    show_default=True,
    help="Alembic target revision (default: head).",
)
def migrate_up_cmd(target: str) -> None:
    """Run ``migrate_up`` and report the backup file + new revision."""
    from multillm.migrations.runner import alembic_config, db_path
    from multillm.migrations.backup import BACKUP_DIR

    pre_existing = set()
    if BACKUP_DIR.exists():
        pre_existing = {p.name for p in BACKUP_DIR.iterdir()}

    try:
        new_rev = migrate_up(target=target)
    except CommandError as exc:
        _emit_error(f"alembic error: {exc}", exit_code=1)
    except FileNotFoundError as exc:
        _emit_error(f"database not found: {exc}", exit_code=2)

    # Surface the new backup file (if any was written this invocation).
    if BACKUP_DIR.exists():
        new_files = sorted(
            (p for p in BACKUP_DIR.iterdir() if p.name not in pre_existing),
            key=lambda p: p.stat().st_mtime,
        )
        for backup in new_files:
            click.echo(f"Backup written: {backup}")

    if new_rev is None:
        click.echo("Migrated to: <base>")
    else:
        click.echo(f"Migrated to: {new_rev}")
    # Suppress unused-import warnings: alembic_config / db_path are reached
    # transitively by migrate_up and exposed here for future expansion.
    _ = (alembic_config, db_path)


@migrate.command(name="down", help="Downgrade to TARGET (e.g. 'base' or a revision ID).")
@click.option(
    "--target",
    required=True,
    help="Alembic target revision (e.g. 'base' or a specific revision id).",
)
def migrate_down_cmd(target: str) -> None:
    """Run ``migrate_down`` and report the new revision."""
    try:
        new_rev = migrate_down(target=target)
    except CommandError as exc:
        _emit_error(f"alembic error: {exc}", exit_code=1)
    except FileNotFoundError as exc:
        _emit_error(f"database not found: {exc}", exit_code=2)

    if new_rev is None:
        click.echo("Migrated to: <base>")
    else:
        click.echo(f"Migrated to: {new_rev}")


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


if __name__ == "__main__":  # pragma: no cover
    app()
