"""Tests for pure metric calculation functions."""
from __future__ import annotations

from decimal import Decimal

import pytest

from cte.analytics.metrics import (
    CompletedTrade,
    avg_loss,
    avg_signal_to_fill_latency_ms,
    avg_slippage_bps,
    avg_win,
    compute_all_metrics,
    count_by_dimension,
    expectancy,
    killed_winners_count,
    max_drawdown_pct,
    no_progress_regret,
    pnl_by_dimension,
    profit_factor,
    runner_mode_outcomes,
    saved_losers_count,
    slippage_drift,
    win_rate,
)


def _trade(
    pnl=10, symbol="BTCUSDT", venue="binance", tier="A", epoch="paper",
    source="paper_simulated",
    exit_reason="winner_trailing", exit_layer=4, hold=300, r=1.0,
    latency=100, slip=5.0, mfe=0.02, mae=0.005, profitable_at_exit=True,
    mode="normal",
) -> CompletedTrade:
    return CompletedTrade(
        symbol=symbol, venue=venue, tier=tier, epoch=epoch, source=source,
        pnl=Decimal(str(pnl)), exit_reason=exit_reason, exit_layer=exit_layer,
        hold_seconds=hold, r_multiple=r, entry_latency_ms=latency,
        modeled_slippage_bps=slip, mfe_pct=mfe, mae_pct=mae,
        was_profitable_at_exit=profitable_at_exit, position_mode=mode,
    )


class TestCoreMetrics:
    def test_win_rate_empty(self):
        assert win_rate([]) == 0.0

    def test_win_rate_all_winners(self):
        trades = [_trade(pnl=10), _trade(pnl=20), _trade(pnl=5)]
        assert win_rate(trades) == pytest.approx(1.0)

    def test_win_rate_mixed(self):
        trades = [_trade(pnl=10), _trade(pnl=-5), _trade(pnl=20), _trade(pnl=-10)]
        assert win_rate(trades) == pytest.approx(0.5)

    def test_expectancy(self):
        trades = [_trade(pnl=100), _trade(pnl=-50)]
        assert expectancy(trades) == pytest.approx(25.0)

    def test_profit_factor(self):
        trades = [_trade(pnl=100), _trade(pnl=50), _trade(pnl=-30)]
        pf = profit_factor(trades)
        assert pf is not None
        assert pf == pytest.approx(5.0)  # 150/30

    def test_profit_factor_no_losses(self):
        trades = [_trade(pnl=100)]
        assert profit_factor(trades) is None

    def test_avg_win_loss(self):
        trades = [_trade(pnl=100), _trade(pnl=200), _trade(pnl=-50)]
        assert avg_win(trades) == pytest.approx(150.0)
        assert avg_loss(trades) == pytest.approx(-50.0)

    def test_max_drawdown(self):
        trades = [
            _trade(pnl=100),   # equity: 10100
            _trade(pnl=200),   # equity: 10300 (peak)
            _trade(pnl=-400),  # equity: 9900 (dd = 400/10300 = 3.88%)
            _trade(pnl=50),    # equity: 9950
        ]
        dd = max_drawdown_pct(trades, 10000)
        assert dd == pytest.approx(400 / 10300, rel=0.01)


class TestDimensionBreakdowns:
    def test_pnl_by_symbol(self):
        trades = [
            _trade(pnl=100, symbol="BTCUSDT"),
            _trade(pnl=-20, symbol="ETHUSDT"),
            _trade(pnl=50, symbol="BTCUSDT"),
        ]
        result = pnl_by_dimension(trades, "symbol")
        assert result["BTCUSDT"] == pytest.approx(150.0)
        assert result["ETHUSDT"] == pytest.approx(-20.0)

    def test_pnl_by_tier(self):
        trades = [
            _trade(pnl=100, tier="A"),
            _trade(pnl=-20, tier="B"),
            _trade(pnl=50, tier="A"),
        ]
        result = pnl_by_dimension(trades, "tier")
        assert result["A"] == pytest.approx(150.0)
        assert result["B"] == pytest.approx(-20.0)

    def test_count_by_exit_reason(self):
        trades = [
            _trade(exit_reason="hard_stop"),
            _trade(exit_reason="winner_trailing"),
            _trade(exit_reason="hard_stop"),
        ]
        result = count_by_dimension(trades, "exit_reason")
        assert result["hard_stop"] == 2
        assert result["winner_trailing"] == 1


class TestExitAnalysis:
    def test_saved_losers(self):
        trades = [
            _trade(exit_layer=1, profitable_at_exit=False),  # L1, losing → saved
            _trade(exit_layer=2, profitable_at_exit=False),  # L2, losing → saved
            _trade(exit_layer=3, profitable_at_exit=False),  # L3, losing → NOT saved (L3 not counted)
            _trade(exit_layer=1, profitable_at_exit=True),   # L1, profitable → NOT saved
        ]
        assert saved_losers_count(trades) == 2

    def test_killed_winners(self):
        trades = [
            _trade(exit_layer=2, profitable_at_exit=True),   # thesis fail while profitable
            _trade(exit_layer=3, profitable_at_exit=True),   # no progress while profitable
            _trade(exit_layer=4, profitable_at_exit=True),   # winner trailing → NOT killed
            _trade(exit_layer=2, profitable_at_exit=False),  # thesis fail, losing → NOT killed
        ]
        assert killed_winners_count(trades) == 2

    def test_no_progress_regret(self):
        trades = [
            _trade(exit_reason="no_progress", mfe=0.01),  # had 1% MFE → regret
            _trade(exit_reason="no_progress", mfe=0.001), # tiny MFE → no regret
            _trade(exit_reason="winner_trailing"),          # not no_progress
        ]
        result = no_progress_regret(trades)
        assert result["count"] == 2
        assert result["had_positive_mfe"] == 1  # only the 1% one
        assert result["regret_rate"] == pytest.approx(0.5)

    def test_runner_outcomes(self):
        trades = [
            _trade(pnl=500, r=3.0, mode="runner"),
            _trade(pnl=200, r=1.5, mode="runner"),
            _trade(pnl=-50, mode="normal"),  # not a runner
        ]
        result = runner_mode_outcomes(trades)
        assert result["count"] == 2
        assert result["avg_r"] == pytest.approx(2.25)
        assert result["win_rate"] == pytest.approx(1.0)


class TestExecutionQuality:
    def test_avg_latency(self):
        trades = [_trade(latency=100), _trade(latency=200)]
        assert avg_signal_to_fill_latency_ms(trades) == pytest.approx(150.0)

    def test_avg_slippage(self):
        trades = [_trade(slip=3.0), _trade(slip=7.0)]
        assert avg_slippage_bps(trades) == pytest.approx(5.0)

    def test_slippage_drift(self):
        paper = [_trade(slip=3.0), _trade(slip=5.0)]
        live = [_trade(slip=6.0), _trade(slip=8.0)]
        result = slippage_drift(paper, live)
        assert result["paper_avg_bps"] == pytest.approx(4.0)
        assert result["live_avg_bps"] == pytest.approx(7.0)
        assert result["drift_bps"] == pytest.approx(3.0)


class TestComputeAll:
    def test_compute_all_returns_complete_dict(self):
        trades = [_trade(pnl=100), _trade(pnl=-30), _trade(pnl=50)]
        result = compute_all_metrics(trades)

        assert "trade_count" in result
        assert result["trade_count"] == 3
        assert "win_rate" in result
        assert "expectancy" in result
        assert "profit_factor" in result
        assert "max_drawdown_pct" in result
        assert "pnl_by_symbol" in result
        assert "pnl_by_tier" in result
        assert "pnl_by_venue" in result
        assert "saved_losers" in result
        assert "killed_winners" in result
        assert "no_progress_regret" in result
        assert "runner_outcomes" in result
        assert "avg_latency_ms" in result
        assert "avg_slippage_bps" in result

    def test_compute_all_empty(self):
        result = compute_all_metrics([])
        assert result["trade_count"] == 0
        assert result["win_rate"] == 0.0

    def test_count_by_source(self):
        trades = [
            _trade(source="seed"),
            _trade(source="paper_simulated"),
            _trade(source="paper_simulated"),
            _trade(source="demo_exchange"),
        ]
        result = compute_all_metrics(trades)
        assert result["count_by_source"]["seed"] == 1
        assert result["count_by_source"]["paper_simulated"] == 2
        assert result["count_by_source"]["demo_exchange"] == 1


class TestSourceFiltering:
    def test_source_in_trade(self):
        t = _trade(source="demo_exchange")
        assert t.source == "demo_exchange"

    def test_seed_vs_paper(self):
        seed = _trade(source="seed", pnl=100)
        paper = _trade(source="paper_simulated", pnl=200)
        trades = [seed, paper]
        paper_only = [t for t in trades if t.source != "seed"]
        assert len(paper_only) == 1
        assert float(paper_only[0].pnl) == 200
