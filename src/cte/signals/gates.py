"""Hard gate checks that can reject a signal before scoring.

Each gate is a binary pass/fail check. If ANY gate fails, the signal
is rejected immediately — no composite score is computed.

Gates exist to prevent trading under structurally dangerous conditions
that no amount of positive momentum can compensate for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from cte.core.events import StreamingFeatureVector

WarmupGateMode = Literal["strict", "dashboard_staged"]


@dataclass(frozen=True)
class GateResult:
    """Result of a single gate check."""
    name: str
    passed: bool
    value: float | None
    threshold: float
    reason: str


@dataclass(frozen=True)
class GateVerdict:
    """Aggregate result of all gate checks."""
    all_passed: bool
    results: list[GateResult]
    rejection_reasons: list[str]


def check_all_gates(
    vector: StreamingFeatureVector,
    min_freshness: float = 0.5,
    max_spread_bps: float = 15.0,
    max_divergence_bps: float = 50.0,
    min_feasibility: float = 0.3,
    warmup_gate_mode: WarmupGateMode = "strict",
) -> GateVerdict:
    """Run all hard gates against a feature vector.

    Returns a GateVerdict with pass/fail for each gate.
    If any gate fails, all_passed is False.

    ``warmup_gate_mode``:
    - ``strict`` (default): require ``data_quality.warmup_complete`` (full).
    - ``dashboard_staged``: allow ``warmup_early_eligible`` OR ``warmup_complete``.
    """
    results = [
        _check_stale_feed(vector, min_freshness),
        _check_max_spread(vector, max_spread_bps),
        _check_max_divergence(vector, max_divergence_bps),
        _check_execution_feasibility(vector, min_feasibility),
        _check_warmup(vector, warmup_gate_mode),
    ]

    rejections = [r.reason for r in results if not r.passed]

    return GateVerdict(
        all_passed=len(rejections) == 0,
        results=results,
        rejection_reasons=rejections,
    )


def _check_stale_feed(
    vector: StreamingFeatureVector,
    min_freshness: float,
) -> GateResult:
    """Reject if data freshness is below threshold.

    Stale data means our features are computed from old prices.
    Trading on stale features is gambling.
    """
    value = vector.freshness.composite
    passed = value >= min_freshness

    return GateResult(
        name="stale_feed",
        passed=passed,
        value=round(value, 4),
        threshold=min_freshness,
        reason="" if passed else f"Data freshness {value:.2f} < {min_freshness} threshold",
    )


def _check_max_spread(
    vector: StreamingFeatureVector,
    max_spread_bps: float,
) -> GateResult:
    """Reject if bid-ask spread exceeds maximum.

    Wide spreads mean poor fills. Even a perfect signal loses money
    if the spread eats the expected move.
    """
    spread = vector.tf_60s.spread_bps
    if spread is None:
        return GateResult(
            name="max_spread",
            passed=True,
            value=None,
            threshold=max_spread_bps,
            reason="",
        )

    passed = spread <= max_spread_bps

    return GateResult(
        name="max_spread",
        passed=passed,
        value=round(spread, 4),
        threshold=max_spread_bps,
        reason="" if passed else f"Spread {spread:.1f} bps > {max_spread_bps} limit",
    )


def _check_max_divergence(
    vector: StreamingFeatureVector,
    max_divergence_bps: float,
) -> GateResult:
    """Reject if Binance-Bybit divergence is extreme.

    Extreme divergence (>50 bps) means venues are pricing differently.
    This could be a data issue, one venue lagging, or a flash crash
    on one venue. Not safe to trade.
    """
    div = vector.tf_60s.venue_divergence_bps
    if div is None:
        return GateResult(
            name="max_divergence",
            passed=True,
            value=None,
            threshold=max_divergence_bps,
            reason="",
        )

    abs_div = abs(div)
    passed = abs_div <= max_divergence_bps

    return GateResult(
        name="max_divergence",
        passed=passed,
        value=round(abs_div, 4),
        threshold=max_divergence_bps,
        reason="" if passed else f"|Divergence| {abs_div:.1f} bps > {max_divergence_bps} limit",
    )


def _check_execution_feasibility(
    vector: StreamingFeatureVector,
    min_feasibility: float,
) -> GateResult:
    """Reject if execution feasibility is too low.

    Feasibility combines spread, depth, and freshness.
    Below threshold means we can't get a reasonable fill.
    """
    value = vector.execution_feasibility
    if value is None:
        return GateResult(
            name="execution_feasibility",
            passed=False,
            value=None,
            threshold=min_feasibility,
            reason="Execution feasibility unavailable (likely missing spread/depth data)",
        )

    passed = value >= min_feasibility

    return GateResult(
        name="execution_feasibility",
        passed=passed,
        value=round(value, 4),
        threshold=min_feasibility,
        reason="" if passed else f"Feasibility {value:.2f} < {min_feasibility} threshold",
    )


def _check_warmup(
    vector: StreamingFeatureVector,
    mode: WarmupGateMode = "strict",
) -> GateResult:
    """Reject if the feature engine hasn't completed warmup.

    During warmup, windows are not full and features are unreliable.
    """
    dq = vector.data_quality
    if mode == "strict":
        passed = bool(dq.warmup_complete)
        reason = "" if passed else "Feature engine warmup not complete"
    else:
        passed = bool(dq.warmup_early_eligible) or bool(dq.warmup_complete)
        reason = (
            ""
            if passed
            else "Dashboard warmup: need early or full mid history (staged gate)"
        )

    return GateResult(
        name="warmup",
        passed=passed,
        value=1.0 if passed else 0.0,
        threshold=1.0,
        reason=reason,
    )
