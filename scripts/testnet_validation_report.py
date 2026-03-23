#!/usr/bin/env python3
"""Print a short human-readable verdict from a validation log (and optional status JSON).

Usage::

    py -3 scripts/testnet_validation_report.py logs/testnet_validation_*.log \\
        --status-json evidence/snapshots/status_after_entry_1_*.json

Exit code 0 only if ``verify_testnet_entry_logs.py`` would exit 0 with the same inputs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cte.ops.testnet_validation_evidence import (
    check_foreign_venue_startup,
    check_paper_status_reconciliation,
    parse_structlog_json_lines,
    verify_entry_instrumentation_events,
    verify_local_qty_matches_attempts,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("--min-entries", type=int, default=3)
    ap.add_argument("--status-json", action="append", default=[])
    args = ap.parse_args()

    text = Path(args.logfile).read_text(encoding="utf-8", errors="replace")
    events = parse_structlog_json_lines(text)
    rep = verify_entry_instrumentation_events(events)
    qty_errs = verify_local_qty_matches_attempts(rep.chains)

    bot_syms = {
        str(c.opened.get("symbol") or "")
        for c in rep.chains
        if c.outcome == "opened" and c.opened
    }
    bot_syms.discard("")

    all_errs: list[str] = []
    for c in rep.chains:
        all_errs.extend(c.errors)
    all_errs.extend(qty_errs)

    foreign = False
    for sj in args.status_json:
        st = json.loads(Path(sj).read_text(encoding="utf-8"))
        foreign = foreign or check_foreign_venue_startup(st)
        all_errs.extend(check_paper_status_reconciliation(st, bot_syms))

    print("=== Entry instrumentation chains ===")
    for i, c in enumerate(rep.chains, 1):
        err_s = f" ERR={c.errors}" if c.errors else ""
        print(
            f"{i}. symbol={c.symbol!r} venue_order_id={c.venue_order_id!r} "
            f"outcome={c.outcome!r} polls={len(c.poll_events)}{err_s}"
        )
    print(f"Successful opens: {rep.successful_entries} (min required {args.min_entries})")
    print(f"Account had foreign venue flag in status files: {foreign}")
    if all_errs:
        print("FAILURES:")
        for e in all_errs:
            print(f"  - {e}")
    ok = rep.successful_entries >= args.min_entries and not all_errs
    print()
    print("24h clean-account validation run: " + ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
