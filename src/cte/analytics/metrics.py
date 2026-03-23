"""Pure metric calculation functions for trade analytics.

Every function takes a list of trade records and returns a metric value.
No I/O, no side effects, fully deterministic, fully testable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class CompletedTrade:
    """Minimal trade record for metric computation."""

    symbol: str
    venue: str
    tier: str
    epoch: str
    direction: str
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
    entry_price: Decimal = Decimal("0")
    exit_price: Decimal = Decimal("0")
    warmup_phase: str = "none"  # none | early | full — dashboard staged warmup
    execution_channel: str | None = None  # e.g. bybit_linear_demo | binance_usdm_testnet
    entry_reason_summary: str = ""
    entry_time: str | None = None
    exit_time: str | None = None
    entry_notional_usd: Decimal = Decimal("0")
    entry_composite_score: float = 0.0
    entry_primary_score: float = 0.0
    entry_context_multiplier: float = 1.0
    entry_strongest_sub_score: str = ""
    entry_strongest_sub_score_value: float = 0.0


def trades_for_promotion_evidence(trades: list[CompletedTrade]) -> list[CompletedTrade]:
    """Trades eligible for readiness / promotion stats (excludes staged early warmup).

    Legacy rows with ``warmup_phase=none`` remain included so historical data still counts.
    """
    return [t for t in trades if t.warmup_phase != "early"]


def compute_phase_metrics_slice(
    trades: list[CompletedTrade], initial_capital: float
) -> dict[str, Any]:
    """Aggregate metrics for one trade subset (e.g. single warmup phase)."""
    if not trades:
        return {
            "trade_count": 0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "avg_slippage_bps": 0.0,
            "max_drawdown_pct": 0.0,
        }

    net = sum(float(t.pnl) for t in trades)
    gross_profit = sum(float(t.pnl) for t in trades if t.pnl > 0)
    gross_loss = abs(sum(float(t.pnl) for t in trades if t.pnl < 0))
    gross_pnl = gross_profit + gross_loss

    return {
        "trade_count": len(trades),
        "win_rate": round(win_rate(trades), 4),
        "expectancy": round(expectancy(trades), 4),
        "gross_pnl": round(gross_pnl, 2),
        "net_pnl": round(net, 2),
        "avg_slippage_bps": round(avg_slippage_bps(trades), 2),
        "max_drawdown_pct": round(max_drawdown_pct(trades, initial_capital), 4),
    }


def compute_warmup_phase_breakdown(
    trades: list[CompletedTrade], initial_capital: float
) -> dict[str, Any]:
    """Side-by-side metrics for early / full / none, plus promotion-evidence-only slice."""
    by_early = [t for t in trades if t.warmup_phase == "early"]
    by_full = [t for t in trades if t.warmup_phase == "full"]
    by_none = [t for t in trades if t.warmup_phase == "none"]
    promo = trades_for_promotion_evidence(trades)
    total_net = sum(float(t.pnl) for t in trades)
    portfolio_dd = max_drawdown_pct(trades, initial_capital) if trades else 0.0

    def _share(phase_trades: list[CompletedTrade]) -> float:
        if not phase_trades or total_net == 0:
            return 0.0
        return round(sum(float(t.pnl) for t in phase_trades) / total_net * 100.0, 2)

    def _dd_contrib(phase_trades: list[CompletedTrade]) -> float:
        """Phase standalone max DD as a fraction of portfolio max DD (0-100)."""
        if not phase_trades or portfolio_dd <= 0:
            return 0.0
        ph_dd = max_drawdown_pct(phase_trades, initial_capital)
        return round(min(100.0, (ph_dd / portfolio_dd) * 100.0), 2)

    early_m = compute_phase_metrics_slice(by_early, initial_capital)
    full_m = compute_phase_metrics_slice(by_full, initial_capital)
    none_m = compute_phase_metrics_slice(by_none, initial_capital)
    promo_m = compute_phase_metrics_slice(promo, initial_capital)

    early_m["net_pnl_share_of_portfolio_pct"] = _share(by_early)
    full_m["net_pnl_share_of_portfolio_pct"] = _share(by_full)
    none_m["net_pnl_share_of_portfolio_pct"] = _share(by_none)
    promo_m["net_pnl_share_of_portfolio_pct"] = (
        round(sum(float(t.pnl) for t in promo) / total_net * 100.0, 2) if total_net != 0 else 0.0
    )
    early_m["max_drawdown_contribution_pct"] = _dd_contrib(by_early)
    full_m["max_drawdown_contribution_pct"] = _dd_contrib(by_full)
    none_m["max_drawdown_contribution_pct"] = _dd_contrib(by_none)
    promo_m["max_drawdown_contribution_pct"] = _dd_contrib(promo)

    return {
        "early": early_m,
        "full": full_m,
        "none": none_m,
        "promotion_evidence": promo_m,
        "promotion_evidence_excludes_early_warmup": True,
        "portfolio_max_drawdown_pct": round(portfolio_dd, 4),
    }


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


def max_drawdown_pct(trades: list[CompletedTrade], initial_capital: float = 10000.0) -> float:
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


def pnl_by_dimension(trades: list[CompletedTrade], dimension: str) -> dict[str, float]:
    """Group PnL by a trade dimension (symbol, venue, tier, epoch, exit_reason)."""
    result: dict[str, float] = {}
    for t in trades:
        key = getattr(t, dimension, "unknown")
        result[key] = result.get(key, 0.0) + float(t.pnl)
    return result


def count_by_dimension(trades: list[CompletedTrade], dimension: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for t in trades:
        key = getattr(t, dimension, "unknown")
        result[key] = result.get(key, 0) + 1
    return result


TIER_KEYS: tuple[str, ...] = ("A", "B", "C")


def metrics_by_tier(trades: list[CompletedTrade]) -> dict[str, dict[str, Any]]:
    """Per-tier expectancy, win rate, and PnL (same slices as ``get_metrics(tier=...)``).

    Used for validation audits: compare ``pnl_by_tier`` sums to ``expectancy * count`` per tier.
    """
    out: dict[str, dict[str, Any]] = {}
    for tier in TIER_KEYS:
        subset = [t for t in trades if t.tier == tier]
        out[tier] = {
            "trade_count": len(subset),
            "pnl": round(float(sum(t.pnl for t in subset)), 2),
            "expectancy": round(expectancy(subset), 4),
            "win_rate": round(win_rate(subset), 4),
            "avg_pnl_per_trade": round(expectancy(subset), 4),
            "avg_slippage_bps": round(avg_slippage_bps(subset), 2) if subset else 0.0,
        }
    other = [t for t in trades if t.tier not in TIER_KEYS]
    if other:
        out["other"] = {
            "trade_count": len(other),
            "pnl": round(float(sum(t.pnl for t in other)), 2),
            "expectancy": round(expectancy(other), 4),
            "win_rate": round(win_rate(other), 4),
            "avg_pnl_per_trade": round(expectancy(other), 4),
            "avg_slippage_bps": round(avg_slippage_bps(other), 2),
        }
    return out


def tier_validation_metrics(trades: list[CompletedTrade]) -> dict[str, dict[str, Any]]:
    """Per-tier validation stats beyond baseline PnL/WR/expectancy."""
    out: dict[str, dict[str, Any]] = {}
    for tier in TIER_KEYS:
        subset = [t for t in trades if t.tier == tier]
        if not subset:
            out[tier] = {
                "trade_count": 0,
                "expectancy": 0.0,
                "win_rate": 0.0,
                "pnl": 0.0,
                "avg_hold_seconds": 0.0,
                "avg_mfe_pct": 0.0,
                "avg_mae_pct": 0.0,
                "exit_layer_distribution": {},
                "exit_reason_distribution": {},
                "notional_weighted_pnl_pct": 0.0,
            }
            continue

        total_notional = float(sum(t.entry_notional_usd for t in subset))
        pnl_total = float(sum(t.pnl for t in subset))
        nw = (pnl_total / total_notional * 100.0) if total_notional > 0 else 0.0
        out[tier] = {
            "trade_count": len(subset),
            "expectancy": round(expectancy(subset), 4),
            "win_rate": round(win_rate(subset), 4),
            "pnl": round(pnl_total, 2),
            "avg_hold_seconds": round(sum(t.hold_seconds for t in subset) / len(subset), 1),
            "avg_mfe_pct": round(sum(t.mfe_pct for t in subset) / len(subset), 6),
            "avg_mae_pct": round(sum(t.mae_pct for t in subset) / len(subset), 6),
            "exit_layer_distribution": count_by_dimension(subset, "exit_layer"),
            "exit_reason_distribution": count_by_dimension(subset, "exit_reason"),
            "notional_weighted_pnl_pct": round(nw, 4),
        }
    return out


def tier_pnl_consistency_check(trades: list[CompletedTrade]) -> dict[str, Any]:
    """``sum(pnl_by_tier values)`` must equal total realized PnL (floating-point tolerant)."""
    by_tier = pnl_by_dimension(trades, "tier")
    total = float(sum(t.pnl for t in trades))
    sum_keys = sum(by_tier.values())
    delta = abs(total - sum_keys)
    return {
        "total_pnl": round(total, 2),
        "sum_pnl_by_tier": round(sum_keys, 2),
        "delta": round(delta, 8),
        "consistent": delta < 1e-4,
    }


def slippage_by_source(trades: list[CompletedTrade]) -> dict[str, dict[str, Any]]:
    """Modeled slippage (bps) split by ``CompletedTrade.source`` (paper vs demo_exchange)."""
    by_src: dict[str, list[CompletedTrade]] = defaultdict(list)
    for t in trades:
        by_src[t.source].append(t)
    result: dict[str, dict[str, Any]] = {}
    for src, ts in sorted(by_src.items()):
        result[src] = {
            "trade_count": len(ts),
            "avg_slippage_bps": round(avg_slippage_bps(ts), 2),
        }
    return result


def exit_effectiveness_audit(trades: list[CompletedTrade]) -> dict[str, Any]:
    """Saved/killed counts and no-progress regret with sample-size caveat (no counterfactual exits)."""
    n = len(trades)
    npr = no_progress_regret(trades)
    notes: list[str] = [
        "saved_losers/killed_winners are layer-based heuristics; they do not prove counterfactual PnL.",
    ]
    if n < 30:
        notes.append("trade_count < 30: tier and exit statistics are high-variance.")
    return {
        "trade_count": n,
        "saved_losers": saved_losers_count(trades),
        "killed_winners": killed_winners_count(trades),
        "no_progress_regret": npr,
        "notes": notes,
    }


def saved_losers_count(trades: list[CompletedTrade]) -> int:
    """Exits by L1/L2 where position was losing → saved from deeper loss."""
    return sum(1 for t in trades if t.exit_layer in (1, 2) and not t.was_profitable_at_exit)


def killed_winners_count(trades: list[CompletedTrade]) -> int:
    """Exits by L2/L3 where position was profitable → potentially killed a winner."""
    return sum(1 for t in trades if t.exit_layer in (2, 3) and t.was_profitable_at_exit)


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


def compute_all_metrics(trades: list[CompletedTrade], initial_capital: float = 10000.0) -> dict:
    """Compute all metrics for a set of trades. Dashboard-ready dict."""
    long_trades = [t for t in trades if t.direction == "long"]
    short_trades = [t for t in trades if t.direction == "short"]

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
        "metrics_by_tier": metrics_by_tier(trades),
        "tier_validation": tier_validation_metrics(trades),
        "tier_pnl_consistency": tier_pnl_consistency_check(trades),
        "slippage_by_source": slippage_by_source(trades),
        "exit_effectiveness_audit": exit_effectiveness_audit(trades),
        "pnl_by_exit_reason": pnl_by_dimension(trades, "exit_reason"),
        "count_by_exit_reason": count_by_dimension(trades, "exit_reason"),
        "saved_losers": saved_losers_count(trades),
        "killed_winners": killed_winners_count(trades),
        "no_progress_regret": no_progress_regret(trades),
        "runner_outcomes": runner_mode_outcomes(trades),
        "avg_latency_ms": round(avg_signal_to_fill_latency_ms(trades), 1),
        "avg_slippage_bps": round(avg_slippage_bps(trades), 2),
        "avg_hold_seconds": (
            round(sum(t.hold_seconds for t in trades) / len(trades), 1) if trades else 0.0
        ),
        "count_by_source": count_by_dimension(trades, "source"),
        "warmup_phase_breakdown": compute_warmup_phase_breakdown(trades, initial_capital),
        "direction_splits": {
            "long_trade_count": len(long_trades),
            "short_trade_count": len(short_trades),
            "long_win_rate": round(win_rate(long_trades), 4) if long_trades else 0.0,
            "short_win_rate": round(win_rate(short_trades), 4) if short_trades else 0.0,
            "long_expectancy": round(expectancy(long_trades), 2) if long_trades else 0.0,
            "short_expectancy": round(expectancy(short_trades), 2) if short_trades else 0.0,
            "long_pnl": round(float(sum(t.pnl for t in long_trades)), 2),
            "short_pnl": round(float(sum(t.pnl for t in short_trades)), 2),
        },
    }
