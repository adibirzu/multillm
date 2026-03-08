"""Tests for adaptive routing scoring — calculate_backend_score and weighted selection."""

import random
import pytest

from multillm.resilience import (
    calculate_backend_score,
    CircuitBreaker,
    SCORE_HEALTH_WEIGHT,
    SCORE_LATENCY_WEIGHT,
    SCORE_ERROR_WEIGHT,
    HEALTH_STATUS_SCORES,
    MAX_LATENCY_MS,
)


# ── Helper ──────────────────────────────────────────────────────────────────

def _score(**overrides) -> dict:
    """Call calculate_backend_score with sane defaults, overridden by kwargs."""
    defaults = dict(
        health_status="healthy",
        breaker_available=True,
        breaker_state="closed",
        breaker_failures=0,
        breaker_threshold=5,
        recent_latency_ms=None,
    )
    defaults.update(overrides)
    return calculate_backend_score(**defaults)


# ── Decomposition structure ─────────────────────────────────────────────────


class TestScoreStructure:
    def test_returns_dict_with_required_keys(self):
        result = _score()
        for key in (
            "score", "health_score", "latency_score", "error_score",
            "health_status", "breaker_state", "half_open_penalty",
            "eliminated", "elimination_reason",
        ):
            assert key in result, f"Missing key: {key}"

    def test_score_between_zero_and_one(self):
        result = _score()
        assert 0.0 <= result["score"] <= 1.0

    def test_component_scores_between_zero_and_one(self):
        result = _score()
        for key in ("health_score", "latency_score", "error_score"):
            assert 0.0 <= result[key] <= 1.0, f"{key} out of range: {result[key]}"


# ── Healthy backends ────────────────────────────────────────────────────────


class TestHealthyBackend:
    def test_perfect_health_no_latency_data(self):
        result = _score(health_status="healthy", breaker_failures=0)
        assert result["health_score"] == 1.0
        assert result["latency_score"] == 0.5  # no data → 0.5
        assert result["error_score"] == 1.0
        assert not result["eliminated"]
        assert result["score"] > 0.5

    def test_perfect_health_low_latency(self):
        result = _score(recent_latency_ms=100.0)
        assert result["latency_score"] > 0.9
        assert result["score"] > 0.9

    def test_perfect_health_high_latency(self):
        result = _score(recent_latency_ms=4500.0)
        assert result["latency_score"] < 0.2
        assert result["score"] < _score(recent_latency_ms=100.0)["score"]

    def test_latency_at_max_scores_zero(self):
        result = _score(recent_latency_ms=MAX_LATENCY_MS)
        assert result["latency_score"] == 0.0

    def test_latency_above_max_clamped(self):
        result = _score(recent_latency_ms=10000.0)
        assert result["latency_score"] == 0.0


# ── Degraded backends ──────────────────────────────────────────────────────


class TestDegradedBackend:
    def test_degraded_health_lower_score(self):
        healthy = _score(health_status="healthy")
        degraded = _score(health_status="degraded")
        assert degraded["health_score"] == 0.6
        assert degraded["score"] < healthy["score"]

    def test_degraded_still_viable(self):
        result = _score(health_status="degraded")
        assert not result["eliminated"]
        assert result["score"] > 0.0


# ── Unknown health ─────────────────────────────────────────────────────────


class TestUnknownHealth:
    def test_unknown_health_uses_default(self):
        result = _score(health_status="unknown")
        assert result["health_score"] == 0.75
        assert not result["eliminated"]

    def test_unknown_status_mapped(self):
        result = _score(health_status="some_new_status")
        assert result["health_score"] == 0.5  # fallback


# ── Eliminated backends ────────────────────────────────────────────────────


class TestElimination:
    def test_unhealthy_eliminated(self):
        result = _score(health_status="unhealthy")
        assert result["eliminated"]
        assert result["score"] == 0.0
        assert "unhealthy" in result["elimination_reason"]

    def test_unconfigured_eliminated(self):
        result = _score(health_status="unconfigured")
        assert result["eliminated"]
        assert result["score"] == 0.0
        assert "unconfigured" in result["elimination_reason"]

    def test_breaker_open_eliminated(self):
        result = _score(breaker_available=False, breaker_state="open")
        assert result["eliminated"]
        assert result["score"] == 0.0
        assert "circuit_breaker" in result["elimination_reason"]


# ── Half-open penalty ──────────────────────────────────────────────────────


class TestHalfOpen:
    def test_half_open_applies_penalty(self):
        closed = _score(breaker_state="closed")
        half_open = _score(breaker_state="half-open")
        assert half_open["half_open_penalty"]
        assert not closed["half_open_penalty"]
        assert half_open["score"] < closed["score"]
        # Should be roughly half
        assert abs(half_open["score"] - closed["score"] * 0.5) < 0.01


# ── Error score ────────────────────────────────────────────────────────────


class TestErrorScore:
    def test_no_failures_full_error_score(self):
        result = _score(breaker_failures=0, breaker_threshold=5)
        assert result["error_score"] == 1.0

    def test_some_failures(self):
        result = _score(breaker_failures=2, breaker_threshold=5)
        assert result["error_score"] == pytest.approx(0.6)

    def test_at_threshold(self):
        result = _score(breaker_failures=5, breaker_threshold=5)
        assert result["error_score"] == 0.0

    def test_failures_above_threshold_clamped(self):
        result = _score(breaker_failures=10, breaker_threshold=5)
        assert result["error_score"] == 0.0

    def test_zero_threshold_no_division_error(self):
        result = _score(breaker_failures=0, breaker_threshold=0)
        assert result["error_score"] == 1.0


# ── Custom weights ─────────────────────────────────────────────────────────


class TestCustomWeights:
    def test_health_only(self):
        result = calculate_backend_score(
            health_status="healthy",
            breaker_available=True,
            breaker_state="closed",
            breaker_failures=0,
            breaker_threshold=5,
            recent_latency_ms=5000.0,  # worst latency
            health_weight=1.0,
            latency_weight=0.0,
            error_weight=0.0,
        )
        assert result["score"] == 1.0

    def test_latency_only(self):
        result = calculate_backend_score(
            health_status="healthy",
            breaker_available=True,
            breaker_state="closed",
            breaker_failures=5,
            breaker_threshold=5,
            recent_latency_ms=0.0,  # perfect latency
            health_weight=0.0,
            latency_weight=1.0,
            error_weight=0.0,
        )
        assert result["score"] == 1.0

    def test_error_only(self):
        result = calculate_backend_score(
            health_status="degraded",
            breaker_available=True,
            breaker_state="closed",
            breaker_failures=0,
            breaker_threshold=5,
            recent_latency_ms=5000.0,
            health_weight=0.0,
            latency_weight=0.0,
            error_weight=1.0,
        )
        assert result["score"] == 1.0

    def test_default_weights_sum_to_one(self):
        total = SCORE_HEALTH_WEIGHT + SCORE_LATENCY_WEIGHT + SCORE_ERROR_WEIGHT
        assert total == pytest.approx(1.0)


# ── Weighted random selection (via gateway) ─────────────────────────────────


class TestWeightedRandomSelect:
    """Test the _weighted_random_select helper from gateway."""

    @pytest.fixture(autouse=True)
    def import_select(self):
        from multillm.gateway import _weighted_random_select, _SCORE_MIN_VIABLE
        self._select = _weighted_random_select
        self._min = _SCORE_MIN_VIABLE

    def _make_candidates(self, scores):
        """Build candidate tuples: (alias, route, score_info)."""
        return [
            (f"backend_{i}/model", {"backend": f"backend_{i}"}, {"score": s})
            for i, s in enumerate(scores)
        ]

    def test_single_candidate_returned(self):
        cands = self._make_candidates([0.8])
        alias, route, info = self._select(cands, "backend_0/model", {"backend": "backend_0"})
        assert info["score"] == 0.8

    def test_filters_below_min_viable(self):
        cands = self._make_candidates([0.05, 0.9])
        # Run many times — the low-score candidate should never win
        results = set()
        for _ in range(50):
            _, _, info = self._select(cands, "x", {})
            results.add(info["score"])
        assert 0.05 not in results

    def test_all_below_min_falls_back(self):
        cands = self._make_candidates([0.05, 0.08])
        # Should still return something (falls back to all candidates)
        _, _, info = self._select(cands, "x", {})
        assert info["score"] in (0.05, 0.08)

    def test_weighted_favors_higher_score(self):
        """With a large score gap, the better candidate should win most runs."""
        random.seed(42)
        cands = self._make_candidates([0.2, 0.95])
        wins = {0.2: 0, 0.95: 0}
        for _ in range(200):
            _, _, info = self._select(cands, "x", {})
            wins[info["score"]] += 1
        assert wins[0.95] > wins[0.2]

    def test_close_scores_show_distribution(self):
        """Near-identical scores should produce a more even distribution."""
        random.seed(42)
        cands = self._make_candidates([0.80, 0.82])
        wins = {0.80: 0, 0.82: 0}
        for _ in range(200):
            _, _, info = self._select(cands, "x", {})
            wins[info["score"]] += 1
        # Both should get a meaningful share
        assert wins[0.80] > 20
        assert wins[0.82] > 20
