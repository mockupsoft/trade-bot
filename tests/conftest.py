"""Shared test fixtures for CTE test suite."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from cte.core.events import (
    FeatureVector,
    OrderbookLevel,
    OrderbookSnapshotEvent,
    OrderType,
    RawTradeEvent,
    RiskAssessmentEvent,
    RiskCheckResult,
    RiskDecision,
    Side,
    SignalAction,
    SignalEvent,
    SignalReason,
    SizedOrderEvent,
    Symbol,
    TradeEvent,
    Venue,
)
from cte.core.settings import (
    CTESettings,
    ExecutionSettings,
    ExitSettings,
    FeatureSettings,
    RiskSettings,
    SignalSettings,
    SizingSettings,
)
from cte.core.streams import StreamPublisher


@pytest.fixture
def mock_publisher() -> StreamPublisher:
    """Mock StreamPublisher that records all published events."""
    publisher = AsyncMock(spec=StreamPublisher)
    publisher.published: list = []

    async def track_publish(stream_key: str, event: object) -> str:
        publisher.published.append((stream_key, event))
        return "mock-msg-id"

    publisher.publish = AsyncMock(side_effect=track_publish)
    return publisher


@pytest.fixture
def sample_raw_trade() -> RawTradeEvent:
    return RawTradeEvent(
        venue=Venue.BINANCE,
        symbol_raw="BTCUSDT",
        price="50000.50",
        quantity="0.001",
        trade_id="123456",
        trade_time=1700000000000,
        is_buyer_maker=False,
    )


@pytest.fixture
def sample_trade() -> TradeEvent:
    return TradeEvent(
        venue=Venue.BINANCE,
        symbol=Symbol.BTCUSDT,
        price=Decimal("50000.50"),
        quantity=Decimal("0.001"),
        side=Side.BUY,
        trade_time=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        venue_trade_id="123456",
    )


@pytest.fixture
def sample_orderbook() -> OrderbookSnapshotEvent:
    return OrderbookSnapshotEvent(
        venue=Venue.BINANCE,
        symbol=Symbol.BTCUSDT,
        bids=[
            OrderbookLevel(price=Decimal("50000"), quantity=Decimal("1.0")),
            OrderbookLevel(price=Decimal("49999"), quantity=Decimal("2.0")),
        ],
        asks=[
            OrderbookLevel(price=Decimal("50001"), quantity=Decimal("0.5")),
            OrderbookLevel(price=Decimal("50002"), quantity=Decimal("1.5")),
        ],
        sequence=1000,
        snapshot_time=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_feature_vector() -> FeatureVector:
    return FeatureVector(
        symbol=Symbol.BTCUSDT,
        window_start=datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC),
        window_end=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        rsi=45.0,
        ema_fast=50100.0,
        ema_slow=50000.0,
        vwap=50050.0,
        volume_24h=15000.0,
        price_change_pct_1h=0.005,
        bid_ask_spread_bps=2.0,
        orderbook_imbalance=0.3,
    )


@pytest.fixture
def sample_signal() -> SignalEvent:
    return SignalEvent(
        symbol=Symbol.BTCUSDT,
        action=SignalAction.OPEN_LONG,
        confidence=0.75,
        reason=SignalReason(
            primary_trigger="ema_crossover_bullish",
            supporting_factors=["rsi_oversold_recovery"],
            context_flags={},
            human_readable="Test signal",
        ),
    )


@pytest.fixture
def sample_risk_approved(sample_signal: SignalEvent) -> RiskAssessmentEvent:
    return RiskAssessmentEvent(
        signal_id=sample_signal.event_id,
        symbol=Symbol.BTCUSDT,
        decision=RiskDecision.APPROVED,
        reason="All risk checks passed",
        checks_performed=[
            RiskCheckResult(check_name="test_check", passed=True),
        ],
    )


@pytest.fixture
def sample_sized_order(sample_signal: SignalEvent) -> SizedOrderEvent:
    return SizedOrderEvent(
        signal_id=sample_signal.event_id,
        symbol=Symbol.BTCUSDT,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.001"),
        notional_usd=Decimal("50.00"),
        leverage=1,
        reason="Test order",
    )


@pytest.fixture
def default_settings() -> CTESettings:
    return CTESettings()


@pytest.fixture
def risk_settings() -> RiskSettings:
    return RiskSettings()


@pytest.fixture
def signal_settings() -> SignalSettings:
    return SignalSettings()


@pytest.fixture
def feature_settings() -> FeatureSettings:
    return FeatureSettings()


@pytest.fixture
def exit_settings() -> ExitSettings:
    return ExitSettings()


@pytest.fixture
def sizing_settings() -> SizingSettings:
    return SizingSettings()


@pytest.fixture
def execution_settings() -> ExecutionSettings:
    return ExecutionSettings()
