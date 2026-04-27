"""Helpers for finding local CLI tools from hook-launched gateway processes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def build_cli_search_path(base_path: str | None = None) -> str:
    entries = []
    for raw_entry in (base_path or os.getenv("PATH", "")).split(os.pathsep):
        entry = raw_entry.strip()
        if entry and entry not in entries:
            entries.append(entry)

    common_dirs = [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    for entry in common_dirs:
        if entry not in entries:
            entries.append(entry)

    return os.pathsep.join(entries)


def resolve_cli_binary(binary: str, *, env_var: str | None = None) -> str | None:
    configured = os.getenv(env_var, "").strip() if env_var else ""
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.exists():
            return str(configured_path)
        found_configured = shutil.which(configured, path=build_cli_search_path())
        if found_configured:
            return found_configured

    return shutil.which(binary, path=build_cli_search_path())
