"""Sub-score computations for the weighted signal scoring model.

Each function is pure: takes feature values, returns a score in [0, 1].
No I/O, no side effects, no randomness. Fully deterministic.

Score semantics (LONG-only in v1):
  0.0 = maximally bearish / unfavorable
  0.5 = neutral / insufficient data
  1.0 = maximally bullish / favorable

When a feature value is None (insufficient data), the score falls back
to 0.5 (neutral). The reason object tracks which inputs were imputed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from cte.core.events import StreamingFeatureVector, TimeframeFeatures


# ── Mapping Helpers ──────────────────────────────────────────────

def z_to_score(z: float | None, neutral: float = 0.5) -> float:
    """Map a z-score to [0, 1] via clamped linear transform.

    z ∈ [-3, +3] → [0, 1] linearly.
    z = 0 → 0.5 (neutral), z = +3 → 1.0, z = -3 → 0.0.

    Clamped linear is chosen over sigmoid because:
    - No transcendental functions (faster, easier to audit)
    - Monotonic and easy to reason about
    - z=2 maps to 0.833, z=1 to 0.667 — sensible scaling
    """
    if z is None:
        return neutral
    return max(0.0, min(1.0, (z + 3.0) / 6.0))


def ratio_to_score(value: float | None, neutral: float = 0.5) -> float:
    """Map a [-1, +1] ratio to [0, 1]. Positive = bullish for longs."""
    if value is None:
        return neutral
    return max(0.0, min(1.0, (value + 1.0) / 2.0))


def inverse_ratio_to_score(value: float | None, neutral: float = 0.5) -> float:
    """Map a [-1, +1] ratio to [0, 1] with inversion. Negative input = high score.

    Used for liquidation imbalance where negative (short squeeze) is bullish.
    """
    if value is None:
        return neutral
    return max(0.0, min(1.0, (-value + 1.0) / 2.0))


# ── Timeframe Weights ────────────────────────────────────────────
# 60s gets the most weight: long enough to filter tick noise,
# short enough to be responsive. 10s is noisy, 5m is lagging.

DEFAULT_TF_WEIGHTS: dict[int, float] = {
    10: 0.15,
    30: 0.25,
    60: 0.35,
    300: 0.25,
}


@dataclass(frozen=True)
class ScoreDetail:
    """Breakdown of how a sub-score was computed."""
    score: float
    components: dict[str, float] = field(default_factory=dict)
    imputed_count: int = 0
    description: str = ""


# ── 1. Momentum Score ────────────────────────────────────────────

def compute_momentum_score(
    vector: StreamingFeatureVector,
    tf_weights: dict[int, float] | None = None,
) -> ScoreDetail:
    """Weighted multi-timeframe momentum from returns_z and momentum_z.

    For each timeframe:
      combined_z = 0.6 × returns_z + 0.4 × momentum_z
      tf_score = z_to_score(combined_z)

    Final = Σ(tf_score × tf_weight)

    Returns_z captures price movement; momentum_z captures flow pressure.
    A move with both rising price AND strong buy flow is more convincing.
    """
    weights = tf_weights or DEFAULT_TF_WEIGHTS
    timeframes = _get_timeframes(vector)
    total = 0.0
    components: dict[str, float] = {}
    imputed = 0

    for ws, tf in timeframes.items():
        w = weights.get(ws, 0.0)
        ret_z = tf.returns_z
        mom_z = tf.momentum_z

        if ret_z is None:
            imputed += 1
        if mom_z is None:
            imputed += 1

        combined = 0.6 * (ret_z if ret_z is not None else 0.0) + \
                   0.4 * (mom_z if mom_z is not None else 0.0)
        tf_score = z_to_score(combined)
        total += tf_score * w
        components[f"tf_{ws}s"] = round(tf_score, 4)

    return ScoreDetail(
        score=round(total, 4),
        components=components,
        imputed_count=imputed,
        description="Multi-timeframe momentum (returns_z × 0.6 + momentum_z × 0.4)",
    )


# ── 2. Orderflow Score ──────────────────────────────────────────

def compute_orderflow_score(
    vector: StreamingFeatureVector,
    tf_weights: dict[int, float] | None = None,
) -> ScoreDetail:
    """Weighted multi-timeframe taker flow imbalance.

    TFI ∈ [-1, +1] mapped to [0, 1].
    Positive TFI (buy-dominant) → high score (bullish for longs).
    """
    weights = tf_weights or DEFAULT_TF_WEIGHTS
    timeframes = _get_timeframes(vector)
    total = 0.0
    components: dict[str, float] = {}
    imputed = 0

    for ws, tf in timeframes.items():
        w = weights.get(ws, 0.0)
        tfi = tf.taker_flow_imbalance

        if tfi is None:
            imputed += 1

        tf_score = ratio_to_score(tfi)
        total += tf_score * w
        components[f"tf_{ws}s"] = round(tf_score, 4)

    return ScoreDetail(
        score=round(total, 4),
        components=components,
        imputed_count=imputed,
        description="Taker flow imbalance across timeframes",
    )


# ── 3. Liquidation Score ────────────────────────────────────────

def compute_liquidation_score(
    vector: StreamingFeatureVector,
) -> ScoreDetail:
    """Liquidation imbalance — inverted because short squeezes are bullish.

    LI > 0 → more longs liquidated → bearish → LOW score
    LI < 0 → more shorts liquidated → bullish → HIGH score
    LI = None → no liquidations → neutral (0.5)

    Uses 60s and 5m timeframes only (liquidations are sparse,
    shorter windows are usually empty).
    """
    li_60 = vector.tf_60s.liquidation_imbalance
    li_5m = vector.tf_5m.liquidation_imbalance
    imputed = 0

    if li_60 is None and li_5m is None:
        return ScoreDetail(
            score=0.5,
            components={"tf_60s": 0.5, "tf_300s": 0.5},
            imputed_count=2,
            description="No liquidation data — neutral",
        )

    s60 = inverse_ratio_to_score(li_60)
    s5m = inverse_ratio_to_score(li_5m)
    if li_60 is None:
        imputed += 1
    if li_5m is None:
        imputed += 1

    score = 0.4 * s60 + 0.6 * s5m

    return ScoreDetail(
        score=round(score, 4),
        components={"tf_60s": round(s60, 4), "tf_300s": round(s5m, 4)},
        imputed_count=imputed,
        description="Liquidation imbalance (inverted: short squeeze = bullish)",
    )


# ── 4. Microstructure Score ──────────────────────────────────────

def compute_microstructure_score(
    vector: StreamingFeatureVector,
    max_spread_bps: float = 15.0,
) -> ScoreDetail:
    """Market microstructure quality from spread, widening, and OB imbalance.

    Components (using 60s timeframe as reference):
    - spread_tightness: 1 - spread_bps / max_spread. Tight = good.
    - widening_health: penalty when spread widens vs average.
    - ob_favorability: OBI mapped to [0,1]. Bid-heavy = good for longs.

    Weighted: 0.4 × spread + 0.3 × widening + 0.3 × OB
    """
    tf = vector.tf_60s
    imputed = 0
    components: dict[str, float] = {}

    # Spread tightness
    if tf.spread_bps is not None:
        spread_score = max(0.0, 1.0 - tf.spread_bps / max_spread_bps)
    else:
        spread_score = 0.5
        imputed += 1
    components["spread_tightness"] = round(spread_score, 4)

    # Widening health
    if tf.spread_widening is not None:
        if tf.spread_widening <= 1.0:
            widening_score = 1.0
        else:
            widening_score = max(0.0, 1.0 - (tf.spread_widening - 1.0) / 3.0)
    else:
        widening_score = 0.5
        imputed += 1
    components["widening_health"] = round(widening_score, 4)

    # OB favorability
    ob_score = ratio_to_score(tf.ob_imbalance)
    if tf.ob_imbalance is None:
        imputed += 1
    components["ob_favorability"] = round(ob_score, 4)

    score = 0.4 * spread_score + 0.3 * widening_score + 0.3 * ob_score

    return ScoreDetail(
        score=round(score, 4),
        components=components,
        imputed_count=imputed,
        description="Microstructure: spread tightness, widening, OB imbalance",
    )


# ── 5. Cross-Venue Score ────────────────────────────────────────

def compute_cross_venue_score(
    vector: StreamingFeatureVector,
) -> ScoreDetail:
    """Binance-vs-Bybit divergence signal.

    Positive divergence (Binance mid > Bybit mid) is mildly bullish:
    the primary, higher-volume venue is pricing higher.

    Maps divergence_bps to [0, 1]:
      -20 bps → 0.0,  0 bps → 0.5,  +20 bps → 1.0
    """
    div = vector.tf_60s.venue_divergence_bps
    imputed = 0

    if div is None:
        return ScoreDetail(
            score=0.5,
            components={"divergence_bps": 0.0},
            imputed_count=1,
            description="No cross-venue data — neutral",
        )

    score = max(0.0, min(1.0, 0.5 + div / 40.0))

    return ScoreDetail(
        score=round(score, 4),
        components={"divergence_bps": round(div, 4)},
        imputed_count=imputed,
        description=f"Venue divergence: {div:+.1f} bps (Binance vs Bybit)",
    )


# ── 6. Context Score (Modifier) ──────────────────────────────────

def compute_context_score(
    vector: StreamingFeatureVector,
) -> ScoreDetail:
    """Context modifier — can only dampen, never amplify.

    Range: [0, 1] where 1.0 = no modification.

    Penalties:
    - whale_risk_flag active: × 0.7
    - urgent_news_flag active: × 0.7
    - warmup not complete: × 0.0 (hard block)

    Both flags active: 0.7 × 0.7 = 0.49

    This is NOT a primary entry source. It gates/dampens the primary scores.
    """
    multiplier = 1.0
    components: dict[str, float] = {}

    if not vector.data_quality.warmup_complete:
        components["warmup"] = 0.0
        return ScoreDetail(
            score=0.0,
            components=components,
            imputed_count=0,
            description="Warmup incomplete — hard block",
        )
    components["warmup"] = 1.0

    if vector.whale_risk_flag:
        multiplier *= 0.7
        components["whale_penalty"] = 0.7
    else:
        components["whale_penalty"] = 1.0

    if vector.urgent_news_flag:
        multiplier *= 0.7
        components["news_penalty"] = 0.7
    else:
        components["news_penalty"] = 1.0

    components["final_multiplier"] = round(multiplier, 4)

    return ScoreDetail(
        score=round(multiplier, 4),
        components=components,
        imputed_count=0,
        description="Context modifier (whale/news gating)",
    )


# ── Helpers ──────────────────────────────────────────────────────

def _get_timeframes(vector: StreamingFeatureVector) -> dict[int, TimeframeFeatures]:
    return {
        10: vector.tf_10s,
        30: vector.tf_30s,
        60: vector.tf_60s,
        300: vector.tf_5m,
    }
