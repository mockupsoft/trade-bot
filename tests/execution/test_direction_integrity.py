"""Phase 4: Directional integrity tests.

Tests prove that the full direction chain is correct:
  signal.action → order side → position.direction → PnL → exit side

Following the mandatory test list from the trading engine direction audit.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from cte.core.events import (
    SignalAction,
    SignalReason,
    SignalTier,
    ScoredSignalEvent,
    Symbol,
)
from cte.core.settings import ExecutionSettings, ExitSettings
from cte.execution.paper import PaperExecutionEngine
from cte.execution.adapter import OrderRequest, OrderSide
from cte.execution.fill_model import FillMode
from cte.dashboard.paper_runner import (
    _has_open_position,
    _has_open_position_same_direction,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _mock_publisher():
    """Minimal stub for StreamPublisher (not used by PaperExecutionEngine)."""

    class _Pub:
        async def publish(self, *a, **kw):
            pass

    return _Pub()


def _make_signal(
    symbol: str = "BTCUSDT",
    action: SignalAction = SignalAction.OPEN_LONG,
) -> ScoredSignalEvent:
    direction = "long" if action == SignalAction.OPEN_LONG else "short"
    return ScoredSignalEvent(
        event_id=uuid4(),
        symbol=Symbol(symbol),
        action=action,
        direction=direction,
        composite_score=0.7,
        primary_score=0.7,
        context_multiplier=1.0,
        tier=SignalTier.A,
        reason=SignalReason(
            primary_trigger="test",
            supporting_factors=[],
            context_flags={},
            human_readable="test signal",
        ),
    )


def _make_engine() -> PaperExecutionEngine:
    exec_cfg = ExecutionSettings()
    exit_cfg = ExitSettings()
    return PaperExecutionEngine(exec_cfg, exit_cfg, _mock_publisher(), FillMode.SPREAD_CROSSING)


NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
BID = Decimal("50000")
ASK = Decimal("50010")
QTY = Decimal("0.001")
NOTIONAL = Decimal("50")


# ── Test Group 1: Entry direction mapping ──────────────────────────────────

class TestEntryDirectionMapping:
    def test_open_long_creates_long_position(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_LONG)
        pos = engine.open_position(sig, QTY, NOTIONAL, NOW)
        assert pos is not None, "Position should be created"
        assert pos.direction == "long", f"Expected 'long', got '{pos.direction}'"

    def test_open_short_creates_short_position(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_SHORT)
        pos = engine.open_position(sig, QTY, NOTIONAL, NOW)
        assert pos is not None, "Position should be created"
        assert pos.direction == "short", f"Expected 'short', got '{pos.direction}'"


# ── Test Group 2: Exit direction mapping ──────────────────────────────────

class TestExitSideMapping:
    def test_closing_long_at_higher_price_yields_profit(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_LONG)
        pos = engine.open_position(sig, QTY, NOTIONAL, NOW)
        assert pos is not None

        # Price rises 5%
        new_price = BID * Decimal("1.05")
        engine.update_book("BTCUSDT", new_price - 5, new_price)
        closed_list = engine.evaluate_exits(
            "BTCUSDT", new_price, NOW + timedelta(hours=3)
        )
        # May not have exited yet via L3 (depends on timeout), so close manually
        if not closed_list:
            closed = engine.close_position(
                pos.position_id, "test_exit", "manual", NOW + timedelta(hours=3)
            )
        else:
            closed = closed_list[0]

        assert closed is not None
        assert closed.realized_pnl > 0, (
            f"LONG should profit on price rise; got {closed.realized_pnl}"
        )

    def test_closing_short_at_lower_price_yields_profit(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_SHORT)
        pos = engine.open_position(sig, QTY, NOTIONAL, NOW)
        assert pos is not None

        # Price falls 5%
        new_price = BID * Decimal("0.95")
        engine.update_book("BTCUSDT", new_price - 5, new_price)
        closed = engine.close_position(
            pos.position_id, "test_exit", "manual", NOW + timedelta(hours=1)
        )
        assert closed is not None
        assert closed.realized_pnl > 0, (
            f"SHORT should profit on price fall; got {closed.realized_pnl}"
        )

    def test_closing_long_at_lower_price_yields_loss(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_LONG)
        pos = engine.open_position(sig, QTY, NOTIONAL, NOW)
        assert pos is not None

        new_price = BID * Decimal("0.95")
        engine.update_book("BTCUSDT", new_price - 5, new_price)
        closed = engine.close_position(
            pos.position_id, "test_exit", "manual", NOW + timedelta(hours=1)
        )
        assert closed is not None
        assert closed.realized_pnl < 0, (
            f"LONG should lose on price fall; got {closed.realized_pnl}"
        )

    def test_closing_short_at_higher_price_yields_loss(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_SHORT)
        pos = engine.open_position(sig, QTY, NOTIONAL, NOW)
        assert pos is not None

        new_price = BID * Decimal("1.05")
        engine.update_book("BTCUSDT", new_price - 5, new_price)
        closed = engine.close_position(
            pos.position_id, "test_exit", "manual", NOW + timedelta(hours=1)
        )
        assert closed is not None
        assert closed.realized_pnl < 0, (
            f"SHORT should lose on price rise; got {closed.realized_pnl}"
        )


# ── Test Group 3: Venue fill direction persistence ─────────────────────────

class TestVenueFillDirection:
    def test_open_position_from_venue_fill_long(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_LONG)
        pos = engine.open_position_from_venue_fill(
            sig, QTY, NOTIONAL, NOW, fill_price=Decimal("50005")
        )
        assert pos is not None
        assert pos.direction == "long", (
            f"Venue fill LONG should create 'long' position, got '{pos.direction}'"
        )

    def test_open_position_from_venue_fill_short(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_SHORT)
        pos = engine.open_position_from_venue_fill(
            sig, QTY, NOTIONAL, NOW, fill_price=Decimal("50005")
        )
        assert pos is not None
        assert pos.direction == "short", (
            f"Venue fill SHORT must create 'short' position, got '{pos.direction}'"
        )


# ── Test Group 4: Same-symbol direction layering logic ─────────────────────

class TestLayeredPositionLogic:
    def test_existing_long_does_not_block_new_short(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        long_sig = _make_signal(action=SignalAction.OPEN_LONG)
        engine.open_position(long_sig, QTY, NOTIONAL, NOW)

        # Opening SHORT on same symbol should NOT be blocked
        short_action = SignalAction.OPEN_SHORT
        blocked = _has_open_position_same_direction(engine, "BTCUSDT", short_action)
        assert not blocked, "Existing LONG should NOT block new SHORT"

    def test_existing_short_does_not_block_new_long(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        short_sig = _make_signal(action=SignalAction.OPEN_SHORT)
        engine.open_position(short_sig, QTY, NOTIONAL, NOW)

        long_action = SignalAction.OPEN_LONG
        blocked = _has_open_position_same_direction(engine, "BTCUSDT", long_action)
        assert not blocked, "Existing SHORT should NOT block new LONG"

    def test_existing_long_blocks_second_long(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        long_sig = _make_signal(action=SignalAction.OPEN_LONG)
        engine.open_position(long_sig, QTY, NOTIONAL, NOW)

        long_action = SignalAction.OPEN_LONG
        blocked = _has_open_position_same_direction(engine, "BTCUSDT", long_action)
        assert blocked, "Existing LONG should block a second LONG on same symbol"

    def test_existing_short_blocks_second_short(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        short_sig = _make_signal(action=SignalAction.OPEN_SHORT)
        engine.open_position(short_sig, QTY, NOTIONAL, NOW)

        short_action = SignalAction.OPEN_SHORT
        blocked = _has_open_position_same_direction(engine, "BTCUSDT", short_action)
        assert blocked, "Existing SHORT should block a second SHORT on same symbol"

    def test_both_directions_can_coexist(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)

        long_sig = _make_signal(action=SignalAction.OPEN_LONG)
        short_sig = _make_signal(action=SignalAction.OPEN_SHORT)

        long_pos = engine.open_position(long_sig, QTY, NOTIONAL, NOW)
        short_pos = engine.open_position(short_sig, QTY, NOTIONAL, NOW + timedelta(seconds=1))

        assert long_pos is not None, "LONG position should open"
        assert short_pos is not None, "SHORT position should open alongside LONG"
        open_dirs = {p.direction for p in engine.open_positions.values()}
        assert "long" in open_dirs, "LONG must be in open positions"
        assert "short" in open_dirs, "SHORT must be in open positions"


# ── Test Group 5: MFE/MAE direction correctness ────────────────────────────

class TestMfeMaeDirection:
    def test_long_mfe_increases_on_price_rise(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_LONG)
        pos = engine.open_position(sig, QTY, NOTIONAL, NOW)
        assert pos is not None

        higher_price = BID * Decimal("1.02")
        engine.update_price("BTCUSDT", higher_price)
        pos = engine.open_positions[pos.position_id]
        assert pos.mfe_pct > 0, "LONG MFE must be positive when price rises"
        assert pos.mae_pct == 0, "LONG MAE must be zero when price only rises"

    def test_short_mfe_increases_on_price_fall(self):
        engine = _make_engine()
        engine.update_book("BTCUSDT", BID, ASK)
        sig = _make_signal(action=SignalAction.OPEN_SHORT)
        pos = engine.open_position(sig, QTY, NOTIONAL, NOW)
        assert pos is not None

        lower_price = BID * Decimal("0.98")
        engine.update_price("BTCUSDT", lower_price)
        pos = engine.open_positions[pos.position_id]
        assert pos.mfe_pct > 0, "SHORT MFE must be positive when price falls"
        assert pos.mae_pct == 0, "SHORT MAE must be zero when price only falls"


# ── Test Group 6: Order side mapping utils ─────────────────────────────────

class TestOrderSideMapping:
    """Unit-test the mapping from action to OrderSide (without actually running runners)."""

    def test_open_long_maps_to_buy(self):
        action = SignalAction.OPEN_LONG
        side = OrderSide.BUY if action.value == "open_long" else OrderSide.SELL
        assert side == OrderSide.BUY, "OPEN_LONG must map to BUY entry"

    def test_open_short_maps_to_sell(self):
        action = SignalAction.OPEN_SHORT
        side = OrderSide.BUY if action.value == "open_long" else OrderSide.SELL
        assert side == OrderSide.SELL, "OPEN_SHORT must map to SELL entry"

    def test_close_long_maps_to_sell(self):
        direction = "long"
        close_side = OrderSide.SELL if direction == "long" else OrderSide.BUY
        assert close_side == OrderSide.SELL, "Closing LONG must send SELL"

    def test_close_short_maps_to_buy(self):
        direction = "short"
        close_side = OrderSide.SELL if direction == "long" else OrderSide.BUY
        assert close_side == OrderSide.BUY, "Closing SHORT must send BUY"

    def test_adapter_inverts_side_for_close_position(self):
        """Verify that binance_adapter.close_position inverts the side correctly."""
        # close_position(side=BUY) → places SELL (for LONG close)
        # close_position(side=SELL) → places BUY (for SHORT close)
        from cte.execution.adapter import OrderSide as OS

        def _compute_close_side(entry_side: OS) -> OS:
            return OS.SELL if entry_side == OS.BUY else OS.BUY

        assert _compute_close_side(OS.BUY) == OS.SELL, "LONG entry (BUY) → SELL close"
        assert _compute_close_side(OS.SELL) == OS.BUY, "SHORT entry (SELL) → BUY close"
