"""Layered Smart Exit Engine — 5 priority layers with tier-specific patience.

Replaces the flat stop/trail/TP exit model with a hierarchical system
where higher-priority layers always override lower ones, and each tier
gets a different patience profile.

Evaluation order (every tick, per position):
  L1 Hard Risk      → immediate exit (safety rails)
  L2 Thesis Failure → feature-based invalidation with confirmation
  L3 No Progress    → time-budget exhaustion
  L4 Winner Prot    → trailing for proved winners
  L5 Runner Mode    → wide trailing for exceptional winners

Every exit produces an ExitDecision with:
- Which layer triggered
- Full evaluation of all layers (for explainability)
- Position mode at exit time
- Analytics hooks (was_profitable_at_exit, exit_gain_pct)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from prometheus_client import Counter, Gauge

from cte.exits.config import get_profile, merge_tier_profile_with_exit_defaults
from cte.exits.layers import (
    ExitContext,
    LayerResult,
    PositionExitState,
    check_layer1_hard_risk,
    check_layer2_thesis_failure,
    check_layer3_no_progress,
    check_layer4_winner_protection,
    check_layer5_runner,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from cte.core.events import StreamingFeatureVector
    from cte.core.settings import ExitSettings
    from cte.execution.position import PaperPosition

logger = structlog.get_logger(__name__)

exit_decisions_total = Counter(
    "cte_exit_decisions_total", "Exit decisions by layer", ["symbol", "layer", "reason"]
)
position_mode_gauge = Gauge(
    "cte_position_mode", "Position mode (0=normal, 1=winner, 2=runner)", ["symbol"]
)
saved_losers_total = Counter("cte_saved_losers_total", "Exits that saved a losing position")
potential_killed_winners = Counter(
    "cte_potential_killed_winners_total",
    "Exits on profitable positions by non-TP layers",
)


@dataclass
class ExitDecision:
    """Full explainability payload for an exit decision."""

    should_exit: bool
    exit_reason: str
    exit_layer: int
    exit_layer_name: str
    exit_detail: str
    all_layers: list[LayerResult]
    position_mode: str
    was_profitable_at_exit: bool
    exit_gain_pct: float
    hold_seconds: int
    current_r: float | None


class LayeredExitEngine:
    """5-layer exit engine with tier-specific patience and full explainability."""

    def __init__(self) -> None:
        self._states: dict[UUID, PositionExitState] = {}

    def _get_state(self, position_id: UUID) -> PositionExitState:
        if position_id not in self._states:
            self._states[position_id] = PositionExitState()
        return self._states[position_id]

    def evaluate(
        self,
        position: PaperPosition,
        current_price: Decimal,
        now: datetime,
        features: StreamingFeatureVector | None = None,
        best_bid: Decimal = Decimal("0"),
        best_ask: Decimal = Decimal("0"),
        *,
        exit_settings: ExitSettings | None = None,
    ) -> ExitDecision:
        """Evaluate all 5 layers for a position. Returns an ExitDecision.

        Deterministic: same inputs → same decision. No wall clock, no randomness.
        """
        profile = get_profile(position.signal_tier)
        if exit_settings is not None:
            profile = merge_tier_profile_with_exit_defaults(profile, exit_settings)
        state = self._get_state(position.position_id)

        position.update_price(current_price)

        ctx = ExitContext(
            position=position,
            current_price=current_price,
            now=now,
            best_bid=best_bid,
            best_ask=best_ask,
            features=features,
        )

        # Evaluate all layers in priority order
        layers: list[LayerResult] = []

        l1 = check_layer1_hard_risk(ctx, profile)
        layers.append(l1)
        if l1.triggered:
            return self._make_decision(ctx, state, l1, layers)

        l2 = check_layer2_thesis_failure(ctx, profile, state)
        layers.append(l2)
        if l2.triggered:
            return self._make_decision(ctx, state, l2, layers)

        # Check L5 before L4 because runner mode overrides winner protection.
        # But runner trailing (L5) is evaluated only if runner qualifies.
        # If not a runner yet, fall through to L4.
        l5 = check_layer5_runner(ctx, profile, state)
        layers.append(l5)
        if l5.triggered:
            return self._make_decision(ctx, state, l5, layers)

        l4 = check_layer4_winner_protection(ctx, profile, state)
        layers.append(l4)
        if l4.triggered:
            return self._make_decision(ctx, state, l4, layers)

        l3 = check_layer3_no_progress(ctx, profile, state)
        layers.append(l3)
        if l3.triggered:
            return self._make_decision(ctx, state, l3, layers)

        # No exit
        mode_val = {"normal": 0, "winner_protection": 1, "runner": 2}
        position_mode_gauge.labels(symbol=position.symbol).set(
            mode_val.get(state.position_mode, 0)
        )

        return ExitDecision(
            should_exit=False,
            exit_reason="",
            exit_layer=0,
            exit_layer_name="",
            exit_detail="",
            all_layers=layers,
            position_mode=state.position_mode,
            was_profitable_at_exit=False,
            exit_gain_pct=ctx.gain_pct,
            hold_seconds=ctx.hold_seconds,
            current_r=ctx.current_r,
        )

    def cleanup(self, position_id: UUID) -> None:
        """Remove per-position state after position is closed."""
        self._states.pop(position_id, None)

    def _make_decision(
        self,
        ctx: ExitContext,
        state: PositionExitState,
        triggered: LayerResult,
        layers: list[LayerResult],
    ) -> ExitDecision:
        was_profitable = ctx.gain_pct > 0

        exit_decisions_total.labels(
            symbol=ctx.position.symbol,
            layer=triggered.layer_name,
            reason=triggered.exit_reason,
        ).inc()

        # Analytics hooks
        if was_profitable and triggered.layer in (2, 3):
            potential_killed_winners.inc()
        if not was_profitable and triggered.layer in (1, 2):
            saved_losers_total.inc()

        return ExitDecision(
            should_exit=True,
            exit_reason=triggered.exit_reason,
            exit_layer=triggered.layer,
            exit_layer_name=triggered.layer_name,
            exit_detail=triggered.detail,
            all_layers=layers,
            position_mode=state.position_mode,
            was_profitable_at_exit=was_profitable,
            exit_gain_pct=ctx.gain_pct,
            hold_seconds=ctx.hold_seconds,
            current_r=ctx.current_r,
        )
