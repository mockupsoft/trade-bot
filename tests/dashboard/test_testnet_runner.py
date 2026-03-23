"""Unit tests for dashboard testnet runner helpers (no live exchange)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cte.core.settings import CTESettings, ExecutionMode
from cte.dashboard.testnet_runner import (
    DashboardTestnetRunner,
    _allow_foreign_positions,
    _entry_fill_complete,
    _entry_order_terminal_failure,
    _entry_qty_matches_request,
    _entry_step_overshoot_pct,
    _recon_qty_tolerance_pct,
    _round_down_qty,
    _round_up_qty,
    venue_loop_enabled_for_settings,
)
from cte.execution.adapter import OrderResult, OrderSide, VenueOrderStatus
from cte.ops.kill_switch import OperationsController


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
    monkeypatch.setenv("CTE_BINANCE_TESTNET_API_KEY", "dummy")
    monkeypatch.setenv("CTE_BINANCE_TESTNET_API_SECRET", "dummy")
    assert not venue_loop_enabled_for_settings(
        _minimal_settings(execution_mode=ExecutionMode.PAPER)
    )
    assert venue_loop_enabled_for_settings(_minimal_settings(execution_mode=ExecutionMode.TESTNET))


def test_venue_loop_respects_cte_dashboard_venue_loop_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTE_DASHBOARD_VENUE_LOOP", "0")
    assert not venue_loop_enabled_for_settings(
        _minimal_settings(execution_mode=ExecutionMode.TESTNET)
    )


@pytest.mark.parametrize(
    ("symbol", "q", "expected"),
    [
        ("BTCUSDT", Decimal("0.0199"), Decimal("0.019")),
        ("ETHUSDT", Decimal("0.0199"), Decimal("0.019")),
        ("DOGEUSDT", Decimal("99.99"), Decimal("99")),
    ],
)
def test_round_down_qty(symbol: str, q: Decimal, expected: Decimal) -> None:
    assert _round_down_qty(symbol, q) == expected


@pytest.mark.parametrize(
    ("symbol", "q", "expected"),
    [
        ("BNBUSDT", Decimal("0.1501"), Decimal("0.16")),
        ("DOGEUSDT", Decimal("99.01"), Decimal("100")),
        ("XRPUSDT", Decimal("35.71"), Decimal("35.8")),
    ],
)
def test_round_up_qty(symbol: str, q: Decimal, expected: Decimal) -> None:
    assert _round_up_qty(symbol, q) == expected


def test_entry_step_overshoot_pct_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CTE_ENTRY_STEP_OVERSHOOT_PCT", raising=False)
    assert _entry_step_overshoot_pct() == Decimal("0.01")


def test_entry_step_overshoot_pct_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTE_ENTRY_STEP_OVERSHOOT_PCT", "0.50")
    assert _entry_step_overshoot_pct() == Decimal("0.50")


def test_entry_qty_matches_request_bnb_step() -> None:
    assert _entry_qty_matches_request("BNBUSDT", Decimal("0.14"), Decimal("0.14"))
    assert not _entry_qty_matches_request("BNBUSDT", Decimal("0.07"), Decimal("0.14"))


def test_round_down_qty_uses_bybit_steps() -> None:
    assert _round_down_qty("SOLUSDT", Decimal("1.149"), "bybit_demo") == Decimal("1.1")
    assert _round_down_qty("ETHUSDT", Decimal("0.048"), "bybit_demo") == Decimal("0.04")


def test_entry_fill_complete_bybit_filled() -> None:
    orez = OrderResult(
        client_order_id="c1",
        venue_order_id="v1",
        symbol="BNBUSDT",
        side=OrderSide.BUY,
        status=VenueOrderStatus.FILLED,
        filled_quantity=Decimal("0.14"),
        raw_response={"orderStatus": "Filled", "cumExecQty": "0.14"},
    )
    assert _entry_fill_complete("bybit_demo", "BNBUSDT", Decimal("0.14"), orez)


def test_entry_fill_complete_bybit_partial_until_qty_match() -> None:
    orez = OrderResult(
        client_order_id="c1",
        venue_order_id="v1",
        symbol="BNBUSDT",
        side=OrderSide.BUY,
        status=VenueOrderStatus.PARTIAL,
        filled_quantity=Decimal("0.14"),
        raw_response={"orderStatus": "PartiallyFilled", "cumExecQty": "0.14"},
    )
    assert _entry_fill_complete("bybit_demo", "BNBUSDT", Decimal("0.14"), orez)


def test_entry_fill_complete_bybit_partial_incomplete() -> None:
    orez = OrderResult(
        client_order_id="c1",
        venue_order_id="v1",
        symbol="BNBUSDT",
        side=OrderSide.BUY,
        status=VenueOrderStatus.PARTIAL,
        filled_quantity=Decimal("0.07"),
        raw_response={"orderStatus": "PartiallyFilled", "cumExecQty": "0.07"},
    )
    assert not _entry_fill_complete("bybit_demo", "BNBUSDT", Decimal("0.14"), orez)


def test_entry_fill_complete_binance_filled() -> None:
    orez = OrderResult(
        client_order_id="c1",
        venue_order_id="v1",
        symbol="BNBUSDT",
        side=OrderSide.BUY,
        status=VenueOrderStatus.FILLED,
        filled_quantity=Decimal("0.14"),
        raw_response={"status": "FILLED", "executedQty": "0.14"},
    )
    assert _entry_fill_complete("binance_testnet", "BNBUSDT", Decimal("0.14"), orez)


def test_entry_fill_complete_binance_qty_match_when_status_unmapped() -> None:
    """Full cumExecQty still counts when REST status string is missing or odd."""
    orez = OrderResult(
        client_order_id="c1",
        venue_order_id="v1",
        symbol="BNBUSDT",
        side=OrderSide.BUY,
        status=VenueOrderStatus.SUBMITTED,
        filled_quantity=Decimal("0.14"),
        raw_response={"status": "", "executedQty": "0.14"},
    )
    assert _entry_fill_complete("binance_testnet", "BNBUSDT", Decimal("0.14"), orez)


def test_recon_qty_tolerance_strict_validation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTE_RECON_STRICT_VALIDATION", "1")
    monkeypatch.delenv("CTE_RECON_QTY_TOLERANCE_PCT", raising=False)
    assert _recon_qty_tolerance_pct() == 0.0


def test_recon_qty_tolerance_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CTE_RECON_STRICT_VALIDATION", raising=False)
    monkeypatch.setenv("CTE_RECON_QTY_TOLERANCE_PCT", "0.005")
    assert _recon_qty_tolerance_pct() == 0.005


def test_allow_foreign_positions_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CTE_ALLOW_FOREIGN_POSITIONS", raising=False)
    assert not _allow_foreign_positions()


def test_allow_foreign_positions_override_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTE_ALLOW_FOREIGN_POSITIONS", "1")
    assert _allow_foreign_positions()


def test_status_exposes_foreign_detection_and_validation_block_flags() -> None:
    runner = DashboardTestnetRunner(
        settings=_minimal_settings(execution_mode=ExecutionMode.TESTNET),
        market_feed=lambda: None,
        analytics_engine=lambda: None,
        ops_controller=lambda: OperationsController(),
        symbols=("BTCUSDT",),
    )
    st = runner.status_dict()
    assert st["foreign_venue_detected"] is False
    assert st["validation_blocked"] is False
    assert "post_exit_cooldown" in st
    assert st["post_exit_cooldown"]["hard_risk_sec"] >= st["post_exit_cooldown"]["default_sec"]


def test_reentry_cooldown_policy_hard_risk_longer() -> None:
    runner = DashboardTestnetRunner(
        settings=_minimal_settings(execution_mode=ExecutionMode.TESTNET),
        market_feed=lambda: None,
        analytics_engine=lambda: None,
        ops_controller=lambda: OperationsController(),
        symbols=("BTCUSDT",),
    )
    assert runner._reentry_cooldown_for_reason(
        "spread_blowout"
    ) >= runner._reentry_cooldown_for_reason("thesis_failure")


def test_entry_order_terminal_failure_binance_canceled() -> None:
    orez = OrderResult(
        client_order_id="c1",
        venue_order_id="v1",
        symbol="BNBUSDT",
        side=OrderSide.BUY,
        status=VenueOrderStatus.CANCELLED,
        filled_quantity=Decimal("0"),
        raw_response={"status": "CANCELED"},
    )
    assert _entry_order_terminal_failure("binance_testnet", orez)
    assert not _entry_fill_complete("binance_testnet", "BNBUSDT", Decimal("0.14"), orez)
