from multillm import config


def test_langfuse_base_url_is_supported_as_host_alias(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.example")

    assert (
        config._first_env(
            "LANGFUSE_HOST",
            "LANGFUSE_BASE_URL",
            default="http://localhost:3001",
        )
        == "https://langfuse.example"
    )
