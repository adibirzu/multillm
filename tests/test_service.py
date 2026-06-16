# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for the OS-service installer (pure render + path resolution)."""
import plistlib
from pathlib import Path

import pytest

from multillm.service import (
    LAUNCHD_LABEL,
    SYSTEMD_UNIT,
    render_launchd_plist,
    render_systemd_unit,
    resolve_paths,
)


class TestRenderLaunchd:

    def test_plist_has_runatload_and_keepalive(self):
        raw = render_launchd_plist(
            python_exe="/usr/bin/python3",
            data_dir=Path("/home/u/.multillm"),
            host="127.0.0.1",
            port=8080,
        )
        plist = plistlib.loads(raw)
        assert plist["Label"] == LAUNCHD_LABEL
        assert plist["RunAtLoad"] is True
        assert plist["KeepAlive"] is True
        assert plist["ProgramArguments"] == ["/usr/bin/python3", "-m", "multillm.gateway"]
        assert plist["EnvironmentVariables"]["GATEWAY_PORT"] == "8080"
        assert plist["EnvironmentVariables"]["MULTILLM_HOME"] == "/home/u/.multillm"
        # PATH must be set so subprocess CLI backends resolve under launchd,
        # and must lead with the interpreter's own bin dir.
        path = plist["EnvironmentVariables"]["PATH"]
        assert path.startswith("/usr/bin")
        assert "/usr/local/bin" in path
        assert plist["StandardOutPath"].endswith(".log")


class TestRenderSystemd:

    def test_unit_has_restart_and_execstart(self):
        unit = render_systemd_unit(
            python_exe="/usr/bin/python3",
            data_dir=Path("/home/u/.multillm"),
            host="0.0.0.0",
            port=9000,
        )
        assert 'ExecStart="/usr/bin/python3" -m multillm.gateway' in unit
        assert "Restart=always" in unit
        assert 'Environment="GATEWAY_PORT=9000"' in unit
        assert "WantedBy=default.target" in unit

    def test_unit_quotes_paths_with_spaces(self):
        unit = render_systemd_unit(
            python_exe="/home/u/my venv/bin/python",
            data_dir=Path("/home/u/data dir"),
            host="127.0.0.1",
            port=8080,
        )
        # Spaced executable must be quoted in ExecStart, env values quoted too.
        assert 'ExecStart="/home/u/my venv/bin/python" -m multillm.gateway' in unit
        assert 'Environment="MULTILLM_HOME=/home/u/data dir"' in unit


class TestResolvePaths:

    def test_darwin_launchagent_path(self):
        paths = resolve_paths(platform="darwin", home=Path("/Users/jane"))
        assert paths.platform == "darwin"
        assert paths.unit_path == Path(f"/Users/jane/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")

    def test_linux_systemd_user_path(self):
        paths = resolve_paths(platform="linux", home=Path("/home/jane"))
        assert paths.platform == "linux"
        assert paths.unit_path == Path(f"/home/jane/.config/systemd/user/{SYSTEMD_UNIT}")

    def test_unsupported_platform_raises(self):
        with pytest.raises(RuntimeError):
            resolve_paths(platform="win32", home=Path("/x"))
