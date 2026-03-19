"""Tests for hard gate checks."""
from __future__ import annotations

from decimal import Decimal

from cte.core.events import (
    DataQuality,
    FreshnessScore,
    StreamingFeatureVector,
    Symbol,
    TimeframeFeatures,
)
from cte.signals.gates import check_all_gates


def _make_vector(**overrides) -> StreamingFeatureVector:
    defaults = {
        "symbol": Symbol.BTCUSDT,
        "tf_10s": TimeframeFeatures(window_seconds=10, trade_count=100, volume=5.0, window_fill_pct=1.0),
        "tf_30s": TimeframeFeatures(window_seconds=30, trade_count=300, volume=15.0, window_fill_pct=1.0),
        "tf_60s": TimeframeFeatures(window_seconds=60, spread_bps=1.2, venue_divergence_bps=1.0,
                                     trade_count=600, volume=30.0, window_fill_pct=1.0),
        "tf_5m": TimeframeFeatures(window_seconds=300, trade_count=3000, volume=150.0, window_fill_pct=0.95),
        "freshness": FreshnessScore(trade_age_ms=50, orderbook_age_ms=100, composite=0.98),
        "execution_feasibility": 0.92,
        "data_quality": DataQuality(warmup_complete=True, binance_connected=True, bybit_connected=True),
        "last_price": Decimal("65000"),
    }
    defaults.update(overrides)
    return StreamingFeatureVector(**defaults)


class TestAllGatesPass:
    def test_healthy_conditions(self):
        v = _make_vector()
        verdict = check_all_gates(v)
        assert verdict.all_passed
        assert len(verdict.rejection_reasons) == 0
        assert all(r.passed for r in verdict.results)


class TestStaleFeedGate:
    def test_stale_data_rejected(self):
        v = _make_vector(freshness=FreshnessScore(composite=0.1))
        verdict = check_all_gates(v)
        assert not verdict.all_passed
        assert any(r.name == "stale_feed" and not r.passed for r in verdict.results)

    def test_fresh_data_passes(self):
        v = _make_vector(freshness=FreshnessScore(composite=0.8))
        verdict = check_all_gates(v)
        stale_gate = next(r for r in verdict.results if r.name == "stale_feed")
        assert stale_gate.passed


class TestMaxSpreadGate:
    def test_wide_spread_rejected(self):
        v = _make_vector(
            tf_60s=TimeframeFeatures(window_seconds=60, spread_bps=20.0,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0)
        )
        verdict = check_all_gates(v)
        assert not verdict.all_passed
        assert any(r.name == "max_spread" and not r.passed for r in verdict.results)

    def test_none_spread_passes(self):
        v = _make_vector(
            tf_60s=TimeframeFeatures(window_seconds=60, spread_bps=None,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0)
        )
        verdict = check_all_gates(v)
        spread_gate = next(r for r in verdict.results if r.name == "max_spread")
        assert spread_gate.passed


class TestMaxDivergenceGate:
    def test_extreme_divergence_rejected(self):
        v = _make_vector(
            tf_60s=TimeframeFeatures(window_seconds=60, venue_divergence_bps=80.0,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0)
        )
        verdict = check_all_gates(v)
        assert not verdict.all_passed
        assert any("Divergence" in r.reason for r in verdict.results if not r.passed)

    def test_negative_divergence_checked_absolute(self):
        v = _make_vector(
            tf_60s=TimeframeFeatures(window_seconds=60, venue_divergence_bps=-60.0,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0)
        )
        verdict = check_all_gates(v)
        div_gate = next(r for r in verdict.results if r.name == "max_divergence")
        assert not div_gate.passed


class TestExecutionFeasibilityGate:
    def test_low_feasibility_rejected(self):
        v = _make_vector(execution_feasibility=0.1)
        verdict = check_all_gates(v)
        assert not verdict.all_passed
        assert any(r.name == "execution_feasibility" and not r.passed for r in verdict.results)

    def test_none_feasibility_rejected(self):
        v = _make_vector(execution_feasibility=None)
        verdict = check_all_gates(v)
        feas_gate = next(r for r in verdict.results if r.name == "execution_feasibility")
        assert not feas_gate.passed


class TestWarmupGate:
    def test_warmup_incomplete_rejected(self):
        v = _make_vector(data_quality=DataQuality(warmup_complete=False))
        verdict = check_all_gates(v)
        assert not verdict.all_passed
        warmup_gate = next(r for r in verdict.results if r.name == "warmup")
        assert not warmup_gate.passed


class TestMultipleGateFailures:
    def test_multiple_rejections_reported(self):
        v = _make_vector(
            freshness=FreshnessScore(composite=0.1),
            execution_feasibility=0.05,
            data_quality=DataQuality(warmup_complete=False),
        )
        verdict = check_all_gates(v)
        assert not verdict.all_passed
        assert len(verdict.rejection_reasons) >= 3
