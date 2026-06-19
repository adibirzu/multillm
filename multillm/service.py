# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Install the MultiLLM gateway as an OS-managed service that starts automatically.

Both are *per-user* services that start at **login / user-session start** (not
true system boot — that would need a LaunchDaemon or a system-level systemd unit
plus ``loginctl enable-linger``):

- **macOS** — a per-user LaunchAgent at
  ``~/Library/LaunchAgents/com.multillm.gateway.plist`` with ``RunAtLoad`` and
  ``KeepAlive`` (auto-restart on crash), loaded via ``launchctl``.
- **Linux** — a systemd *user* unit at
  ``~/.config/systemd/user/multillm.service`` enabled with ``systemctl --user``.

The render functions are pure (no I/O) so they can be unit-tested; the
``install``/``uninstall``/``status`` helpers perform the filesystem + subprocess
side effects.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LAUNCHD_LABEL = "com.multillm.gateway"
SYSTEMD_UNIT = "multillm.service"


@dataclass(frozen=True)
class ServicePaths:
    """Resolved locations for the current platform's service definition."""

    platform: str  # "darwin" | "linux"
    unit_path: Path
    label: str


def _python_exe() -> str:
    """Absolute path to the interpreter running this process."""
    return sys.executable or "python3"


def _service_path(python_exe: str) -> str:
    """PATH for the service env, including the interpreter's bin dir.

    launchd gives boot processes a minimal PATH, so the gateway's subprocess
    backends (``codex``, ``gemini``, ``ollama``) would not be found. Prepend the
    interpreter's directory and common install locations.
    """
    bin_dir = str(Path(python_exe).parent)
    home_local = str(Path.home() / ".local" / "bin")
    lmstudio_bin = str(Path.home() / ".lmstudio" / "bin")  # LM Studio CLI (lms)
    parts = [
        bin_dir,
        "/usr/local/bin",
        "/opt/homebrew/bin",
        home_local,
        lmstudio_bin,
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered = [p for p in parts if not (p in seen or seen.add(p))]
    return ":".join(ordered)


def render_launchd_plist(
    *,
    python_exe: str,
    data_dir: Path,
    host: str,
    port: int,
    label: str = LAUNCHD_LABEL,
) -> bytes:
    """Build a macOS LaunchAgent plist as bytes.

    ``RunAtLoad`` starts the gateway at login; ``KeepAlive`` restarts it if it
    exits — replacing the manual PID-file babysitting in the SessionStart hook.
    ``PATH`` is set explicitly so subprocess CLI backends resolve under launchd.
    """
    log_file = str(data_dir / "gateway-launchd.log")
    plist: dict = {
        "Label": label,
        "ProgramArguments": [python_exe, "-m", "multillm.gateway"],
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": {
            "PATH": _service_path(python_exe),
            "HOME": str(Path.home()),
            "GATEWAY_HOST": host,
            "GATEWAY_PORT": str(port),
            "MULTILLM_HOME": str(data_dir),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": log_file,
        "StandardErrorPath": log_file,
        "ProcessType": "Background",
    }
    return plistlib.dumps(plist)


def render_systemd_unit(
    *,
    python_exe: str,
    data_dir: Path,
    host: str,
    port: int,
) -> str:
    """Build a systemd *user* unit file body (Linux)."""
    log_file = data_dir / "gateway.log"
    # Quote values so paths containing spaces or special chars can't break the
    # unit or inject extra directives.
    return (
        "[Unit]\n"
        "Description=MultiLLM Gateway\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f'Environment="PATH={_service_path(python_exe)}"\n'
        f'Environment="GATEWAY_HOST={host}"\n'
        f'Environment="GATEWAY_PORT={int(port)}"\n'
        f'Environment="MULTILLM_HOME={data_dir}"\n'
        f'ExecStart="{python_exe}" -m multillm.gateway\n'
        "Restart=always\n"
        "RestartSec=3\n"
        f"StandardOutput=append:{log_file}\n"
        f"StandardError=append:{log_file}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def resolve_paths(
    platform: str | None = None, home: Path | None = None
) -> ServicePaths:
    """Resolve the service definition path for ``platform`` (defaults to host)."""
    plat = platform or sys.platform
    home = home or Path.home()
    if plat == "darwin":
        return ServicePaths(
            platform="darwin",
            unit_path=home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist",
            label=LAUNCHD_LABEL,
        )
    if plat.startswith("linux"):
        return ServicePaths(
            platform="linux",
            unit_path=home / ".config" / "systemd" / "user" / SYSTEMD_UNIT,
            label=SYSTEMD_UNIT,
        )
    raise RuntimeError(f"Unsupported platform for service install: {plat}")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def install_service(*, host: str, port: int, data_dir: Path) -> ServicePaths:
    """Write and load the service so the gateway starts on boot/login."""
    paths = resolve_paths()
    paths.unit_path.parent.mkdir(parents=True, exist_ok=True)
    py = _python_exe()

    if paths.platform == "darwin":
        paths.unit_path.write_bytes(
            render_launchd_plist(python_exe=py, data_dir=data_dir, host=host, port=port)
        )
        # Reload idempotently: unload an existing definition, then load.
        _run(["launchctl", "unload", str(paths.unit_path)])
        result = _run(["launchctl", "load", "-w", str(paths.unit_path)])
        if result.returncode != 0:
            raise RuntimeError(f"launchctl load failed: {result.stderr.strip()}")
    else:
        paths.unit_path.write_text(
            render_systemd_unit(python_exe=py, data_dir=data_dir, host=host, port=port)
        )
        _run(["systemctl", "--user", "daemon-reload"])
        result = _run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT])
        if result.returncode != 0:
            raise RuntimeError(f"systemctl enable failed: {result.stderr.strip()}")
    return paths


def uninstall_service() -> ServicePaths:
    """Stop, disable, and remove the service definition."""
    paths = resolve_paths()
    if paths.platform == "darwin":
        if paths.unit_path.exists():
            _run(["launchctl", "unload", "-w", str(paths.unit_path)])
            paths.unit_path.unlink()
    else:
        _run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT])
        if paths.unit_path.exists():
            paths.unit_path.unlink()
        _run(["systemctl", "--user", "daemon-reload"])
    return paths


def service_status() -> dict:
    """Return installed/loaded state for the host platform's service."""
    paths = resolve_paths()
    installed = paths.unit_path.exists()
    loaded = False
    detail = ""
    if paths.platform == "darwin":
        result = _run(["launchctl", "list"])
        loaded = LAUNCHD_LABEL in result.stdout
        detail = (
            "loaded"
            if loaded
            else ("installed, not loaded" if installed else "not installed")
        )
    else:
        result = _run(["systemctl", "--user", "is-active", SYSTEMD_UNIT])
        detail = result.stdout.strip() or "unknown"
        loaded = detail == "active"
    return {
        "platform": paths.platform,
        "unit_path": str(paths.unit_path),
        "installed": installed,
        "loaded": loaded,
        "detail": detail,
    }
