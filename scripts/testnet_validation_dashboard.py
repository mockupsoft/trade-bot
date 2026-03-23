#!/usr/bin/env python3
"""Start the dashboard with stdout+stderr tee to a fresh log file (structlog JSON).

Loads repo-root ``.env``, then applies defaults for a validation session::

    CTE_ENGINE_MODE=demo
    CTE_EXECUTION_MODE=testnet
    CTE_DASHBOARD_VENUE_LOOP=1
    CTE_ENGINE_LOG_LEVEL=INFO

Usage (from repo root)::

    py -3 scripts/testnet_validation_dashboard.py

Optional::

    py -3 scripts/testnet_validation_dashboard.py --port 8080 --log-dir logs

See plan: Fresh testnet session — log capture and evidence checklist.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def _ensure_validation_defaults() -> None:
    os.environ.setdefault("CTE_ENGINE_MODE", "demo")
    os.environ.setdefault("CTE_EXECUTION_MODE", "testnet")
    os.environ.setdefault("CTE_DASHBOARD_VENUE_LOOP", "1")
    os.environ.setdefault("CTE_ENGINE_LOG_LEVEL", "INFO")


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    load_dotenv(repo / ".env", override=True)
    _ensure_validation_defaults()

    p = argparse.ArgumentParser(description="Run dashboard with validation log capture.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--log-dir", type=Path, default=repo / "logs")
    args = p.parse_args()

    args.log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    log_path = args.log_dir / f"testnet_validation_{ts}.log"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "cte.dashboard.app:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    print(f"Logging to: {log_path}", file=sys.stderr)
    print("Preflight: close testnet positions; run scripts/verify_binance_testnet_auth.py", file=sys.stderr)
    print(f"Meta check: GET http://{args.host}:{args.port}/api/dashboard/meta", file=sys.stderr)

    with log_path.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                logf.write(line)
                sys.stdout.write(line)
        except KeyboardInterrupt:
            proc.terminate()
            return 130
        rc = proc.wait()
        return rc


if __name__ == "__main__":
    raise SystemExit(main())
