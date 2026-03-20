#!/usr/bin/env python3
"""Run dashboard + poll until one full venue trade cycle (entry + exit) or timeout.

Requires **valid** Binance Futures testnet keys. Prerequisite::

    python3 scripts/verify_binance_testnet_auth.py

Environment (typical)::

    CTE_ENGINE_MODE=demo
    CTE_EXECUTION_MODE=testnet
    CTE_DASHBOARD_VENUE_LOOP=1
    CTE_ENGINE_SYMBOLS='[\"BTCUSDT\"]'
    CTE_SIGNALS_COOLDOWN_SECONDS=0
    CTE_EXITS_MAX_HOLD_MINUTES=3
    CTE_SIZING_MAX_ORDER_USD=200

Does not fake fills.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080")
TIMEOUT_SEC = int(os.environ.get("E2E_TIMEOUT_SEC", "2400"))
POLL_SEC = float(os.environ.get("E2E_POLL_SEC", "3"))


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> int:
    v = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_binance_testnet_auth.py")],
        cwd=REPO_ROOT,
        check=False,
    )
    if v.returncode != 0:
        print("Fix credentials first (see scripts/verify_binance_testnet_auth.py).", file=sys.stderr)
        return 2

    print("Starting uvicorn (set E2E_SKIP_START=1 if dashboard already running)...")
    if os.environ.get("E2E_SKIP_START", "").strip().lower() not in ("1", "true", "yes"):
        env = os.environ.copy()
        env.setdefault("CTE_ENGINE_MODE", "demo")
        env.setdefault("CTE_EXECUTION_MODE", "testnet")
        env.setdefault("CTE_DASHBOARD_VENUE_LOOP", "1")
        env.setdefault("CTE_ENGINE_SYMBOLS", '["BTCUSDT"]')
        env.setdefault("CTE_SIGNALS_COOLDOWN_SECONDS", "0")
        env.setdefault("CTE_EXITS_MAX_HOLD_MINUTES", "3")
        env.setdefault("CTE_DASHBOARD_PAPER_INTERVAL_SEC", "1.0")
        env.setdefault("CTE_DASHBOARD_PAPER_TIER_C_THRESHOLD", "0.26")
        env.setdefault("CTE_SIZING_MAX_ORDER_USD", "200")
        env.setdefault("CTE_BINANCE_TESTNET_REST_URL", "https://testnet.binancefuture.com")
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "cte.dashboard.app:app", "--host", "127.0.0.1", "--port", "8080"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(5)

    t0 = time.time()
    balance_before: dict | None = None
    first_order_id: str | None = None

    while time.time() - t0 < TIMEOUT_SEC:
        try:
            st = _get("/api/paper/status")
        except (urllib.error.URLError, TimeoutError) as e:
            print("waiting for dashboard...", e)
            time.sleep(2)
            continue

        if balance_before is None and isinstance(st.get("venue_balance_usdt"), dict):
            b = st["venue_balance_usdt"]
            if "wallet" in b:
                balance_before = dict(b)

        vom = st.get("venue_order_metrics") or {}
        if first_order_id is None and vom.get("first_venue_order_id"):
            first_order_id = str(vom["first_venue_order_id"])

        ent = int(st.get("entries_total") or 0)
        ex = int(st.get("exits_recorded") or 0)
        print(
            f"t={int(time.time()-t0)}s entries_total={ent} exits={ex} "
            f"sent={vom.get('entry_orders_sent')} filled={vom.get('entry_orders_filled')} "
            f"venue_err={st.get('venue_last_error')}",
        )

        if ent >= 1 and ex >= 1:
            trades = _get("/api/analytics/trades?epoch=crypto_v1_demo&limit=5")
            rows = trades if isinstance(trades, list) else []
            out = {
                "duration_sec": round(time.time() - t0, 1),
                "first_venue_order_id": first_order_id,
                "balance_before": balance_before,
                "balance_after": st.get("venue_balance_usdt"),
                "reconciliation": st.get("reconciliation"),
                "runner": st.get("runner_class"),
                "execution_mode": st.get("execution_mode"),
                "last_trade_rows": rows[:3],
            }
            print(json.dumps(out, indent=2))
            return 0

        time.sleep(POLL_SEC)

    print("TIMEOUT: no full cycle (entry+exit) within E2E_TIMEOUT_SEC", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
