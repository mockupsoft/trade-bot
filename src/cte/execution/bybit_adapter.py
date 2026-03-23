"""Bybit v5 demo/testnet execution adapter.

Implements ExecutionAdapter for Bybit's demo trading environment.
Uses HMAC-SHA256 signed REST requests.

Demo endpoint: https://api-demo.bybit.com
Testnet endpoint: https://api-testnet.bybit.com

Rate limits: 10 requests/sec for order endpoints.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from decimal import Decimal

import aiohttp
import orjson
import structlog
from prometheus_client import Counter, Histogram

from cte.core.exceptions import ExecutionError, OrderRejectedError
from cte.execution.adapter import (
    AdapterHealth,
    ExecutionAdapter,
    OrderRequest,
    OrderResult,
    OrderSide,
    VenueOrderStatus,
    VenuePosition,
)
from cte.execution.rate_limiter import BYBIT_LIMITS, TokenBucketRateLimiter

logger = structlog.get_logger(__name__)

bybit_requests_total = Counter(
    "cte_bybit_requests_total", "Bybit API requests", ["endpoint", "status"]
)
bybit_latency = Histogram(
    "cte_bybit_request_latency_seconds", "Bybit API latency", ["endpoint"]
)

BYBIT_STATUS_MAP: dict[str, VenueOrderStatus] = {
    "New": VenueOrderStatus.SUBMITTED,
    "PartiallyFilled": VenueOrderStatus.PARTIAL,
    "Filled": VenueOrderStatus.FILLED,
    "Cancelled": VenueOrderStatus.CANCELLED,
    "PartiallyFilledCanceled": VenueOrderStatus.CANCELLED,
    "Rejected": VenueOrderStatus.REJECTED,
    "Deactivated": VenueOrderStatus.EXPIRED,
}


class BybitDemoAdapter(ExecutionAdapter):
    """Bybit v5 unified trade API adapter for demo/testnet.

    Requires: CTE_BYBIT_DEMO_API_KEY, CTE_BYBIT_DEMO_API_SECRET
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api-demo.bybit.com",
        recv_window: int = 5000,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url
        self._recv_window = recv_window
        self._session: aiohttp.ClientSession | None = None
        self._rate_limiter = TokenBucketRateLimiter(BYBIT_LIMITS)
        self._connected = False
        self._error_count = 0

    def _position_idx_for(self, request: OrderRequest) -> int:
        """0 = one-way (default); 1/2 = hedge long/short (``CTE_BYBIT_LINEAR_POSITION_MODE=hedge``)."""
        mode = (os.environ.get("CTE_BYBIT_LINEAR_POSITION_MODE") or "one_way").strip().lower()
        if mode in ("oneway", "one_way", "one-way"):
            return 0
        return 1 if request.direction == "long" else 2

    @property
    def venue_name(self) -> str:
        return "bybit_demo"

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        )
        self._connected = True
        await logger.ainfo("bybit_demo_started", base_url=self._base_url)

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
        self._connected = False

    async def place_order(self, request: OrderRequest) -> OrderResult:
        body = {
            "category": "linear",
            "symbol": request.symbol,
            "side": "Buy" if request.side == OrderSide.BUY else "Sell",
            "positionIdx": self._position_idx_for(request),
            "orderType": "Market" if request.order_type.value == "market" else "Limit",
            "qty": str(request.quantity),
            "orderLinkId": request.client_order_id,
        }

        if request.order_type.value == "limit" and request.price is not None:
            body["price"] = str(request.price)
            body["timeInForce"] = request.time_in_force.value

        if request.reduce_only:
            body["reduceOnly"] = True

        data = await self._signed_request("POST", "/v5/order/create", body)
        return self._parse_order_response(data, request)

    async def get_usdt_wallet_snapshot(self) -> dict[str, Decimal]:
        """USDT balance for unified or contract wallet (demo/testnet REST)."""
        zeros = {
            "wallet": Decimal("0"),
            "available": Decimal("0"),
            "cross_wallet": Decimal("0"),
        }
        for acct in ("UNIFIED", "CONTRACT"):
            params: dict[str, str] = {"accountType": acct, "coin": "USDT"}
            data = await self._signed_get("/v5/account/wallet-balance", params)
            if data.get("retCode") != 0:
                continue
            lst = (data.get("result") or {}).get("list") or []
            if not lst:
                continue
            coins = lst[0].get("coin") or []
            for c in coins:
                if c.get("coin") == "USDT":
                    avail = c.get("availableToWithdraw") or c.get("availableBalance") or "0"
                    wall = c.get("walletBalance") or "0"
                    return {
                        "wallet": Decimal(str(wall)),
                        "available": Decimal(str(avail)),
                        "cross_wallet": Decimal(str(wall)),
                    }
        return zeros

    async def cancel_order(
        self, symbol: str, client_order_id: str
    ) -> OrderResult:
        body = {
            "category": "linear",
            "symbol": symbol,
            "orderLinkId": client_order_id,
        }
        data = await self._signed_request("POST", "/v5/order/cancel", body)
        return self._parse_cancel_response(data)

    async def get_order(
        self, symbol: str, client_order_id: str
    ) -> OrderResult | None:
        params = {
            "category": "linear",
            "symbol": symbol,
            "orderLinkId": client_order_id,
        }
        data = await self._signed_get("/v5/order/realtime", params)
        result_list = data.get("result", {}).get("list", [])
        if not result_list:
            return None
        return self._parse_query_response(result_list[0], full=data)

    async def get_open_orders(
        self, symbol: str | None = None
    ) -> list[OrderResult]:
        params: dict = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        data = await self._signed_get("/v5/order/realtime", params)
        result_list = data.get("result", {}).get("list", [])
        return [self._parse_query_response(o, full=data) for o in result_list]

    async def get_positions(
        self, symbol: str | None = None
    ) -> list[VenuePosition]:
        """Linear USDT perps on unified/demo accounts.

        ``settleCoin=USDT`` is required for reliable visibility on v5 unified
        linear USDT-M contracts (matches ``place_order`` category ``linear``).
        Keep ``CTE_BYBIT_LINEAR_POSITION_MODE`` aligned with the account's
        one-way vs hedge mode in the Bybit UI.
        """
        params: dict = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        data = await self._signed_get("/v5/position/list", params)
        result_list = data.get("result", {}).get("list", [])

        positions = []
        for p in result_list:
            qty = Decimal(str(p.get("size", "0")))
            if qty == 0:
                continue
            positions.append(VenuePosition(
                symbol=p.get("symbol", ""),
                side="long" if p.get("side") == "Buy" else "short",
                quantity=qty,
                entry_price=Decimal(str(p.get("avgPrice", "0"))),
                unrealized_pnl=Decimal(str(p.get("unrealisedPnl", "0"))),
                leverage=int(p.get("leverage", 1)),
                margin_type="cross" if p.get("tradeMode") == 0 else "isolated",
            ))
        return positions

    async def close_position(
        self, symbol: str, quantity: Decimal, side: OrderSide, direction: str = "long"
    ) -> OrderResult:
        close_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        request = OrderRequest(
            symbol=symbol,
            side=close_side,
            direction=direction,
            quantity=quantity,
            reduce_only=True,
        )
        return await self.place_order(request)

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            connected=self._connected,
            last_heartbeat_ms=int(time.monotonic() * 1000),
            rate_limit_remaining=int(self._rate_limiter.available_tokens),
            error_count_1m=self._error_count,
        )

    # ── Internal HTTP ─────────────────────────────────────────

    def _sign(self, timestamp: int, body_str: str) -> str:
        param_str = f"{timestamp}{self._api_key}{self._recv_window}{body_str}"
        return hmac.new(
            self._api_secret.encode(), param_str.encode(), hashlib.sha256
        ).hexdigest()

    async def _signed_request(
        self, method: str, path: str, body: dict
    ) -> dict:
        if not self._session:
            raise ExecutionError("Adapter not started")

        await self._rate_limiter.acquire(1)

        ts = int(time.time() * 1000)
        body_str = orjson.dumps(body).decode()
        signature = self._sign(ts, body_str)

        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": str(self._recv_window),
            "Content-Type": "application/json",
        }

        url = f"{self._base_url}{path}"

        for attempt in range(3):
            try:
                start = time.monotonic()
                async with self._session.request(
                    method, url, headers=headers, data=body_str
                ) as resp:
                    elapsed = time.monotonic() - start
                    bybit_latency.labels(endpoint=path).observe(elapsed)
                    data = await resp.json()

                    ret_code = data.get("retCode", -1)

                    if resp.status == 429 or ret_code == 10006:
                        self._rate_limiter.report_429()
                        bybit_requests_total.labels(endpoint=path, status="429").inc()
                        continue

                    if ret_code != 0:
                        msg = data.get("retMsg", "Unknown")
                        bybit_requests_total.labels(endpoint=path, status="error").inc()
                        self._error_count += 1

                        if ret_code in (10001, 110007, 110012):
                            raise OrderRejectedError(
                                f"Bybit rejected: {msg}",
                                context={"code": ret_code, "body": body},
                            )
                        raise ExecutionError(
                            f"Bybit API error: {ret_code} {msg}",
                            context={"code": ret_code, "path": path},
                        )

                    bybit_requests_total.labels(endpoint=path, status="ok").inc()
                    return data

            except (aiohttp.ClientError, TimeoutError):
                self._error_count += 1
                if attempt == 2:
                    raise ExecutionError(f"Bybit request failed after 3 attempts: {path}") from None

        return {}

    async def _signed_get(self, path: str, params: dict) -> dict:
        if not self._session:
            raise ExecutionError("Adapter not started")

        await self._rate_limiter.acquire(1)

        ts = int(time.time() * 1000)
        from urllib.parse import urlencode
        query = urlencode(params)
        signature = self._sign(ts, query)

        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": str(self._recv_window),
        }

        url = f"{self._base_url}{path}?{query}"

        async with self._session.get(url, headers=headers) as resp:
            data = await resp.json()
            bybit_requests_total.labels(endpoint=path, status="ok").inc()
            return data

    @staticmethod
    def _parse_order_response(data: dict, request: OrderRequest) -> OrderResult:
        if data.get("retCode") not in (0, None):
            return OrderResult(
                client_order_id=request.client_order_id,
                venue_order_id="",
                symbol=request.symbol,
                side=request.side,
                status=VenueOrderStatus.REJECTED,
                requested_quantity=request.quantity,
                error_code=str(data.get("retCode", "")),
                error_message=str(data.get("retMsg", "")),
                raw_response=data,
            )
        result = data.get("result", {}) or {}
        st = result.get("orderStatus") or "New"
        status = BYBIT_STATUS_MAP.get(st, VenueOrderStatus.SUBMITTED)
        filled = Decimal(str(result.get("cumExecQty", "0") or "0"))
        avg = Decimal(str(result.get("avgPrice", "0") or "0"))
        return OrderResult(
            client_order_id=result.get("orderLinkId", request.client_order_id),
            venue_order_id=str(result.get("orderId", "")),
            symbol=request.symbol,
            side=request.side,
            status=status,
            requested_quantity=Decimal(str(result.get("qty", request.quantity))),
            filled_quantity=filled,
            average_price=avg,
            raw_response=result,
        )

    @staticmethod
    def _parse_cancel_response(data: dict) -> OrderResult:
        result = data.get("result", {})
        return OrderResult(
            client_order_id=result.get("orderLinkId", ""),
            venue_order_id=result.get("orderId", ""),
            status=VenueOrderStatus.CANCELLED,
        )

    @staticmethod
    def _parse_query_response(order: dict, full: dict | None = None) -> OrderResult:
        status = BYBIT_STATUS_MAP.get(
            order.get("orderStatus", ""), VenueOrderStatus.REJECTED
        )
        raw = dict(order)
        if full is not None:
            raw["_api"] = full
        return OrderResult(
            client_order_id=order.get("orderLinkId", ""),
            venue_order_id=order.get("orderId", ""),
            symbol=order.get("symbol", ""),
            side=OrderSide.BUY if order.get("side") == "Buy" else OrderSide.SELL,
            status=status,
            requested_quantity=Decimal(str(order.get("qty", "0"))),
            filled_quantity=Decimal(str(order.get("cumExecQty", "0"))),
            average_price=Decimal(str(order.get("avgPrice", "0"))),
            fees=Decimal(str(order.get("cumExecFee", "0"))),
            raw_response=raw,
        )
