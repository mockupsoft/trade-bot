"""Warmup phase breakdown metrics (early vs full vs promotion evidence)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from cte.analytics.metrics import (
    CompletedTrade,
    compute_phase_metrics_slice,
    compute_warmup_phase_breakdown,
    trades_for_promotion_evidence,
)


def _t(pnl: float, phase: str) -> CompletedTrade:
    return CompletedTrade(
        symbol="BTCUSDT",
        venue="binance",
        tier="A",
        epoch="e",
        source="paper_simulated",
        pnl=Decimal(str(pnl)),
        exit_reason="no_progress",
        exit_layer=3,
        hold_seconds=60,
        r_multiple=0.0,
        entry_latency_ms=10,
        modeled_slippage_bps=3.0,
        mfe_pct=0.0,
        mae_pct=0.0,
        was_profitable_at_exit=pnl > 0,
        position_mode="normal",
        warmup_phase=phase,
    )


def test_trades_for_promotion_evidence_drops_early() -> None:
    trades = [_t(1, "early"), _t(2, "full"), _t(3, "none")]
    promo = trades_for_promotion_evidence(trades)
    assert len(promo) == 2
    assert sum(float(x.pnl) for x in promo) == pytest.approx(5.0)


def test_compute_warmup_phase_breakdown_splits() -> None:
    trades = [_t(10, "early"), _t(-5, "early"), _t(20, "full")]
    b = compute_warmup_phase_breakdown(trades, 10000.0)
    assert b["early"]["trade_count"] == 2
    assert b["full"]["trade_count"] == 1
    assert b["promotion_evidence"]["trade_count"] == 1
    assert b["promotion_evidence_excludes_early_warmup"] is True


def test_compute_phase_metrics_slice_net_and_gross() -> None:
    trades = [_t(100, "full"), _t(-40, "full")]
    m = compute_phase_metrics_slice(trades, 10000.0)
    assert m["net_pnl"] == pytest.approx(60.0)
    assert m["gross_pnl"] == pytest.approx(140.0)
