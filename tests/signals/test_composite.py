"""Tests for weighted composite scoring and tier mapping."""
from __future__ import annotations

import pytest

from cte.signals.composite import (
    DEFAULT_WEIGHTS,
    SignalTier,
    compute_composite,
)
from cte.signals.scorer import ScoreDetail


def _score(val: float) -> ScoreDetail:
    return ScoreDetail(score=val, components={}, imputed_count=0)


class TestCompositeFormula:
    def test_all_max(self):
        result = compute_composite(
            momentum=_score(1.0),
            orderflow=_score(1.0),
            liquidation=_score(1.0),
            microstructure=_score(1.0),
            cross_venue=_score(1.0),
            context=_score(1.0),
        )
        assert result.composite_score == pytest.approx(1.0)
        assert result.tier == SignalTier.A

    def test_all_min(self):
        result = compute_composite(
            momentum=_score(0.0),
            orderflow=_score(0.0),
            liquidation=_score(0.0),
            microstructure=_score(0.0),
            cross_venue=_score(0.0),
            context=_score(1.0),
        )
        assert result.composite_score == pytest.approx(0.0)
        assert result.tier == SignalTier.REJECT

    def test_all_neutral(self):
        result = compute_composite(
            momentum=_score(0.5),
            orderflow=_score(0.5),
            liquidation=_score(0.5),
            microstructure=_score(0.5),
            cross_venue=_score(0.5),
            context=_score(1.0),
        )
        assert result.composite_score == pytest.approx(0.5)

    def test_context_dampens(self):
        no_context = compute_composite(
            momentum=_score(0.8),
            orderflow=_score(0.7),
            liquidation=_score(0.6),
            microstructure=_score(0.75),
            cross_venue=_score(0.6),
            context=_score(1.0),
        )
        with_context = compute_composite(
            momentum=_score(0.8),
            orderflow=_score(0.7),
            liquidation=_score(0.6),
            microstructure=_score(0.75),
            cross_venue=_score(0.6),
            context=_score(0.7),
        )
        assert with_context.composite_score < no_context.composite_score
        assert with_context.primary_score == no_context.primary_score
        assert with_context.context_multiplier == pytest.approx(0.7)

    def test_context_zero_blocks(self):
        result = compute_composite(
            momentum=_score(0.9),
            orderflow=_score(0.9),
            liquidation=_score(0.9),
            microstructure=_score(0.9),
            cross_venue=_score(0.9),
            context=_score(0.0),
        )
        assert result.composite_score == 0.0
        assert result.tier == SignalTier.REJECT

    def test_weights_sum_to_one(self):
        total = sum(DEFAULT_WEIGHTS.values())
        assert total == pytest.approx(1.0)


class TestTierMapping:
    def test_tier_a(self):
        result = compute_composite(
            momentum=_score(0.85),
            orderflow=_score(0.80),
            liquidation=_score(0.70),
            microstructure=_score(0.85),
            cross_venue=_score(0.60),
            context=_score(1.0),
        )
        assert result.tier == SignalTier.A
        assert result.composite_score >= 0.72

    def test_tier_b(self):
        result = compute_composite(
            momentum=_score(0.65),
            orderflow=_score(0.60),
            liquidation=_score(0.55),
            microstructure=_score(0.65),
            cross_venue=_score(0.55),
            context=_score(1.0),
        )
        assert result.tier == SignalTier.B

    def test_tier_c(self):
        result = compute_composite(
            momentum=_score(0.50),
            orderflow=_score(0.45),
            liquidation=_score(0.45),
            microstructure=_score(0.50),
            cross_venue=_score(0.45),
            context=_score(1.0),
        )
        assert result.tier == SignalTier.C

    def test_reject(self):
        result = compute_composite(
            momentum=_score(0.35),
            orderflow=_score(0.30),
            liquidation=_score(0.40),
            microstructure=_score(0.35),
            cross_venue=_score(0.30),
            context=_score(1.0),
        )
        assert result.tier == SignalTier.REJECT

    def test_custom_thresholds(self):
        custom = {SignalTier.A: 0.90, SignalTier.B: 0.70, SignalTier.C: 0.50}
        result = compute_composite(
            momentum=_score(0.8),
            orderflow=_score(0.7),
            liquidation=_score(0.6),
            microstructure=_score(0.75),
            cross_venue=_score(0.6),
            context=_score(1.0),
            tier_thresholds=custom,
        )
        assert result.tier == SignalTier.B


class TestCompositeResult:
    def test_result_contains_all_details(self):
        result = compute_composite(
            momentum=ScoreDetail(score=0.8, components={"a": 0.8}, imputed_count=1),
            orderflow=_score(0.7),
            liquidation=_score(0.6),
            microstructure=_score(0.75),
            cross_venue=_score(0.6),
            context=_score(1.0),
        )
        assert "momentum" in result.sub_scores
        assert "momentum" in result.details
        assert result.details["momentum"].imputed_count == 1
        assert result.total_imputed >= 1
        assert result.weights == DEFAULT_WEIGHTS

    def test_custom_weights(self):
        custom_w = {
            "momentum": 0.50,
            "orderflow": 0.20,
            "liquidation": 0.05,
            "microstructure": 0.15,
            "cross_venue": 0.10,
        }
        result = compute_composite(
            momentum=_score(1.0),
            orderflow=_score(0.0),
            liquidation=_score(0.0),
            microstructure=_score(0.0),
            cross_venue=_score(0.0),
            context=_score(1.0),
            weights=custom_w,
        )
        assert result.composite_score == pytest.approx(0.50)
