"""Tests for environment-driven configuration."""

import importlib

import multillm.config as config_module


def test_oca_env_aliases_are_supported(monkeypatch, tmp_path):
    with monkeypatch.context() as m:
        m.delenv("OCA_IDCS_URL", raising=False)
        m.delenv("OCA_CLIENT_ID", raising=False)
        m.setenv("OCA_IDCS_OAUTH_URL", "https://idcs.example.test")
        m.setenv("OCA_IDCS_CLIENT_ID", "client-123")
        m.setenv("OCA_CACHE_DIR", str(tmp_path))

        reloaded = importlib.reload(config_module)

        assert reloaded.OCA_IDCS_URL == "https://idcs.example.test"
        assert reloaded.OCA_CLIENT_ID == "client-123"
        assert reloaded.OCA_TOKEN_CACHE == tmp_path

    importlib.reload(config_module)
