#!/usr/bin/env python3
"""Verify Binance USDⓈ-M Futures **testnet** API credentials (signed balance call).

Exits 0 only if ``GET /fapi/v2/balance`` succeeds for USDT. Exits 1 on auth/format errors.

Usage::

    export CTE_BINANCE_TESTNET_API_KEY=...
    export CTE_BINANCE_TESTNET_API_SECRET=...
    python3 scripts/verify_binance_testnet_auth.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from cte.execution.binance_adapter import BinanceTestnetAdapter


async def main() -> int:
    key = (os.environ.get("CTE_BINANCE_TESTNET_API_KEY") or "").strip()
    secret = (os.environ.get("CTE_BINANCE_TESTNET_API_SECRET") or "").strip()
    base = (os.environ.get("CTE_BINANCE_TESTNET_REST_URL") or "").strip() or (
        "https://testnet.binancefuture.com"
    )
    if not key or not secret:
        print("Missing CTE_BINANCE_TESTNET_API_KEY or CTE_BINANCE_TESTNET_API_SECRET", file=sys.stderr)
        return 1
    ad = BinanceTestnetAdapter(api_key=key, api_secret=secret, base_url=base)
    await ad.start()
    try:
        snap = await ad.get_usdt_wallet_snapshot()
    except Exception as e:
        print(f"AUTH_FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        await ad.stop()
    print("AUTH_OK wallet=", snap.get("wallet"), "available=", snap.get("available"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
