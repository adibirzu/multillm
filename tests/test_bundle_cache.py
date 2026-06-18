# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for the stale-while-revalidate dashboard bundle cache."""

import asyncio
import time

import pytest

from multillm import bundle_cache


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    # Isolate the on-disk cache file per test and start from an empty cache.
    monkeypatch.setattr(bundle_cache, "_CACHE_FILE", tmp_path / "bundle.json")
    bundle_cache.cache_clear()
    yield
    bundle_cache.cache_clear()


def _payload(tag):
    return {"unified": {"tag": tag}, "performance": {"elapsedMs": 1.0}}


def test_cold_compute_returns_fresh_with_meta():
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return _payload("v1")

    async def run():
        out = await bundle_cache.get_bundle("k", compute)
        assert out["unified"]["tag"] == "v1"
        assert out["performance"]["cacheState"] == "cold"
        assert out["performance"]["ageSeconds"] >= 0
        assert "computedAt" in out["performance"]
        return out

    asyncio.run(run())
    assert calls["n"] == 1


def test_fresh_entry_is_served_without_recompute():
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return _payload(f"v{calls['n']}")

    async def run():
        await bundle_cache.get_bundle("k", compute)        # cold → compute
        out = await bundle_cache.get_bundle("k", compute)   # fresh → no compute
        assert out["performance"]["cacheState"] == "fresh"

    asyncio.run(run())
    assert calls["n"] == 1


def test_stale_entry_served_immediately_then_revalidated():
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return _payload(f"v{calls['n']}")

    async def run():
        await bundle_cache.get_bundle("k", compute)  # cold → v1
        # Force staleness by ageing the stored entry past the fresh TTL.
        bundle_cache._mem["k"].wall_time = time.time() - (bundle_cache.FRESH_TTL_SECONDS + 5)
        out = await bundle_cache.get_bundle("k", compute)
        assert out["unified"]["tag"] == "v1"  # stale value served immediately
        assert out["performance"]["cacheState"] == "stale-refreshing"
        # Let the background refresh complete, then the next read is fresh v2.
        await asyncio.sleep(0.05)
        out2 = await bundle_cache.get_bundle("k", compute)
        assert out2["unified"]["tag"] == "v2"
        assert out2["performance"]["cacheState"] == "fresh"

    asyncio.run(run())
    assert calls["n"] == 2


def test_force_always_recomputes():
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return _payload(f"v{calls['n']}")

    async def run():
        await bundle_cache.get_bundle("k", compute)
        out = await bundle_cache.get_bundle("k", compute, force=True)
        assert out["performance"]["cacheState"] == "forced"
        assert out["unified"]["tag"] == "v2"

    asyncio.run(run())
    assert calls["n"] == 2


def test_persisted_bundle_survives_warm_load():
    async def compute():
        return _payload("persisted")

    async def run():
        await bundle_cache.get_bundle("k", compute)

    asyncio.run(run())
    # Simulate a restart: drop in-memory state, then warm-load from disk.
    bundle_cache.cache_clear()
    loaded = bundle_cache.warm_load()
    assert loaded == 1
    assert bundle_cache._mem["k"].data["unified"]["tag"] == "persisted"


def test_warm_load_skips_expired_entries(monkeypatch):
    async def compute():
        return _payload("old")

    async def run():
        await bundle_cache.get_bundle("k", compute)

    asyncio.run(run())
    # Age the on-disk entry beyond the max disk age, then warm-load.
    bundle_cache._mem["k"].wall_time = time.time() - (bundle_cache.MAX_DISK_AGE_SECONDS + 10)
    bundle_cache._persist()
    bundle_cache.cache_clear()
    assert bundle_cache.warm_load() == 0


def test_make_key_is_stable_and_distinct():
    a = bundle_cache.make_key(hours=168, project=None, session_limit=50, direct_session_limit=100)
    b = bundle_cache.make_key(hours=168, project=None, session_limit=50, direct_session_limit=100)
    c = bundle_cache.make_key(hours=720, project="x", session_limit=50, direct_session_limit=100)
    assert a == b
    assert a != c


def test_make_key_distinguishes_session_limits():
    a = bundle_cache.make_key(hours=168, project=None, session_limit=50, direct_session_limit=100)
    b = bundle_cache.make_key(hours=168, project=None, session_limit=51, direct_session_limit=100)
    assert a != b
