"""Common execution adapter interface.

Every execution backend (paper, testnet, live) implements ExecutionAdapter.
This ensures the signal engine, risk manager, and exit engine don't need
to know which backend is active — they call the same interface.

The interface is designed around what a real exchange needs:
- place/cancel/query orders
- get positions
- close positions (reduce-only)
- health/connectivity check
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from datetime import datetime


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderRequestType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(StrEnum):
    GTC = "GTC"   # Good til cancelled
    IOC = "IOC"   # Immediate or cancel
    FOK = "FOK"   # Fill or kill


class VenueOrderStatus(StrEnum):
    """Comprehensive order status covering all venue states."""
    PENDING = "pending"           # created locally, not yet sent
    SUBMITTING = "submitting"     # send in progress
    SUBMITTED = "submitted"       # accepted by venue (NEW on Binance, New on Bybit)
    PARTIAL = "partial"           # partially filled
    FILLED = "filled"             # fully filled
    CANCELLING = "cancelling"     # cancel request sent
    CANCELLED = "cancelled"       # confirmed cancelled
    REJECTED = "rejected"         # venue rejected (bad qty, insufficient margin, etc.)
    EXPIRED = "expired"           # venue expired (TTL or FOK/IOC unfilled)
    SUBMIT_FAILED = "submit_failed"   # network error on submit
    CANCEL_FAILED = "cancel_failed"   # network error on cancel


@dataclass
class OrderRequest:
    """Order to be placed on a venue."""
    client_order_id: str = field(default_factory=lambda: str(uuid4()))
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderRequestType = OrderRequestType.MARKET
    quantity: Decimal = Decimal("0")
    price: Decimal | None = None         # required for LIMIT orders
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    signal_id: UUID = field(default_factory=uuid4)
    idempotency_key: str = ""            # venue-specific dedup key


@dataclass
class OrderResult:
    """Result of an order operation (place, cancel, query)."""
    client_order_id: str = ""
    venue_order_id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    status: VenueOrderStatus = VenueOrderStatus.PENDING
    requested_quantity: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    average_price: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    fee_asset: str = ""
    venue_timestamp: datetime | None = None
    error_code: str = ""
    error_message: str = ""
    raw_response: dict = field(default_factory=dict)


@dataclass
class VenuePosition:
    """Position as reported by the venue (for reconciliation)."""
    symbol: str = ""
    side: str = ""                  # "long" | "short" | "both"
    quantity: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    leverage: int = 1
    margin_type: str = "cross"
    venue_timestamp: datetime | None = None


@dataclass
class AdapterHealth:
    """Health status of an execution adapter."""
    connected: bool = False
    last_heartbeat_ms: int = 0
    orders_in_flight: int = 0
    rate_limit_remaining: int = 0
    rate_limit_reset_ms: int = 0
    error_count_1m: int = 0


class ExecutionAdapter(ABC):
    """Abstract interface for all execution backends.

    Implementations: PaperAdapter, BinanceTestnetAdapter, BybitDemoAdapter.
    Every method is async to support real exchange I/O.
    """

    @property
    @abstractmethod
    def venue_name(self) -> str:
        """Venue identifier (e.g. 'paper', 'binance_testnet', 'bybit_demo')."""

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Place an order on the venue. Returns immediately with order status.

        For market orders, status is typically FILLED on return.
        For limit orders, status may be SUBMITTED (waiting for fill).
        """

    @abstractmethod
    async def cancel_order(
        self, symbol: str, client_order_id: str
    ) -> OrderResult:
        """Cancel an open order. Returns updated order status."""

    @abstractmethod
    async def get_order(
        self, symbol: str, client_order_id: str
    ) -> OrderResult | None:
        """Query current status of an order."""

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        """List all open (unfilled, partially filled) orders."""

    @abstractmethod
    async def get_positions(
        self, symbol: str | None = None
    ) -> list[VenuePosition]:
        """Query venue positions for reconciliation."""

    @abstractmethod
    async def close_position(
        self, symbol: str, quantity: Decimal, side: OrderSide
    ) -> OrderResult:
        """Close a position with a reduce-only market order."""

    @abstractmethod
    async def health(self) -> AdapterHealth:
        """Check adapter connectivity and rate limit status."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize connections (WebSocket user stream, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down connections."""
