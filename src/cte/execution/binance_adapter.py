"""Binance USDⓈ-M Futures testnet execution adapter.

Implements ExecutionAdapter for Binance's testnet environment.
Uses HMAC-SHA256 signed REST requests and WebSocket user data stream
for order updates.

Testnet endpoint: https://testnet.binancefuture.com
Testnet WS: wss://stream.binancefuture.com

Rate limits mirror production: 2400 weight/min.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

import aiohttp
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
from cte.execution.rate_limiter import BINANCE_LIMITS, TokenBucketRateLimiter

logger = structlog.get_logger(__name__)

binance_requests_total = Counter(
    "cte_binance_requests_total", "Binance API requests", ["endpoint", "status"]
)
binance_latency = Histogram(
    "cte_binance_request_latency_seconds", "Binance API latency", ["endpoint"]
)

# Binance order status → our canonical status
BINANCE_STATUS_MAP: dict[str, VenueOrderStatus] = {
    "NEW": VenueOrderStatus.SUBMITTED,
    "PARTIALLY_FILLED": VenueOrderStatus.PARTIAL,
    "FILLED": VenueOrderStatus.FILLED,
    "CANCELED": VenueOrderStatus.CANCELLED,
    "REJECTED": VenueOrderStatus.REJECTED,
    "EXPIRED": VenueOrderStatus.EXPIRED,
    "NEW_INSURANCE": VenueOrderStatus.SUBMITTED,
    "NEW_ADL": VenueOrderStatus.SUBMITTED,
}


class BinanceTestnetAdapter(ExecutionAdapter):
    """Binance USDⓈ-M Futures testnet execution adapter.

    Requires API key and secret configured in environment:
    CTE_BINANCE_TESTNET_API_KEY, CTE_BINANCE_TESTNET_API_SECRET
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://testnet.binancefuture.com",
        recv_window: int = 5000,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url
        self._recv_window = recv_window
        self._session: aiohttp.ClientSession | None = None
        self._rate_limiter = TokenBucketRateLimiter(BINANCE_LIMITS)
        self._connected = False
        self._error_count = 0

    @property
    def venue_name(self) -> str:
        return "binance_testnet"

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"X-MBX-APIKEY": self._api_key},
            timeout=aiohttp.ClientTimeout(total=10),
        )
        self._connected = True
        await logger.ainfo("binance_testnet_started", base_url=self._base_url)

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
        self._connected = False

    async def place_order(self, request: OrderRequest) -> OrderResult:
        params = {
            "symbol": request.symbol,
            "side": "BUY" if request.side == OrderSide.BUY else "SELL",
            "positionSide": "LONG" if request.direction == "long" else "SHORT",
            "type": request.order_type.value.upper(),
            "quantity": str(request.quantity),
            "newClientOrderId": request.client_order_id,
            "recvWindow": self._recv_window,
        }

        if request.order_type.value == "limit" and request.price is not None:
            params["price"] = str(request.price)
            params["timeInForce"] = request.time_in_force.value

        # Hedge mode always sends ``positionSide``; Binance USD-M then rejects
        # ``reduceOnly`` with -1106 ("Parameter 'reduceonly' sent when not required").
        # Opposite side + ``positionSide`` is sufficient to close a leg.
        if request.reduce_only and "positionSide" not in params:
            params["reduceOnly"] = "true"

        data = await self._signed_request("POST", "/fapi/v1/order", params, weight=1)
        return self._parse_order_response(data)

    async def cancel_order(
        self, symbol: str, client_order_id: str
    ) -> OrderResult:
        params = {
            "symbol": symbol,
            "origClientOrderId": client_order_id,
            "recvWindow": self._recv_window,
        }
        data = await self._signed_request("DELETE", "/fapi/v1/order", params, weight=1)
        return self._parse_order_response(data)

    async def get_order(
        self, symbol: str, client_order_id: str
    ) -> OrderResult | None:
        params = {
            "symbol": symbol,
            "origClientOrderId": client_order_id,
            "recvWindow": self._recv_window,
        }
        data = await self._signed_request("GET", "/fapi/v1/order", params, weight=1)
        if not data:
            return None
        return self._parse_order_response(data)

    async def get_open_orders(
        self, symbol: str | None = None
    ) -> list[OrderResult]:
        params: dict = {"recvWindow": self._recv_window}
        if symbol:
            params["symbol"] = symbol
        data = await self._signed_request("GET", "/fapi/v1/openOrders", params, weight=1)
        if not isinstance(data, list):
            return []
        return [self._parse_order_response(d) for d in data]

    async def get_positions(
        self, symbol: str | None = None
    ) -> list[VenuePosition]:
        params: dict = {"recvWindow": self._recv_window}
        data = await self._signed_request("GET", "/fapi/v2/positionRisk", params, weight=5)
        if not isinstance(data, list):
            return []

        positions = []
        for p in data:
            qty = Decimal(str(p.get("positionAmt", "0")))
            if qty == 0 and symbol:
                continue
            if symbol and p.get("symbol") != symbol:
                continue
            positions.append(VenuePosition(
                symbol=p.get("symbol", ""),
                side="long" if qty > 0 else "short" if qty < 0 else "both",
                quantity=abs(qty),
                entry_price=Decimal(str(p.get("entryPrice", "0"))),
                unrealized_pnl=Decimal(str(p.get("unRealizedProfit", "0"))),
                leverage=int(p.get("leverage", 1)),
                margin_type=p.get("marginType", "cross"),
            ))
        return positions

    async def get_usdt_wallet_snapshot(self) -> dict[str, Decimal]:
        """USD-M futures wallet balances for USDT (cross-margin wallet)."""
        params: dict = {"recvWindow": self._recv_window}
        data = await self._signed_request("GET", "/fapi/v2/balance", params, weight=5)
        if not isinstance(data, list):
            return {
                "wallet": Decimal("0"),
                "available": Decimal("0"),
                "cross_wallet": Decimal("0"),
            }
        for b in data:
            if b.get("asset") == "USDT":
                return {
                    "wallet": Decimal(str(b.get("walletBalance", "0"))),
                    "available": Decimal(str(b.get("availableBalance", "0"))),
                    "cross_wallet": Decimal(str(b.get("crossWalletBalance", "0"))),
                }
        return {
            "wallet": Decimal("0"),
            "available": Decimal("0"),
            "cross_wallet": Decimal("0"),
        }

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

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: dict,
        weight: int = 1,
    ) -> dict | list:
        """Send a signed request to Binance API with rate limiting and retry."""
        if not self._session:
            raise ExecutionError("Adapter not started")

        await self._rate_limiter.acquire(weight)

        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        query += f"&signature={signature}"

        url = f"{self._base_url}{path}?{query}"

        for attempt in range(3):
            try:
                start = time.monotonic()
                async with self._session.request(method, url) as resp:
                    elapsed = time.monotonic() - start
                    binance_latency.labels(endpoint=path).observe(elapsed)

                    data = await resp.json()

                    if resp.status == 429:
                        self._rate_limiter.report_429()
                        binance_requests_total.labels(endpoint=path, status="429").inc()
                        await logger.awarning("binance_rate_limited", attempt=attempt)
                        continue

                    if resp.status >= 400:
                        code = data.get("code", resp.status)
                        msg = data.get("msg", "Unknown error")
                        binance_requests_total.labels(endpoint=path, status="error").inc()
                        self._error_count += 1

                        if code in (-2019, -2022, -4131):
                            raise OrderRejectedError(
                                f"Binance rejected: {msg}",
                                context={"code": code, "params": params},
                            )
                        raise ExecutionError(
                            f"Binance API error: {code} {msg}",
                            context={"code": code, "path": path},
                        )

                    binance_requests_total.labels(endpoint=path, status="ok").inc()
                    return data

            except (aiohttp.ClientError, TimeoutError):
                self._error_count += 1
                if attempt == 2:
                    raise ExecutionError(
                        f"Binance request failed after 3 attempts: {method} {path}"
                    ) from None
                await logger.awarning("binance_retry", attempt=attempt, path=path)

        return {}

    @staticmethod
    def _parse_order_response(data: dict) -> OrderResult:
        status_str = data.get("status", "UNKNOWN")
        status = BINANCE_STATUS_MAP.get(status_str, VenueOrderStatus.REJECTED)

        return OrderResult(
            client_order_id=data.get("clientOrderId", ""),
            venue_order_id=str(data.get("orderId", "")),
            symbol=data.get("symbol", ""),
            side=OrderSide.BUY if data.get("side") == "BUY" else OrderSide.SELL,
            status=status,
            requested_quantity=Decimal(str(data.get("origQty", "0"))),
            filled_quantity=Decimal(str(data.get("executedQty", "0"))),
            remaining_quantity=(
                Decimal(str(data.get("origQty", "0")))
                - Decimal(str(data.get("executedQty", "0")))
            ),
            average_price=Decimal(str(data.get("avgPrice", "0"))),
            venue_timestamp=(
                datetime.fromtimestamp(
                    data.get("updateTime", 0) / 1000, tz=UTC
                )
                if data.get("updateTime")
                else None
            ),
            error_code=str(data.get("code", "")),
            error_message=data.get("msg", ""),
            raw_response=data,
        )
