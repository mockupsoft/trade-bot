"""Rule-based signal strategies for v1.

Each strategy is a pure function that takes a FeatureVector and returns
a signal action with confidence and reason, or None if no signal.
"""
from __future__ import annotations

from dataclasses import dataclass

from cte.core.events import FeatureVector, SignalAction, SignalReason


@dataclass
class StrategyResult:
    action: SignalAction
    confidence: float
    reason: SignalReason


def ema_crossover_strategy(
    current: FeatureVector,
    prev_ema_fast: float | None = None,
    prev_ema_slow: float | None = None,
) -> StrategyResult | None:
    """EMA crossover with RSI confirmation.

    Triggers OPEN_LONG when:
    - EMA fast crosses above EMA slow (bullish crossover)
    - RSI is between 30 and 70 (not overbought, recovering from oversold preferred)
    - Volume is present
    """
    if current.ema_fast is None or current.ema_slow is None or current.rsi is None:
        return None

    if prev_ema_fast is None or prev_ema_slow is None:
        return None

    was_below = prev_ema_fast < prev_ema_slow
    is_above = current.ema_fast > current.ema_slow

    if not (was_below and is_above):
        return None

    if current.rsi > 70:
        return None

    confidence = 0.5
    factors: list[str] = []

    if current.rsi < 40:
        confidence += 0.15
        factors.append(f"rsi_oversold_recovery ({current.rsi:.1f})")
    elif current.rsi < 55:
        confidence += 0.05
        factors.append(f"rsi_neutral ({current.rsi:.1f})")

    if current.orderbook_imbalance is not None and current.orderbook_imbalance > 0.2:
        confidence += 0.1
        factors.append(f"orderbook_bid_imbalance ({current.orderbook_imbalance:.2f})")

    if current.price_change_pct_1h is not None and current.price_change_pct_1h > 0:
        confidence += 0.05
        factors.append(f"positive_momentum_1h ({current.price_change_pct_1h:.4f})")

    if current.bid_ask_spread_bps is not None and current.bid_ask_spread_bps < 5:
        confidence += 0.05
        factors.append(f"tight_spread ({current.bid_ask_spread_bps:.1f} bps)")

    confidence = min(confidence, 0.95)

    reason = SignalReason(
        primary_trigger="ema_crossover_bullish",
        supporting_factors=factors,
        context_flags={},
        human_readable=(
            f"EMA {12}/{26} bullish crossover. RSI at {current.rsi:.1f}. "
            f"{'Strong' if confidence > 0.7 else 'Moderate'} conviction."
        ),
    )

    return StrategyResult(
        action=SignalAction.OPEN_LONG,
        confidence=confidence,
        reason=reason,
    )


def rsi_reversal_strategy(current: FeatureVector) -> StrategyResult | None:
    """RSI mean-reversion strategy.

    Triggers OPEN_LONG when:
    - RSI drops below 30 (oversold)
    - EMA fast > EMA slow (still in uptrend context)
    """
    if current.rsi is None or current.ema_fast is None or current.ema_slow is None:
        return None

    if current.rsi >= 30:
        return None

    if current.ema_fast <= current.ema_slow:
        return None

    confidence = 0.55
    factors: list[str] = []

    if current.rsi < 20:
        confidence += 0.1
        factors.append(f"deeply_oversold ({current.rsi:.1f})")

    if current.orderbook_imbalance is not None and current.orderbook_imbalance > 0.3:
        confidence += 0.1
        factors.append(f"strong_bid_support ({current.orderbook_imbalance:.2f})")

    if current.volume_24h is not None:
        factors.append(f"volume_context ({current.volume_24h:.0f})")

    confidence = min(confidence, 0.90)

    reason = SignalReason(
        primary_trigger="rsi_oversold_reversal",
        supporting_factors=factors,
        context_flags={},
        human_readable=(
            f"RSI at {current.rsi:.1f} (oversold) with bullish EMA alignment. "
            f"Expecting mean reversion bounce."
        ),
    )

    return StrategyResult(
        action=SignalAction.OPEN_LONG,
        confidence=confidence,
        reason=reason,
    )
