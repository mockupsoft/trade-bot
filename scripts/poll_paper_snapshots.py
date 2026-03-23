#!/usr/bin/env python3
"""Poll ``/api/paper/status`` and ``/api/paper/positions`` when ``entries_total`` increases.

Saves JSON files for evidence (plan: capture after each new entry).

Usage::

    py -3 scripts/poll_paper_snapshots.py --output-dir evidence/snapshots

Environment::

    PAPER_SNAPSHOT_BASE_URL=http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--base-url",
        default=os.environ.get("PAPER_SNAPSHOT_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
    )
    ap.add_argument("--poll-sec", type=float, default=3.0)
    ap.add_argument("--output-dir", type=Path, default=Path("evidence/snapshots"))
    ap.add_argument(
        "--min-entries",
        type=int,
        default=3,
        help="Exit 0 after saving this many distinct entry increments (default 3).",
    )
    ap.add_argument(
        "--max-wait-minutes",
        type=float,
        default=240.0,
        help="Give up after this many minutes if entries_total does not reach min-entries.",
    )
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    last_entries: int | None = None
    capture_idx = 0
    deadline = time.monotonic() + args.max_wait_minutes * 60.0
    while capture_idx < args.min_entries:
        if time.monotonic() > deadline:
            print(
                f"timeout after {args.max_wait_minutes} min "
                f"(captured {capture_idx}/{args.min_entries} entry increments)",
                file=sys.stderr,
            )
            return 1
        try:
            st = _get_json(f"{args.base_url}/api/paper/status")
            pos = _get_json(f"{args.base_url}/api/paper/positions")
        except urllib.error.URLError as e:
            print(f"poll failed: {e}", file=sys.stderr)
            time.sleep(args.poll_sec)
            continue

        ent = st.get("entries_total")
        try:
            ent_i = int(ent) if ent is not None else 0
        except (TypeError, ValueError):
            ent_i = 0

        if last_entries is None:
            last_entries = ent_i
            time.sleep(args.poll_sec)
            continue

        if ent_i > last_entries:
            last_entries = ent_i
            capture_idx += 1
            ts = int(time.time())
            sp = args.output_dir / f"status_after_entry_{capture_idx}_{ts}.json"
            pp = args.output_dir / f"positions_after_entry_{capture_idx}_{ts}.json"
            sp.write_text(json.dumps(st, indent=2), encoding="utf-8")
            pp.write_text(json.dumps(pos, indent=2), encoding="utf-8")
            print(f"saved {sp.name} {pp.name} entries_total={ent_i}", flush=True)

        if capture_idx >= args.min_entries:
            break
        time.sleep(args.poll_sec)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
