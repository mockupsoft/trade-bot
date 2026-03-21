"""5-layer exit check implementations.

Each layer is a pure function: takes position + context → LayerResult.
No I/O, no side effects, fully deterministic, fully explainable.

Priority order (highest first):
  L1 Hard Risk    → position invalid, data stale, spread blowout, hard stop
  L2 Thesis Fail  → orderflow flips, momentum collapses, liquidation shifts
  L3 No Progress  → expected move fails to materialize within time budget
  L4 Winner Prot  → trailing stop for proved winners
  L5 Runner Mode  → wide trailing for exceptional winners
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from cte.core.events import StreamingFeatureVector
    from cte.execution.position import PaperPosition
    from cte.exits.config import TierExitProfile


@dataclass(frozen=True)
class LayerResult:
    """Result from one exit layer evaluation."""

    layer: int
    layer_name: str
    triggered: bool
    exit_reason: str
    detail: str


@dataclass
class ExitContext:
    """All inputs needed for exit evaluation at a point in time."""

    position: PaperPosition
    current_price: Decimal
    now: datetime
    best_bid: Decimal = Decimal("0")
    best_ask: Decimal = Decimal("0")
    features: StreamingFeatureVector | None = None

    @property
    def gain_pct(self) -> float:
        if self.position.entry_price <= 0:
            return 0.0
        if self.position.direction == "long":
            return float(
                (self.current_price - self.position.entry_price)
                / self.position.entry_price
            )
        return float(
            (self.position.entry_price - self.current_price)
            / self.position.entry_price
        )

    @property
    def loss_pct(self) -> float:
        g = self.gain_pct
        return -g if g < 0 else 0.0

    @property
    def hold_seconds(self) -> int:
        if not self.position.fill_time:
            return 0
        return int((self.now - self.position.fill_time).total_seconds())

    @property
    def hold_minutes(self) -> float:
        return self.hold_seconds / 60.0

    @property
    def current_r(self) -> float | None:
        if self.position.stop_distance_usd <= 0:
            return None
        if self.position.direction == "long":
            unrealized = (self.current_price - self.position.entry_price) * self.position.quantity
        else:
            unrealized = (self.position.entry_price - self.current_price) * self.position.quantity
        return float(unrealized / self.position.stop_distance_usd)


@dataclass
class PositionExitState:
    """Mutable per-position state tracked by the exit engine across ticks."""

    thesis_fail_count: int = 0
    position_mode: str = "normal"  # "normal" | "winner_protection" | "runner"
    mode_transitions: list[tuple[str, str, str]] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════
# LAYER 1: HARD RISK STOP
# ══════════════════════════════════════════════════════════════════

def check_layer1_hard_risk(
    ctx: ExitContext,
    profile: TierExitProfile,
) -> LayerResult:
    """Unconditional safety rails. Always checked first, never overridden.

    Triggers:
    - Absolute loss exceeds hard_stop_pct
    - Data freshness below threshold (stale feed)
    - Spread blowout above threshold
    """
    # Hard stop loss
    if ctx.loss_pct >= profile.hard_stop_pct:
        return LayerResult(
            layer=1, layer_name="hard_risk", triggered=True,
            exit_reason="hard_stop",
            detail=f"Loss {ctx.loss_pct:.2%} ≥ hard stop {profile.hard_stop_pct:.2%}",
        )

    # Stale data
    if ctx.features is not None:
        freshness = ctx.features.freshness.composite
        if freshness < profile.min_freshness:
            return LayerResult(
                layer=1, layer_name="hard_risk", triggered=True,
                exit_reason="stale_data",
                detail=f"Freshness {freshness:.2f} < {profile.min_freshness} threshold",
            )

    # Spread blowout
    if ctx.features is not None:
        spread = ctx.features.tf_60s.spread_bps
        if spread is not None and spread > profile.max_spread_bps:
            return LayerResult(
                layer=1, layer_name="hard_risk", triggered=True,
                exit_reason="spread_blowout",
                detail=f"Spread {spread:.1f} bps > {profile.max_spread_bps} limit",
            )

    return LayerResult(
        layer=1, layer_name="hard_risk", triggered=False,
        exit_reason="", detail="All hard risk checks passed",
    )


# ══════════════════════════════════════════════════════════════════
# LAYER 2: THESIS FAILURE
# ══════════════════════════════════════════════════════════════════

def check_layer2_thesis_failure(
    ctx: ExitContext,
    profile: TierExitProfile,
    state: PositionExitState,
) -> LayerResult:
    """Entry thesis invalidated by live feature data.

    For LONG positions, thesis fails when:
    - Taker flow imbalance flips negative (buyers retreat)
    - Momentum z-score collapses (price movement stalls/reverses)
    - Liquidation imbalance shifts against position (longs being liquidated)

    For SHORT positions, thesis fails when:
    - Taker flow imbalance flips positive (buyers overwhelm)
    - Momentum z-score surges (price movement spikes)
    - Liquidation imbalance shifts against position (shorts being liquidated)

    Uses a confirmation count to avoid whipsaw: the thesis must be
    negative for N consecutive checks before triggering.
    """
    if ctx.features is None:
        return LayerResult(
            layer=2, layer_name="thesis_failure", triggered=False,
            exit_reason="", detail="No features available for thesis check",
        )

    tf = ctx.features.tf_60s
    failures: list[str] = []

    is_long = ctx.position.direction == "long"

    # Orderflow flip
    if tf.taker_flow_imbalance is not None:
        if is_long and tf.taker_flow_imbalance < profile.thesis_tfi_flip_threshold:
            failures.append(
                f"TFI={tf.taker_flow_imbalance:.2f} < {profile.thesis_tfi_flip_threshold}"
            )
        elif not is_long and tf.taker_flow_imbalance > -profile.thesis_tfi_flip_threshold:
            failures.append(
                f"TFI={tf.taker_flow_imbalance:.2f} > {-profile.thesis_tfi_flip_threshold}"
            )

    # Momentum collapse
    if tf.returns_z is not None:
        if is_long and tf.returns_z < profile.thesis_momentum_collapse_z:
            failures.append(
                f"returns_z={tf.returns_z:.2f} < {profile.thesis_momentum_collapse_z}"
            )
        elif not is_long and tf.returns_z > -profile.thesis_momentum_collapse_z:
            failures.append(
                f"returns_z={tf.returns_z:.2f} > {-profile.thesis_momentum_collapse_z}"
            )

    # Liquidation shift
    if tf.liquidation_imbalance is not None:
        if is_long and tf.liquidation_imbalance > profile.thesis_liq_shift_threshold:
            failures.append(
                f"liq_imbalance={tf.liquidation_imbalance:.2f} > {profile.thesis_liq_shift_threshold}"
            )
        elif not is_long and tf.liquidation_imbalance < -profile.thesis_liq_shift_threshold:
            failures.append(
                f"liq_imbalance={tf.liquidation_imbalance:.2f} < {-profile.thesis_liq_shift_threshold}"
            )

    if failures:
        state.thesis_fail_count += 1
    else:
        state.thesis_fail_count = 0

    if state.thesis_fail_count >= profile.thesis_confirm_count:
        return LayerResult(
            layer=2, layer_name="thesis_failure", triggered=True,
            exit_reason="thesis_failure",
            detail=(
                f"Thesis invalid for {state.thesis_fail_count} consecutive checks: "
                + "; ".join(failures)
            ),
        )

    return LayerResult(
        layer=2, layer_name="thesis_failure", triggered=False,
        exit_reason="",
        detail=(
            f"Thesis fail count: {state.thesis_fail_count}/{profile.thesis_confirm_count}"
            + (f" — current: {'; '.join(failures)}" if failures else "")
        ),
    )


# ══════════════════════════════════════════════════════════════════
# LAYER 3: NO PROGRESS
# ══════════════════════════════════════════════════════════════════

def check_layer3_no_progress(
    ctx: ExitContext,
    profile: TierExitProfile,
    state: PositionExitState,
) -> LayerResult:
    """Expected move fails to materialize within tier-based time budget.

    Tier A: 15 min patience. Tier B: 8 min. Tier C: 4 min.
    If the position hasn't gained at least min_gain_pct within the budget,
    the entry thesis is considered failed-by-omission.

    Suspended for positions in runner mode (runners are allowed to consolidate).
    """
    if state.position_mode == "runner" and profile.runner_suspend_no_progress:
        return LayerResult(
            layer=3, layer_name="no_progress", triggered=False,
            exit_reason="",
            detail="Suspended — position in runner mode",
        )

    if ctx.hold_minutes < profile.no_progress_timeout_minutes:
        return LayerResult(
            layer=3, layer_name="no_progress", triggered=False,
            exit_reason="",
            detail=f"Within budget: {ctx.hold_minutes:.1f}/{profile.no_progress_timeout_minutes:.0f} min",
        )

    if ctx.gain_pct < profile.no_progress_min_gain_pct:
        return LayerResult(
            layer=3, layer_name="no_progress", triggered=True,
            exit_reason="no_progress",
            detail=(
                f"Gain {ctx.gain_pct:.3%} < {profile.no_progress_min_gain_pct:.3%} "
                f"after {ctx.hold_minutes:.1f} min "
                f"(budget: {profile.no_progress_timeout_minutes:.0f} min)"
            ),
        )

    return LayerResult(
        layer=3, layer_name="no_progress", triggered=False,
        exit_reason="",
        detail=f"Progress OK: {ctx.gain_pct:.3%} gain in {ctx.hold_minutes:.1f} min",
    )


# ══════════════════════════════════════════════════════════════════
# LAYER 4: WINNER PROTECTION
# ══════════════════════════════════════════════════════════════════

def check_layer4_winner_protection(
    ctx: ExitContext,
    profile: TierExitProfile,
    state: PositionExitState,
) -> LayerResult:
    """Trailing stop for positions that have proved themselves profitable.

    Activation: position must reach winner_activation_r (R-multiple)
    OR winner_activation_pct (gain %). Once activated, a trailing stop
    from the position's highest price is enforced.

    Winner protection is LESS aggressive than runner mode trailing.
    If runner mode is active, this layer defers to Layer 5.
    """
    if state.position_mode == "runner":
        return LayerResult(
            layer=4, layer_name="winner_protection", triggered=False,
            exit_reason="",
            detail="Deferred to runner mode (Layer 5)",
        )

    r = ctx.current_r
    is_winner = (
        ctx.gain_pct >= profile.winner_activation_pct
        or (r is not None and r >= profile.winner_activation_r)
    )

    if not is_winner:
        return LayerResult(
            layer=4, layer_name="winner_protection", triggered=False,
            exit_reason="",
            detail=f"Not yet a winner: gain={ctx.gain_pct:.3%}, R={r or 0:.2f}",
        )

    # Activate winner protection mode
    if state.position_mode == "normal":
        state.position_mode = "winner_protection"
        state.mode_transitions.append(("normal", "winner_protection", ctx.now.isoformat()))

    # Check trailing stop from high (or low for shorts)
    pos = ctx.position

    is_long = pos.direction == "long"
    drawdown_from_best = 0.0
    best_price_str = ""

    if is_long and pos.highest_price > 0:
        drawdown_from_best = float((pos.highest_price - ctx.current_price) / pos.highest_price)
        best_price_str = f"high {pos.highest_price}"
    elif not is_long and pos.lowest_price > 0:
        drawdown_from_best = float((ctx.current_price - pos.lowest_price) / pos.lowest_price)
        best_price_str = f"low {pos.lowest_price}"

    if best_price_str and drawdown_from_best >= profile.winner_trailing_pct:
        return LayerResult(
            layer=4, layer_name="winner_protection", triggered=True,
            exit_reason="winner_trailing",
            detail=(
                f"Drawdown {drawdown_from_best:.2%} from {best_price_str} "
                f"≥ trailing {profile.winner_trailing_pct:.2%}"
            ),
        )

    return LayerResult(
        layer=4, layer_name="winner_protection", triggered=False,
        exit_reason="",
        detail=f"Winner protected: trailing {profile.winner_trailing_pct:.2%} from best price",
    )


# ══════════════════════════════════════════════════════════════════
# LAYER 5: RUNNER MODE
# ══════════════════════════════════════════════════════════════════

def check_layer5_runner(
    ctx: ExitContext,
    profile: TierExitProfile,
    state: PositionExitState,
) -> LayerResult:
    """Wide trailing stop for exceptional winners.

    Activation: position must reach runner_activation_r OR runner_activation_pct.
    Runner mode replaces winner protection with a wider trailing stop,
    and suspends the no-progress timer (runners are allowed to consolidate).

    The logic is: if a trade reaches 2R+, the entry thesis was strongly
    correct. Don't kill it with a tight trailing stop. Let it run.

    Additional confirmation: if features are available, check that
    momentum hasn't completely died (returns_z > -1). A runner with
    collapsed momentum gets downgraded back to winner protection.
    """
    r = ctx.current_r
    is_runner = (
        ctx.gain_pct >= profile.runner_activation_pct
        or (r is not None and r >= profile.runner_activation_r)
    )

    if not is_runner:
        return LayerResult(
            layer=5, layer_name="runner", triggered=False,
            exit_reason="",
            detail=f"Not a runner: gain={ctx.gain_pct:.3%}, R={r or 0:.2f}",
        )

    # Check for runner downgrade: momentum completely dead
    if ctx.features is not None:
        ret_z = ctx.features.tf_60s.returns_z
        if ret_z is not None:
            is_long = ctx.position.direction == "long"
            collapsed = (is_long and ret_z < -1.5) or (not is_long and ret_z > 1.5)
            if collapsed:
                if state.position_mode == "runner":
                    state.position_mode = "winner_protection"
                    state.mode_transitions.append(
                        ("runner", "winner_protection", ctx.now.isoformat())
                    )
                return LayerResult(
                    layer=5, layer_name="runner", triggered=False,
                    exit_reason="",
                    detail=f"Runner downgraded: returns_z={ret_z:.2f} (momentum collapsed)",
                )

    # Activate runner mode
    if state.position_mode != "runner":
        old = state.position_mode
        state.position_mode = "runner"
        state.mode_transitions.append((old, "runner", ctx.now.isoformat()))

    # Wide trailing stop
    pos = ctx.position

    is_long = pos.direction == "long"
    drawdown_from_best = 0.0
    best_price_str = ""

    if is_long and pos.highest_price > 0:
        drawdown_from_best = float((pos.highest_price - ctx.current_price) / pos.highest_price)
        best_price_str = f"high {pos.highest_price}"
    elif not is_long and pos.lowest_price > 0:
        drawdown_from_best = float((ctx.current_price - pos.lowest_price) / pos.lowest_price)
        best_price_str = f"low {pos.lowest_price}"

    if best_price_str and drawdown_from_best >= profile.runner_trailing_pct:
        return LayerResult(
            layer=5, layer_name="runner", triggered=True,
            exit_reason="runner_trailing",
            detail=(
                f"Runner drawdown {drawdown_from_best:.2%} from {best_price_str} "
                f"≥ runner trailing {profile.runner_trailing_pct:.2%}"
            ),
        )

    return LayerResult(
        layer=5, layer_name="runner", triggered=False,
        exit_reason="",
        detail=f"Runner active: trailing {profile.runner_trailing_pct:.2%} from best price",
    )
