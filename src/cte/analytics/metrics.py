"""Pure metric calculation functions for trade analytics.

Every function takes a list of trade records and returns a metric value.
No I/O, no side effects, fully deterministic, fully testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal


@dataclass(frozen=True)
class CompletedTrade:
    """Minimal trade record for metric computation."""

    symbol: str
    venue: str
    tier: str
    epoch: str
    source: str  # "seed" | "paper_simulated" | "demo_exchange"
    pnl: Decimal
    exit_reason: str
    exit_layer: int
    hold_seconds: int
    r_multiple: float | None
    entry_latency_ms: int
    modeled_slippage_bps: float
    mfe_pct: float
    mae_pct: float
    was_profitable_at_exit: bool
    position_mode: str  # normal | winner_protection | runner


def win_rate(trades: list[CompletedTrade]) -> float:
    if not trades:
        return 0.0
    return sum(1 for t in trades if t.pnl > 0) / len(trades)


def expectancy(trades: list[CompletedTrade]) -> float:
    """Average PnL per trade. Positive = profitable system."""
    if not trades:
        return 0.0
    return float(sum(t.pnl for t in trades) / len(trades))


def profit_factor(trades: list[CompletedTrade]) -> float | None:
    """Gross profit / gross loss. >1 = profitable. None if no losses."""
    gross_profit = sum(float(t.pnl) for t in trades if t.pnl > 0)
    gross_loss = abs(sum(float(t.pnl) for t in trades if t.pnl < 0))
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def avg_win(trades: list[CompletedTrade]) -> float:
    winners = [float(t.pnl) for t in trades if t.pnl > 0]
    return sum(winners) / len(winners) if winners else 0.0


def avg_loss(trades: list[CompletedTrade]) -> float:
    losers = [float(t.pnl) for t in trades if t.pnl <= 0]
    return sum(losers) / len(losers) if losers else 0.0


def max_drawdown_pct(
    trades: list[CompletedTrade], initial_capital: float = 10000.0
) -> float:
    """Peak-to-trough equity drawdown as a percentage."""
    if not trades:
        return 0.0

    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0

    for t in trades:
        equity += float(t.pnl)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return max_dd


def pnl_by_dimension(
    trades: list[CompletedTrade], dimension: str
) -> dict[str, float]:
    """Group PnL by a trade dimension (symbol, venue, tier, epoch, exit_reason)."""
    result: dict[str, float] = {}
    for t in trades:
        key = getattr(t, dimension, "unknown")
        result[key] = result.get(key, 0.0) + float(t.pnl)
    return result


def count_by_dimension(
    trades: list[CompletedTrade], dimension: str
) -> dict[str, int]:
    result: dict[str, int] = {}
    for t in trades:
        key = getattr(t, dimension, "unknown")
        result[key] = result.get(key, 0) + 1
    return result


def saved_losers_count(trades: list[CompletedTrade]) -> int:
    """Exits by L1/L2 where position was losing → saved from deeper loss."""
    return sum(
        1 for t in trades
        if t.exit_layer in (1, 2) and not t.was_profitable_at_exit
    )


def killed_winners_count(trades: list[CompletedTrade]) -> int:
    """Exits by L2/L3 where position was profitable → potentially killed a winner."""
    return sum(
        1 for t in trades
        if t.exit_layer in (2, 3) and t.was_profitable_at_exit
    )


def no_progress_regret(trades: list[CompletedTrade]) -> dict:
    """Analyze no-progress exits: how many had positive MFE (they moved, just not fast enough)?"""
    no_prog = [t for t in trades if t.exit_reason == "no_progress"]
    if not no_prog:
        return {"count": 0, "had_positive_mfe": 0, "avg_mfe_pct": 0.0}

    had_mfe = sum(1 for t in no_prog if t.mfe_pct > 0.003)
    avg_mfe = sum(t.mfe_pct for t in no_prog) / len(no_prog) if no_prog else 0.0

    return {
        "count": len(no_prog),
        "had_positive_mfe": had_mfe,
        "avg_mfe_pct": round(avg_mfe, 6),
        "regret_rate": round(had_mfe / len(no_prog), 4) if no_prog else 0.0,
    }


def runner_mode_outcomes(trades: list[CompletedTrade]) -> dict:
    """Analyze positions that entered runner mode."""
    runners = [t for t in trades if t.position_mode == "runner"]
    if not runners:
        return {"count": 0, "avg_r": 0.0, "avg_pnl": 0.0, "win_rate": 0.0}

    r_vals = [t.r_multiple for t in runners if t.r_multiple is not None]
    return {
        "count": len(runners),
        "avg_r": round(sum(r_vals) / len(r_vals), 4) if r_vals else 0.0,
        "avg_pnl": round(float(sum(t.pnl for t in runners)) / len(runners), 2),
        "win_rate": round(sum(1 for t in runners if t.pnl > 0) / len(runners), 4),
    }


def avg_signal_to_fill_latency_ms(trades: list[CompletedTrade]) -> float:
    latencies = [t.entry_latency_ms for t in trades if t.entry_latency_ms > 0]
    return sum(latencies) / len(latencies) if latencies else 0.0


def avg_slippage_bps(trades: list[CompletedTrade]) -> float:
    slips = [t.modeled_slippage_bps for t in trades]
    return sum(slips) / len(slips) if slips else 0.0


def slippage_drift(
    paper_trades: list[CompletedTrade],
    live_trades: list[CompletedTrade],
) -> dict:
    """Compare paper slippage vs demo/live slippage to detect model drift."""
    paper_avg = avg_slippage_bps(paper_trades)
    live_avg = avg_slippage_bps(live_trades)
    drift = live_avg - paper_avg

    return {
        "paper_avg_bps": round(paper_avg, 2),
        "live_avg_bps": round(live_avg, 2),
        "drift_bps": round(drift, 2),
        "drift_pct": round(drift / paper_avg * 100, 2) if paper_avg > 0 else 0.0,
    }


def compute_all_metrics(
    trades: list[CompletedTrade], initial_capital: float = 10000.0
) -> dict:
    """Compute all metrics for a set of trades. Dashboard-ready dict."""
    return {
        "trade_count": len(trades),
        "win_rate": round(win_rate(trades), 4),
        "expectancy": round(expectancy(trades), 2),
        "profit_factor": profit_factor(trades),
        "avg_win": round(avg_win(trades), 2),
        "avg_loss": round(avg_loss(trades), 2),
        "max_drawdown_pct": round(max_drawdown_pct(trades, initial_capital), 4),
        "total_pnl": round(float(sum(t.pnl for t in trades)), 2),
        "pnl_by_symbol": pnl_by_dimension(trades, "symbol"),
        "pnl_by_venue": pnl_by_dimension(trades, "venue"),
        "pnl_by_tier": pnl_by_dimension(trades, "tier"),
        "pnl_by_exit_reason": pnl_by_dimension(trades, "exit_reason"),
        "count_by_exit_reason": count_by_dimension(trades, "exit_reason"),
        "saved_losers": saved_losers_count(trades),
        "killed_winners": killed_winners_count(trades),
        "no_progress_regret": no_progress_regret(trades),
        "runner_outcomes": runner_mode_outcomes(trades),
        "avg_latency_ms": round(avg_signal_to_fill_latency_ms(trades), 1),
        "avg_slippage_bps": round(avg_slippage_bps(trades), 2),
        "avg_hold_seconds": (
            round(sum(t.hold_seconds for t in trades) / len(trades), 1)
            if trades else 0.0
        ),
        "count_by_source": count_by_dimension(trades, "source"),
    }
