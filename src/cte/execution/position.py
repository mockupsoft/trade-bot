"""Paper position with state machine, MFE/MAE tracking, and full audit trail.

Position Lifecycle:
  PENDING → OPEN → CLOSED
                → REDUCED → CLOSED  (future: partial closes)

Every price tick updates MFE (Max Favorable Excursion) and MAE
(Max Adverse Excursion), which measure the best and worst unrealized
PnL the position experienced during its lifetime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4


class PositionStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    REDUCED = "reduced"
    CLOSED = "closed"


@dataclass
class PaperPosition:
    """Full-featured paper position with analytics tracking.

    All monetary values use Decimal. All timestamps are UTC.
    """

    position_id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    direction: str = "long"
    status: PositionStatus = PositionStatus.PENDING

    # Signal provenance
    signal_id: UUID = field(default_factory=uuid4)
    signal_tier: str = ""
    entry_reason: str = ""
    composite_score: float = 0.0

    # Fill details
    entry_price: Decimal = Decimal("0")
    fill_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    notional_usd: Decimal = Decimal("0")
    leverage: int = 1

    # Slippage and cost tracking
    signal_price: Decimal = Decimal("0")     # price at signal generation time
    modeled_slippage_bps: Decimal = Decimal("0")
    effective_spread_bps: Decimal = Decimal("0")
    fill_model_used: str = ""
    estimated_fees_usd: Decimal = Decimal("0")

    # Timing
    signal_time: datetime | None = None
    fill_time: datetime | None = None
    close_time: datetime | None = None
    entry_latency_ms: int = 0                # signal_time → fill_time
    modeled_fill_latency_ms: int = 0         # simulated exchange processing

    # Risk
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    stop_distance_usd: Decimal = Decimal("0")  # entry × stop_pct × qty

    # Price tracking (updated on every tick)
    current_price: Decimal = Decimal("0")
    highest_price: Decimal = Decimal("0")
    lowest_price: Decimal = Decimal("0")

    # Excursion analytics
    mfe_pct: float = 0.0  # max favorable excursion (%)
    mae_pct: float = 0.0  # max adverse excursion (%)
    mfe_usd: Decimal = Decimal("0")
    mae_usd: Decimal = Decimal("0")

    # PnL
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")

    # Exit
    exit_price: Decimal = Decimal("0")
    exit_reason: str = ""
    exit_detail: str = ""

    # State history
    state_transitions: list[tuple[str, str, str]] = field(default_factory=list)

    def open(self, fill_price: Decimal, fill_time: datetime) -> None:
        """Transition from PENDING to OPEN."""
        if self.status != PositionStatus.PENDING:
            raise ValueError(f"Cannot open position in state {self.status}")

        self.status = PositionStatus.OPEN
        self.fill_price = fill_price
        self.entry_price = fill_price
        self.fill_time = fill_time
        self.current_price = fill_price
        self.highest_price = fill_price
        self.lowest_price = fill_price

        if self.signal_time:
            delta = fill_time - self.signal_time
            self.entry_latency_ms = int(delta.total_seconds() * 1000)

        self.stop_distance_usd = (
            self.entry_price * Decimal(str(self.stop_loss_pct)) * self.quantity
        )

        self.state_transitions.append(
            ("pending", "open", fill_time.isoformat())
        )

    def update_price(self, price: Decimal) -> None:
        """Update market price and recalculate MFE/MAE."""
        if self.status not in (PositionStatus.OPEN, PositionStatus.REDUCED):
            return

        self.current_price = price

        if price > self.highest_price:
            self.highest_price = price
        if price < self.lowest_price:
            self.lowest_price = price

        if self.entry_price <= 0:
            return

        if self.direction == "long":
            favorable_pct = float((price - self.entry_price) / self.entry_price)
            adverse_pct = float((self.entry_price - price) / self.entry_price)
        else:
            favorable_pct = float((self.entry_price - price) / self.entry_price)
            adverse_pct = float((price - self.entry_price) / self.entry_price)

        if favorable_pct > self.mfe_pct:
            self.mfe_pct = favorable_pct
            self.mfe_usd = Decimal(str(favorable_pct)) * self.entry_price * self.quantity

        if adverse_pct > self.mae_pct:
            self.mae_pct = adverse_pct
            self.mae_usd = Decimal(str(adverse_pct)) * self.entry_price * self.quantity

        if self.direction == "long":
            self.unrealized_pnl = (price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - price) * self.quantity

    def close(
        self,
        exit_price: Decimal,
        close_time: datetime,
        exit_reason: str,
        exit_detail: str = "",
    ) -> None:
        """Transition from OPEN to CLOSED."""
        if self.status not in (PositionStatus.OPEN, PositionStatus.REDUCED):
            raise ValueError(f"Cannot close position in state {self.status}")

        self.update_price(exit_price)

        old_status = self.status.value
        self.status = PositionStatus.CLOSED
        self.exit_price = exit_price
        self.close_time = close_time
        self.exit_reason = exit_reason
        self.exit_detail = exit_detail

        if self.direction == "long":
            self.realized_pnl = (exit_price - self.entry_price) * self.quantity
        else:
            self.realized_pnl = (self.entry_price - exit_price) * self.quantity

        self.realized_pnl -= self.estimated_fees_usd
        self.unrealized_pnl = Decimal("0")

        self.state_transitions.append(
            (old_status, "closed", close_time.isoformat())
        )

    @property
    def r_multiple(self) -> float | None:
        """PnL expressed as a multiple of initial risk (stop distance).

        R = 1.0 → earned exactly what was risked
        R = 2.0 → earned 2x the risk
        R = -1.0 → lost exactly the stop distance
        """
        if self.stop_distance_usd <= 0:
            return None
        return float(self.realized_pnl / self.stop_distance_usd)

    @property
    def hold_duration_seconds(self) -> int:
        if not self.fill_time:
            return 0
        end = self.close_time or self.fill_time
        return int((end - self.fill_time).total_seconds())

    @property
    def is_open(self) -> bool:
        return self.status in (PositionStatus.OPEN, PositionStatus.REDUCED)

    @property
    def is_winner(self) -> bool:
        return self.realized_pnl > 0

    def to_dict(self) -> dict:
        """Serialize to dict for DB persistence and API responses."""
        return {
            "position_id": str(self.position_id),
            "symbol": self.symbol,
            "direction": self.direction,
            "status": self.status.value,
            "signal_id": str(self.signal_id),
            "signal_tier": self.signal_tier,
            "entry_reason": self.entry_reason,
            "composite_score": self.composite_score,
            "entry_price": str(self.entry_price),
            "fill_price": str(self.fill_price),
            "quantity": str(self.quantity),
            "notional_usd": str(self.notional_usd),
            "modeled_slippage_bps": str(self.modeled_slippage_bps),
            "effective_spread_bps": str(self.effective_spread_bps),
            "fill_model_used": self.fill_model_used,
            "signal_time": self.signal_time.isoformat() if self.signal_time else None,
            "fill_time": self.fill_time.isoformat() if self.fill_time else None,
            "close_time": self.close_time.isoformat() if self.close_time else None,
            "entry_latency_ms": self.entry_latency_ms,
            "modeled_fill_latency_ms": self.modeled_fill_latency_ms,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "stop_distance_usd": str(self.stop_distance_usd),
            "mfe_pct": round(self.mfe_pct, 6),
            "mae_pct": round(self.mae_pct, 6),
            "mfe_usd": str(self.mfe_usd),
            "mae_usd": str(self.mae_usd),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "exit_price": str(self.exit_price),
            "exit_reason": self.exit_reason,
            "exit_detail": self.exit_detail,
            "r_multiple": self.r_multiple,
            "hold_duration_seconds": self.hold_duration_seconds,
            "highest_price": str(self.highest_price),
            "lowest_price": str(self.lowest_price),
            "state_transitions": self.state_transitions,
        }
