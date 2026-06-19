# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for on-demand local backend startup (no real processes spawned)."""

from unittest.mock import patch

import pytest

from multillm import local_launch
from multillm.local_launch import (
    backend_binary,
    ensure_any_local_backend,
    ensure_local_backend,
    is_backend_installed,
)


class TestInstalledDetection:
    def test_backend_binary_resolves_via_which(self):
        with patch(
            "multillm.local_launch.shutil.which", return_value="/usr/local/bin/ollama"
        ):
            assert backend_binary("ollama") == "/usr/local/bin/ollama"
            assert is_backend_installed("ollama") is True

    def test_not_installed_when_which_returns_none(self):
        with patch("multillm.local_launch.shutil.which", return_value=None):
            assert is_backend_installed("ollama") is False

    def test_unknown_backend_has_no_binary(self):
        assert backend_binary("nope") is None

    def test_lmstudio_found_via_extra_paths_when_not_on_path(self):
        # `lms` is not on PATH but exists in ~/.lmstudio/bin — must still resolve.
        with (
            patch("multillm.local_launch.shutil.which", return_value=None),
            patch("multillm.local_launch.Path.is_file", return_value=True),
            patch("multillm.local_launch.os.access", return_value=True),
        ):
            resolved = backend_binary("lmstudio")
            # Assert inside the patched context — otherwise is_backend_installed
            # hits the real filesystem (passes only where LM Studio is installed).
            assert resolved is not None
            assert resolved.endswith("/.lmstudio/bin/lms")
            assert is_backend_installed("lmstudio") is True


class TestEnsureLocalBackend:
    @pytest.mark.asyncio
    async def test_already_reachable_does_not_spawn(self):
        with (
            patch("multillm.local_launch._probe_backend", return_value=True) as probe,
            patch("multillm.local_launch._spawn_backend") as spawn,
        ):
            ok = await ensure_local_backend("ollama")
        assert ok is True
        spawn.assert_not_called()
        probe.assert_awaited()

    @pytest.mark.asyncio
    async def test_not_installed_returns_false_without_spawn(self):
        with (
            patch("multillm.local_launch._probe_backend", return_value=False),
            patch("multillm.local_launch.is_backend_installed", return_value=False),
            patch("multillm.local_launch._spawn_backend") as spawn,
        ):
            ok = await ensure_local_backend("ollama")
        assert ok is False
        spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_spawns_then_becomes_ready(self):
        # Down on the outside-lock probe AND the post-lock re-check, then the
        # readiness poll after spawn succeeds.
        probes = iter([False, False, True])

        async def fake_probe(_backend):
            return next(probes, True)

        with (
            patch("multillm.local_launch._probe_backend", side_effect=fake_probe),
            patch("multillm.local_launch.is_backend_installed", return_value=True),
            patch("multillm.local_launch._spawn_backend") as spawn,
        ):
            ok = await ensure_local_backend("ollama", timeout=2)
        assert ok is True
        spawn.assert_called_once_with("ollama")

    @pytest.mark.asyncio
    async def test_does_not_start_remote_backend(self, monkeypatch):
        # A non-localhost URL must never be auto-started.
        monkeypatch.setitem(
            local_launch._LAUNCHERS,
            "ollama",
            {**local_launch._LAUNCHERS["ollama"], "url": "http://10.0.0.5:11434"},
        )
        with (
            patch("multillm.local_launch._probe_backend", return_value=False),
            patch("multillm.local_launch.is_backend_installed", return_value=True),
            patch("multillm.local_launch._spawn_backend") as spawn,
        ):
            ok = await ensure_local_backend("ollama")
        assert ok is False
        spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_any_returns_first_ready(self):
        with (
            patch(
                "multillm.local_launch.installed_local_backends",
                return_value=["ollama", "lmstudio"],
            ),
            patch(
                "multillm.local_launch.ensure_local_backend", return_value=True
            ) as ensure,
        ):
            name = await ensure_any_local_backend()
        assert name == "ollama"
        ensure.assert_awaited()

    @pytest.mark.asyncio
    async def test_ensure_any_returns_none_when_nothing_installed(self):
        with patch("multillm.local_launch.installed_local_backends", return_value=[]):
            assert await ensure_any_local_backend() is None
