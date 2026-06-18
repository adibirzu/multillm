# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for budget caps, alert states, and enforcement gate."""

from multillm import budgets


def test_no_caps_means_no_status_or_alerts():
    out = budgets.evaluate_budgets(config={}, spent_today=5.0, spent_month=50.0)
    assert out["global"]["daily"] is None
    assert out["global"]["monthly"] is None
    assert out["alerts"] == []
    assert out["blocked"] is False


def test_ok_state_under_warn_threshold():
    out = budgets.evaluate_budgets(
        config={"daily_usd": 10.0, "alert_thresholds": [0.8, 1.0]},
        spent_today=5.0, spent_month=0.0,
    )
    assert out["global"]["daily"]["state"] == "ok"
    assert out["global"]["daily"]["percentUsed"] == 50.0
    assert out["alerts"] == []


def test_warn_state_at_threshold():
    out = budgets.evaluate_budgets(
        config={"daily_usd": 10.0, "alert_thresholds": [0.8, 1.0]},
        spent_today=8.5, spent_month=0.0,
    )
    assert out["global"]["daily"]["state"] == "warn"
    assert len(out["alerts"]) == 1
    assert out["alerts"][0]["window"] == "daily"
    assert out["alerts"][0]["state"] == "warn"


def test_exceeded_state_at_cap():
    out = budgets.evaluate_budgets(
        config={"monthly_usd": 100.0},
        spent_today=0.0, spent_month=120.0,
    )
    assert out["global"]["monthly"]["state"] == "exceeded"
    assert out["global"]["monthly"]["remainingUSD"] == 0.0
    assert any(a["state"] == "exceeded" for a in out["alerts"])


def test_blocked_only_when_enabled_and_exceeded():
    cfg = {"enabled": True, "daily_usd": 10.0}
    out = budgets.evaluate_budgets(config=cfg, spent_today=11.0, spent_month=0.0)
    assert out["blocked"] is True

    cfg_disabled = {"enabled": False, "daily_usd": 10.0}
    out2 = budgets.evaluate_budgets(config=cfg_disabled, spent_today=11.0, spent_month=0.0)
    assert out2["blocked"] is False


def test_per_project_caps_and_spend():
    cfg = {
        "enabled": True,
        "per_project": {"alpha": {"daily_usd": 5.0}, "beta": {"monthly_usd": 50.0}},
    }
    out = budgets.evaluate_budgets(
        config=cfg, spent_today=0.0, spent_month=0.0,
        project_spend={"alpha": {"today": 6.0}, "beta": {"month": 10.0}},
    )
    states = {p["label"]: p for p in out["projects"]}
    assert states["alpha"]["daily"]["state"] == "exceeded"
    assert states["beta"]["monthly"]["state"] == "ok"


def test_check_request_allowed_passes_when_disabled():
    allowed, reason = budgets.check_request_allowed(
        config={"enabled": False, "daily_usd": 1.0}, project="p",
        spent_today=100.0, spent_month=100.0,
    )
    assert allowed is True
    assert reason is None


def test_check_request_blocked_on_global_cap():
    allowed, reason = budgets.check_request_allowed(
        config={"enabled": True, "daily_usd": 10.0}, project="p",
        spent_today=15.0, spent_month=0.0,
    )
    assert allowed is False
    assert "budget" in reason.lower()


def test_check_request_blocked_on_matching_project_only():
    cfg = {"enabled": True, "per_project": {"alpha": {"daily_usd": 5.0}}}
    # Request for project beta is unaffected by alpha's exceeded cap.
    allowed, _ = budgets.check_request_allowed(
        config=cfg, project="beta", spent_today=0.0, spent_month=0.0,
        project_spend={"alpha": {"today": 99.0}},
    )
    assert allowed is True
    # Request for project alpha is blocked.
    blocked, reason = budgets.check_request_allowed(
        config=cfg, project="alpha", spent_today=0.0, spent_month=0.0,
        project_spend={"alpha": {"today": 99.0}},
    )
    assert blocked is False
    assert "alpha" in reason
