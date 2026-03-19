"""Fill models for paper trading — bid/ask-aware, no mid-price assumptions.

Three fill modes:
1. SpreadCrossing (default): BUY fills at ask + slippage, SELL at bid - slippage.
   This is the realistic baseline — a market order crosses the spread.
2. VWAPDepth: Walk the orderbook levels and fill at volume-weighted price.
   More realistic for larger orders that would eat through multiple levels.
3. WorstCase: Pessimistic fill for stress testing — wider slippage.

All models are deterministic: same inputs → same fill price. No randomness.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class FillMode(str, Enum):
    SPREAD_CROSSING = "spread_crossing"
    VWAP_DEPTH = "vwap_depth"
    WORST_CASE = "worst_case"


@dataclass(frozen=True)
class FillResult:
    """Result of a fill model computation."""

    fill_price: Decimal
    slippage_bps: Decimal
    effective_spread_bps: Decimal
    model_used: FillMode
    detail: str


@dataclass(frozen=True)
class BookLevel:
    """Single orderbook level for VWAP depth fills."""

    price: Decimal
    quantity: Decimal


def compute_fill(
    side: str,
    quantity: Decimal,
    best_bid: Decimal,
    best_ask: Decimal,
    slippage_bps: int = 5,
    mode: FillMode = FillMode.SPREAD_CROSSING,
    book_levels: list[BookLevel] | None = None,
) -> FillResult:
    """Compute a deterministic paper fill price.

    For BUY (LONG entry): you lift the ask. Fill = ask + slippage.
    For SELL (LONG exit): you hit the bid. Fill = bid - slippage.

    This is fundamentally different from the naive mid-price model:
    a BUY at mid ignores that you actually pay the ask, which can be
    significantly worse in wide-spread conditions.
    """
    if best_bid <= 0 or best_ask <= 0:
        raise ValueError(f"Invalid book: bid={best_bid}, ask={best_ask}")

    mid = (best_bid + best_ask) / 2
    raw_spread_bps = (best_ask - best_bid) / mid * 10000

    slip_factor = Decimal(str(slippage_bps)) / Decimal("10000")

    if mode == FillMode.VWAP_DEPTH and book_levels:
        return _vwap_depth_fill(side, quantity, book_levels, slip_factor, raw_spread_bps)

    if mode == FillMode.WORST_CASE:
        return _worst_case_fill(side, best_bid, best_ask, slip_factor, raw_spread_bps)

    return _spread_crossing_fill(side, best_bid, best_ask, slip_factor, raw_spread_bps)


def _spread_crossing_fill(
    side: str,
    best_bid: Decimal,
    best_ask: Decimal,
    slip_factor: Decimal,
    raw_spread_bps: Decimal,
) -> FillResult:
    """Default model: cross the spread + fixed slippage."""
    if side == "buy":
        base = best_ask
        fill = best_ask * (1 + slip_factor)
    else:
        base = best_bid
        fill = best_bid * (1 - slip_factor)

    mid = (best_bid + best_ask) / 2
    effective_spread = abs(fill - mid) / mid * 10000 if mid > 0 else Decimal("0")
    actual_slip = abs(fill - base) / base * 10000 if base > 0 else Decimal("0")

    return FillResult(
        fill_price=fill.quantize(Decimal("0.01")),
        slippage_bps=actual_slip.quantize(Decimal("0.01")),
        effective_spread_bps=effective_spread.quantize(Decimal("0.01")),
        model_used=FillMode.SPREAD_CROSSING,
        detail=f"{'Ask' if side == 'buy' else 'Bid'} + {slip_factor * 10000:.0f} bps slippage",
    )


def _vwap_depth_fill(
    side: str,
    quantity: Decimal,
    levels: list[BookLevel],
    slip_factor: Decimal,
    raw_spread_bps: Decimal,
) -> FillResult:
    """Walk the orderbook and fill at VWAP across touched levels."""
    remaining = quantity
    total_cost = Decimal("0")
    levels_touched = 0

    for level in levels:
        if remaining <= 0:
            break
        fill_at_level = min(remaining, level.quantity)
        total_cost += fill_at_level * level.price
        remaining -= fill_at_level
        levels_touched += 1

    if remaining > 0:
        if levels:
            worst_price = levels[-1].price
            total_cost += remaining * worst_price
        else:
            return FillResult(
                fill_price=Decimal("0"),
                slippage_bps=Decimal("0"),
                effective_spread_bps=Decimal("0"),
                model_used=FillMode.VWAP_DEPTH,
                detail="No orderbook levels available",
            )

    vwap_price = total_cost / quantity
    fill = vwap_price * (1 + slip_factor) if side == "buy" else vwap_price * (1 - slip_factor)

    base = levels[0].price if levels else vwap_price
    actual_slip = abs(fill - base) / base * 10000 if base > 0 else Decimal("0")
    mid_approx = base
    eff_spread = abs(fill - mid_approx) / mid_approx * 10000 if mid_approx > 0 else Decimal("0")

    return FillResult(
        fill_price=fill.quantize(Decimal("0.01")),
        slippage_bps=actual_slip.quantize(Decimal("0.01")),
        effective_spread_bps=eff_spread.quantize(Decimal("0.01")),
        model_used=FillMode.VWAP_DEPTH,
        detail=f"VWAP across {levels_touched} levels + {slip_factor * 10000:.0f} bps",
    )


def _worst_case_fill(
    side: str,
    best_bid: Decimal,
    best_ask: Decimal,
    slip_factor: Decimal,
    raw_spread_bps: Decimal,
) -> FillResult:
    """Pessimistic model: 2x slippage for stress testing."""
    doubled_slip = slip_factor * 2

    if side == "buy":
        fill = best_ask * (1 + doubled_slip)
    else:
        fill = best_bid * (1 - doubled_slip)

    mid = (best_bid + best_ask) / 2
    eff_spread = abs(fill - mid) / mid * 10000 if mid > 0 else Decimal("0")

    base = best_ask if side == "buy" else best_bid
    actual_slip = abs(fill - base) / base * 10000 if base > 0 else Decimal("0")

    return FillResult(
        fill_price=fill.quantize(Decimal("0.01")),
        slippage_bps=actual_slip.quantize(Decimal("0.01")),
        effective_spread_bps=eff_spread.quantize(Decimal("0.01")),
        model_used=FillMode.WORST_CASE,
        detail=f"Worst-case: 2× slippage ({doubled_slip * 10000:.0f} bps)",
    )
