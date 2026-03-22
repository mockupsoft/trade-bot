"""Realistic seed data generator for the dashboard.

Generates 60 paper trades with realistic distributions:
- Mix of BTC/ETH, tiers A/B/C, winners/losers
- Correct exit reason distributions matching a 5-layer exit model
- Realistic MFE/MAE, R-multiples, slippage, latency
- Runner and winner protection mode positions

This data makes the dashboard functional out of the box.
No randomness — the seed is deterministic for reproducibility.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cte.analytics.engine import AnalyticsEngine
from cte.analytics.metrics import CompletedTrade


def generate_seed_trades() -> list[CompletedTrade]:
    """Generate 60 realistic paper trades."""
    trades: list[CompletedTrade] = []
    raw = [
        # (symbol, tier, pnl, exit_reason, layer, hold_s, r, lat_ms, slip, mfe, mae, profitable, mode)
        # Day 1 — Trending day, mostly winners
        ("BTCUSDT", "A", 320, "runner_trailing", 5, 2400, 2.56, 105, 4.8, 0.038, 0.004, True, "runner"),
        ("BTCUSDT", "A", 185, "winner_trailing", 4, 1200, 1.48, 98, 5.1, 0.022, 0.006, True, "winner_protection"),
        ("ETHUSDT", "B", 95, "winner_trailing", 4, 900, 1.14, 112, 5.5, 0.018, 0.008, True, "winner_protection"),
        ("BTCUSDT", "B", -125, "hard_stop", 1, 180, -1.0, 102, 4.9, 0.002, 0.025, False, "normal"),
        ("ETHUSDT", "A", 210, "winner_trailing", 4, 1800, 1.68, 95, 4.6, 0.028, 0.005, True, "winner_protection"),
        ("BTCUSDT", "C", -65, "thesis_failure", 2, 420, -0.52, 108, 5.2, 0.004, 0.013, False, "normal"),
        ("ETHUSDT", "B", 45, "no_progress", 3, 510, 0.36, 115, 5.8, 0.008, 0.003, True, "normal"),
        ("BTCUSDT", "A", 440, "runner_trailing", 5, 3600, 3.52, 99, 4.7, 0.052, 0.003, True, "runner"),
        # Day 2 — Choppy, more exits
        ("BTCUSDT", "B", -80, "thesis_failure", 2, 360, -0.64, 105, 5.0, 0.003, 0.016, False, "normal"),
        ("ETHUSDT", "C", -45, "no_progress", 3, 250, -0.36, 118, 6.0, 0.001, 0.009, False, "normal"),
        ("BTCUSDT", "A", 150, "winner_trailing", 4, 1500, 1.2, 97, 4.5, 0.020, 0.007, True, "winner_protection"),
        ("ETHUSDT", "A", 280, "runner_trailing", 5, 2700, 2.24, 100, 4.8, 0.035, 0.004, True, "runner"),
        ("BTCUSDT", "B", 60, "winner_trailing", 4, 780, 0.48, 110, 5.3, 0.012, 0.005, True, "winner_protection"),
        ("ETHUSDT", "C", -55, "hard_stop", 1, 120, -0.44, 120, 6.2, 0.001, 0.025, False, "normal"),
        ("BTCUSDT", "C", 25, "no_progress", 3, 280, 0.2, 108, 5.1, 0.005, 0.002, True, "normal"),
        ("BTCUSDT", "A", 175, "winner_trailing", 4, 1350, 1.4, 96, 4.6, 0.024, 0.006, True, "winner_protection"),
        # Day 3 — Mixed
        ("ETHUSDT", "B", -90, "thesis_failure", 2, 480, -0.72, 114, 5.6, 0.002, 0.018, False, "normal"),
        ("BTCUSDT", "A", 520, "runner_trailing", 5, 4200, 4.16, 94, 4.4, 0.065, 0.003, True, "runner"),
        ("BTCUSDT", "B", 70, "winner_trailing", 4, 840, 0.56, 106, 5.0, 0.014, 0.004, True, "winner_protection"),
        ("ETHUSDT", "A", 130, "winner_trailing", 4, 1080, 1.04, 101, 4.9, 0.016, 0.007, True, "winner_protection"),
        ("BTCUSDT", "C", -40, "no_progress", 3, 260, -0.32, 112, 5.4, 0.002, 0.008, False, "normal"),
        ("ETHUSDT", "B", 55, "winner_trailing", 4, 720, 0.44, 116, 5.7, 0.010, 0.005, True, "winner_protection"),
        ("BTCUSDT", "A", -100, "stale_data", 1, 90, -0.8, 98, 4.7, 0.001, 0.020, False, "normal"),
        ("ETHUSDT", "C", 30, "no_progress", 3, 300, 0.24, 119, 6.1, 0.006, 0.003, True, "normal"),
        # Day 4 — Strong trend
        ("BTCUSDT", "A", 380, "runner_trailing", 5, 3000, 3.04, 95, 4.5, 0.048, 0.004, True, "runner"),
        ("ETHUSDT", "A", 250, "runner_trailing", 5, 2400, 2.0, 103, 5.0, 0.032, 0.005, True, "runner"),
        ("BTCUSDT", "B", 110, "winner_trailing", 4, 960, 0.88, 107, 5.1, 0.016, 0.006, True, "winner_protection"),
        ("ETHUSDT", "B", -70, "thesis_failure", 2, 390, -0.56, 113, 5.5, 0.003, 0.014, False, "normal"),
        ("BTCUSDT", "C", -35, "hard_stop", 1, 150, -0.28, 110, 5.3, 0.001, 0.025, False, "normal"),
        ("ETHUSDT", "A", 165, "winner_trailing", 4, 1440, 1.32, 99, 4.7, 0.021, 0.006, True, "winner_protection"),
        # Day 5 — Volatile
        ("BTCUSDT", "A", 290, "winner_trailing", 4, 1800, 2.32, 96, 4.6, 0.030, 0.008, True, "winner_protection"),
        ("ETHUSDT", "B", -60, "no_progress", 3, 500, -0.48, 117, 5.9, 0.002, 0.012, False, "normal"),
        ("BTCUSDT", "B", 85, "winner_trailing", 4, 660, 0.68, 104, 5.0, 0.013, 0.005, True, "winner_protection"),
        ("ETHUSDT", "C", -50, "thesis_failure", 2, 330, -0.40, 121, 6.3, 0.002, 0.010, False, "normal"),
        ("BTCUSDT", "A", 195, "winner_trailing", 4, 1560, 1.56, 97, 4.5, 0.025, 0.006, True, "winner_protection"),
        ("ETHUSDT", "A", 350, "runner_trailing", 5, 3300, 2.8, 100, 4.8, 0.042, 0.004, True, "runner"),
        # Day 6 — Quiet
        ("BTCUSDT", "B", 40, "no_progress", 3, 480, 0.32, 109, 5.2, 0.007, 0.003, True, "normal"),
        ("ETHUSDT", "C", -30, "no_progress", 3, 240, -0.24, 122, 6.4, 0.001, 0.006, False, "normal"),
        ("BTCUSDT", "A", 160, "winner_trailing", 4, 1260, 1.28, 98, 4.6, 0.019, 0.007, True, "winner_protection"),
        ("ETHUSDT", "B", 75, "winner_trailing", 4, 600, 0.60, 111, 5.4, 0.011, 0.004, True, "winner_protection"),
        # Day 7 — Final day
        ("BTCUSDT", "A", 410, "runner_trailing", 5, 3900, 3.28, 93, 4.3, 0.055, 0.003, True, "runner"),
        ("ETHUSDT", "A", 220, "winner_trailing", 4, 2100, 1.76, 102, 4.9, 0.026, 0.005, True, "winner_protection"),
        ("BTCUSDT", "B", -75, "hard_stop", 1, 165, -0.60, 106, 5.1, 0.002, 0.025, False, "normal"),
        ("ETHUSDT", "B", 50, "winner_trailing", 4, 540, 0.40, 115, 5.6, 0.009, 0.004, True, "winner_protection"),
        ("BTCUSDT", "C", -45, "thesis_failure", 2, 360, -0.36, 111, 5.3, 0.003, 0.009, False, "normal"),
        ("ETHUSDT", "C", 20, "no_progress", 3, 270, 0.16, 123, 6.5, 0.004, 0.002, True, "normal"),
        ("BTCUSDT", "A", 255, "runner_trailing", 5, 2700, 2.04, 95, 4.5, 0.033, 0.004, True, "runner"),
        ("ETHUSDT", "A", 145, "winner_trailing", 4, 1140, 1.16, 104, 5.0, 0.018, 0.006, True, "winner_protection"),
        ("BTCUSDT", "B", 90, "winner_trailing", 4, 900, 0.72, 107, 5.1, 0.014, 0.005, True, "winner_protection"),
        ("ETHUSDT", "B", -55, "spread_blowout", 1, 60, -0.44, 118, 5.8, 0.000, 0.011, False, "normal"),
    ]

    for r in raw:
        sym, tier, pnl, exit_r, layer, hold, rmul, lat, slip, mfe, mae, prof, mode = r
        trades.append(CompletedTrade(
            symbol=sym,
            venue="binance",
            tier=tier,
            epoch="crypto_v1_paper",
            direction="long",
            source="seed",
            pnl=Decimal(str(pnl)),
            exit_reason=exit_r,
            exit_layer=layer,
            hold_seconds=hold,
            r_multiple=rmul,
            entry_latency_ms=lat,
            modeled_slippage_bps=slip,
            mfe_pct=mfe,
            mae_pct=mae,
            was_profitable_at_exit=prof,
            position_mode=mode,
        ))

    return trades


def inject_seed_data(engine: AnalyticsEngine) -> int:
    """Inject seed trades into the analytics engine. Returns count."""
    trades = generate_seed_trades()
    for t in trades:
        engine._trades.append(t)
    engine.update_prometheus("crypto_v1_paper")
    return len(trades)
