"""Tests for the position sizing engine."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from cte.core.events import (
    RiskAssessmentEvent,
    RiskCheckResult,
    RiskDecision,
    SignalAction,
    SignalEvent,
    SignalReason,
    Symbol,
)
from cte.core.settings import SizingSettings
from cte.core.streams import StreamPublisher
from cte.sizing.engine import SizingEngine


@pytest.fixture
def publisher():
    return AsyncMock(spec=StreamPublisher, publish=AsyncMock(return_value="x"))


@pytest.fixture
def engine(publisher):
    settings = SizingSettings(
        fixed_fraction_pct=0.02, min_order_usd=10.0, max_order_usd=1000.0
    )
    return SizingEngine(settings, publisher, portfolio_value=Decimal("10000"))


def _signal(confidence=0.75, action=SignalAction.OPEN_LONG):
    return SignalEvent(
        symbol=Symbol.BTCUSDT, action=action, confidence=confidence,
        reason=SignalReason(primary_trigger="test", human_readable="Test"),
    )


def _approved(signal):
    return RiskAssessmentEvent(
        signal_id=signal.event_id, symbol=Symbol.BTCUSDT,
        decision=RiskDecision.APPROVED, reason="OK",
        checks_performed=[RiskCheckResult(check_name="test", passed=True)],
    )


def _rejected(signal):
    return RiskAssessmentEvent(
        signal_id=signal.event_id, symbol=Symbol.BTCUSDT,
        decision=RiskDecision.REJECTED, reason="Blocked",
    )


class TestSizingBasic:
    @pytest.mark.asyncio
    async def test_approved_signal_produces_order(self, engine):
        sig = _signal(confidence=0.75)
        order = await engine.size_order(sig, _approved(sig), Decimal("50000"))
        assert order is not None
        assert order.quantity > 0
        assert order.notional_usd > 0

    @pytest.mark.asyncio
    async def test_rejected_signal_returns_none(self, engine):
        sig = _signal()
        order = await engine.size_order(sig, _rejected(sig), Decimal("50000"))
        assert order is None

    @pytest.mark.asyncio
    async def test_hold_action_returns_none(self, engine):
        sig = _signal(action=SignalAction.HOLD)
        order = await engine.size_order(sig, _approved(sig), Decimal("50000"))
        assert order is None

    @pytest.mark.asyncio
    async def test_zero_price_returns_none(self, engine):
        sig = _signal()
        order = await engine.size_order(sig, _approved(sig), Decimal("0"))
        assert order is None


class TestSizingBounds:
    @pytest.mark.asyncio
    async def test_min_order_enforced(self, engine):
        sig = _signal(confidence=0.01)  # very low confidence → tiny notional
        order = await engine.size_order(sig, _approved(sig), Decimal("50000"))
        assert order is not None
        assert order.notional_usd >= Decimal("10")

    @pytest.mark.asyncio
    async def test_max_order_enforced(self, engine):
        sig = _signal(confidence=1.0)
        order = await engine.size_order(sig, _approved(sig), Decimal("50000"))
        assert order is not None
        assert order.notional_usd <= Decimal("1000")

    @pytest.mark.asyncio
    async def test_higher_confidence_larger_position(self, engine):
        sig_low = _signal(confidence=0.5)
        sig_high = _signal(confidence=0.9)
        o_low = await engine.size_order(sig_low, _approved(sig_low), Decimal("50000"))
        o_high = await engine.size_order(sig_high, _approved(sig_high), Decimal("50000"))
        assert o_high.notional_usd >= o_low.notional_usd


class TestKellySizing:
    @pytest.mark.asyncio
    async def test_kelly_mode(self, publisher):
        settings = SizingSettings(method="kelly", kelly_half=True,
                                   min_order_usd=10.0, max_order_usd=1000.0)
        engine = SizingEngine(settings, publisher, Decimal("10000"))
        sig = _signal(confidence=0.75)
        order = await engine.size_order(sig, _approved(sig), Decimal("50000"))
        assert order is not None
        assert order.quantity > 0
