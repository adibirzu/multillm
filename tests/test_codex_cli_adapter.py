# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for Codex CLI adapter profile/model resolution."""

from multillm.adapters import codex_cli


def test_resolve_codex_exec_target_prefers_matching_profile(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                'profile = "internal-gpt-5-4"',
                "",
                "[profiles.internal-gpt-5-4]",
                'model = "gpt-5.4"',
                'review_model = "gpt-5.4"',
            ]
        )
        + "\n"
    )

    monkeypatch.setattr(codex_cli, "CODEX_CONFIG_FILE", config_path)
    codex_cli._load_codex_profiles_cached.cache_clear()

    exec_target, resolved_model = codex_cli._resolve_codex_exec_target("gpt-5-4")

    assert exec_target == ["-p", "internal-gpt-5-4"]
    assert resolved_model == "gpt-5.4"


def test_resolve_codex_exec_target_falls_back_to_direct_model(tmp_path, monkeypatch):
    missing_config = tmp_path / "missing.toml"

    monkeypatch.setattr(codex_cli, "CODEX_CONFIG_FILE", missing_config)
    codex_cli._load_codex_profiles_cached.cache_clear()

    exec_target, resolved_model = codex_cli._resolve_codex_exec_target("gpt-5-3-codex")

    assert exec_target == ["-m", "gpt-5.3-codex"]
    assert resolved_model == "gpt-5.3-codex"


def test_run_codex_exec_uses_modern_flags(monkeypatch):
    """Regression fence: exec must NOT use removed --full-auto and MUST pass
    --skip-git-repo-check (the gateway runs from a non-repo working dir)."""
    import asyncio

    captured = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self, input=None):
            return (b"hi", b"")

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr(
        codex_cli, "resolve_cli_binary", lambda *a, **k: "/usr/bin/codex"
    )
    monkeypatch.setattr(codex_cli.asyncio, "create_subprocess_exec", _fake_exec)

    rc, out, err = asyncio.run(
        codex_cli._run_codex_exec("hello", "read-only", ["-p", "prof"])
    )
    assert rc == 0
    args = captured["args"]
    assert "--full-auto" not in args
    assert "--skip-git-repo-check" in args
    assert "exec" in args and "-s" in args and "read-only" in args


def test_run_codex_exec_isolates_per_request_effort_and_verbosity(monkeypatch):
    import asyncio

    captured = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self, input=None):
            return (b"hi", b"")

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr(
        codex_cli, "resolve_cli_binary", lambda *a, **k: "/usr/bin/codex"
    )
    monkeypatch.setattr(codex_cli.asyncio, "create_subprocess_exec", _fake_exec)

    asyncio.run(
        codex_cli._run_codex_exec(
            "hello",
            "read-only",
            ["-p", "prof"],
            config_overrides={
                "model_reasoning_effort": "low",
                "model_verbosity": "concise",
            },
        )
    )

    args = captured["args"]
    effort_index = args.index("model_reasoning_effort=\"low\"")
    verbosity_index = args.index("model_verbosity=\"concise\"")
    assert args[effort_index - 1] == "-c"
    assert args[verbosity_index - 1] == "-c"
