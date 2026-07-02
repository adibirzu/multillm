# SPDX-License-Identifier: Apache-2.0

"""Regression checks for the dashboard's direct Fusion workbench."""

from pathlib import Path


def test_dashboard_exposes_fusion_selector_and_endpoint():
    dashboard = (
        Path(__file__).resolve().parents[1] / "multillm" / "static" / "dashboard.html"
    ).read_text()

    assert 'id="councilMode"' in dashboard
    assert 'value="fusion"' in dashboard
    assert "`${API}/api/fusion`" in dashboard
    assert "llm_fusion" in dashboard


def test_dashboard_renders_detected_claude_subscription_without_account_details():
    dashboard = (
        Path(__file__).resolve().parents[1] / "multillm" / "static" / "dashboard.html"
    ).read_text()

    assert "data.subscription" in dashboard
    assert "subscription.plan" in dashboard


def test_dashboard_only_reveals_private_credit_overlay_when_configured():
    dashboard = (
        Path(__file__).resolve().parents[1] / "multillm" / "static" / "dashboard.html"
    ).read_text()

    assert "privateCreditPanel" in dashboard
    assert "data.configured" in dashboard
    assert "mappedCostUSD" in dashboard
