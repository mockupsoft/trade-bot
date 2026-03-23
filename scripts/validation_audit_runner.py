#!/usr/bin/env python3
"""Emit code-backed validation audit JSON for a trade list (or synthetic sample).

Usage (repo root):
  python scripts/validation_audit_runner.py

Tier / PnL: uses ``compute_all_metrics`` including ``metrics_by_tier`` and
``tier_pnl_consistency``. Execution: ``slippage_by_source`` (paper vs demo).
Exit: ``exit_effectiveness_audit`` (heuristic counts; no counterfactual).

For live data, inject trades via AnalyticsEngine in a dashboard session or
replay; this script demonstrates structure with a minimal synthetic sample.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

# Repo root on path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cte.analytics.metrics import CompletedTrade, compute_all_metrics  # noqa: E402


def _synthetic_trades() -> list[CompletedTrade]:
    """Minimal mixed-tier sample for structural checks (not statistical edge)."""
    rows: list[CompletedTrade] = []
    specs = [
        ("A", "paper_simulated", 120, 2.5, 1, False, "hard_risk"),
        ("A", "paper_simulated", -40, 2.8, 2, False, "thesis_failure"),
        ("B", "paper_simulated", 55, 3.0, 4, True, "winner_trailing"),
        ("C", "demo_exchange", 15, 8.0, 3, True, "no_progress"),
        ("C", "demo_exchange", -22, 7.5, 2, True, "no_progress"),
    ]
    for tier, source, pnl, slip, layer, prof, reason in specs:
        rows.append(
            CompletedTrade(
                symbol="BTCUSDT",
                venue="binance",
                tier=tier,
                epoch="paper",
                direction="long",
                source=source,
                pnl=Decimal(str(pnl)),
                exit_reason=reason,
                exit_layer=layer,
                hold_seconds=300,
                r_multiple=0.5,
                entry_latency_ms=50,
                modeled_slippage_bps=slip,
                mfe_pct=0.01,
                mae_pct=0.005,
                was_profitable_at_exit=prof,
                position_mode="normal",
                warmup_phase="full",
            )
        )
    return rows


def main() -> None:
    trades = _synthetic_trades()
    metrics = compute_all_metrics(trades, initial_capital=10000.0)
    recon_note = (
        "Reconciliation: check venue vs local via PositionReconciler and "
        "Prometheus cte_recon_discrepancies_total — not derivable from trade rows alone."
    )
    out = {
        "validation_audit": {
            "tier_and_pnl": {
                "metrics_by_tier": metrics["metrics_by_tier"],
                "pnl_by_tier": metrics["pnl_by_tier"],
                "tier_pnl_consistency": metrics["tier_pnl_consistency"],
            },
            "execution_slippage": {
                "slippage_by_source": metrics["slippage_by_source"],
                "avg_slippage_bps": metrics["avg_slippage_bps"],
                "note": (
                    "Modeled slippage on CompletedTrade; compare paper vs demo_exchange "
                    "for drift (see slippage_drift in epoch comparison)."
                ),
            },
            "exit_heuristics": metrics["exit_effectiveness_audit"],
            "reconciliation": recon_note,
        },
        "full_metrics_keys_added": [
            "metrics_by_tier",
            "tier_pnl_consistency",
            "slippage_by_source",
            "exit_effectiveness_audit",
        ],
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
