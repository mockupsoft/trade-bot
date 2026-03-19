"""Tests for sub-score computations."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cte.core.events import (
    DataQuality,
    FreshnessScore,
    StreamingFeatureVector,
    Symbol,
    TimeframeFeatures,
)
from cte.signals.scorer import (
    compute_context_score,
    compute_cross_venue_score,
    compute_liquidation_score,
    compute_microstructure_score,
    compute_momentum_score,
    compute_orderflow_score,
    inverse_ratio_to_score,
    ratio_to_score,
    z_to_score,
)


def _make_vector(**overrides) -> StreamingFeatureVector:
    """Build a StreamingFeatureVector with sensible defaults."""
    defaults = {
        "symbol": Symbol.BTCUSDT,
        "tf_10s": TimeframeFeatures(window_seconds=10, returns_z=1.0, momentum_z=0.8,
                                     taker_flow_imbalance=0.2, spread_bps=1.5,
                                     spread_widening=0.9, ob_imbalance=0.15,
                                     liquidation_imbalance=None, venue_divergence_bps=1.0,
                                     trade_count=100, volume=5.0, window_fill_pct=1.0),
        "tf_30s": TimeframeFeatures(window_seconds=30, returns_z=1.2, momentum_z=1.0,
                                     taker_flow_imbalance=0.15, spread_bps=1.5,
                                     spread_widening=0.95, ob_imbalance=0.2,
                                     liquidation_imbalance=None, venue_divergence_bps=0.8,
                                     trade_count=300, volume=15.0, window_fill_pct=1.0),
        "tf_60s": TimeframeFeatures(window_seconds=60, returns_z=1.5, momentum_z=1.3,
                                     taker_flow_imbalance=0.1, spread_bps=1.2,
                                     spread_widening=0.88, ob_imbalance=0.25,
                                     liquidation_imbalance=-0.4, venue_divergence_bps=1.5,
                                     trade_count=600, volume=30.0, window_fill_pct=1.0),
        "tf_5m": TimeframeFeatures(window_seconds=300, returns_z=2.0, momentum_z=1.5,
                                    taker_flow_imbalance=0.12, spread_bps=1.2,
                                    spread_widening=0.91, ob_imbalance=0.2,
                                    liquidation_imbalance=-0.3, venue_divergence_bps=1.0,
                                    trade_count=3000, volume=150.0, window_fill_pct=0.95),
        "freshness": FreshnessScore(trade_age_ms=50, orderbook_age_ms=100,
                                     binance_age_ms=50, bybit_age_ms=200, composite=0.98),
        "execution_feasibility": 0.92,
        "whale_risk_flag": False,
        "urgent_news_flag": False,
        "last_price": Decimal("65000"),
        "best_bid": Decimal("64999"),
        "best_ask": Decimal("65001"),
        "mid_price": Decimal("65000"),
        "data_quality": DataQuality(warmup_complete=True, binance_connected=True,
                                     bybit_connected=True,
                                     window_fill_pct={"10s": 1.0, "30s": 1.0, "60s": 1.0, "5m": 0.95}),
    }
    defaults.update(overrides)
    return StreamingFeatureVector(**defaults)


class TestMappingHelpers:
    def test_z_to_score_zero(self):
        assert z_to_score(0.0) == pytest.approx(0.5)

    def test_z_to_score_positive(self):
        assert z_to_score(3.0) == pytest.approx(1.0)

    def test_z_to_score_negative(self):
        assert z_to_score(-3.0) == pytest.approx(0.0)

    def test_z_to_score_clamped(self):
        assert z_to_score(10.0) == 1.0
        assert z_to_score(-10.0) == 0.0

    def test_z_to_score_none(self):
        assert z_to_score(None) == 0.5

    def test_ratio_to_score(self):
        assert ratio_to_score(1.0) == pytest.approx(1.0)
        assert ratio_to_score(-1.0) == pytest.approx(0.0)
        assert ratio_to_score(0.0) == pytest.approx(0.5)
        assert ratio_to_score(None) == 0.5

    def test_inverse_ratio(self):
        assert inverse_ratio_to_score(-1.0) == pytest.approx(1.0)
        assert inverse_ratio_to_score(1.0) == pytest.approx(0.0)
        assert inverse_ratio_to_score(0.0) == pytest.approx(0.5)


class TestMomentumScore:
    def test_bullish_momentum(self):
        v = _make_vector()
        result = compute_momentum_score(v)
        assert result.score > 0.6
        assert len(result.components) == 4

    def test_neutral_momentum(self):
        v = _make_vector(
            tf_10s=TimeframeFeatures(window_seconds=10, returns_z=0.0, momentum_z=0.0,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0),
            tf_30s=TimeframeFeatures(window_seconds=30, returns_z=0.0, momentum_z=0.0,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0),
            tf_60s=TimeframeFeatures(window_seconds=60, returns_z=0.0, momentum_z=0.0,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0),
            tf_5m=TimeframeFeatures(window_seconds=300, returns_z=0.0, momentum_z=0.0,
                                     trade_count=100, volume=5.0, window_fill_pct=1.0),
        )
        result = compute_momentum_score(v)
        assert result.score == pytest.approx(0.5, abs=0.01)

    def test_none_features_imputed(self):
        tf = TimeframeFeatures(window_seconds=10, returns_z=None, momentum_z=None,
                                trade_count=0, volume=0.0, window_fill_pct=0.0)
        v = _make_vector(tf_10s=tf, tf_30s=TimeframeFeatures(window_seconds=30, returns_z=None, momentum_z=None, trade_count=0, volume=0.0, window_fill_pct=0.0),
                         tf_60s=TimeframeFeatures(window_seconds=60, returns_z=None, momentum_z=None, trade_count=0, volume=0.0, window_fill_pct=0.0),
                         tf_5m=TimeframeFeatures(window_seconds=300, returns_z=None, momentum_z=None, trade_count=0, volume=0.0, window_fill_pct=0.0))
        result = compute_momentum_score(v)
        assert result.score == pytest.approx(0.5, abs=0.01)
        assert result.imputed_count == 8  # 4 timeframes × 2 features


class TestOrderflowScore:
    def test_buy_dominant(self):
        v = _make_vector()  # default has positive TFI
        result = compute_orderflow_score(v)
        assert result.score > 0.5

    def test_sell_dominant(self):
        tf = TimeframeFeatures(window_seconds=60, taker_flow_imbalance=-0.8,
                                trade_count=100, volume=5.0, window_fill_pct=1.0)
        v = _make_vector(
            tf_10s=TimeframeFeatures(window_seconds=10, taker_flow_imbalance=-0.8, trade_count=100, volume=5.0, window_fill_pct=1.0),
            tf_30s=TimeframeFeatures(window_seconds=30, taker_flow_imbalance=-0.8, trade_count=100, volume=5.0, window_fill_pct=1.0),
            tf_60s=tf,
            tf_5m=TimeframeFeatures(window_seconds=300, taker_flow_imbalance=-0.8, trade_count=100, volume=5.0, window_fill_pct=1.0),
        )
        result = compute_orderflow_score(v)
        assert result.score < 0.2


class TestLiquidationScore:
    def test_short_squeeze_bullish(self):
        v = _make_vector()  # default has negative liq_imbalance (short squeeze)
        result = compute_liquidation_score(v)
        assert result.score > 0.5

    def test_long_cascade_bearish(self):
        v = _make_vector(
            tf_60s=TimeframeFeatures(window_seconds=60, liquidation_imbalance=0.8,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0),
            tf_5m=TimeframeFeatures(window_seconds=300, liquidation_imbalance=0.7,
                                     trade_count=100, volume=5.0, window_fill_pct=1.0),
        )
        result = compute_liquidation_score(v)
        assert result.score < 0.3

    def test_no_liquidations(self):
        v = _make_vector(
            tf_60s=TimeframeFeatures(window_seconds=60, liquidation_imbalance=None,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0),
            tf_5m=TimeframeFeatures(window_seconds=300, liquidation_imbalance=None,
                                     trade_count=100, volume=5.0, window_fill_pct=1.0),
        )
        result = compute_liquidation_score(v)
        assert result.score == pytest.approx(0.5)
        assert result.imputed_count == 2


class TestMicrostructureScore:
    def test_good_microstructure(self):
        v = _make_vector()  # tight spread, no widening, bid-heavy
        result = compute_microstructure_score(v)
        assert result.score > 0.7

    def test_wide_spread_tanks_score(self):
        v = _make_vector(
            tf_60s=TimeframeFeatures(window_seconds=60, spread_bps=14.0,
                                      spread_widening=2.5, ob_imbalance=-0.3,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0),
        )
        result = compute_microstructure_score(v)
        assert result.score < 0.3


class TestCrossVenueScore:
    def test_binance_higher(self):
        v = _make_vector()
        result = compute_cross_venue_score(v)
        assert result.score > 0.5

    def test_no_divergence_data(self):
        v = _make_vector(
            tf_60s=TimeframeFeatures(window_seconds=60, venue_divergence_bps=None,
                                      trade_count=100, volume=5.0, window_fill_pct=1.0),
        )
        result = compute_cross_venue_score(v)
        assert result.score == 0.5


class TestContextScore:
    def test_clean_context(self):
        v = _make_vector()
        result = compute_context_score(v)
        assert result.score == 1.0

    def test_whale_penalty(self):
        v = _make_vector(whale_risk_flag=True)
        result = compute_context_score(v)
        assert result.score == pytest.approx(0.7)

    def test_both_flags(self):
        v = _make_vector(whale_risk_flag=True, urgent_news_flag=True)
        result = compute_context_score(v)
        assert result.score == pytest.approx(0.49)

    def test_warmup_blocks(self):
        v = _make_vector(data_quality=DataQuality(warmup_complete=False))
        result = compute_context_score(v)
        assert result.score == 0.0

    def test_context_never_amplifies(self):
        v = _make_vector()
        result = compute_context_score(v)
        assert result.score <= 1.0
