"""Tests for Codex CLI adapter profile/model resolution."""

from multillm.adapters import codex_cli


def test_resolve_codex_exec_target_prefers_matching_profile(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '\n'.join(
            [
                'profile = "oca-gpt-5-4"',
                "",
                "[profiles.oca-gpt-5-4]",
                'model = "gpt-5.4"',
                'review_model = "oca/gpt-5.4"',
            ]
        )
        + "\n"
    )

    monkeypatch.setattr(codex_cli, "CODEX_CONFIG_FILE", config_path)
    codex_cli._load_codex_profiles_cached.cache_clear()

    exec_target, resolved_model = codex_cli._resolve_codex_exec_target("gpt-5-4")

    assert exec_target == ["-p", "oca-gpt-5-4"]
    assert resolved_model == "gpt-5.4"


def test_resolve_codex_exec_target_falls_back_to_direct_model(tmp_path, monkeypatch):
    missing_config = tmp_path / "missing.toml"

    monkeypatch.setattr(codex_cli, "CODEX_CONFIG_FILE", missing_config)
    codex_cli._load_codex_profiles_cached.cache_clear()

    exec_target, resolved_model = codex_cli._resolve_codex_exec_target("gpt-5-3-codex")

    assert exec_target == ["-m", "gpt-5.3-codex"]
    assert resolved_model == "gpt-5.3-codex"
