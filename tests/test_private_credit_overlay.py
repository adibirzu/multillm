# SPDX-License-Identifier: Apache-2.0

"""Tests for the local-only credit-to-cost overlay."""

import json

from multillm import private_credit_overlay as overlay


def _write_overlay(path, payload, mode=0o600):
    path.write_text(json.dumps(payload))
    path.chmod(mode)


def test_private_credit_overlay_maps_credits_to_cost(tmp_path, monkeypatch):
    config = tmp_path / "private-credit.json"
    _write_overlay(
        config,
        {
            "enabled": True,
            "period": "2026-06",
            "credits_used": 120.5,
            "credit_to_usd": 0.25,
            "required_email_domain": "oracle.com",
        },
    )
    monkeypatch.setattr(overlay, "PRIVATE_CREDIT_OVERLAY_FILE", config)

    result = overlay.get_private_credit_overlay()

    assert result == {
        "configured": True,
        "period": "2026-06",
        "creditsUsed": 120.5,
        "creditToUsd": 0.25,
        "mappedCostUSD": 30.125,
        "requiredEmailDomain": "oracle.com",
    }


def test_private_credit_overlay_does_not_guess_a_cost_rate(tmp_path, monkeypatch):
    config = tmp_path / "private-credit.json"
    _write_overlay(
        config,
        {"enabled": True, "period": "2026-06", "credits_used": 120.5},
    )
    monkeypatch.setattr(overlay, "PRIVATE_CREDIT_OVERLAY_FILE", config)

    result = overlay.get_private_credit_overlay()

    assert result["configured"] is True
    assert result["creditsUsed"] == 120.5
    assert result["mappedCostUSD"] is None
    assert result["creditToUsd"] is None


def test_private_credit_overlay_rejects_files_visible_to_other_users(tmp_path, monkeypatch):
    config = tmp_path / "private-credit.json"
    _write_overlay(
        config,
        {"enabled": True, "period": "2026-06", "credits_used": 120.5},
        mode=0o644,
    )
    monkeypatch.setattr(overlay, "PRIVATE_CREDIT_OVERLAY_FILE", config)

    assert overlay.get_private_credit_overlay() == {"configured": False}


def test_save_private_credit_overlay_writes_mode_600_file(tmp_path, monkeypatch):
    config = tmp_path / "private-credit.json"
    monkeypatch.setattr(overlay, "PRIVATE_CREDIT_OVERLAY_FILE", config)

    result = overlay.save_private_credit_overlay(
        {
            "enabled": True,
            "period": "2026-06",
            "credits_used": 120.5,
            "credit_to_usd": 0.25,
            "required_email_domain": "oracle.com",
        }
    )

    assert result["mappedCostUSD"] == 30.125
    assert config.stat().st_mode & 0o777 == 0o600
    assert json.loads(config.read_text())["credits_used"] == 120.5
    assert result["requiredEmailDomain"] == "oracle.com"
