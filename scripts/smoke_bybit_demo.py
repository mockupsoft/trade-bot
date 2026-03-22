#!/usr/bin/env python3
"""Bybit demo/testnet REST smoke: wallet auth → market entry → positions → reduce-only close.

Set credentials only via environment (never pass secrets on the CLI):
  CTE_BYBIT_DEMO_API_KEY
  CTE_BYBIT_DEMO_API_SECRET

Optional:
  CTE_BYBIT_REST_BASE_URL   (default: https://api-demo.bybit.com)
  CTE_SMOKE_SYMBOL          (default: ADAUSDT)
  CTE_SMOKE_DIRECTION       long | short  (default: long; short = REST proof only, not strategy)
  CTE_BYBIT_LINEAR_POSITION_MODE  one_way | hedge  (default: one_way → positionIdx 0)

Exit 0 on success JSON; non-zero with ``classification: BLOCKED`` and exact error payloads.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal

from cte.execution.adapter import OrderRequest, OrderSide
from cte.execution.bybit_adapter import BybitDemoAdapter


def _mask(s: str, n: int = 4) -> str:
    s = s.strip()
    if len(s) <= 2 * n:
        return "***"
    return f"{s[:n]}…{s[-n:]}"


async def _poll_filled(
    adapter: BybitDemoAdapter, symbol: str, client_order_id: str, first: object
) -> object:
    orez = first
    for _ in range(45):
        from cte.execution.adapter import VenueOrderStatus

        raw = getattr(orez, "raw_response", None) or {}
        st = str(raw.get("orderStatus", ""))
        if getattr(orez, "filled_quantity", Decimal("0")) > 0:
            break
        if getattr(orez, "status", None) in (
            VenueOrderStatus.FILLED,
            VenueOrderStatus.PARTIAL,
        ):
            break
        if st in ("Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled"):
            break
        await asyncio.sleep(0.12)
        nxt = await adapter.get_order(symbol, client_order_id)
        if nxt is not None:
            orez = nxt
    return orez


async def main() -> int:
    key = (os.environ.get("CTE_BYBIT_DEMO_API_KEY") or "").strip()
    secret = (os.environ.get("CTE_BYBIT_DEMO_API_SECRET") or "").strip()
    base = (os.environ.get("CTE_BYBIT_REST_BASE_URL") or "https://api-demo.bybit.com").strip()
    symbol = (os.environ.get("CTE_SMOKE_SYMBOL") or "ADAUSDT").strip().upper()
    dir_raw = (os.environ.get("CTE_SMOKE_DIRECTION") or "long").strip().lower()
    if dir_raw in ("short", "sell", "s"):
        direction = "short"
        entry_side = OrderSide.SELL
    else:
        direction = "long"
        entry_side = OrderSide.BUY
    # Bybit linear min order value is often 5 USDT; size qty for liquid alts accordingly.
    qty_raw = (os.environ.get("CTE_SMOKE_QTY") or "25").strip()
    try:
        qty_dec = Decimal(qty_raw)
    except Exception:
        qty_dec = Decimal("25")

    out: dict = {
        "active_venue": "bybit_demo",
        "active_symbol": symbol,
        "smoke_direction": direction,
        "smoke_qty": str(qty_dec),
        "rest_base": base,
        "masked": {"api_key": _mask(key) if key else "(empty)"},
    }

    if not key or not secret:
        out["classification"] = "BLOCKED"
        out["blocker"] = {
            "reason": "missing_credentials",
            "detail": "Set CTE_BYBIT_DEMO_API_KEY and CTE_BYBIT_DEMO_API_SECRET",
        }
        print(json.dumps(out, indent=2))
        return 1

    adapter = BybitDemoAdapter(api_key=key, api_secret=secret, base_url=base)
    await adapter.start()
    try:
        try:
            snap = await adapter.get_usdt_wallet_snapshot()
            out["wallet_snapshot"] = {k: str(v) for k, v in snap.items()}
        except Exception as e:
            out["classification"] = "BLOCKED"
            out["blocker"] = {"reason": "wallet_snapshot_failed", "detail": repr(e)}
            print(json.dumps(out, indent=2))
            return 1

        req = OrderRequest(
            symbol=symbol,
            side=entry_side,
            direction=direction,
            quantity=qty_dec,
        )
        try:
            orez = await adapter.place_order(req)
        except Exception as e:
            out["classification"] = "BLOCKED"
            out["blocker"] = {"reason": "place_order_exception", "detail": repr(e)}
            print(json.dumps(out, indent=2))
            return 1

        orez = await _poll_filled(adapter, symbol, req.client_order_id, orez)
        raw = getattr(orez, "raw_response", None) or {}
        out["first_order_proof"] = {
            "client_order_id": orez.client_order_id,
            "venue_order_id": orez.venue_order_id,
            "status": orez.status.value,
            "filled_qty": str(orez.filled_quantity),
            "avg_price": str(orez.average_price),
            "venue_payload": raw,
        }

        from cte.execution.adapter import VenueOrderStatus

        if orez.status not in (VenueOrderStatus.FILLED, VenueOrderStatus.PARTIAL):
            out["classification"] = "BLOCKED"
            out["blocker"] = {
                "reason": "order_not_filled",
                "detail": "See first_order_proof.venue_payload",
                "payload": raw,
            }
            print(json.dumps(out, indent=2))
            return 1

        pos = await adapter.get_positions(symbol)
        out["positions_proof"] = [
            {
                "symbol": p.symbol,
                "side": p.side,
                "quantity": str(p.quantity),
                "entry_price": str(p.entry_price),
            }
            for p in pos
        ]

        if not pos:
            out["classification"] = "BLOCKED"
            out["blocker"] = {"reason": "no_position_after_fill", "detail": "get_positions empty"}
            print(json.dumps(out, indent=2))
            return 1

        p0 = pos[0]
        entry_side = OrderSide.BUY if p0.side == "long" else OrderSide.SELL
        try:
            cr = await adapter.close_position(
                symbol, p0.quantity, entry_side, direction=p0.side
            )
            cr = await _poll_filled(adapter, symbol, cr.client_order_id, cr)
            out["close_order_proof"] = {
                "status": cr.status.value,
                "venue_payload": getattr(cr, "raw_response", None) or {},
            }
        except Exception as e:
            out["classification"] = "BLOCKED"
            out["blocker"] = {"reason": "close_failed", "detail": repr(e)}
            print(json.dumps(out, indent=2))
            return 1

        out["classification"] = "OK"
        out["note"] = (
            "Analytics journal proof requires the dashboard process with demo epoch; "
            "this script validates venue REST only."
        )
        print(json.dumps(out, indent=2))
        return 0
    finally:
        await adapter.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
