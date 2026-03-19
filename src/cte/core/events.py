"""Canonical event models for the CTE event bus.

These Pydantic v2 models define the schema for all events flowing through Redis Streams.
Every model is immutable (frozen) and carries provenance metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Venue(str, Enum):
    BINANCE = "binance"
    BYBIT = "bybit"


class Symbol(str, Enum):
    BTCUSDT = "BTCUSDT"
    ETHUSDT = "ETHUSDT"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class SignalAction(str, Enum):
    OPEN_LONG = "open_long"
    CLOSE_LONG = "close_long"
    HOLD = "hold"


class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    TIMEOUT = "timeout"
    INVALIDATION = "invalidation"
    EMERGENCY = "emergency"
    MANUAL = "manual"


class RiskDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Base Event
# ---------------------------------------------------------------------------

class BaseEvent(BaseModel):
    """Base for all CTE events. Immutable with provenance metadata."""

    model_config = {"frozen": True, "extra": "forbid"}

    event_id: UUID = Field(default_factory=_new_id)
    timestamp: datetime = Field(default_factory=_utc_now)
    source: str = ""


# ---------------------------------------------------------------------------
# Market Data Events (Raw)
# ---------------------------------------------------------------------------

class RawTradeEvent(BaseEvent):
    """Raw trade from a venue WebSocket, before normalization."""

    source: str = "connector"
    venue: Venue
    symbol_raw: str
    price: str
    quantity: str
    trade_id: str
    trade_time: int  # venue epoch ms
    is_buyer_maker: bool


class RawOrderbookEvent(BaseEvent):
    """Raw orderbook snapshot/delta from a venue WebSocket."""

    source: str = "connector"
    venue: Venue
    symbol_raw: str
    event_type: str  # snapshot | delta
    bids: list[list[str]]  # [[price, qty], ...]
    asks: list[list[str]]
    update_id: int
    venue_timestamp: int  # epoch ms


# ---------------------------------------------------------------------------
# Normalized (Canonical) Market Events
# ---------------------------------------------------------------------------

class TradeEvent(BaseEvent):
    """Canonical trade event, venue-agnostic."""

    source: str = "normalizer"
    venue: Venue
    symbol: Symbol
    price: Decimal
    quantity: Decimal
    side: Side
    trade_time: datetime
    venue_trade_id: str


class OrderbookLevel(BaseModel):
    model_config = {"frozen": True}

    price: Decimal
    quantity: Decimal


class OrderbookSnapshotEvent(BaseEvent):
    """Canonical orderbook snapshot, venue-agnostic."""

    source: str = "normalizer"
    venue: Venue
    symbol: Symbol
    bids: list[OrderbookLevel]
    asks: list[OrderbookLevel]
    sequence: int
    snapshot_time: datetime


# ---------------------------------------------------------------------------
# Feature Events
# ---------------------------------------------------------------------------

class FeatureVector(BaseEvent):
    """Computed feature set for a symbol at a point in time."""

    source: str = "feature_engine"
    symbol: Symbol
    window_start: datetime
    window_end: datetime

    rsi: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    vwap: float | None = None
    volume_24h: float | None = None
    price_change_pct_1h: float | None = None
    bid_ask_spread_bps: float | None = None
    orderbook_imbalance: float | None = None

    extra_features: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Signal Events
# ---------------------------------------------------------------------------

class SignalEvent(BaseEvent):
    """Trade signal emitted by the signal engine."""

    source: str = "signal_engine"
    symbol: Symbol
    action: SignalAction
    confidence: float = Field(ge=0.0, le=1.0)
    reason: SignalReason

    features_snapshot: dict[str, Any] = Field(default_factory=dict)


class SignalReason(BaseModel):
    """Explainable reason payload attached to every signal."""

    model_config = {"frozen": True}

    primary_trigger: str
    supporting_factors: list[str] = Field(default_factory=list)
    context_flags: dict[str, Any] = Field(default_factory=dict)
    human_readable: str


# ---------------------------------------------------------------------------
# Risk Events
# ---------------------------------------------------------------------------

class RiskAssessmentEvent(BaseEvent):
    """Risk manager decision on a signal."""

    source: str = "risk_manager"
    signal_id: UUID
    symbol: Symbol
    decision: RiskDecision
    reason: str
    checks_performed: list[RiskCheckResult] = Field(default_factory=list)


class RiskCheckResult(BaseModel):
    model_config = {"frozen": True}

    check_name: str
    passed: bool
    value: float | None = None
    threshold: float | None = None
    detail: str = ""


# ---------------------------------------------------------------------------
# Sizing Events
# ---------------------------------------------------------------------------

class SizedOrderEvent(BaseEvent):
    """Order with calculated position size, ready for execution."""

    source: str = "sizing_engine"
    signal_id: UUID
    symbol: Symbol
    side: Side
    order_type: OrderType
    quantity: Decimal
    notional_usd: Decimal
    leverage: int = 1
    reason: str


# ---------------------------------------------------------------------------
# Execution Events
# ---------------------------------------------------------------------------

class OrderEvent(BaseEvent):
    """Order lifecycle event from the execution engine."""

    source: str = "execution_engine"
    order_id: UUID = Field(default_factory=_new_id)
    signal_id: UUID
    symbol: Symbol
    side: Side
    order_type: OrderType
    status: OrderStatus
    requested_quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    average_price: Decimal = Decimal("0")
    venue: Venue = Venue.BINANCE
    venue_order_id: str = ""
    reason: str = ""
    fees: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Exit Events
# ---------------------------------------------------------------------------

class ExitEvent(BaseEvent):
    """Exit decision for an open position."""

    source: str = "exit_engine"
    position_id: UUID
    symbol: Symbol
    exit_reason: ExitReason
    exit_price: Decimal
    pnl: Decimal
    hold_duration_seconds: int
    reason_detail: str


# ---------------------------------------------------------------------------
# Position Events
# ---------------------------------------------------------------------------

class PositionSnapshot(BaseEvent):
    """Current state of an open position."""

    source: str = "execution_engine"
    position_id: UUID
    symbol: Symbol
    side: Side
    entry_price: Decimal
    current_price: Decimal
    quantity: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal = Decimal("0")
    leverage: int = 1
    opened_at: datetime
    signal_id: UUID
    highest_price: Decimal
    lowest_price: Decimal


# ---------------------------------------------------------------------------
# Context Events (whale, news – read-only signals, not primary triggers)
# ---------------------------------------------------------------------------

class WhaleAlertEvent(BaseEvent):
    """Large on-chain transfer detected (context only, not a trigger)."""

    source: str = "whale_alert"
    blockchain: str
    tx_hash: str
    from_address: str
    to_address: str
    amount_usd: Decimal
    token: str
    from_label: str = ""
    to_label: str = ""


class OnChainContextEvent(BaseEvent):
    """Aggregated on-chain context (Etherscan, etc). Read-only context."""

    source: str = "etherscan"
    chain: str
    metric: str
    value: float
    detail: str = ""


# ---------------------------------------------------------------------------
# Redis Stream Key Mapping
# ---------------------------------------------------------------------------

STREAM_KEYS = {
    "raw_trade": "cte:raw:trade",
    "raw_orderbook": "cte:raw:orderbook",
    "trade": "cte:market:trade",
    "orderbook": "cte:market:orderbook",
    "feature": "cte:feature:vector",
    "signal": "cte:signal:event",
    "risk": "cte:risk:assessment",
    "sized_order": "cte:sizing:order",
    "order": "cte:execution:order",
    "exit": "cte:exit:event",
    "position": "cte:position:snapshot",
    "whale": "cte:context:whale",
    "onchain": "cte:context:onchain",
}
