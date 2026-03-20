"""Paper execution engine — bid/ask-aware, replay-safe, full audit trail.

Converts scored signals into paper positions using realistic fill models.
No asyncio.sleep, no datetime.now — all timing comes from event timestamps
to support deterministic replay.

Key differences from the old paper engine:
- Fills at bid/ask, not mid-price
- Carries signal tier, entry reason, composite score onto position
- Tracks MFE/MAE on every price update
- Records entry latency, modeled fill latency, slip cost
- Position state machine (PENDING → OPEN → CLOSED)
- Optional VWAP depth-based fills
- All timestamps from event clock (replay-safe)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from prometheus_client import Counter, Gauge, Histogram

from cte.core.events import (
    ExitReason,
    ScoredSignalEvent,
    StreamingFeatureVector,
)
from cte.execution.fill_model import BookLevel, FillMode, compute_fill
from cte.execution.position import PaperPosition, PositionStatus
from cte.exits.engine import LayeredExitEngine

if TYPE_CHECKING:
    from uuid import UUID

    from cte.core.settings import ExecutionSettings, ExitSettings
    from cte.core.streams import StreamPublisher

logger = structlog.get_logger(__name__)

paper_fills_total = Counter(
    "cte_paper_fills_total", "Paper fills executed", ["symbol"]
)
paper_positions_open = Gauge(
    "cte_paper_positions_open", "Open paper positions", ["symbol"]
)
paper_fill_slip = Histogram(
    "cte_paper_fill_slippage_bps", "Fill slippage in bps", ["symbol"],
    buckets=[0.5, 1, 2, 3, 5, 7, 10, 15, 20],
)
paper_pnl_total = Gauge("cte_paper_pnl_total_usd", "Total paper PnL")


def _paper_gain_pct(pos: PaperPosition, price: Decimal) -> float:
    """Unrealized gain fraction (positive = profit for long)."""
    if pos.entry_price <= 0:
        return 0.0
    if pos.direction == "long":
        return float((price - pos.entry_price) / pos.entry_price)
    return float((pos.entry_price - price) / pos.entry_price)


def _hold_minutes(pos: PaperPosition, now: datetime) -> float:
    if not pos.fill_time:
        return 0.0
    return (now - pos.fill_time).total_seconds() / 60.0


class PaperExecutionEngine:
    """Bid/ask-aware paper execution engine with full position tracking."""

    def __init__(
        self,
        exec_settings: ExecutionSettings,
        exit_settings: ExitSettings,
        publisher: StreamPublisher,
        fill_mode: FillMode = FillMode.SPREAD_CROSSING,
    ) -> None:
        self._exec = exec_settings
        self._exits = exit_settings
        self._publisher = publisher
        self._fill_mode = fill_mode

        self._positions: dict[UUID, PaperPosition] = {}
        self._closed_positions: list[PaperPosition] = []

        # Latest book per symbol (bid, ask)
        self._books: dict[str, tuple[Decimal, Decimal]] = {}
        self._book_levels: dict[str, tuple[list[BookLevel], list[BookLevel]]] = {}

        self._total_realized_pnl = Decimal("0")
        self._layered_exit = LayeredExitEngine()

    # ── Book Updates ──────────────────────────────────────────

    def update_book(
        self,
        symbol: str,
        best_bid: Decimal,
        best_ask: Decimal,
        bid_levels: list[BookLevel] | None = None,
        ask_levels: list[BookLevel] | None = None,
    ) -> None:
        """Update the latest orderbook for a symbol."""
        self._books[symbol] = (best_bid, best_ask)
        if bid_levels is not None and ask_levels is not None:
            self._book_levels[symbol] = (bid_levels, ask_levels)

    def update_price(self, symbol: str, price: Decimal) -> None:
        """Update market price and re-evaluate all open positions for this symbol."""
        for pos in self._positions.values():
            if pos.symbol == symbol and pos.is_open:
                pos.update_price(price)

    # ── Entry ─────────────────────────────────────────────────

    def open_position(
        self,
        signal: ScoredSignalEvent,
        quantity: Decimal,
        notional_usd: Decimal,
        event_time: datetime,
        *,
        warmup_phase: str = "full",
    ) -> PaperPosition | None:
        """Create and fill a paper position from a scored signal.

        Uses bid/ask from the latest book update. If no book available,
        rejects the fill (cannot fill without a quote).
        """
        symbol = signal.symbol.value
        book = self._books.get(symbol)
        if book is None:
            return None

        best_bid, best_ask = book

        # Determine fill side
        side = "buy" if signal.action.value == "open_long" else "sell"

        # Choose book levels for VWAP mode
        levels = None
        if self._fill_mode == FillMode.VWAP_DEPTH:
            stored = self._book_levels.get(symbol)
            if stored:
                levels = stored[1] if side == "buy" else stored[0]

        # Compute fill
        fill_result = compute_fill(
            side=side,
            quantity=quantity,
            best_bid=best_bid,
            best_ask=best_ask,
            slippage_bps=self._exec.slippage_bps,
            mode=self._fill_mode,
            book_levels=levels,
        )

        # Modeled latency: offset from event time (no sleep!)
        fill_time = event_time + timedelta(milliseconds=self._exec.fill_delay_ms)

        # Signal price = mid at signal time
        signal_price = (best_bid + best_ask) / 2

        position = PaperPosition(
            symbol=symbol,
            direction="long" if side == "buy" else "short",
            status=PositionStatus.PENDING,
            signal_id=signal.event_id,
            signal_tier=signal.tier.value,
            entry_reason=signal.reason.human_readable,
            composite_score=signal.composite_score,
            warmup_phase=warmup_phase,
            quantity=quantity,
            notional_usd=notional_usd,
            signal_price=signal_price,
            modeled_slippage_bps=fill_result.slippage_bps,
            effective_spread_bps=fill_result.effective_spread_bps,
            fill_model_used=fill_result.model_used.value,
            signal_time=event_time,
            modeled_fill_latency_ms=self._exec.fill_delay_ms,
            stop_loss_pct=self._exits.stop_loss_pct,
            take_profit_pct=self._exits.take_profit_pct,
            estimated_fees_usd=notional_usd * Decimal("0.0004"),  # 4 bps taker fee
        )

        # Transition PENDING → OPEN
        position.open(fill_result.fill_price, fill_time)

        self._positions[position.position_id] = position

        paper_fills_total.labels(symbol=symbol).inc()
        paper_positions_open.labels(symbol=symbol).inc()
        paper_fill_slip.labels(symbol=symbol).observe(float(fill_result.slippage_bps))

        return position

    def open_position_from_venue_fill(
        self,
        signal: ScoredSignalEvent,
        quantity: Decimal,
        notional_usd: Decimal,
        event_time: datetime,
        fill_price: Decimal,
        *,
        warmup_phase: str = "full",
        venue_order_id: str = "",
        entry_client_order_id: str = "",
        entry_fees_usd: Decimal | None = None,
    ) -> PaperPosition | None:
        """Register an OPEN position using a venue-reported fill (no bid/ask simulation)."""
        symbol = signal.symbol.value
        book = self._books.get(symbol)
        if book is None:
            return None

        best_bid, best_ask = book
        signal_price = (best_bid + best_ask) / Decimal("2")
        fees = entry_fees_usd
        if fees is None:
            fees = notional_usd * Decimal("0.0004")

        fill_time = event_time + timedelta(milliseconds=self._exec.fill_delay_ms)

        position = PaperPosition(
            symbol=symbol,
            direction="long",
            status=PositionStatus.PENDING,
            signal_id=signal.event_id,
            signal_tier=signal.tier.value,
            entry_reason=signal.reason.human_readable,
            composite_score=signal.composite_score,
            warmup_phase=warmup_phase,
            quantity=quantity,
            notional_usd=notional_usd,
            signal_price=signal_price,
            modeled_slippage_bps=Decimal("0"),
            effective_spread_bps=Decimal("0"),
            fill_model_used="venue_market",
            signal_time=event_time,
            modeled_fill_latency_ms=self._exec.fill_delay_ms,
            stop_loss_pct=self._exits.stop_loss_pct,
            take_profit_pct=self._exits.take_profit_pct,
            estimated_fees_usd=fees,
            venue_order_id=venue_order_id,
            entry_client_order_id=entry_client_order_id,
        )

        position.open(fill_price, fill_time)
        self._positions[position.position_id] = position
        paper_fills_total.labels(symbol=symbol).inc()
        paper_positions_open.labels(symbol=symbol).inc()
        return position

    # ── Exit ──────────────────────────────────────────────────

    def close_position(
        self,
        position_id: UUID,
        exit_reason: str,
        exit_detail: str,
        event_time: datetime,
    ) -> PaperPosition | None:
        """Close a position at current bid/ask."""
        position = self._positions.get(position_id)
        if position is None or not position.is_open:
            return None

        book = self._books.get(position.symbol)
        if book is None:
            return None

        best_bid, best_ask = book
        side = "sell" if position.direction == "long" else "buy"

        fill_result = compute_fill(
            side=side,
            quantity=position.quantity,
            best_bid=best_bid,
            best_ask=best_ask,
            slippage_bps=self._exec.slippage_bps,
            mode=self._fill_mode,
        )

        position.close(
            exit_price=fill_result.fill_price,
            close_time=event_time,
            exit_reason=exit_reason,
            exit_detail=exit_detail,
        )

        del self._positions[position_id]
        self._closed_positions.append(position)

        self._total_realized_pnl += position.realized_pnl

        self._layered_exit.cleanup(position_id)

        paper_positions_open.labels(symbol=position.symbol).dec()
        paper_pnl_total.set(float(self._total_realized_pnl))

        return position

    def close_position_external_fill(
        self,
        position_id: UUID,
        exit_price: Decimal,
        event_time: datetime,
        exit_reason: str,
        exit_detail: str,
        *,
        additional_exit_fees_usd: Decimal = Decimal("0"),
    ) -> PaperPosition | None:
        """Close a position at a venue-reported exit price (no bid/ask fill model)."""
        position = self._positions.get(position_id)
        if position is None or not position.is_open:
            return None

        position.close(
            exit_price=exit_price,
            close_time=event_time,
            exit_reason=exit_reason,
            exit_detail=exit_detail,
            additional_exit_fees_usd=additional_exit_fees_usd,
        )

        del self._positions[position_id]
        self._closed_positions.append(position)

        self._total_realized_pnl += position.realized_pnl
        self._layered_exit.cleanup(position_id)

        paper_positions_open.labels(symbol=position.symbol).dec()
        paper_pnl_total.set(float(self._total_realized_pnl))

        return position

    def plan_exits(
        self,
        symbol: str,
        current_price: Decimal,
        event_time: datetime,
        features: StreamingFeatureVector | None = None,
    ) -> list[tuple[UUID, str, str]]:
        """Plan which positions would exit (layered engine + TP cap + max hold).

        Same rules as :meth:`evaluate_exits` but does not close; used when a venue
        fill must be executed before local state is finalized.
        """
        planned: list[tuple[UUID, str, str]] = []
        position_ids = [
            pid for pid, pos in self._positions.items()
            if pos.symbol == symbol and pos.is_open
        ]

        book = self._books.get(symbol)
        best_bid = current_price
        best_ask = current_price
        if book is not None:
            best_bid, best_ask = book

        for pid in position_ids:
            pos = self._positions[pid]

            decision = self._layered_exit.evaluate(
                pos,
                current_price,
                event_time,
                features,
                best_bid,
                best_ask,
                exit_settings=self._exits,
            )

            if decision.should_exit:
                planned.append((pid, decision.exit_reason, decision.exit_detail))
                continue

            gain = _paper_gain_pct(pos, current_price)
            hold_min = _hold_minutes(pos, event_time)

            if gain >= self._exits.take_profit_pct:
                detail = (
                    f"Gain {gain:.2%} reached take-profit cap {self._exits.take_profit_pct:.2%} "
                    "(after layered evaluation)"
                )
                planned.append((pid, ExitReason.TAKE_PROFIT.value, detail))
                continue

            if hold_min >= self._exits.max_hold_minutes:
                detail = (
                    f"Held {hold_min:.0f}m ≥ max_hold {self._exits.max_hold_minutes}m "
                    "(operational cap after layered evaluation)"
                )
                planned.append((pid, ExitReason.TIMEOUT.value, detail))

        return planned

    # ── Exit Condition Evaluation ─────────────────────────────

    def evaluate_exits(
        self,
        symbol: str,
        current_price: Decimal,
        event_time: datetime,
        features: StreamingFeatureVector | None = None,
    ) -> list[PaperPosition]:
        """Evaluate 5-layer smart exits for all open positions of ``symbol``.

        Uses :class:`LayeredExitEngine` (L1→L5). After layers, applies configured
        **take-profit cap** and **max hold** from ``ExitSettings`` so ``defaults.toml``
        targets remain enforceable alongside tier patience.

        Returns list of positions that were closed.
        """
        closed: list[PaperPosition] = []
        for pid, reason, detail in self.plan_exits(
            symbol, current_price, event_time, features
        ):
            result = self.close_position(pid, reason, detail, event_time)
            if result:
                closed.append(result)
        return closed

    # ── Accessors ─────────────────────────────────────────────

    @property
    def open_positions(self) -> dict[UUID, PaperPosition]:
        return dict(self._positions)

    @property
    def closed_positions(self) -> list[PaperPosition]:
        return list(self._closed_positions)

    @property
    def total_realized_pnl(self) -> Decimal:
        return self._total_realized_pnl

    def get_position(self, position_id: UUID) -> PaperPosition | None:
        return self._positions.get(position_id) or next(
            (p for p in self._closed_positions if p.position_id == position_id), None
        )
