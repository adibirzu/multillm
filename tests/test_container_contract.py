from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_image_includes_optional_langfuse_observability_dependency():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'pip install --prefix=/install ".[langfuse]"' in dockerfile
    assert "USER multillm" in dockerfile


def test_compose_publishes_gateway_on_loopback_by_default():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:8080:8080"' in compose
