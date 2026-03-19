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
# Mark Price & Liquidation Events (new inputs for streaming feature engine)
# ---------------------------------------------------------------------------

class MarkPriceEvent(BaseEvent):
    """Mark price update from venue (Binance @markPrice, Bybit ticker).

    Mark price is the fair-value index price used for PnL calculation
    and liquidation triggers. Divergence from last traded price can
    indicate funding-rate arbitrage or lagging fills.
    """

    source: str = "normalizer"
    venue: Venue
    symbol: Symbol
    mark_price: Decimal
    index_price: Decimal = Decimal("0")
    funding_rate: Decimal = Decimal("0")
    next_funding_time: datetime | None = None
    venue_timestamp: datetime | None = None


class LiquidationEvent(BaseEvent):
    """Forced liquidation event from venue (Binance @forceOrder, Bybit liquidation).

    Liquidation of longs → bearish pressure (long_liq=True)
    Liquidation of shorts → bullish pressure (long_liq=False)
    """

    source: str = "normalizer"
    venue: Venue
    symbol: Symbol
    side: Side
    price: Decimal
    quantity: Decimal
    is_long_liquidation: bool
    venue_timestamp: datetime | None = None


# ---------------------------------------------------------------------------
# Streaming Feature Engine Output
# ---------------------------------------------------------------------------

class TimeframeFeatures(BaseModel):
    """Features computed for a single rolling window timeframe."""

    model_config = {"frozen": True}

    window_seconds: int
    returns: float | None = None
    returns_z: float | None = None
    momentum_z: float | None = None
    taker_flow_imbalance: float | None = None
    spread_bps: float | None = None
    spread_widening: float | None = None
    ob_imbalance: float | None = None
    liquidation_imbalance: float | None = None
    venue_divergence_bps: float | None = None
    vwap: float | None = None
    trade_count: int = 0
    volume: float = 0.0
    window_fill_pct: float = 0.0


class FreshnessScore(BaseModel):
    """Data freshness across all sources."""

    model_config = {"frozen": True}

    trade_age_ms: int = 0
    orderbook_age_ms: int = 0
    binance_age_ms: int = 0
    bybit_age_ms: int = 0
    composite: float = 0.0


class DataQuality(BaseModel):
    """Diagnostic metadata about data quality at feature computation time."""

    model_config = {"frozen": True}

    warmup_complete: bool = False
    binance_connected: bool = False
    bybit_connected: bool = False
    window_fill_pct: dict[str, float] = Field(default_factory=dict)


class StreamingFeatureVector(BaseEvent):
    """Multi-timeframe streaming feature vector.

    Emitted once per second per symbol. Contains features for all
    four timeframes (10s, 30s, 60s, 5m) plus cross-timeframe scalars.

    This is the primary input for the signal engine in Phase 2+.
    The legacy FeatureVector is kept for backward compatibility.
    """

    source: str = "streaming_feature_engine"
    symbol: Symbol

    # Multi-timeframe feature blocks
    tf_10s: TimeframeFeatures
    tf_30s: TimeframeFeatures
    tf_60s: TimeframeFeatures
    tf_5m: TimeframeFeatures

    # Cross-timeframe / scalar features
    freshness: FreshnessScore
    execution_feasibility: float | None = None
    whale_risk_flag: bool = False
    urgent_news_flag: bool = False

    # Latest raw values for downstream consumers
    last_price: Decimal = Decimal("0")
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    mid_price: Decimal | None = None
    mark_price: Decimal | None = None

    # Diagnostic metadata
    data_quality: DataQuality = Field(default_factory=DataQuality)


# ---------------------------------------------------------------------------
# Redis Stream Key Mapping
# ---------------------------------------------------------------------------

STREAM_KEYS = {
    "raw_trade": "cte:raw:trade",
    "raw_orderbook": "cte:raw:orderbook",
    "raw_mark_price": "cte:raw:mark_price",
    "raw_liquidation": "cte:raw:liquidation",
    "trade": "cte:market:trade",
    "orderbook": "cte:market:orderbook",
    "mark_price": "cte:market:mark_price",
    "liquidation": "cte:market:liquidation",
    "feature": "cte:feature:vector",
    "feature_streaming": "cte:feature:streaming",
    "signal": "cte:signal:event",
    "risk": "cte:risk:assessment",
    "sized_order": "cte:sizing:order",
    "order": "cte:execution:order",
    "exit": "cte:exit:event",
    "position": "cte:position:snapshot",
    "whale": "cte:context:whale",
    "onchain": "cte:context:onchain",
}
