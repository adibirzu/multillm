"""Tests for local stats caching."""

from multillm.stats_cache import ttl_cache


def test_ttl_cache_returns_copies_to_prevent_response_mutation():
    calls = {"count": 0}

    @ttl_cache(seconds=60, maxsize=4)
    def load_value():
        calls["count"] += 1
        return {"items": [1]}

    first = load_value()
    first["items"].append(2)
    second = load_value()

    assert calls["count"] == 1
    assert second == {"items": [1]}


def test_ttl_cache_clear_forces_reload():
    calls = {"count": 0}

    @ttl_cache(seconds=60, maxsize=4)
    def load_value():
        calls["count"] += 1
        return {"count": calls["count"]}

    assert load_value()["count"] == 1
    load_value.cache_clear()

    assert load_value()["count"] == 2
