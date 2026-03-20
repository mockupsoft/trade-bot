"""Tests for canonical event models."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cte.core.events import (
    BaseEvent,
    FeatureVector,
    RawTradeEvent,
    RiskAssessmentEvent,
    RiskCheckResult,
    RiskDecision,
    SignalAction,
    SignalEvent,
    SignalReason,
    Symbol,
    TradeEvent,
)


class TestBaseEvent:
    def test_base_event_has_id_and_timestamp(self):
        event = BaseEvent()
        assert event.event_id is not None
        assert event.timestamp is not None
        assert event.timestamp.tzinfo is not None

    def test_base_event_is_frozen(self):
        event = BaseEvent()
        with pytest.raises((TypeError, ValueError)):
            event.source = "modified"


class TestRawTradeEvent:
    def test_raw_trade_serialization(self, sample_raw_trade):
        data = sample_raw_trade.model_dump(mode="json")
        assert data["venue"] == "binance"
        assert data["price"] == "50000.50"
        assert data["symbol_raw"] == "BTCUSDT"

    def test_raw_trade_roundtrip(self, sample_raw_trade):
        data = sample_raw_trade.model_dump(mode="json")
        restored = RawTradeEvent.model_validate(data)
        assert restored.venue == sample_raw_trade.venue
        assert restored.price == sample_raw_trade.price


class TestTradeEvent:
    def test_trade_event_decimal_price(self, sample_trade):
        assert isinstance(sample_trade.price, Decimal)
        assert sample_trade.price == Decimal("50000.50")

    def test_trade_event_serialization(self, sample_trade):
        data = sample_trade.model_dump(mode="json")
        assert data["symbol"] == "BTCUSDT"
        assert data["side"] == "buy"

    def test_trade_event_roundtrip(self, sample_trade):
        data = sample_trade.model_dump(mode="json")
        restored = TradeEvent.model_validate(data)
        assert restored.price == sample_trade.price
        assert restored.symbol == Symbol.BTCUSDT


class TestOrderbookSnapshotEvent:
    def test_orderbook_levels(self, sample_orderbook):
        assert len(sample_orderbook.bids) == 2
        assert len(sample_orderbook.asks) == 2
        assert sample_orderbook.bids[0].price == Decimal("50000")

    def test_orderbook_serialization(self, sample_orderbook):
        data = sample_orderbook.model_dump(mode="json")
        assert len(data["bids"]) == 2
        assert data["venue"] == "binance"


class TestSignalEvent:
    def test_signal_has_reason(self, sample_signal):
        assert sample_signal.reason.primary_trigger == "ema_crossover_bullish"
        assert sample_signal.confidence == 0.75

    def test_signal_confidence_bounds(self):
        with pytest.raises((TypeError, ValueError)):
            SignalEvent(
                symbol=Symbol.BTCUSDT,
                action=SignalAction.OPEN_LONG,
                confidence=1.5,
                reason=SignalReason(
                    primary_trigger="test",
                    human_readable="test",
                ),
            )

    def test_signal_serialization(self, sample_signal):
        data = sample_signal.model_dump(mode="json")
        assert data["action"] == "open_long"
        assert "reason" in data
        assert data["reason"]["primary_trigger"] == "ema_crossover_bullish"


class TestFeatureVector:
    def test_feature_vector_optional_fields(self):
        vector = FeatureVector(
            symbol=Symbol.BTCUSDT,
            window_start=datetime(2024, 1, 1, tzinfo=UTC),
            window_end=datetime(2024, 1, 1, 1, tzinfo=UTC),
        )
        assert vector.rsi is None
        assert vector.ema_fast is None

    def test_feature_vector_with_values(self, sample_feature_vector):
        assert sample_feature_vector.rsi == 45.0
        assert sample_feature_vector.ema_fast == 50100.0


class TestRiskAssessmentEvent:
    def test_risk_approved(self, sample_risk_approved):
        assert sample_risk_approved.decision == RiskDecision.APPROVED

    def test_risk_rejected(self):
        assessment = RiskAssessmentEvent(
            signal_id=SignalEvent(
                symbol=Symbol.BTCUSDT,
                action=SignalAction.OPEN_LONG,
                confidence=0.7,
                reason=SignalReason(primary_trigger="test", human_readable="test"),
            ).event_id,
            symbol=Symbol.BTCUSDT,
            decision=RiskDecision.REJECTED,
            reason="max_position_size exceeded",
            checks_performed=[
                RiskCheckResult(
                    check_name="max_position_size",
                    passed=False,
                    value=0.08,
                    threshold=0.05,
                ),
            ],
        )
        assert assessment.decision == RiskDecision.REJECTED
        assert not assessment.checks_performed[0].passed
