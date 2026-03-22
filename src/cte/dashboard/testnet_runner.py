"""In-process Binance USDⓈ-M **testnet** execution loop for the dashboard.

Mirrors :class:`DashboardPaperRunner` (signal → risk → size) but sends **real**
REST orders to ``https://testnet.binancefuture.com`` and keeps local
:class:`PaperExecutionEngine` state for 5-layer exits (venue close on trigger).

Requires ``CTE_ENGINE_MODE=demo``, ``CTE_EXECUTION_MODE=testnet``, testnet API
keys, and ``CTE_DASHBOARD_VENUE_LOOP`` not disabled (default on when testnet).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from decimal import ROUND_DOWN, Decimal
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import structlog

from cte.core.events import RiskDecision, SignalEvent
from cte.core.exceptions import ExecutionError, OrderRejectedError
from cte.core.settings import CTESettings, ExecutionMode, ExecutionSettings
from cte.dashboard.paper_runner import _SYMBOL_MAP as _SYMBOL_MAP
from cte.dashboard.paper_runner import (
    EntryDiagnostics,
    _dashboard_early_size_mult,
    _dashboard_paper_interval_sec,
    _dashboard_risk_settings,
    _dashboard_signal_settings,
    _dashboard_stall_warn_sec,
    _dashboard_warmup_thresholds,
    _env_bool,
    _event_time_utc,
    _has_open_position_same_direction,
    _iso_utc,
    _mid_price,
    try_build_streaming_vector_from_ticker,
)
from cte.execution.adapter import OrderRequest, OrderResult, OrderSide, VenueOrderStatus
from cte.execution.binance_adapter import BinanceTestnetAdapter
from cte.execution.bybit_adapter import BybitDemoAdapter
from cte.execution.paper import PaperExecutionEngine
from cte.execution.reconciliation import LocalPositionView, PositionReconciler
from cte.analytics.engine import AnalyticsEngine
from cte.market.feed import MarketDataFeed, TickerState
from cte.ops.kill_switch import OperationsController
from cte.risk.manager import PortfolioState, RiskManager
from cte.signals.engine import ScoringSignalEngine
from cte.sizing.engine import SizingEngine

if TYPE_CHECKING:
    from datetime import datetime

    from cte.execution.position import PaperPosition

logger = structlog.get_logger("dashboard.testnet_runner")

# Binance linear futures quantity step (conservative; enough for dashboard sizes).
_QTY_STEP: dict[str, Decimal] = {
    "BTCUSDT": Decimal("0.001"),
    "ETHUSDT": Decimal("0.001"),
    "BNBUSDT": Decimal("0.01"),
    "SOLUSDT": Decimal("0.01"),
    "XRPUSDT": Decimal("0.1"),
    "DOGEUSDT": Decimal("1"),
    "ADAUSDT": Decimal("1"),
    "AVAXUSDT": Decimal("0.01"),
    "LINKUSDT": Decimal("0.01"),
    "DOTUSDT": Decimal("0.01"),
}


def _round_down_qty(symbol: str, q: Decimal) -> Decimal:
    step = _QTY_STEP.get(symbol, Decimal("0.001"))
    if q <= 0:
        return Decimal("0")
    n = (q / step).to_integral_value(rounding=ROUND_DOWN)
    return (n * step).quantize(step)


def dashboard_execution_venue() -> str:
    """Explicit venue for dashboard REST execution: ``binance_testnet`` | ``bybit_demo``."""
    return (os.environ.get("CTE_DASHBOARD_EXECUTION_VENUE") or "binance_testnet").strip().lower()


def venue_proof_symbol() -> str | None:
    """When set, only this symbol receives venue entries (feed may include more symbols)."""
    raw = (os.environ.get("CTE_DASHBOARD_VENUE_PROOF_SYMBOL") or "").strip().upper()
    return raw if raw else None


def venue_loop_enabled_for_settings(settings: CTESettings) -> bool:
    """True when dashboard should run venue REST loop instead of pure paper."""
    from cte.dashboard.paper_runner import paper_loop_enabled

    if not paper_loop_enabled():
        return False
    if settings.execution.mode != ExecutionMode.TESTNET:
        return False
    raw = (os.environ.get("CTE_DASHBOARD_VENUE_LOOP") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    v = dashboard_execution_venue()
    if v == "bybit_demo":
        k = (os.environ.get("CTE_BYBIT_DEMO_API_KEY") or "").strip()
        s = (os.environ.get("CTE_BYBIT_DEMO_API_SECRET") or "").strip()
        return bool(k and s)
    k = (os.environ.get("CTE_BINANCE_TESTNET_API_KEY") or "").strip()
    s = (os.environ.get("CTE_BINANCE_TESTNET_API_SECRET") or "").strip()
    return bool(k and s)


def build_dashboard_venue_runner(
    *,
    settings: CTESettings,
    market_feed: Callable[[], MarketDataFeed | None],
    analytics_engine: Callable[[], AnalyticsEngine | None],
    ops_controller: Callable[[], OperationsController],
    symbols: tuple[str, ...],
) -> DashboardTestnetRunner:
    """Construct the venue runner for the configured ``CTE_DASHBOARD_EXECUTION_VENUE``."""
    v = dashboard_execution_venue()
    if v == "bybit_demo":
        key = (os.environ.get("CTE_BYBIT_DEMO_API_KEY") or "").strip()
        secret = (os.environ.get("CTE_BYBIT_DEMO_API_SECRET") or "").strip()
        base = (os.environ.get("CTE_BYBIT_REST_BASE_URL") or "").strip() or "https://api-demo.bybit.com"
        adapter = BybitDemoAdapter(api_key=key, api_secret=secret, base_url=base)
        return DashboardTestnetRunner(
            settings=settings,
            market_feed=market_feed,
            analytics_engine=analytics_engine,
            ops_controller=ops_controller,
            symbols=symbols,
            adapter=adapter,
            execution_channel="bybit_linear_demo",
            analytics_venue="bybit_demo",
            proof_symbol=venue_proof_symbol(),
        )
    key = (os.environ.get("CTE_BINANCE_TESTNET_API_KEY") or "").strip()
    secret = (os.environ.get("CTE_BINANCE_TESTNET_API_SECRET") or "").strip()
    rest = (os.environ.get("CTE_BINANCE_TESTNET_REST_URL") or "").strip()
    base = rest or "https://testnet.binancefuture.com"
    adapter = BinanceTestnetAdapter(api_key=key, api_secret=secret, base_url=base)
    return DashboardTestnetRunner(
        settings=settings,
        market_feed=market_feed,
        analytics_engine=analytics_engine,
        ops_controller=ops_controller,
        symbols=symbols,
        adapter=adapter,
        execution_channel="binance_usdm_testnet",
        analytics_venue="binance_testnet",
        proof_symbol=venue_proof_symbol(),
    )


class DashboardTestnetRunner:
    """Live testnet orders + local mirror for layered exits and analytics."""

    def __init__(
        self,
        *,
        settings: CTESettings,
        market_feed: Callable[[], MarketDataFeed | None],
        analytics_engine: Callable[[], AnalyticsEngine | None],
        ops_controller: Callable[[], OperationsController],
        symbols: tuple[str, ...],
        adapter: BinanceTestnetAdapter | BybitDemoAdapter | None = None,
        execution_channel: str = "binance_usdm_testnet",
        analytics_venue: str = "binance_testnet",
        proof_symbol: str | None = None,
    ) -> None:
        self._settings = settings
        self._market_feed = market_feed
        self._analytics_engine = analytics_engine
        self._ops = ops_controller
        self._symbols = symbols
        self._execution_channel = execution_channel
        self._analytics_venue = analytics_venue
        self._proof_symbol = proof_symbol
        self._warmup_early, self._warmup_full = _dashboard_warmup_thresholds()

        self._publisher = AsyncMock()
        self._publisher.publish = AsyncMock(return_value="ok")

        self._portfolio = PortfolioState(initial_capital=Decimal("10000"))
        self._risk = RiskManager(
            _dashboard_risk_settings(settings.risk, len(symbols)),
            self._publisher,
            self._portfolio,
        )

        sig = _dashboard_signal_settings(settings.signals)
        raw_tier = (os.environ.get("CTE_DASHBOARD_PAPER_TIER_C_THRESHOLD") or "").strip()
        self._demo_entries = _env_bool("CTE_DASHBOARD_PAPER_DEMO_ENTRIES", True)
        if raw_tier:
            sig = sig.model_copy(update={"tier_c_threshold": float(raw_tier)})
        elif self._demo_entries:
            sig = sig.model_copy(
                update={"tier_c_threshold": min(sig.tier_c_threshold, 0.36)},
            )
        self._signal_settings = sig
        self._signal_engine = ScoringSignalEngine(
            sig,
            self._publisher,
            warmup_gate_mode="dashboard_staged",
        )

        exec_paper = ExecutionSettings(
            mode=ExecutionMode.PAPER,
            slippage_bps=settings.execution.slippage_bps,
            fill_delay_ms=settings.execution.fill_delay_ms,
            max_retries=settings.execution.max_retries,
            retry_delay_sec=settings.execution.retry_delay_sec,
            fill_model=settings.execution.fill_model,
            fee_bps=settings.execution.fee_bps,
        )
        self._mirror = PaperExecutionEngine(
            exec_paper,
            settings.exits,
            self._publisher,
        )

        if adapter is None:
            key = (os.environ.get("CTE_BINANCE_TESTNET_API_KEY") or "").strip()
            secret = (os.environ.get("CTE_BINANCE_TESTNET_API_SECRET") or "").strip()
            rest = (os.environ.get("CTE_BINANCE_TESTNET_REST_URL") or "").strip()
            base = rest or "https://testnet.binancefuture.com"
            self._adapter = BinanceTestnetAdapter(
                api_key=key,
                api_secret=secret,
                base_url=base,
            )
        else:
            self._adapter = adapter

        self._mid_history: dict[str, deque[Decimal]] = {
            s: deque(maxlen=400) for s in symbols
        }

        self._reconciler = PositionReconciler()
        self._recon_mismatches = 0
        self._recon_last: dict[str, Any] = {"status": "not_run", "details": []}
        self._last_balance: dict[str, str] = {}
        # First wallet sync must reset daily_high_water; otherwise portfolio_value
        # jumps from paper default (10k) to real available (~few k) and risk sees
        # fake ~50% daily drawdown (veto everything).
        self._wallet_portfolio_initialized: bool = False

        self._running = False
        self._last_error: str | None = None
        self._ticks_ok = 0
        self._entries_total = 0
        self._exits_recorded = 0
        self._last_skip: dict[str, str] = {}

        self._diag = EntryDiagnostics()
        self._runner_started_mono: float | None = None
        self._first_eligible_mono: float | None = None
        self._first_entry_mono: float | None = None
        self._first_entry_ticks: int | None = None
        self._stall_warned = False
        self._symbol_gate_state: dict[str, str] = {}
        self._last_eligible_signal_at: datetime | None = None
        self._last_risk_approved_at: datetime | None = None
        self._last_nonzero_sizing_at: datetime | None = None
        self._last_execution_attempt_at: datetime | None = None
        self._recon_tick = 0
        self._venue_entry_orders_sent = 0
        self._venue_entry_orders_filled = 0
        self._venue_exit_orders_sent = 0
        self._venue_exit_orders_filled = 0
        self._first_venue_order_id: str | None = None
        self._last_venue_error: str | None = None

    def stop(self) -> None:
        self._running = False

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _pipeline_stall_analysis(self) -> dict[str, Any]:
        if self._entries_total > 0:
            return {
                "stalled": False,
                "furthest_stage": "opened",
                "dominant_blocker": None,
                "hint": "Testnet entries have occurred.",
            }
        pos_counts = {k: v for k, v in self._diag.global_counts.items() if v > 0}
        top = max(pos_counts.items(), key=lambda x: x[1])[0] if pos_counts else None
        if self._last_execution_attempt_at:
            stage = "execution_attempted"
        elif self._last_nonzero_sizing_at:
            stage = "sized"
        elif self._last_risk_approved_at:
            stage = "risk_approved"
        elif self._last_eligible_signal_at:
            stage = "eligible_signal"
        else:
            stage = "pre_signal"
        hints: dict[str, str] = {
            "pre_signal": "No tier-eligible signal yet — warmup, gates, tier floor, cooldown.",
            "eligible_signal": "Signal passed scoring; not yet risk-approved.",
            "risk_approved": "Risk approved; sizing returned None or zero.",
            "sized": "Sized order ready; venue entry not placed or rejected.",
            "execution_attempted": "REST order sent; inspect venue response / min notional.",
        }
        hint = hints.get(stage, "")
        if top:
            hint += f" Most frequent block: {top}."
        return {
            "stalled": self._ticks_ok > 5,
            "furthest_stage": stage,
            "dominant_blocker": top,
            "hint": hint,
        }

    def status_dict(self) -> dict[str, Any]:
        open_n = sum(1 for p in self._mirror.open_positions.values() if p.is_open)
        now_m = time.monotonic()
        started = self._runner_started_mono
        stall_sec = _dashboard_stall_warn_sec()
        stall_active = False
        top_blocker: str | None = None
        pos_counts = {k: v for k, v in self._diag.global_counts.items() if v > 0}
        if pos_counts:
            top_blocker = max(pos_counts.items(), key=lambda x: x[1])[0]
        if (
            started is not None
            and self._entries_total == 0
            and (now_m - started) > stall_sec
            and self._ticks_ok > 30
        ):
            stall_active = True
            if not self._stall_warned:
                self._stall_warned = True
                logger.warning(
                    "testnet_stall_no_entries",
                    seconds=int(now_m - started),
                    ticks=self._ticks_ok,
                    top_blocker=top_blocker,
                )

        t_elig = (
            None
            if self._first_eligible_mono is None or started is None
            else self._first_eligible_mono - started
        )
        t_entry = (
            None
            if self._first_entry_mono is None or started is None
            else self._first_entry_mono - started
        )

        pipe = self._pipeline_stall_analysis()
        return {
            "runner_class": "DashboardTestnetRunner",
            "in_process_execution": "demo_exchange",
            "execution_mode": "testnet",
            "execution_channel": self._execution_channel,
            "dashboard_execution_venue": dashboard_execution_venue(),
            "venue_proof_symbol": self._proof_symbol,
            "ticks_ok": self._ticks_ok,
            "entries_total": self._entries_total,
            "exits_recorded": self._exits_recorded,
            "open_positions": open_n,
            "last_error": self._last_error,
            "pipeline_timestamps": {
                "last_eligible_signal_at": _iso_utc(self._last_eligible_signal_at),
                "last_risk_approved_signal_at": _iso_utc(self._last_risk_approved_at),
                "last_nonzero_sizing_at": _iso_utc(self._last_nonzero_sizing_at),
                "last_execution_attempt_at": _iso_utc(self._last_execution_attempt_at),
            },
            "pipeline_stall": pipe,
            "warmup": {
                "early_mids": self._warmup_early,
                "full_mids": self._warmup_full,
                "interval_sec": _dashboard_paper_interval_sec(),
                "early_size_mult": str(_dashboard_early_size_mult()),
            },
            "entry_diagnostics": {
                "global_counts": dict(self._diag.global_counts),
                "entry_attempts": self._diag.entry_attempts,
                "eligible_signals": self._diag.eligible_signals,
                "last_blocked": list(self._diag.last_blocked),
            },
            "first_open_metrics": {
                "time_to_first_eligible_signal_sec": t_elig,
                "time_to_first_entry_sec": t_entry,
                "ticks_to_first_entry": self._first_entry_ticks,
            },
            "stall_warning_active": stall_active,
            "stall": {
                "warn_after_sec": stall_sec,
                "stall_active": stall_active,
                "top_blocker": top_blocker,
            },
            "venue_balance_usdt": self._last_balance,
            "reconciliation": {
                "mismatches_total": self._recon_mismatches,
                "last": self._recon_last,
            },
            "venue_order_metrics": {
                "entry_orders_sent": self._venue_entry_orders_sent,
                "entry_orders_filled": self._venue_entry_orders_filled,
                "exit_orders_sent": self._venue_exit_orders_sent,
                "exit_orders_filled": self._venue_exit_orders_filled,
                "first_venue_order_id": self._first_venue_order_id,
            },
            "venue_last_error": self._last_venue_error,
        }

    def warmup_snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "early_mids": self._warmup_early,
            "full_mids": self._warmup_full,
            "interval_sec": _dashboard_paper_interval_sec(),
            "symbols": {},
        }
        interval = _dashboard_paper_interval_sec()
        for sym in self._symbols:
            n = len(self._mid_history[sym])
            pct = min(100.0, 100.0 * n / float(self._warmup_full))
            eta_sec = None
            if n < self._warmup_full:
                need = self._warmup_full - n
                eta_sec = round(need * interval, 1)
            gate = "warming_up"
            if n >= self._warmup_full:
                gate = "ready_full"
            elif n >= self._warmup_early:
                gate = "ready_early"
            phase = "none"
            if n >= self._warmup_full:
                phase = "full"
            elif n >= self._warmup_early:
                phase = "early"
            out["symbols"][sym] = {
                "mid_count": n,
                "warmup_phase": phase,
                "warmup_gate": gate,
                "progress_pct": round(pct, 1),
                "eta_sec_to_full": eta_sec,
                "first_eligible_after_full": n >= self._warmup_full,
            }
        return out

    def entry_diagnostics_payload(self) -> dict[str, Any]:
        return {
            "global_counts": dict(self._diag.global_counts),
            "per_symbol": {k: dict(v) for k, v in self._diag.per_symbol.items()},
            "entry_attempts": self._diag.entry_attempts,
            "eligible_signals": self._diag.eligible_signals,
            "last_blocked": list(self._diag.last_blocked),
        }

    def open_positions_payload(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for pos in self._mirror.open_positions.values():
            if not pos.is_open:
                continue
            out.append(
                {
                    "position_id": str(pos.position_id),
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "entry_price": str(pos.entry_price),
                    "quantity": str(pos.quantity),
                    "notional_usd": str(pos.notional_usd),
                    "unrealized_pnl": str(pos.unrealized_pnl),
                    "signal_tier": pos.signal_tier,
                    "composite_score": pos.composite_score,
                    "warmup_phase": pos.warmup_phase,
                    "entry_reason": (pos.entry_reason or "")[:500],
                    "opened_at": pos.fill_time.isoformat() if pos.fill_time else "",
                    "venue_order_id": pos.venue_order_id,
                    "entry_client_order_id": pos.entry_client_order_id,
                    "execution_mode": "testnet",
                    "execution_channel": self._execution_channel,
                    "trade_source": "demo_exchange",
                }
            )
        return out

    async def _refresh_balance(self) -> None:
        try:
            snap = await self._adapter.get_usdt_wallet_snapshot()
            self._last_balance = {
                "wallet": str(snap["wallet"]),
                "available": str(snap["available"]),
                "cross_wallet": str(snap.get("cross_wallet", Decimal("0"))),
            }
        except Exception as e:
            self._last_balance = {"error": str(e)[:200]}

    def _sync_portfolio_from_wallet(self) -> None:
        """Sync PortfolioState.portfolio_value from the real exchange wallet.

        Uses *available* balance (not wallet balance) as the sizing basis so
        we only size against margin that is not already locked in open positions.
        Falls back to the existing in-memory value if the balance fetch failed
        or returned zero, preventing accidental sizing-to-zero.
        """
        raw_available = self._last_balance.get("available", "")
        if not raw_available or raw_available == "0" or "error" in self._last_balance:
            # No valid wallet data yet - keep the existing in-memory value.
            return
        try:
            available = Decimal(raw_available)
        except Exception:
            return
        if available <= Decimal("0"):
            return
        # Only update when the difference is material (>1 USDT) to avoid
        # micro-jitter from floating-point rounding in the REST response.
        diff = abs(available - self._portfolio.portfolio_value)
        if diff > Decimal("1"):
            self._portfolio.portfolio_value = available
            if not self._wallet_portfolio_initialized:
                self._portfolio.daily_high_water = available
                self._wallet_portfolio_initialized = True
                logger.info(
                    "testnet_portfolio_baseline_from_wallet",
                    available=str(available),
                    daily_high_water=str(self._portfolio.daily_high_water),
                )
            logger.debug(
                "portfolio_synced_from_wallet",
                available=str(available),
            )

    async def _await_order_settled(
        self,
        symbol: str,
        client_order_id: str,
        first: OrderResult,
    ) -> OrderResult:
        """Poll venue until Binance leaves ``NEW`` or Bybit reports a terminal/fill state."""
        orez = first
        for _ in range(40):
            raw = orez.raw_response or {}
            if self._adapter.venue_name == "binance_testnet":
                if raw.get("status") != "NEW":
                    break
            else:
                st = str(raw.get("orderStatus", ""))
                if orez.filled_quantity > 0 or orez.status in (
                    VenueOrderStatus.FILLED,
                    VenueOrderStatus.PARTIAL,
                ):
                    break
                if st in ("Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled"):
                    break
            await asyncio.sleep(0.1)
            nxt = await self._adapter.get_order(symbol, client_order_id)
            if nxt is not None:
                orez = nxt
        return orez

    async def _reconcile_tick(self) -> None:
        self._recon_tick += 1
        if self._recon_tick % 10 != 0:
            return
        local: list[LocalPositionView] = []
        for pos in self._mirror.open_positions.values():
            if pos.is_open:
                local.append(
                    LocalPositionView(
                        symbol=pos.symbol,
                        side=pos.direction,
                        quantity=pos.quantity,
                    )
                )
        try:
            result = await self._reconciler.reconcile(self._adapter, local)
            details = [
                {
                    "symbol": d.symbol,
                    "type": d.dtype.value,
                    "detail": d.detail,
                }
                for d in result.discrepancies
            ]
            if not result.is_clean:
                self._recon_mismatches += len(result.discrepancies)
                await logger.aerror(
                    "testnet_reconciliation_mismatch",
                    count=len(result.discrepancies),
                    details=details[:5],
                )
            self._recon_last = {
                "status": "clean" if result.is_clean else "mismatch",
                "details": details,
                "local_count": result.local_position_count,
                "venue_count": result.venue_position_count,
            }
        except Exception as e:
            self._recon_last = {"status": "error", "error": str(e)[:200]}

    async def _on_position_closed(
        self,
        position: PaperPosition,
        analytics: AnalyticsEngine,
    ) -> None:
        sym = position.symbol
        self._portfolio.remove_position(sym)
        self._portfolio.portfolio_value += position.realized_pnl
        self._portfolio.update_daily_drawdown()
        was_prof = position.realized_pnl > 0
        analytics.record_trade(
            position,
            venue=self._analytics_venue,
            was_profitable_at_exit=was_prof,
            source="demo_exchange",
            warmup_phase=position.warmup_phase,
            execution_channel=self._execution_channel,
        )
        self._exits_recorded += 1
        await logger.ainfo(
            "testnet_position_closed",
            symbol=sym,
            pnl=str(position.realized_pnl),
            exit_reason=position.exit_reason,
            warmup_phase=position.warmup_phase,
        )

    async def _maybe_log_warmup_transition(
        self, sym: str, vec: Any, t: TickerState
    ) -> None:
        if vec is None:
            return
        dq = vec.data_quality
        prev = self._symbol_gate_state.get(sym, "none")
        if dq.warmup_early_eligible and prev == "none":
            self._symbol_gate_state[sym] = "ready"
            await logger.ainfo(
                "testnet_symbol_warmup_ready",
                symbol=sym,
                phase=dq.warmup_phase,
                mids=dq.warmup_mid_count,
            )
        elif prev == "ready" and t.is_stale:
            self._symbol_gate_state[sym] = "degraded"
            await logger.awarning("testnet_symbol_data_degraded", symbol=sym)

    async def run_forever(self, interval_sec: float | None = None) -> None:
        self._running = True
        if interval_sec is None:
            interval_sec = _dashboard_paper_interval_sec()
        self._runner_started_mono = time.monotonic()
        await self._adapter.start()
        await logger.ainfo(
            "testnet_runner_started",
            interval_sec=interval_sec,
            symbols=list(self._symbols),
            warmup_early=self._warmup_early,
            warmup_full=self._warmup_full,
        )
        try:
            while self._running:
                try:
                    await self.tick()
                    self._ticks_ok += 1
                    self._last_error = None
                except asyncio.CancelledError:
                    self._running = False
                    raise
                except Exception as e:
                    self._last_error = str(e)
                    await logger.aexception("testnet_runner_tick_failed", error=str(e))
                await asyncio.sleep(interval_sec)
        finally:
            await self._adapter.stop()
            await logger.ainfo("testnet_runner_stopped")

    async def tick(self) -> None:
        feed = self._market_feed()
        analytics = self._analytics_engine()
        if not feed or not analytics:
            return

        ops = self._ops()
        mirror = self._mirror

        await self._refresh_balance()
        self._sync_portfolio_from_wallet()
        await self._reconcile_tick()

        for sym in self._symbols:
            sym_enum = _SYMBOL_MAP.get(sym)
            if not sym_enum:
                continue
            t = feed.get_ticker(sym)
            if not t:
                self._last_skip[sym] = "no_ticker"
                continue
            mid = _mid_price(t)
            if mid is None or mid <= 0:
                self._last_skip[sym] = "no_mid_or_book"
                continue

            event_now = _event_time_utc(t)

            self._mid_history[sym].append(mid)
            vec, vec_rej = try_build_streaming_vector_from_ticker(
                sym_enum,
                self._mid_history[sym],
                t,
                self._signal_settings,
                early_mids=self._warmup_early,
                full_mids=self._warmup_full,
            )

            await self._maybe_log_warmup_transition(sym, vec, t)

            bid, ask = t.best_bid, t.best_ask
            if bid > 0 and ask > 0:
                mirror.update_book(sym, bid, ask)

            mark = t.mark_price if t.mark_price > 0 else mid
            mirror.update_price(sym, mark)

            plans = mirror.plan_exits(sym, mark, event_now, vec)
            for pid, reason, detail in plans:
                pos = mirror.open_positions.get(pid)
                if pos is None or not pos.is_open:
                    continue
                self._last_execution_attempt_at = event_now
                self._venue_exit_orders_sent += 1
                try:
                    # close_position() takes the ENTRY side and inverts it internally.
                    # LONG was entered with BUY → adapter sends SELL to close.
                    # SHORT was entered with SELL → adapter sends BUY to close.
                    entry_side = OrderSide.BUY if pos.direction == "long" else OrderSide.SELL
                    orez = await self._adapter.close_position(
                        sym,
                        pos.quantity,
                        entry_side,
                        direction=pos.direction,
                    )
                    orez = await self._await_order_settled(sym, orez.client_order_id, orez)
                except (ExecutionError, OrderRejectedError) as e:
                    self._last_venue_error = str(e)[:500]
                    self._diag.record(sym, "rejected_venue_rest", f"exit: {e!s}"[:240])
                    await logger.aerror(
                        "testnet_close_order_failed",
                        symbol=sym,
                        error=str(e),
                    )
                    continue
                await logger.ainfo(
                    "testnet_close_order_result",
                    symbol=sym,
                    quantity=str(pos.quantity),
                    client_order_id=orez.client_order_id,
                    venue_order_id=orez.venue_order_id,
                    status=orez.status.value,
                    avg_price=str(orez.average_price),
                    filled_qty=str(orez.filled_quantity),
                )
                exit_px = orez.average_price
                if exit_px <= 0:
                    exit_px = mark
                exit_fees = Decimal("0")
                if orez.status not in (
                    VenueOrderStatus.FILLED,
                    VenueOrderStatus.PARTIAL,
                ):
                    await logger.aerror(
                        "testnet_exit_order_not_filled",
                        symbol=sym,
                        status=orez.status.value,
                        msg=orez.error_message,
                    )
                    continue
                self._venue_exit_orders_filled += 1
                self._last_venue_error = None
                closed = mirror.close_position_external_fill(
                    pid,
                    exit_px,
                    event_now,
                    reason,
                    detail,
                    additional_exit_fees_usd=exit_fees,
                )
                if closed:
                    await self._on_position_closed(closed, analytics)

            if not ops.is_entries_allowed:
                self._diag.record(sym, "rejected_entries_paused", "ops mode blocks entries")
                continue
            if not ops.is_symbol_enabled(sym):
                self._diag.record(sym, "rejected_symbol_disabled", "")
                continue


            if vec is None:
                if vec_rej:
                    self._diag.record(sym, vec_rej, "feature build")
                continue

            ev = await self._signal_engine.evaluate_with_reason(vec)
            self._diag.entry_attempts += 1
            if ev.signal is not None:
                self._diag.eligible_signals += 1
                if self._first_eligible_mono is None and self._runner_started_mono is not None:
                    self._first_eligible_mono = time.monotonic()

            if ev.signal is None:
                if ev.rejection:
                    self._diag.record(sym, ev.rejection, "")
                continue

            scored = ev.signal
            if self._proof_symbol and sym != self._proof_symbol:
                self._diag.record(sym, "rejected_venue_proof_symbol", self._proof_symbol)
                continue

            if _has_open_position_same_direction(mirror, sym, scored.action):
                self._diag.record(sym, "rejected_existing_position", f"same-direction {scored.direction} already open")
                continue

            self._last_eligible_signal_at = event_now
            entry_wp = vec.data_quality.warmup_phase
            if entry_wp not in ("early", "full"):
                entry_wp = "full" if vec.data_quality.warmup_complete else "early"

            legacy = SignalEvent(
                symbol=scored.symbol,
                action=scored.action,
                confidence=scored.composite_score,
                reason=scored.reason,
            )

            sizing_settings = self._settings.sizing
            risk_settings = self._settings.risk
            est = min(
                Decimal(str(sizing_settings.max_order_usd)),
                self._portfolio.portfolio_value * Decimal(str(risk_settings.max_position_pct)),
            )
            if entry_wp == "early":
                est = (est * _dashboard_early_size_mult()).quantize(Decimal("0.01"))

            if est < Decimal(str(sizing_settings.min_order_usd)):
                self._diag.record(sym, "rejected_min_notional", f"est={est}")
                continue

            assessment = await self._risk.assess_signal(legacy, est)
            if assessment.decision != RiskDecision.APPROVED:
                self._diag.record(sym, "rejected_risk", str(assessment.decision))
                continue
            self._last_risk_approved_at = event_now

            sizer = SizingEngine(sizing_settings, self._publisher, self._portfolio.portfolio_value)
            sized = await sizer.size_order(legacy, assessment, mark)
            if sized is None:
                self._diag.record(sym, "rejected_sizing_failed", "")
                continue
            if sized.notional_usd > 0:
                self._last_nonzero_sizing_at = event_now

            qty = _round_down_qty(sym, sized.quantity)
            if qty <= 0:
                self._diag.record(sym, "rejected_sizing_failed", "qty zero after step")
                continue

            self._last_execution_attempt_at = event_now
            entry_side = OrderSide.BUY if scored.action.value == "open_long" else OrderSide.SELL
            req = OrderRequest(
                symbol=sym,
                side=entry_side,
                direction=scored.direction,
                quantity=qty,
            )
            self._venue_entry_orders_sent += 1
            try:
                orez = await self._adapter.place_order(req)
                orez = await self._await_order_settled(sym, req.client_order_id, orez)
            except (ExecutionError, OrderRejectedError) as e:
                self._last_venue_error = str(e)[:500]
                self._diag.record(sym, "rejected_venue_rest", str(e)[:240])
                await logger.aerror(
                    "testnet_place_order_failed",
                    symbol=sym,
                    error=str(e),
                )
                continue
            notion_pre = mark * qty
            await logger.ainfo(
                "testnet_place_order_result",
                symbol=sym,
                side=req.side.value,
                quantity=str(qty),
                notional_usd=str(notion_pre.quantize(Decimal("0.000001"))),
                client_order_id=orez.client_order_id,
                venue_order_id=orez.venue_order_id,
                status=orez.status.value,
                avg_price=str(orez.average_price),
                filled_qty=str(orez.filled_quantity),
                error_code=orez.error_code,
                error_message=(orez.error_message or "")[:200],
            )
            if orez.status not in (VenueOrderStatus.FILLED, VenueOrderStatus.PARTIAL):
                self._diag.record(
                    sym,
                    "rejected_no_quote",
                    f"venue {orez.status.value} {orez.error_message}",
                )
                continue

            self._venue_entry_orders_filled += 1
            self._last_venue_error = None
            if self._first_venue_order_id is None and orez.venue_order_id:
                self._first_venue_order_id = orez.venue_order_id
            fill_px = orez.average_price
            if fill_px <= 0:
                fill_px = mark
            filled_qty = orez.filled_quantity if orez.filled_quantity > 0 else qty
            notion = fill_px * filled_qty

            opened = mirror.open_position_from_venue_fill(
                scored,
                filled_qty,
                notion,
                event_now,
                fill_px,
                warmup_phase=entry_wp,
                venue_order_id=orez.venue_order_id,
                entry_client_order_id=orez.client_order_id,
            )
            if opened is not None:
                self._portfolio.update_exposure(sym, notion)
                self._entries_total += 1
                if self._first_entry_mono is None and self._runner_started_mono is not None:
                    self._first_entry_mono = time.monotonic()
                    self._first_entry_ticks = self._ticks_ok
                await logger.ainfo(
                    "testnet_position_opened",
                    symbol=sym,
                    tier=scored.tier.value,
                    notional=str(notion),
                    venue_order_id=orez.venue_order_id,
                    warmup_phase=entry_wp,
                )
            else:
                self._diag.record(sym, "rejected_no_quote", "mirror open failed")
