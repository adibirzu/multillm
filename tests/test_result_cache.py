# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for the council/fusion result cache."""

import time

import pytest

from multillm import result_cache


@pytest.fixture(autouse=True)
def _clean():
    result_cache.clear()
    yield
    result_cache.clear()


def test_make_key_is_order_insensitive_for_models():
    a = result_cache.make_key(
        kind="council", prompt="hi", models=["a", "b"], judge=None, max_tokens=100
    )
    b = result_cache.make_key(
        kind="council", prompt="hi", models=["b", "a"], judge=None, max_tokens=100
    )
    assert a == b


def test_make_key_distinguishes_kind_prompt_judge_tokens():
    base = dict(kind="fusion", prompt="hi", models=["a"], judge="j", max_tokens=100)
    k = result_cache.make_key(**base)
    assert k != result_cache.make_key(**{**base, "kind": "council"})
    assert k != result_cache.make_key(**{**base, "prompt": "ho"})
    assert k != result_cache.make_key(**{**base, "judge": "k"})
    assert k != result_cache.make_key(**{**base, "max_tokens": 200})


def test_get_set_roundtrip_and_hit_stats():
    key = "k1"
    assert result_cache.get(key) is None  # miss
    result_cache.set(key, {"answer": 42})
    assert result_cache.get(key) == {"answer": 42}  # hit
    s = result_cache.stats()
    assert s["hits"] == 1 and s["misses"] == 1
    assert s["hitRate"] == 0.5


def test_expired_entry_is_evicted():
    result_cache.set("k2", "v", ttl=0.01)
    time.sleep(0.05)
    assert result_cache.get("k2") is None
    assert result_cache.stats()["entries"] == 0
