"""Tests for the PaperExecutionEngine — full integration."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from cte.core.events import (
    ScoredSignalEvent,
    SignalAction,
    SignalReason,
    SignalTier,
    Symbol,
)
from cte.core.settings import ExecutionSettings, ExitSettings
from cte.core.streams import StreamPublisher
from cte.execution.fill_model import FillMode
from cte.execution.paper import PaperExecutionEngine
from cte.execution.position import PositionStatus


@pytest.fixture
def publisher():
    return AsyncMock(spec=StreamPublisher, publish=AsyncMock(return_value="x"))


@pytest.fixture
def exec_settings():
    return ExecutionSettings(slippage_bps=5, fill_delay_ms=100)


@pytest.fixture
def exit_settings():
    return ExitSettings(stop_loss_pct=0.02, take_profit_pct=0.03, trailing_stop_pct=0.015,
                        max_hold_minutes=1440)


@pytest.fixture
def engine(exec_settings, exit_settings, publisher):
    eng = PaperExecutionEngine(exec_settings, exit_settings, publisher)
    eng.update_book("BTCUSDT", Decimal("64999"), Decimal("65001"))
    return eng


def _signal(symbol="BTCUSDT", tier="A", score=0.82):
    return ScoredSignalEvent(
        symbol=Symbol(symbol),
        action=SignalAction.OPEN_LONG,
        composite_score=score,
        primary_score=score,
        context_multiplier=1.0,
        tier=SignalTier(tier),
        reason=SignalReason(
            primary_trigger="composite_score_A",
            human_readable="Test signal",
        ),
    )


def _t(second=0, minute=0):
    return datetime(2024, 1, 1, 12, minute, second, tzinfo=UTC)


class TestOpenPosition:
    def test_creates_position(self, engine):
        signal = _signal()
        pos = engine.open_position(signal, Decimal("0.01"), Decimal("650"), _t())
        assert pos is not None
        assert pos.status == PositionStatus.OPEN
        assert pos.symbol == "BTCUSDT"

    def test_fills_above_ask_for_buy(self, engine):
        pos = engine.open_position(_signal(), Decimal("0.01"), Decimal("650"), _t())
        # Ask = 65001, slippage = 5 bps → fill > 65001
        assert pos.fill_price > Decimal("65001")

    def test_carries_signal_provenance(self, engine):
        pos = engine.open_position(_signal(tier="B", score=0.61), Decimal("0.01"), Decimal("650"), _t())
        assert pos.signal_tier == "B"
        assert pos.composite_score == 0.61

    def test_no_book_returns_none(self, exec_settings, exit_settings, publisher):
        eng = PaperExecutionEngine(exec_settings, exit_settings, publisher)
        # No book update → no fill
        pos = eng.open_position(_signal(), Decimal("0.01"), Decimal("650"), _t())
        assert pos is None

    def test_entry_latency_modeled(self, engine):
        signal_time = _t(second=0)
        pos = engine.open_position(_signal(), Decimal("0.01"), Decimal("650"), signal_time)
        # fill_delay_ms=100 → 0.1s latency
        assert pos.entry_latency_ms == 100

    def test_stop_distance_calculated(self, engine):
        pos = engine.open_position(_signal(), Decimal("1"), Decimal("65000"), _t())
        # stop_loss_pct=0.02, entry ≈ 65001+slip, qty=1
        assert pos.stop_distance_usd > 0

    def test_tracks_open_positions(self, engine):
        engine.open_position(_signal(), Decimal("0.01"), Decimal("650"), _t())
        assert len(engine.open_positions) == 1


class TestClosePosition:
    def test_close_at_bid(self, engine):
        pos = engine.open_position(_signal(), Decimal("0.01"), Decimal("650"), _t())
        closed = engine.close_position(pos.position_id, "take_profit", "Test", _t(second=30))
        assert closed is not None
        assert closed.status == PositionStatus.CLOSED
        # Exit fill should be below bid (65000 - slippage)
        assert closed.exit_price < Decimal("65000")

    def test_realized_pnl_calculated(self, engine):
        pos = engine.open_position(_signal(), Decimal("1"), Decimal("65000"), _t())
        # Move price up
        engine.update_book("BTCUSDT", Decimal("66000"), Decimal("66002"))
        closed = engine.close_position(pos.position_id, "take_profit", "Test", _t(second=30))
        assert closed.realized_pnl > 0

    def test_moves_to_closed_list(self, engine):
        pos = engine.open_position(_signal(), Decimal("0.01"), Decimal("650"), _t())
        engine.close_position(pos.position_id, "manual", "Test", _t(second=30))
        assert len(engine.open_positions) == 0
        assert len(engine.closed_positions) == 1

    def test_close_nonexistent_returns_none(self, engine):
        from uuid import uuid4
        result = engine.close_position(uuid4(), "test", "Test", _t())
        assert result is None


class TestExitEvaluation:
    def test_stop_loss_triggered(self, engine):
        pos = engine.open_position(_signal(), Decimal("1"), Decimal("65000"), _t())
        entry = pos.entry_price
        # Drop 3% below entry → exceeds 2% stop
        drop_price = entry * Decimal("0.97")
        engine.update_book("BTCUSDT", drop_price - 1, drop_price + 1)
        closed = engine.evaluate_exits("BTCUSDT", drop_price, _t(second=30))
        assert len(closed) == 1
        assert closed[0].exit_reason == "stop_loss"

    def test_take_profit_triggered(self, engine):
        pos = engine.open_position(_signal(), Decimal("1"), Decimal("65000"), _t())
        entry = pos.entry_price
        # Rise 4% above entry → exceeds 3% TP
        rise_price = entry * Decimal("1.04")
        engine.update_book("BTCUSDT", rise_price - 1, rise_price + 1)
        closed = engine.evaluate_exits("BTCUSDT", rise_price, _t(second=30))
        assert len(closed) == 1
        assert closed[0].exit_reason == "take_profit"

    def test_timeout_triggered(self, engine):
        engine.open_position(_signal(), Decimal("1"), Decimal("65000"), _t())
        # Time far in the future (>24h)
        future = _t() + __import__("datetime").timedelta(hours=25)
        closed = engine.evaluate_exits("BTCUSDT", Decimal("65000"), future)
        assert len(closed) == 1
        assert closed[0].exit_reason == "timeout"

    def test_no_exit_when_in_range(self, engine):
        engine.open_position(_signal(), Decimal("1"), Decimal("65000"), _t())
        # Price barely moved → no exit
        closed = engine.evaluate_exits("BTCUSDT", Decimal("65050"), _t(second=10))
        assert len(closed) == 0

    def test_mfe_mae_tracked_through_lifecycle(self, engine):
        pos = engine.open_position(_signal(), Decimal("1"), Decimal("65000"), _t())
        entry = pos.entry_price

        # Price goes up
        up_price = entry * Decimal("1.015")
        engine.update_book("BTCUSDT", up_price - 1, up_price + 1)
        engine.evaluate_exits("BTCUSDT", up_price, _t(second=5))

        # Price comes back down (no exit yet since trailing stop requires profit)
        down_price = entry * Decimal("0.995")
        engine.update_book("BTCUSDT", down_price - 1, down_price + 1)
        engine.evaluate_exits("BTCUSDT", down_price, _t(second=10))

        pos_ref = engine.open_positions[pos.position_id]
        assert pos_ref.mfe_pct > 0
        assert pos_ref.mae_pct > 0


class TestDeterministicReplay:
    def test_same_sequence_same_results(self, exec_settings, exit_settings, publisher):
        """Run the same event sequence twice and verify identical results."""
        results = []

        for _ in range(2):
            eng = PaperExecutionEngine(exec_settings, exit_settings, publisher)
            eng.update_book("BTCUSDT", Decimal("64999"), Decimal("65001"))

            sig = _signal()
            pos = eng.open_position(sig, Decimal("1"), Decimal("65000"), _t())

            # Price sequence
            for i, price in enumerate([65100, 65200, 65050, 64800]):
                p = Decimal(str(price))
                eng.update_book("BTCUSDT", p - 1, p + 1)
                eng.evaluate_exits("BTCUSDT", p, _t(second=i + 1))

            results.append({
                "fill_price": pos.fill_price,
                "mfe_pct": pos.mfe_pct,
                "mae_pct": pos.mae_pct,
                "status": pos.status.value,
            })

        assert results[0] == results[1]


class TestVWAPMode:
    def test_vwap_fill(self, exit_settings, publisher):
        from cte.execution.fill_model import BookLevel
        settings = ExecutionSettings(slippage_bps=0, fill_delay_ms=50)
        eng = PaperExecutionEngine(settings, exit_settings, publisher, FillMode.VWAP_DEPTH)

        levels = [
            BookLevel(price=Decimal("65001"), quantity=Decimal("5")),
            BookLevel(price=Decimal("65010"), quantity=Decimal("5")),
        ]
        eng.update_book("BTCUSDT", Decimal("64999"), Decimal("65001"),
                        bid_levels=[], ask_levels=levels)

        pos = eng.open_position(_signal(), Decimal("10"), Decimal("650000"), _t())
        assert pos is not None
        # VWAP of 5x65001 + 5x65010 = 65005.5 with 0 slip
        assert pos.fill_price == Decimal("65005.50")
        assert pos.fill_model_used == "vwap_depth"
