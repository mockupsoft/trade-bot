"""Tests for the ScoringSignalEngine coordinator."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from cte.core.events import (
    DataQuality,
    FreshnessScore,
    ScoredSignalEvent,
    SignalAction,
    SignalTier,
    StreamingFeatureVector,
    Symbol,
    TimeframeFeatures,
)
from cte.core.settings import SignalSettings
from cte.core.streams import StreamPublisher
from cte.signals.engine import ScoringSignalEngine


@pytest.fixture
def settings() -> SignalSettings:
    return SignalSettings(cooldown_seconds=0, max_signals_per_hour=100)


@pytest.fixture
def publisher() -> StreamPublisher:
    p = AsyncMock(spec=StreamPublisher)
    p.publish = AsyncMock(return_value="mock-id")
    return p


@pytest.fixture
def engine(settings, publisher) -> ScoringSignalEngine:
    return ScoringSignalEngine(settings=settings, publisher=publisher)


def _bullish_vector(symbol: str = "BTCUSDT") -> StreamingFeatureVector:
    """Strong bullish conditions across all timeframes."""
    return StreamingFeatureVector(
        symbol=Symbol(symbol),
        tf_10s=TimeframeFeatures(window_seconds=10, returns_z=2.0, momentum_z=1.8,
                                  taker_flow_imbalance=0.3, spread_bps=1.0,
                                  spread_widening=0.8, ob_imbalance=0.3,
                                  venue_divergence_bps=2.0,
                                  trade_count=200, volume=10.0, window_fill_pct=1.0),
        tf_30s=TimeframeFeatures(window_seconds=30, returns_z=2.2, momentum_z=2.0,
                                  taker_flow_imbalance=0.25, spread_bps=1.0,
                                  spread_widening=0.85, ob_imbalance=0.25,
                                  venue_divergence_bps=1.5,
                                  trade_count=500, volume=25.0, window_fill_pct=1.0),
        tf_60s=TimeframeFeatures(window_seconds=60, returns_z=2.5, momentum_z=2.2,
                                  taker_flow_imbalance=0.2, spread_bps=0.8,
                                  spread_widening=0.75, ob_imbalance=0.35,
                                  liquidation_imbalance=-0.5,
                                  venue_divergence_bps=2.0,
                                  trade_count=1000, volume=50.0, window_fill_pct=1.0),
        tf_5m=TimeframeFeatures(window_seconds=300, returns_z=2.8, momentum_z=2.0,
                                 taker_flow_imbalance=0.18, spread_bps=0.8,
                                 spread_widening=0.82, ob_imbalance=0.28,
                                 liquidation_imbalance=-0.4,
                                 venue_divergence_bps=1.5,
                                 trade_count=5000, volume=250.0, window_fill_pct=0.98),
        freshness=FreshnessScore(trade_age_ms=30, orderbook_age_ms=80,
                                  binance_age_ms=30, bybit_age_ms=150, composite=0.99),
        execution_feasibility=0.95,
        last_price=Decimal("65000"),
        best_bid=Decimal("64999"),
        best_ask=Decimal("65001"),
        data_quality=DataQuality(warmup_complete=True, binance_connected=True,
                                  bybit_connected=True,
                                  window_fill_pct={"10s": 1.0, "30s": 1.0, "60s": 1.0, "5m": 0.98}),
    )


def _neutral_vector() -> StreamingFeatureVector:
    """Neutral conditions — all z-scores near zero."""
    def tf(ws):
        return TimeframeFeatures(
            window_seconds=ws, returns_z=0.0, momentum_z=0.0,
            taker_flow_imbalance=0.0, spread_bps=3.0,
            spread_widening=1.0, ob_imbalance=0.0,
            venue_divergence_bps=0.0,
            trade_count=100, volume=5.0, window_fill_pct=1.0,
        )
    return StreamingFeatureVector(
        symbol=Symbol.BTCUSDT,
        tf_10s=tf(10), tf_30s=tf(30), tf_60s=tf(60), tf_5m=tf(300),
        freshness=FreshnessScore(composite=0.95),
        execution_feasibility=0.80,
        last_price=Decimal("65000"),
        data_quality=DataQuality(warmup_complete=True),
    )


def _gated_vector() -> StreamingFeatureVector:
    """Conditions that should fail gates."""
    return StreamingFeatureVector(
        symbol=Symbol.BTCUSDT,
        tf_10s=TimeframeFeatures(window_seconds=10, trade_count=0, volume=0.0, window_fill_pct=0.0),
        tf_30s=TimeframeFeatures(window_seconds=30, trade_count=0, volume=0.0, window_fill_pct=0.0),
        tf_60s=TimeframeFeatures(window_seconds=60, spread_bps=25.0,
                                  trade_count=0, volume=0.0, window_fill_pct=0.0),
        tf_5m=TimeframeFeatures(window_seconds=300, trade_count=0, volume=0.0, window_fill_pct=0.0),
        freshness=FreshnessScore(composite=0.1),
        execution_feasibility=0.05,
        last_price=Decimal("65000"),
        data_quality=DataQuality(warmup_complete=False),
    )


class TestScoringSignalEngineBasic:
    @pytest.mark.asyncio
    async def test_bullish_produces_signal(self, engine):
        v = _bullish_vector()
        result = await engine.evaluate(v)
        assert result is not None
        assert isinstance(result, ScoredSignalEvent)
        assert result.action == SignalAction.OPEN_LONG
        assert result.composite_score > 0.0
        assert result.tier in (SignalTier.A, SignalTier.B, SignalTier.C)

    @pytest.mark.asyncio
    async def test_gated_returns_none(self, engine):
        v = _gated_vector()
        result = await engine.evaluate(v)
        assert result is None

    @pytest.mark.asyncio
    async def test_neutral_produces_low_tier(self, engine):
        v = _neutral_vector()
        result = await engine.evaluate(v)
        # Neutral (all 0.5) with spread penalty → score around 0.5
        # Microstructure spread_score for 3 bps = 1 - 3/15 = 0.8 (decent)
        # So neutral can land in B or C depending on microstructure
        if result is not None:
            assert result.tier in (SignalTier.B, SignalTier.C)
            assert result.composite_score < 0.72  # not tier A


class TestSignalAuditability:
    @pytest.mark.asyncio
    async def test_signal_has_sub_scores(self, engine):
        result = await engine.evaluate(_bullish_vector())
        assert result is not None
        assert "momentum" in result.sub_scores
        assert "orderflow" in result.sub_scores
        assert "liquidation" in result.sub_scores
        assert "microstructure" in result.sub_scores
        assert "cross_venue" in result.sub_scores

    @pytest.mark.asyncio
    async def test_signal_has_weights(self, engine):
        result = await engine.evaluate(_bullish_vector())
        assert result is not None
        assert sum(result.weights.values()) == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_signal_has_gate_results(self, engine):
        result = await engine.evaluate(_bullish_vector())
        assert result is not None
        assert result.gates_passed
        assert len(result.gate_results) == 5
        assert all(g.passed for g in result.gate_results)

    @pytest.mark.asyncio
    async def test_signal_has_reason(self, engine):
        result = await engine.evaluate(_bullish_vector())
        assert result is not None
        assert result.reason.primary_trigger.startswith("composite_score_")
        assert len(result.reason.human_readable) > 0

    @pytest.mark.asyncio
    async def test_signal_has_features_used(self, engine):
        result = await engine.evaluate(_bullish_vector())
        assert result is not None
        assert "tf_60s_returns_z" in result.features_used
        assert "freshness_composite" in result.features_used

    @pytest.mark.asyncio
    async def test_sub_score_details_present(self, engine):
        result = await engine.evaluate(_bullish_vector())
        assert result is not None
        assert "momentum" in result.sub_score_details
        detail = result.sub_score_details["momentum"]
        assert detail.score >= 0.0
        assert len(detail.description) > 0


class TestCooldownAndLimits:
    @pytest.mark.asyncio
    async def test_cooldown_blocks_rapid_signals(self):
        settings = SignalSettings(cooldown_seconds=300, max_signals_per_hour=100)
        publisher = AsyncMock(spec=StreamPublisher)
        publisher.publish = AsyncMock(return_value="mock-id")
        engine = ScoringSignalEngine(settings=settings, publisher=publisher)

        v = _bullish_vector()
        r1 = await engine.evaluate(v)
        assert r1 is not None

        r2 = await engine.evaluate(v)
        assert r2 is None  # on cooldown

    @pytest.mark.asyncio
    async def test_hourly_limit(self):
        settings = SignalSettings(cooldown_seconds=0, max_signals_per_hour=2)
        publisher = AsyncMock(spec=StreamPublisher)
        publisher.publish = AsyncMock(return_value="mock-id")
        engine = ScoringSignalEngine(settings=settings, publisher=publisher)

        v = _bullish_vector()
        r1 = await engine.evaluate(v)
        r2 = await engine.evaluate(v)
        r3 = await engine.evaluate(v)

        assert r1 is not None
        assert r2 is not None
        assert r3 is None  # hourly limit reached


class TestContextGating:
    @pytest.mark.asyncio
    async def test_whale_flag_reduces_score(self, engine):
        v_clean = _bullish_vector()
        r_clean = await engine.evaluate(v_clean)

        engine_fresh = ScoringSignalEngine(
            settings=SignalSettings(cooldown_seconds=0, max_signals_per_hour=100),
            publisher=AsyncMock(spec=StreamPublisher, publish=AsyncMock(return_value="x")),
        )
        v_whale = StreamingFeatureVector(
            **{**v_clean.model_dump(), "whale_risk_flag": True}
        )
        r_whale = await engine_fresh.evaluate(v_whale)

        if r_clean and r_whale:
            assert r_whale.composite_score < r_clean.composite_score
            assert r_whale.context_multiplier < r_clean.context_multiplier


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_same_input_same_output(self):
        """Same feature vector → identical scored signal."""
        results = []
        for _ in range(3):
            settings = SignalSettings(cooldown_seconds=0, max_signals_per_hour=100)
            pub = AsyncMock(spec=StreamPublisher, publish=AsyncMock(return_value="x"))
            eng = ScoringSignalEngine(settings=settings, publisher=pub)
            r = await eng.evaluate(_bullish_vector())
            if r:
                results.append(r)

        assert len(results) == 3
        for i in range(1, len(results)):
            assert results[i].composite_score == results[0].composite_score
            assert results[i].tier == results[0].tier
            assert results[i].sub_scores == results[0].sub_scores
