"""Weighted composite score computation and tier mapping.

The composite formula:
  primary_score = Σ(weight_i x sub_score_i)  for i in {momentum, orderflow, liquidation, micro, cross_venue}
  composite     = primary_score x context_multiplier

Context can only dampen (multiply by [0, 1]), never amplify.
This ensures context flags (whale, news) act as gates, not entry triggers.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cte.signals.scorer import ScoreDetail


class SignalTier(StrEnum):
    """Signal quality tier based on composite score."""
    A = "A"       # Strong — high confidence, favorable conditions
    B = "B"       # Moderate — acceptable, proceed with normal sizing
    C = "C"       # Weak — minimum threshold, reduce sizing
    REJECT = "REJECT"  # Below minimum — do not trade


# Default tier boundaries. Conservative for v1.
DEFAULT_TIER_THRESHOLDS = {
    SignalTier.A: 0.72,
    SignalTier.B: 0.55,
    SignalTier.C: 0.40,
}

# Default sub-score weights. Must sum to 1.0.
DEFAULT_WEIGHTS = {
    "momentum": 0.35,
    "orderflow": 0.25,
    "liquidation": 0.10,
    "microstructure": 0.20,
    "cross_venue": 0.10,
}


@dataclass(frozen=True)
class CompositeResult:
    """Full breakdown of the composite scoring computation."""
    composite_score: float
    primary_score: float
    context_multiplier: float
    tier: SignalTier
    sub_scores: dict[str, float]
    weights: dict[str, float]
    details: dict[str, ScoreDetail]
    total_imputed: int


def compute_composite(
    momentum: ScoreDetail,
    orderflow: ScoreDetail,
    liquidation: ScoreDetail,
    microstructure: ScoreDetail,
    cross_venue: ScoreDetail,
    context: ScoreDetail,
    weights: dict[str, float] | None = None,
    tier_thresholds: dict[SignalTier, float] | None = None,
) -> CompositeResult:
    """Compute the weighted composite score and assign tier.

    Formula:
      primary = w_mom x momentum + w_flow x orderflow + w_liq x liquidation
              + w_micro x microstructure + w_xv x cross_venue
      composite = primary x context_multiplier

    The context multiplier is in [0, 1]. It can only reduce the score.
    """
    w = weights or DEFAULT_WEIGHTS
    thresholds = tier_thresholds or DEFAULT_TIER_THRESHOLDS

    sub_scores = {
        "momentum": momentum.score,
        "orderflow": orderflow.score,
        "liquidation": liquidation.score,
        "microstructure": microstructure.score,
        "cross_venue": cross_venue.score,
    }

    primary = sum(sub_scores[k] * w[k] for k in sub_scores)
    primary = round(primary, 4)

    context_mult = max(0.0, min(1.0, context.score))
    composite = round(primary * context_mult, 4)

    tier = _score_to_tier(composite, thresholds)

    total_imputed = (
        momentum.imputed_count
        + orderflow.imputed_count
        + liquidation.imputed_count
        + microstructure.imputed_count
        + cross_venue.imputed_count
    )

    return CompositeResult(
        composite_score=composite,
        primary_score=primary,
        context_multiplier=round(context_mult, 4),
        tier=tier,
        sub_scores=sub_scores,
        weights=w,
        details={
            "momentum": momentum,
            "orderflow": orderflow,
            "liquidation": liquidation,
            "microstructure": microstructure,
            "cross_venue": cross_venue,
            "context": context,
        },
        total_imputed=total_imputed,
    )


def _score_to_tier(
    score: float,
    thresholds: dict[SignalTier, float],
) -> SignalTier:
    """Map composite score to tier. Checked in descending order."""
    if score >= thresholds[SignalTier.A]:
        return SignalTier.A
    if score >= thresholds[SignalTier.B]:
        return SignalTier.B
    if score >= thresholds[SignalTier.C]:
        return SignalTier.C
    return SignalTier.REJECT
