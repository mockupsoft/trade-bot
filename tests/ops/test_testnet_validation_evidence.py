"""Tests for testnet validation evidence helpers."""

from __future__ import annotations

import json
from cte.ops.testnet_validation_evidence import (
    check_foreign_venue_startup,
    check_paper_status_reconciliation,
    parse_structlog_json_lines,
    verify_entry_instrumentation_events,
    verify_local_qty_matches_attempts,
)


def _chain(symbol: str, vid: str, n_polls: int, *, fail_last: bool = False) -> list[dict]:
    out: list[dict] = []
    for i in range(n_polls):
        fc = i == n_polls - 1 and not fail_last
        out.append(
            {
                "event": "testnet_entry_order_poll",
                "symbol": symbol,
                "venue_order_id": vid,
                "fill_complete": fc,
                "terminal_failure": False,
            }
        )
    out.append(
        {
            "event": "testnet_entry_mirror_open_attempt",
            "symbol": symbol,
            "venue_order_id": vid,
            "requested_qty": "0.14",
            "filled_qty": "0.14",
            "mirror_open_called": True,
        }
    )
    out.append(
        {
            "event": "testnet_entry_mirror_opened",
            "symbol": symbol,
            "venue_order_id": vid,
            "local_qty": "0.14",
            "paper_position_created": True,
        }
    )
    return out


def test_parse_and_verify_three_entries() -> None:
    ev: list[dict] = []
    for k in range(3):
        ev.extend(_chain("BNBUSDT", f"v{k}", 2))
    text = "\n".join(json.dumps(x) for x in ev)
    parsed = parse_structlog_json_lines(text)
    rep = verify_entry_instrumentation_events(parsed)
    assert rep.successful_entries == 3
    assert not any(c.errors for c in rep.chains)
    assert not verify_local_qty_matches_attempts(rep.chains)


def test_verify_fails_on_mirror_failed() -> None:
    ev = _chain("BNBUSDT", "v1", 2)
    ev[-1] = {
        "event": "testnet_entry_mirror_failed",
        "symbol": "BNBUSDT",
        "venue_order_id": "v1",
        "paper_position_created": False,
    }
    rep = verify_entry_instrumentation_events(ev)
    assert any("mirror_failed" in e for c in rep.chains for e in c.errors)


def test_recon_bot_symbol_mismatch() -> None:
    st = {
        "reconciliation": {
            "last": {
                "status": "mismatch",
                "persistent_details": [
                    {"symbol": "BNBUSDT", "type": "quantity_mismatch", "detail": "x"},
                ],
            }
        }
    }
    errs = check_paper_status_reconciliation(st, {"BNBUSDT"})
    assert errs


def test_foreign_venue_startup() -> None:
    assert check_foreign_venue_startup(
        {"reconciliation": {"last": {"reason": "foreign_venue_positions"}}}
    )


def test_requested_vs_local_decimal() -> None:
    from cte.ops.testnet_validation_evidence import requested_vs_local_ok

    assert requested_vs_local_ok("0.14", "0.14")
    assert not requested_vs_local_ok("0.14", "0.07")
