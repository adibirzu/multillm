# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for multi-user / multi-account team usage monitoring."""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multillm import team_usage as tu
from multillm import team_usage_api


TEST_DAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _clean_team_table():
    """Isolate each test — the temp DB is shared across the session."""
    with tu._get_db() as conn:
        conn.execute("DELETE FROM team_usage")
    yield
    with tu._get_db() as conn:
        conn.execute("DELETE FROM team_usage")


def _rec(**kw):
    base = dict(
        tenant_id="adi",
        backend="claude",
        account="adi@x.com",
        model="claude-opus-4-6",
        day=TEST_DAY,
        input_tokens=1000,
        output_tokens=500,
        cache_tokens=100,
        requests=5,
        cost_usd=0.12,
        source_host="devvm",
    )
    base.update(kw)
    return tu.TeamUsageRecord(**base)


# ── Storage / aggregation ────────────────────────────────────────────────────


def test_record_and_total():
    assert tu.record_team_usage([_rec()]) == 1
    data = tu.get_team_usage(hours=720)
    assert data["totals"]["users"] == 1
    assert data["totals"]["output_tokens"] == 500


def test_upsert_is_idempotent_snapshot_replace():
    """Re-sending the same daily snapshot must not double-count."""
    tu.record_team_usage([_rec(output_tokens=500)])
    tu.record_team_usage([_rec(output_tokens=900)])  # later snapshot for same day
    data = tu.get_team_usage(hours=720)
    # Replaced, not summed.
    assert data["totals"]["output_tokens"] == 900


def test_distinct_accounts_and_backends_split():
    tu.record_team_usage(
        [
            _rec(tenant_id="adi", backend="claude", account="adi@x.com", model="m1"),
            _rec(tenant_id="adi", backend="codex", account="adi@oa", model="gpt-5.4"),
            _rec(
                tenant_id="royce", backend="claude", account="royce@y.com", model="m2"
            ),
        ]
    )
    data = tu.get_team_usage(hours=720)
    assert data["totals"]["users"] == 2
    assert data["totals"]["accounts"] == 3
    backends = {b["bucket"] for b in data["by_backend"]}
    assert backends == {"claude", "codex"}


def test_tenant_filter():
    tu.record_team_usage(
        [
            _rec(tenant_id="adi"),
            _rec(tenant_id="royce", account="royce@y.com"),
        ]
    )
    data = tu.get_team_usage(hours=720, tenant="royce")
    assert data["totals"]["users"] == 1
    assert all(u["bucket"] == "royce" for u in data["by_user"])


# ── Validation ───────────────────────────────────────────────────────────────


def test_record_from_dict_rejects_bad_backend():
    with pytest.raises(ValueError):
        tu.record_from_dict(
            {"tenant_id": "adi", "backend": "bogus", "day": "2026-05-30"}
        )


def test_record_from_dict_rejects_bad_day():
    with pytest.raises(ValueError):
        tu.record_from_dict(
            {"tenant_id": "adi", "backend": "claude", "day": "30-05-2026"}
        )


def test_record_from_dict_requires_tenant():
    with pytest.raises(ValueError):
        tu.record_from_dict({"backend": "claude", "day": "2026-05-30"})


# ── Collector normalization ──────────────────────────────────────────────────


def test_records_from_stats_prefers_daily():
    stats = {
        "daily": [
            {"date": "2026-05-29", "input_tokens": 10, "output_tokens": 20},
            {"date": "2026-05-30", "input_tokens": 30, "output_tokens": 40},
        ],
        "total": {"input_tokens": 40, "output_tokens": 60},
    }
    recs = tu._records_from_stats(
        stats, tenant="adi", backend="claude", account="a", host="h", today="2026-05-30"
    )
    assert len(recs) == 2
    assert {r.day for r in recs} == {"2026-05-29", "2026-05-30"}


def test_records_from_stats_falls_back_to_total():
    stats = {"total": {"input": 100, "output": 50}, "cost_estimate": 0.3}
    recs = tu._records_from_stats(
        stats, tenant="adi", backend="codex", account="", host="h", today="2026-05-30"
    )
    assert len(recs) == 1
    assert recs[0].input_tokens == 100
    assert recs[0].output_tokens == 50
    assert recs[0].day == "2026-05-30"


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app = FastAPI()
    team_usage_api.register(app)
    return TestClient(app)


def test_ingest_endpoint_writes_and_skips_invalid(client):
    batch = {
        "tenant_id": "adi",
        "source_host": "devvm",
        "records": [
            {
                "backend": "claude",
                "model": "m",
                "day": TEST_DAY,
                "input_tokens": 100,
                "output_tokens": 50,
            },
            {"backend": "nope", "model": "x", "day": "2026-05-30"},
        ],
    }
    r = client.post("/api/usage/ingest", json=batch)
    assert r.status_code == 200
    body = r.json()
    assert body["written"] == 1
    assert body["skipped"] == 1


def test_ingest_inherits_batch_tenant(client):
    batch = {
        "tenant_id": "royce",
        "source_host": "devvm",
        "records": [
            {
                "backend": "gemini",
                "model": "gemini-2.5-pro",
                "day": TEST_DAY,
                "output_tokens": 10,
            }
        ],
    }
    client.post("/api/usage/ingest", json=batch)
    data = client.get("/api/team-usage?hours=720").json()
    assert data["by_user"][0]["bucket"] == "royce"


def test_team_usage_budget_flag(client, monkeypatch):
    monkeypatch.setenv("MULTILLM_USER_BUDGETS", "adi=0.10")
    client.post(
        "/api/usage/ingest",
        json={
            "tenant_id": "adi",
            "source_host": "devvm",
            "records": [
                {
                    "backend": "claude",
                    "model": "m",
                    "day": TEST_DAY,
                    "cost_usd": 0.5,
                }
            ],
        },
    )
    data = client.get("/api/team-usage?hours=720").json()
    user = next(u for u in data["by_user"] if u["bucket"] == "adi")
    assert user["over_budget"] is True
    assert user["daily_budget_usd"] == 0.10
