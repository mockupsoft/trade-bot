"""Unit tests for dashboard testnet runner helpers (no live exchange)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cte.core.settings import CTESettings, ExecutionMode
from cte.dashboard.testnet_runner import _round_down_qty, venue_loop_enabled_for_settings


def _minimal_settings(*, execution_mode: ExecutionMode) -> CTESettings:
    s = CTESettings()
    return s.model_copy(
        update={
            "engine": s.engine.model_copy(update={"mode": "demo"}),
            "execution": s.execution.model_copy(update={"mode": execution_mode}),
        },
    )


def test_venue_loop_enabled_only_for_testnet_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTE_DASHBOARD_PAPER_LOOP", "1")
    assert not venue_loop_enabled_for_settings(_minimal_settings(execution_mode=ExecutionMode.PAPER))
    assert venue_loop_enabled_for_settings(_minimal_settings(execution_mode=ExecutionMode.TESTNET))


def test_venue_loop_respects_cte_dashboard_venue_loop_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTE_DASHBOARD_VENUE_LOOP", "0")
    assert not venue_loop_enabled_for_settings(_minimal_settings(execution_mode=ExecutionMode.TESTNET))


@pytest.mark.parametrize(
    ("symbol", "q", "expected"),
    [
        ("BTCUSDT", Decimal("0.0199"), Decimal("0.019")),
        ("DOGEUSDT", Decimal("99.99"), Decimal("99")),
    ],
)
def test_round_down_qty(symbol: str, q: Decimal, expected: Decimal) -> None:
    assert _round_down_qty(symbol, q) == expected
