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
import sys
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


@migrate.command(name="down", help="Downgrade to TARGET (e.g. 'base' or a revision ID).")
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


if __name__ == "__main__":  # pragma: no cover
    app()
