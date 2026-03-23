"""Pure helpers for testnet entry instrumentation and reconciliation evidence review.

Used by ``scripts/verify_testnet_entry_logs.py`` and unit tests. No I/O here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def parse_structlog_json_lines(text: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON; skip blank lines and invalid JSON."""
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


@dataclass
class EntryChainResult:
    """One entry from mirror_open_attempt through mirror_opened/failed."""

    symbol: str
    venue_order_id: str
    poll_events: list[dict[str, Any]] = field(default_factory=list)
    last_poll_fill_complete: bool | None = None
    last_poll_terminal_failure: bool | None = None
    attempt: dict[str, Any] | None = None
    outcome: str | None = None  # "opened" | "failed" | None
    opened: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class EntryInstrumentationReport:
    """Result of checking structlog events for entry-path instrumentation."""

    chains: list[EntryChainResult]
    fatal_errors: list[str] = field(default_factory=list)

    @property
    def successful_entries(self) -> int:
        return sum(1 for c in self.chains if c.outcome == "opened" and not c.errors)


def _event_name(ev: dict[str, Any]) -> str:
    e = ev.get("event")
    return str(e) if e is not None else ""


def _bool_field(ev: dict[str, Any], key: str) -> bool | None:
    v = ev.get(key)
    if v is True:
        return True
    if v is False:
        return False
    return None


def _matches_sym_vid(ev: dict[str, Any], sym: str, vid: str) -> bool:
    if str(ev.get("symbol") or "") != sym:
        return False
    got = str(ev.get("venue_order_id") or "")
    if not vid:
        return True
    if not got:
        return True
    return got == vid


def verify_entry_instrumentation_events(events: list[dict[str, Any]]) -> EntryInstrumentationReport:
    """Verify poll → fill_complete → mirror_open_attempt → mirror_opened chains.

    Fails each chain (records errors) when:
    - No ``testnet_entry_order_poll`` lines precede ``mirror_open_attempt``.
    - Last poll is not ``fill_complete=true`` and ``terminal_failure=false``.
    - ``testnet_entry_mirror_failed`` follows ``mirror_open_attempt``.
    - No ``testnet_entry_mirror_opened`` for the same symbol / venue_order_id.
    """
    fatal: list[str] = []
    chains: list[EntryChainResult] = []

    indexed: list[tuple[int, dict[str, Any]]] = [
        (i, ev) for i, ev in enumerate(events) if _event_name(ev)
    ]

    k = 0
    while k < len(indexed):
        _, ev_attempt = indexed[k]
        if _event_name(ev_attempt) != "testnet_entry_mirror_open_attempt":
            k += 1
            continue

        sym = str(ev_attempt.get("symbol") or "")
        vid = str(ev_attempt.get("venue_order_id") or "")

        start_k = 0
        for k2 in range(k - 1, -1, -1):
            en = _event_name(indexed[k2][1])
            if en in ("testnet_entry_mirror_opened", "testnet_entry_mirror_failed"):
                start_k = k2 + 1
                break

        poll_slice: list[dict[str, Any]] = []
        for k2 in range(start_k, k):
            ev = indexed[k2][1]
            if _event_name(ev) != "testnet_entry_order_poll":
                continue
            if str(ev.get("symbol") or "") != sym:
                continue
            pvid = str(ev.get("venue_order_id") or "")
            if vid and pvid and pvid != vid:
                continue
            poll_slice.append(ev)

        chain = EntryChainResult(
            symbol=sym,
            venue_order_id=vid,
            poll_events=poll_slice,
            attempt=ev_attempt,
        )
        if poll_slice:
            last = poll_slice[-1]
            chain.last_poll_fill_complete = _bool_field(last, "fill_complete")
            chain.last_poll_terminal_failure = _bool_field(last, "terminal_failure")

        chain.outcome = None
        chain.opened = None
        for k2 in range(k + 1, len(indexed)):
            ev = indexed[k2][1]
            en = _event_name(ev)
            if en == "testnet_entry_mirror_open_attempt":
                break
            if en == "testnet_entry_mirror_opened" and _matches_sym_vid(ev, sym, vid):
                chain.outcome = "opened"
                chain.opened = ev
                break
            if en == "testnet_entry_mirror_failed" and _matches_sym_vid(ev, sym, vid):
                chain.outcome = "failed"
                break

        if not poll_slice:
            chain.errors.append(
                f"{sym}: no testnet_entry_order_poll lines before mirror_open_attempt "
                f"(venue_order_id={vid!r})."
            )
        else:
            if chain.last_poll_fill_complete is not True:
                chain.errors.append(
                    f"{sym}: last poll before mirror_open_attempt does not have fill_complete=true."
                )
            if chain.last_poll_terminal_failure is not False:
                chain.errors.append(
                    f"{sym}: last poll before mirror_open_attempt does not have terminal_failure=false."
                )

        if chain.outcome == "failed":
            chain.errors.append(
                f"{sym}: testnet_entry_mirror_failed after fill path "
                f"(venue_order_id={vid!r})."
            )

        if chain.outcome is None:
            chain.errors.append(
                f"{sym}: no testnet_entry_mirror_opened after mirror_open_attempt "
                f"(venue_order_id={vid!r})."
            )

        chains.append(chain)
        k += 1

    return EntryInstrumentationReport(chains=chains, fatal_errors=fatal)


def check_paper_status_reconciliation(
    status: dict[str, Any],
    bot_opened_symbols: set[str],
) -> list[str]:
    """Return error strings if bot-opened symbols have bad recon entries.

    Inspects ``reconciliation.last.persistent_details`` for ``phantom_venue`` or
    ``quantity_mismatch`` on symbols the bot opened in this session.
    """
    errors: list[str] = []
    recon = status.get("reconciliation")
    if not isinstance(recon, dict):
        return errors
    last = recon.get("last")
    if not isinstance(last, dict):
        return errors

    pers = last.get("persistent_details")
    if not isinstance(pers, list):
        pers = []

    bad_types = frozenset({"phantom_venue", "quantity_mismatch"})
    for d in pers:
        if not isinstance(d, dict):
            continue
        sym = str(d.get("symbol") or "")
        dtype = str(d.get("type") or "")
        if sym in bot_opened_symbols and dtype in bad_types:
            errors.append(
                f"reconciliation persistent_details: symbol={sym!r} type={dtype!r} detail={d.get('detail')!r}"
            )

    notes = last.get("operational_notes")
    if isinstance(notes, list) and bot_opened_symbols:
        for note in notes:
            if not isinstance(note, str):
                continue
            low = note.lower()
            if "phantom" in low and "foreign" not in low:
                # Foreign startup note mentions phantom — skip if only generic
                pass

    return errors


def check_foreign_venue_startup(status: dict[str, Any]) -> bool:
    """True if status indicates foreign/pre-existing venue positions at startup."""
    recon = status.get("reconciliation")
    if not isinstance(recon, dict):
        return False
    last = recon.get("last")
    if not isinstance(last, dict):
        return False
    if last.get("reason") == "foreign_venue_positions":
        return True
    if last.get("status") == "unclean" and last.get("reason") == "foreign_venue_positions":
        return True
    return False


def requested_vs_local_ok(requested_qty: str, local_qty: str) -> bool:
    """Compare qty strings after normalizing (strip, Decimal equality)."""
    from decimal import Decimal
    from decimal import InvalidOperation

    try:
        return Decimal(str(requested_qty).strip()) == Decimal(str(local_qty).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return False


def verify_local_qty_matches_attempts(chains: list[EntryChainResult]) -> list[str]:
    """For opened entries, ensure local_qty matches requested_qty on attempt/opened."""
    errs: list[str] = []
    for c in chains:
        if c.outcome != "opened" or not c.attempt or not c.opened:
            continue
        req = c.attempt.get("requested_qty")
        loc = c.opened.get("local_qty")
        if req is None or loc is None:
            errs.append(f"{c.symbol}: missing requested_qty or local_qty on attempt/opened.")
            continue
        if not requested_vs_local_ok(str(req), str(loc)):
            errs.append(
                f"{c.symbol}: local_qty {loc!r} does not match requested_qty {req!r} "
                f"(after rounding check)."
            )
    return errs
