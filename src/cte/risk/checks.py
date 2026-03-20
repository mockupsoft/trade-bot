"""Individual risk check implementations.

Each check is a pure function that returns a RiskCheckResult.
The risk manager composes these checks into a pipeline.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from cte.core.events import RiskCheckResult

if TYPE_CHECKING:
    from decimal import Decimal


def check_max_position_size(
    requested_notional: Decimal,
    portfolio_value: Decimal,
    max_position_pct: float,
) -> RiskCheckResult:
    """Ensure single position doesn't exceed portfolio percentage limit."""
    if portfolio_value <= 0:
        return RiskCheckResult(
            check_name="max_position_size",
            passed=False,
            detail="Portfolio value is zero or negative",
        )

    position_pct = float(requested_notional / portfolio_value)
    threshold = max_position_pct
    passed = position_pct <= threshold

    return RiskCheckResult(
        check_name="max_position_size",
        passed=passed,
        value=position_pct,
        threshold=threshold,
        detail=f"Position {position_pct:.2%} of portfolio (limit: {threshold:.2%})",
    )


def check_total_exposure(
    current_exposure: Decimal,
    new_notional: Decimal,
    portfolio_value: Decimal,
    max_exposure_pct: float,
) -> RiskCheckResult:
    """Ensure total exposure across all positions doesn't exceed limit."""
    if portfolio_value <= 0:
        return RiskCheckResult(
            check_name="total_exposure",
            passed=False,
            detail="Portfolio value is zero or negative",
        )

    new_total = float((current_exposure + new_notional) / portfolio_value)
    threshold = max_exposure_pct
    passed = new_total <= threshold

    return RiskCheckResult(
        check_name="total_exposure",
        passed=passed,
        value=new_total,
        threshold=threshold,
        detail=f"Total exposure would be {new_total:.2%} (limit: {threshold:.2%})",
    )


def check_daily_drawdown(
    current_drawdown: float,
    max_drawdown_pct: float,
) -> RiskCheckResult:
    """Ensure daily drawdown hasn't exceeded limit."""
    passed = current_drawdown < max_drawdown_pct

    return RiskCheckResult(
        check_name="daily_drawdown",
        passed=passed,
        value=current_drawdown,
        threshold=max_drawdown_pct,
        detail=f"Daily drawdown at {current_drawdown:.2%} (limit: {max_drawdown_pct:.2%})",
    )


def _pair_correlation(sym_a: str, sym_b: str) -> float:
    """Heuristic pairwise correlation for USDⓈ-M majors (audit-friendly constants)."""
    if sym_a == sym_b:
        return 1.0
    pair = frozenset({sym_a, sym_b})
    if pair == frozenset({"BTCUSDT", "ETHUSDT"}):
        return 0.85
    if "BTCUSDT" in pair:
        return 0.72
    return 0.62


def check_correlation(
    symbol: str,
    open_symbols: list[str],
    max_correlation: float,
) -> RiskCheckResult:
    """Check correlation between new position and existing positions.

    Uses tiered heuristics: BTC-ETH highest, other BTC pairs lower, alt-alt cluster.
    """
    max_corr_found = 0.0
    for open_sym in open_symbols:
        corr = _pair_correlation(symbol, open_sym)
        max_corr_found = max(max_corr_found, corr)

    passed = max_corr_found <= max_correlation

    return RiskCheckResult(
        check_name="correlation",
        passed=passed,
        value=max_corr_found,
        threshold=max_correlation,
        detail=f"Max correlation with open positions: {max_corr_found:.2f} (limit: {max_correlation:.2f})",
    )


def check_emergency_stop(
    current_drawdown: float,
    emergency_threshold: float,
) -> RiskCheckResult:
    """Emergency stop check. If triggered, ALL positions must be closed."""
    passed = current_drawdown < emergency_threshold

    return RiskCheckResult(
        check_name="emergency_stop",
        passed=passed,
        value=current_drawdown,
        threshold=emergency_threshold,
        detail=(
            f"EMERGENCY: Drawdown {current_drawdown:.2%} exceeds {emergency_threshold:.2%}"
            if not passed
            else f"Drawdown {current_drawdown:.2%} within emergency limit"
        ),
    )
