"""Tier-specific exit configuration for the 5-layer exit model.

Each signal tier (A/B/C) gets a different patience profile.
Tier A = high-conviction signal → more patience, wider trails.
Tier C = marginal signal → tight leash, prove yourself fast.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cte.core.settings import ExitSettings


@dataclass(frozen=True)
class TierExitProfile:
    """Exit parameters for one signal tier."""

    # ── Layer 1: Hard Risk (same for all tiers — non-negotiable) ──
    hard_stop_pct: float = 0.025           # 2.5% absolute max loss
    max_spread_bps: float = 20.0           # spread blowout threshold
    min_freshness: float = 0.3             # data staleness kill

    # ── Layer 2: Thesis Failure ───────────────────────────────────
    thesis_confirm_count: int = 3          # consecutive negative checks before exit
    thesis_tfi_flip_threshold: float = -0.1  # TFI below this = orderflow flipped
    thesis_momentum_collapse_z: float = -1.0  # returns_z below this = momentum dead
    thesis_liq_shift_threshold: float = 0.3   # liq imbalance above this = longs liquidating

    # ── Layer 3: No Progress ─────────────────────────────────────
    no_progress_timeout_minutes: float = 15.0  # time budget to show progress
    no_progress_min_gain_pct: float = 0.003    # must gain at least 0.3% within budget

    # ── Layer 4: Winner Protection ───────────────────────────────
    winner_activation_r: float = 1.0       # activate at 1R profit
    winner_activation_pct: float = 0.01    # or 1% gain
    winner_trailing_pct: float = 0.02      # trailing stop from high

    # ── Layer 5: Runner Mode ─────────────────────────────────────
    runner_activation_r: float = 2.0       # activate at 2R profit
    runner_activation_pct: float = 0.025   # or 2.5% gain
    runner_trailing_pct: float = 0.035     # wide trailing for runners
    runner_suspend_no_progress: bool = True # runners don't get no-progress killed


# ── Pre-built tier profiles ──────────────────────────────────────

TIER_A_PROFILE = TierExitProfile(
    # Layer 2: More confirmation needed before killing a strong signal
    thesis_confirm_count=3,
    # Layer 3: Patient — strong signals get more time
    no_progress_timeout_minutes=15.0,
    no_progress_min_gain_pct=0.003,
    # Layer 4: Wide trailing — let good entries breathe
    winner_activation_r=1.0,
    winner_activation_pct=0.01,
    winner_trailing_pct=0.020,
    # Layer 5: Runners get very wide leash
    runner_activation_r=2.0,
    runner_activation_pct=0.025,
    runner_trailing_pct=0.035,
)

TIER_B_PROFILE = TierExitProfile(
    thesis_confirm_count=2,
    no_progress_timeout_minutes=8.0,
    no_progress_min_gain_pct=0.003,
    winner_activation_r=1.0,
    winner_activation_pct=0.01,
    winner_trailing_pct=0.015,
    runner_activation_r=2.5,
    runner_activation_pct=0.03,
    runner_trailing_pct=0.030,
)

TIER_C_PROFILE = TierExitProfile(
    thesis_confirm_count=1,
    no_progress_timeout_minutes=4.0,
    no_progress_min_gain_pct=0.003,
    winner_activation_r=1.0,
    winner_activation_pct=0.01,
    winner_trailing_pct=0.010,
    runner_activation_r=3.0,
    runner_activation_pct=0.035,
    runner_trailing_pct=0.025,
)

DEFAULT_PROFILES: dict[str, TierExitProfile] = {
    "A": TIER_A_PROFILE,
    "B": TIER_B_PROFILE,
    "C": TIER_C_PROFILE,
}


def get_profile(tier: str) -> TierExitProfile:
    """Get exit profile for a signal tier. Falls back to Tier C (tightest)."""
    return DEFAULT_PROFILES.get(tier, TIER_C_PROFILE)


def merge_tier_profile_with_exit_defaults(
    profile: TierExitProfile,
    exits: ExitSettings,
) -> TierExitProfile:
    """Align Layer 1 hard stop with ``ExitSettings.stop_loss_pct`` (R-multiple base).

    Keeps tier patience (L2-L5) from the tier profile; only hard risk rail follows
    configured stop distance so ``PaperPosition.stop_distance_usd`` stays consistent.
    """
    return replace(
        profile,
        hard_stop_pct=float(exits.stop_loss_pct),
    )
