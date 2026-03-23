#!/usr/bin/env python3
"""Verify structlog JSON lines for testnet entry instrumentation (see plan checklist).

Usage::

    py -3 scripts/verify_testnet_entry_logs.py logs/testnet_validation_*.log
    type testnet_validation.log | py -3 scripts/verify_testnet_entry_logs.py -

Exits 0 if all chains pass and ``--min-entries`` successful opens met; else 1.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cte.ops.testnet_validation_evidence import (
    check_paper_status_reconciliation,
    parse_structlog_json_lines,
    verify_entry_instrumentation_events,
    verify_local_qty_matches_attempts,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", help="Path to log file, or '-' for stdin")
    ap.add_argument("--min-entries", type=int, default=3)
    ap.add_argument(
        "--status-json",
        action="append",
        default=[],
        help="Optional /api/paper/status JSON file(s) for reconciliation check (repeatable).",
    )
    args = ap.parse_args()

    if args.logfile == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.logfile).read_text(encoding="utf-8", errors="replace")

    events = parse_structlog_json_lines(text)
    rep = verify_entry_instrumentation_events(events)
    qty_errs = verify_local_qty_matches_attempts(rep.chains)

    all_errs: list[str] = []
    for c in rep.chains:
        all_errs.extend(c.errors)
    all_errs.extend(qty_errs)

    bot_syms = {
        str(c.opened.get("symbol") or "")
        for c in rep.chains
        if c.outcome == "opened" and c.opened
    }
    bot_syms.discard("")

    for sj in args.status_json:
        try:
            st = json.loads(Path(sj).read_text(encoding="utf-8"))
        except OSError as e:
            all_errs.append(f"status file {sj!r}: {e}")
            continue
        all_errs.extend(check_paper_status_reconciliation(st, bot_syms))

    ok_entries = rep.successful_entries
    print(json.dumps({"successful_entries": ok_entries, "chains": len(rep.chains)}, indent=2))
    if all_errs:
        print("FAILURES:", file=sys.stderr)
        for e in all_errs:
            print(f"  {e}", file=sys.stderr)
    if ok_entries < args.min_entries:
        print(
            f"Need at least {args.min_entries} successful entry chains; got {ok_entries}.",
            file=sys.stderr,
        )
        return 1
    if all_errs:
        return 1
    print("OK: entry instrumentation and optional recon checks passed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
