"""Position reconciliation between local state and venue state.

Periodically queries the venue for its view of positions and compares
with our local tracking. Discrepancies are logged and can trigger
emergency actions.

Types of discrepancy:
- PHANTOM_LOCAL: We think we have a position but venue doesn't
- PHANTOM_VENUE: Venue shows a position we don't track
- QUANTITY_MISMATCH: Both agree position exists but quantities differ
- SIDE_MISMATCH: Position exists on both sides but direction differs
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog
from prometheus_client import Counter

if TYPE_CHECKING:
    from cte.execution.adapter import ExecutionAdapter, VenuePosition

logger = structlog.get_logger(__name__)

recon_runs_total = Counter("cte_recon_runs_total", "Reconciliation runs")
recon_discrepancies = Counter(
    "cte_recon_discrepancies_total", "Reconciliation discrepancies", ["type"]
)


class DiscrepancyType(StrEnum):
    PHANTOM_LOCAL = "phantom_local"     # local has position, venue doesn't
    PHANTOM_VENUE = "phantom_venue"     # venue has position, local doesn't
    QUANTITY_MISMATCH = "quantity_mismatch"
    SIDE_MISMATCH = "side_mismatch"


@dataclass(frozen=True)
class Discrepancy:
    """A single reconciliation discrepancy."""
    symbol: str
    dtype: DiscrepancyType
    local_qty: Decimal
    venue_qty: Decimal
    local_side: str
    venue_side: str
    detail: str


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""
    is_clean: bool
    discrepancies: list[Discrepancy]
    local_position_count: int
    venue_position_count: int


@dataclass
class LocalPositionView:
    """Simplified view of a local position for reconciliation."""
    symbol: str
    side: str        # "long" | "short"
    quantity: Decimal


class PositionReconciler:
    """Compares local positions against venue positions."""

    def __init__(self, tolerance_pct: float = 0.01) -> None:
        self._tolerance_pct = tolerance_pct

    async def reconcile(
        self,
        adapter: ExecutionAdapter,
        local_positions: list[LocalPositionView],
    ) -> ReconciliationResult:
        """Run a reconciliation check.

        Queries the venue for current positions and compares
        with the provided local position view.
        """
        recon_runs_total.inc()

        venue_positions = await adapter.get_positions()

        local_by_symbol: dict[str, LocalPositionView] = {
            lp.symbol: lp for lp in local_positions
        }
        venue_by_symbol: dict[str, VenuePosition] = {
            vp.symbol: vp for vp in venue_positions if vp.quantity > 0
        }

        discrepancies: list[Discrepancy] = []

        all_symbols = set(local_by_symbol.keys()) | set(venue_by_symbol.keys())

        for symbol in all_symbols:
            local = local_by_symbol.get(symbol)
            venue = venue_by_symbol.get(symbol)

            if local and not venue:
                discrepancies.append(Discrepancy(
                    symbol=symbol,
                    dtype=DiscrepancyType.PHANTOM_LOCAL,
                    local_qty=local.quantity,
                    venue_qty=Decimal("0"),
                    local_side=local.side,
                    venue_side="",
                    detail=f"Local has {local.side} {local.quantity}, venue has nothing",
                ))
                recon_discrepancies.labels(type="phantom_local").inc()

            elif venue and not local:
                discrepancies.append(Discrepancy(
                    symbol=symbol,
                    dtype=DiscrepancyType.PHANTOM_VENUE,
                    local_qty=Decimal("0"),
                    venue_qty=venue.quantity,
                    local_side="",
                    venue_side=venue.side,
                    detail=f"Venue has {venue.side} {venue.quantity}, local has nothing",
                ))
                recon_discrepancies.labels(type="phantom_venue").inc()

            elif local and venue:
                if local.side != venue.side:
                    discrepancies.append(Discrepancy(
                        symbol=symbol,
                        dtype=DiscrepancyType.SIDE_MISMATCH,
                        local_qty=local.quantity,
                        venue_qty=venue.quantity,
                        local_side=local.side,
                        venue_side=venue.side,
                        detail=f"Side mismatch: local={local.side}, venue={venue.side}",
                    ))
                    recon_discrepancies.labels(type="side_mismatch").inc()

                elif not self._quantities_match(local.quantity, venue.quantity):
                    discrepancies.append(Discrepancy(
                        symbol=symbol,
                        dtype=DiscrepancyType.QUANTITY_MISMATCH,
                        local_qty=local.quantity,
                        venue_qty=venue.quantity,
                        local_side=local.side,
                        venue_side=venue.side,
                        detail=(
                            f"Qty mismatch: local={local.quantity}, "
                            f"venue={venue.quantity}"
                        ),
                    ))
                    recon_discrepancies.labels(type="quantity_mismatch").inc()

        for d in discrepancies:
            await logger.awarning(
                "reconciliation_discrepancy",
                symbol=d.symbol,
                type=d.dtype.value,
                detail=d.detail,
            )

        return ReconciliationResult(
            is_clean=len(discrepancies) == 0,
            discrepancies=discrepancies,
            local_position_count=len(local_positions),
            venue_position_count=len(venue_positions),
        )

    def _quantities_match(self, local: Decimal, venue: Decimal) -> bool:
        if local == venue:
            return True
        if local == 0:
            return venue == 0
        diff_pct = abs(float((local - venue) / local))
        return diff_pct <= self._tolerance_pct
