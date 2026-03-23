"""Venue partial exit accounting on PaperExecutionEngine."""
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
from cte.execution.paper import PaperExecutionEngine
from cte.execution.position import PositionStatus


@pytest.fixture
def publisher():
    return AsyncMock(spec=StreamPublisher, publish=AsyncMock(return_value="x"))


@pytest.fixture
def engine(publisher):
    exec_settings = ExecutionSettings(slippage_bps=5, fill_delay_ms=100)
    exit_settings = ExitSettings(
        stop_loss_pct=0.02,
        take_profit_pct=0.03,
        trailing_stop_pct=0.015,
        max_hold_minutes=1440,
    )
    eng = PaperExecutionEngine(exec_settings, exit_settings, publisher)
    eng.update_book("BTCUSDT", Decimal("64999"), Decimal("65001"))
    return eng


def _signal():
    return ScoredSignalEvent(
        symbol=Symbol("BTCUSDT"),
        action=SignalAction.OPEN_LONG,
        direction="long",
        composite_score=0.82,
        primary_score=0.82,
        context_multiplier=1.0,
        tier=SignalTier("A"),
        reason=SignalReason(
            primary_trigger="composite_score_A",
            human_readable="Test signal",
        ),
    )


def _t(minute: int = 0) -> datetime:
    return datetime(2024, 1, 1, 12, minute, 0, tzinfo=UTC)


def test_partial_venue_exit_then_full_close(engine: PaperExecutionEngine) -> None:
    sig = _signal()
    pos = engine.open_position_from_venue_fill(
        sig,
        Decimal("1"),
        Decimal("65000"),
        _t(),
        Decimal("65000"),
        entry_fees_usd=Decimal("0"),
    )
    assert pos is not None
    pid = pos.position_id

    p1 = engine.close_position_external_fill(
        pid,
        Decimal("65100"),
        _t(minute=1),
        "take_profit",
        "slice_a",
        filled_exit_quantity=Decimal("0.4"),
    )
    assert p1 is None

    open_map = engine.open_positions
    assert pid in open_map
    rem = open_map[pid]
    assert rem.status == PositionStatus.REDUCED
    assert rem.quantity == Decimal("0.6")
    # (65100 - 65000) * 0.4
    assert rem.realized_pnl == Decimal("40")

    p2 = engine.close_position_external_fill(
        pid,
        Decimal("65200"),
        _t(minute=2),
        "take_profit",
        "final",
        filled_exit_quantity=Decimal("0.6"),
    )
    assert p2 is not None
    assert p2.status == PositionStatus.CLOSED
    assert pid not in engine.open_positions
    # 40 + (65200 - 65000) * 0.6 = 40 + 120 = 160 (no entry fee)
    assert p2.realized_pnl == Decimal("160")


def test_filled_exit_none_defaults_to_full_close(engine: PaperExecutionEngine) -> None:
    sig = _signal()
    pos = engine.open_position_from_venue_fill(
        sig,
        Decimal("0.1"),
        Decimal("6500"),
        _t(),
        Decimal("65000"),
        entry_fees_usd=Decimal("0"),
    )
    assert pos is not None
    closed = engine.close_position_external_fill(
        pos.position_id,
        Decimal("65100"),
        _t(minute=1),
        "tp",
        "x",
        filled_exit_quantity=None,
    )
    assert closed is not None
    assert closed.status == PositionStatus.CLOSED
