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
from datetime import UTC, datetime, timedelta
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
    _dashboard_post_exit_cooldown_sec,
    _dashboard_post_exit_hard_risk_cooldown_sec,
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
from cte.execution.reconciliation import (
    DiscrepancyType,
    LocalPositionView,
    PositionReconciler,
    ReconciliationResult,
)
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

# Contract lot sizes for venue order qty.
_QTY_STEP_BINANCE: dict[str, Decimal] = {
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

_QTY_STEP_BYBIT: dict[str, Decimal] = {
    "BTCUSDT": Decimal("0.001"),
    "ETHUSDT": Decimal("0.01"),
    "BNBUSDT": Decimal("0.01"),
    "SOLUSDT": Decimal("0.1"),
    "XRPUSDT": Decimal("0.1"),
    "DOGEUSDT": Decimal("1"),
    "ADAUSDT": Decimal("1"),
    "AVAXUSDT": Decimal("0.1"),
    "LINKUSDT": Decimal("0.1"),
    "DOTUSDT": Decimal("0.1"),
}


def _qty_step(symbol: str, venue_name: str | None = None) -> Decimal:
    if venue_name == "bybit_demo":
        return _QTY_STEP_BYBIT.get(symbol, Decimal("0.001"))
    return _QTY_STEP_BINANCE.get(symbol, Decimal("0.001"))


def _round_down_qty(symbol: str, q: Decimal, venue_name: str | None = None) -> Decimal:
    step = _qty_step(symbol, venue_name)
    if q <= 0:
        return Decimal("0")
    n = (q / step).to_integral_value(rounding=ROUND_DOWN)
    return (n * step).quantize(step)


def _round_up_qty(symbol: str, q: Decimal, venue_name: str | None = None) -> Decimal:
    step = _qty_step(symbol, venue_name)
    if q <= 0:
        return Decimal("0")
    down = _round_down_qty(symbol, q, venue_name)
    if down == q:
        return q.quantize(step)
    return (down + step).quantize(step)


def _entry_step_overshoot_pct() -> Decimal:
    raw = (os.environ.get("CTE_ENTRY_STEP_OVERSHOOT_PCT") or "0.01").strip()
    try:
        v = Decimal(raw)
    except Exception:
        return Decimal("0.01")
    if v < Decimal("0"):
        return Decimal("0")
    if v > Decimal("0.50"):
        return Decimal("0.50")
    return v


def _entry_qty_matches_request(
    symbol: str,
    filled: Decimal,
    requested: Decimal,
    venue_name: str | None = None,
) -> bool:
    """True when filled size matches requested order qty after contract-step rounding."""
    rf = _round_down_qty(symbol, filled, venue_name)
    rq = _round_down_qty(symbol, requested, venue_name)
    return rf > 0 and rq > 0 and rf == rq


def _raw_entry_order_status(venue_name: str, orez: OrderResult) -> str:
    raw = orez.raw_response or {}
    if venue_name == "binance_testnet":
        return str(raw.get("status", ""))
    return str(raw.get("orderStatus", ""))


def _entry_order_terminal_failure(venue_name: str, orez: OrderResult) -> bool:
    """Order reached a terminal non-fill state (cancel/reject)."""
    raw = orez.raw_response or {}
    if venue_name == "binance_testnet":
        return raw.get("status", "") in ("CANCELED", "REJECTED", "EXPIRED")
    st = str(raw.get("orderStatus", ""))
    return st in ("Cancelled", "Rejected", "PartiallyFilledCanceled", "Deactivated")


def _entry_fill_complete(
    venue_name: str,
    symbol: str,
    requested_qty: Decimal,
    orez: OrderResult,
) -> bool:
    """Whether entry mirroring may proceed: explicit FILLED or full size on the wire.

    Ends with ``_entry_qty_matches_request`` so flaky/missing status strings still
    admit a full fill when ``cumExecQty``/``executedQty`` matches requested qty.
    """
    raw = orez.raw_response or {}
    if venue_name == "binance_testnet":
        st = raw.get("status", "")
        if st in ("CANCELED", "REJECTED", "EXPIRED"):
            return False
        if st == "FILLED":
            return True
    else:
        st = str(raw.get("orderStatus", ""))
        if st in ("Cancelled", "Rejected", "PartiallyFilledCanceled", "Deactivated"):
            return False
        if st == "Filled":
            return True
    return _entry_qty_matches_request(symbol, orez.filled_quantity, requested_qty, venue_name)


# Entry orders: poll longer than exits so slow venues can reach full fill before mirroring.
ENTRY_ORDER_POLL_MAX = 80
EXIT_ORDER_POLL_MAX = 40


def dashboard_execution_venue() -> str:
    """Explicit venue for dashboard REST execution: ``binance_testnet`` | ``bybit_demo``."""
    return (os.environ.get("CTE_DASHBOARD_EXECUTION_VENUE") or "binance_testnet").strip().lower()


def venue_proof_symbol() -> str | None:
    """When set, only this symbol receives venue entries (feed may include more symbols)."""
    raw = (os.environ.get("CTE_DASHBOARD_VENUE_PROOF_SYMBOL") or "").strip().upper()
    return raw if raw else None


def _recon_phantom_grace_sec() -> float:
    """Monotonic grace after a venue fill before PHANTOM_LOCAL counts as persistent."""
    raw = (os.environ.get("CTE_RECON_PHANTOM_GRACE_SEC") or "5.0").strip()
    try:
        return max(1.0, min(60.0, float(raw)))
    except ValueError:
        return 5.0


def _recon_qty_tolerance_pct() -> float:
    """Relative qty tolerance for :class:`PositionReconciler` (``0`` = exact match).

    - ``CTE_RECON_STRICT_VALIDATION=1`` → ``0.0`` (clean-account / 24h validation runs).
    - Else ``CTE_RECON_QTY_TOLERANCE_PCT`` if set, otherwise ``0.01`` (legacy default).
    """
    if _env_bool("CTE_RECON_STRICT_VALIDATION", False):
        return 0.0
    raw = (os.environ.get("CTE_RECON_QTY_TOLERANCE_PCT") or "").strip()
    if not raw:
        return 0.01
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.01


def _allow_foreign_positions() -> bool:
    """Whether validation may continue when startup foreign positions exist."""
    return _env_bool("CTE_ALLOW_FOREIGN_POSITIONS", False)


def _recon_snapshot_meta(reconciler: PositionReconciler) -> dict[str, Any]:
    """Expose tolerance on ``reconciliation.last`` for API / validation audits."""
    t = reconciler.tolerance_pct
    return {
        "qty_tolerance_pct": t,
        "strict_qty_match": t == 0.0,
    }


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
        base = (
            os.environ.get("CTE_BYBIT_REST_BASE_URL") or ""
        ).strip() or "https://api-demo.bybit.com"
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

        self._mid_history: dict[str, deque[Decimal]] = {s: deque(maxlen=400) for s in symbols}

        self._reconciler = PositionReconciler(tolerance_pct=_recon_qty_tolerance_pct())
        self._recon_mismatches = 0
        self._recon_last: dict[str, Any] = {"status": "not_run", "details": []}
        #: Per-symbol monotonic deadline for classifying PHANTOM_LOCAL as transient (venue list lag).
        self._recon_grace_until: dict[str, float] = {}
        self._last_balance: dict[str, str] = {}
        # First wallet sync must reset daily_high_water; otherwise portfolio_value
        # jumps from paper default (10k) to real available (~few k) and risk sees
        # fake ~50% daily drawdown (veto everything).
        self._wallet_portfolio_initialized: bool = False
        self._foreign_venue_at_startup: bool = False
        self._foreign_venue_startup_details: list[dict[str, str]] | None = None
        self._validation_blocked: bool = False

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
        self._reentry_cooldown_sec = _dashboard_post_exit_cooldown_sec()
        self._reentry_hard_risk_cooldown_sec = _dashboard_post_exit_hard_risk_cooldown_sec()
        self._reentry_block_until: dict[str, datetime] = {}

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

    def _portfolio_concentration_metrics(self) -> dict[str, Any]:
        open_positions = [p for p in self._mirror.open_positions.values() if p.is_open]
        if not open_positions:
            return {
                "portfolio_notional": 0.0,
                "largest_cluster_notional": 0.0,
                "concentration_ratio": 0.0,
                "cluster_count": 0,
                "direction_weighted_exposure_by_symbol": {},
            }

        total_notional = 0.0
        clusters: dict[str, float] = {}
        exposure_by_symbol: dict[str, float] = {}
        for pos in open_positions:
            n = float(abs(pos.notional_usd))
            if n <= 0:
                continue
            total_notional += n
            sign = 1.0 if pos.direction == "long" else -1.0
            exposure_by_symbol[pos.symbol] = exposure_by_symbol.get(pos.symbol, 0.0) + sign * n

            time_bucket = int(pos.fill_time.timestamp()) // 300 if pos.fill_time else -1
            if pos.entry_price > 0:
                move = (
                    float((pos.entry_price - pos.current_price) / pos.entry_price)
                    if pos.direction == "short"
                    else float((pos.current_price - pos.entry_price) / pos.entry_price)
                )
            else:
                move = 0.0
            move_bucket = int(move / 0.005)  # 0.5% bands
            key = f"{pos.direction}:{time_bucket}:{move_bucket}"
            clusters[key] = clusters.get(key, 0.0) + n

        largest = max(clusters.values()) if clusters else 0.0
        ratio = (largest / total_notional) if total_notional > 0 else 0.0
        return {
            "portfolio_notional": round(total_notional, 4),
            "largest_cluster_notional": round(largest, 4),
            "concentration_ratio": round(ratio, 4),
            "cluster_count": len(clusters),
            "direction_weighted_exposure_by_symbol": {
                k: round(v, 4) for k, v in sorted(exposure_by_symbol.items())
            },
        }

    def _reentry_cooldown_for_reason(self, exit_reason: str) -> int:
        if exit_reason in {"spread_blowout", "stale_data"}:
            return self._reentry_hard_risk_cooldown_sec
        return self._reentry_cooldown_sec

    def _arm_reentry_cooldown(self, symbol: str, event_time: datetime, exit_reason: str) -> None:
        sec = self._reentry_cooldown_for_reason(exit_reason)
        if sec <= 0:
            return
        self._reentry_block_until[symbol] = event_time + timedelta(seconds=sec)

    def _reentry_cooldown_remaining(self, symbol: str, event_time: datetime) -> int:
        until = self._reentry_block_until.get(symbol)
        if until is None:
            return 0
        rem = int((until - event_time).total_seconds())
        if rem <= 0:
            self._reentry_block_until.pop(symbol, None)
            return 0
        return rem

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
        conc = self._portfolio_concentration_metrics()
        cooldown_active = sum(
            1 for ts in self._reentry_block_until.values() if ts > datetime.now(UTC)
        )
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
            "post_exit_cooldown": {
                "default_sec": self._reentry_cooldown_sec,
                "hard_risk_sec": self._reentry_hard_risk_cooldown_sec,
                "active_symbols": cooldown_active,
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
            "foreign_venue_detected": self._foreign_venue_at_startup,
            "validation_blocked": self._validation_blocked,
            "portfolio_notional": conc["portfolio_notional"],
            "largest_cluster_notional": conc["largest_cluster_notional"],
            "concentration_ratio": conc["concentration_ratio"],
            "cluster_count": conc["cluster_count"],
            "direction_weighted_exposure_by_symbol": conc["direction_weighted_exposure_by_symbol"],
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

    def _recon_symbol_status(self, symbol: str) -> tuple[str, str]:
        last = self._recon_last if isinstance(self._recon_last, dict) else {}
        persistent = last.get("persistent_details")
        if not isinstance(persistent, list):
            persistent = []
        for d in persistent:
            if not isinstance(d, dict):
                continue
            if str(d.get("symbol") or "") != symbol:
                continue
            dtype = str(d.get("type") or "mismatch")
            detail = str(d.get("detail") or "")
            return "mismatch", f"{dtype}: {detail}"[:240]
        return "clean", ""

    def open_positions_payload(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        open_positions = [p for p in self._mirror.open_positions.values() if p.is_open]
        planned_by_id: dict[str, str] = {}
        by_symbol: dict[str, list[PaperPosition]] = {}
        for pos in open_positions:
            by_symbol.setdefault(pos.symbol, []).append(pos)
        for sym, positions in by_symbol.items():
            current_price = max(
                (p.current_price for p in positions if p.current_price > 0),
                default=positions[0].entry_price,
            )
            for pid, reason, _detail in self._mirror.plan_exits(sym, current_price, now, None):
                planned_by_id[str(pid)] = reason

        out: list[dict[str, Any]] = []
        for pos in open_positions:
            notional = pos.notional_usd
            pnl_pct = Decimal("0")
            if notional > 0:
                pnl_pct = (pos.unrealized_pnl / notional) * Decimal("100")
            recon_status, recon_detail = self._recon_symbol_status(pos.symbol)
            out.append(
                {
                    "position_id": str(pos.position_id),
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "entry_price": str(pos.entry_price),
                    "current_price": str(pos.current_price),
                    "quantity": str(pos.quantity),
                    "notional_usd": str(pos.notional_usd),
                    "unrealized_pnl": str(pos.unrealized_pnl),
                    "pnl_pct": str(pnl_pct.quantize(Decimal("0.0001"))),
                    "hold_seconds": pos.hold_duration_seconds,
                    "signal_tier": pos.signal_tier,
                    "composite_score": pos.composite_score,
                    "primary_score": pos.primary_score,
                    "context_multiplier": pos.context_multiplier,
                    "strongest_sub_score": pos.strongest_sub_score,
                    "strongest_sub_score_value": pos.strongest_sub_score_value,
                    "warmup_phase": pos.warmup_phase,
                    "stop_loss_pct": pos.stop_loss_pct,
                    "take_profit_pct": pos.take_profit_pct,
                    "exit_pressure": planned_by_id.get(str(pos.position_id), "none"),
                    "recon_status": recon_status,
                    "recon_detail": recon_detail,
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
        *,
        for_entry: bool = False,
        requested_qty: Decimal | None = None,
    ) -> OrderResult:
        """Poll venue until the order reaches a settle point.

        **Entry** (``for_entry=True``): do not open the local mirror until
        :func:`_entry_fill_complete` is true (FILLED or full size by qty match).

        **Exit** (default): unchanged — any non-NEW or first non-zero fill
        (Bybit) / non-NEW (Binance) still returns promptly for partial closes.
        """
        orez = first
        vname = self._adapter.venue_name
        max_polls = (
            ENTRY_ORDER_POLL_MAX if for_entry and requested_qty is not None else EXIT_ORDER_POLL_MAX
        )
        for poll_idx in range(max_polls):
            if for_entry and requested_qty is not None:
                fc = _entry_fill_complete(vname, symbol, requested_qty, orez)
                tf = _entry_order_terminal_failure(vname, orez)
                await logger.ainfo(
                    "testnet_entry_order_poll",
                    poll=poll_idx,
                    symbol=symbol,
                    venue_order_id=orez.venue_order_id or "",
                    client_order_id=client_order_id,
                    requested_qty=str(requested_qty),
                    order_status_raw=_raw_entry_order_status(vname, orez),
                    filled_quantity=str(orez.filled_quantity),
                    order_status_enum=orez.status.value,
                    fill_complete=fc,
                    terminal_failure=tf,
                )
                if fc or tf:
                    break
            elif vname == "binance_testnet":
                raw = orez.raw_response or {}
                if raw.get("status", "") != "NEW":
                    break
            else:
                st = str((orez.raw_response or {}).get("orderStatus", ""))
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

    def _local_position_views(self) -> list[LocalPositionView]:
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
        return local

    async def _run_reconciliation(self) -> None:
        local = self._local_position_views()
        try:
            result = await self._reconciler.reconcile(
                self._adapter,
                local,
                grace_until_mono=self._recon_grace_until,
            )
            details = [
                {
                    "symbol": d.symbol,
                    "type": d.dtype.value,
                    "detail": d.detail,
                }
                for d in result.discrepancies
            ]
            pers_details = [
                {
                    "symbol": d.symbol,
                    "type": d.dtype.value,
                    "detail": d.detail,
                }
                for d in result.persistent_discrepancies
            ]
            trans_details = [
                {
                    "symbol": d.symbol,
                    "type": d.dtype.value,
                    "detail": d.detail,
                }
                for d in result.transient_discrepancies
            ]
            np = len(result.persistent_discrepancies)
            nt = len(result.transient_discrepancies)
            if np:
                self._recon_mismatches += np
                await logger.aerror(
                    "testnet_reconciliation_mismatch",
                    count=np,
                    transient_count=nt,
                    details=pers_details[:5],
                )
            elif nt:
                await logger.ainfo(
                    "testnet_reconciliation_transient",
                    count=nt,
                    details=trans_details[:5],
                )
            self._recon_last = {
                "status": "clean" if result.is_clean else "mismatch",
                "details": details,
                "persistent_details": pers_details,
                "transient_details": trans_details,
                "persistent_count": np,
                "transient_count": nt,
                "local_count": result.local_position_count,
                "venue_count": result.venue_position_count,
                **_recon_snapshot_meta(self._reconciler),
            }
            self._annotate_operational_recon_notes(result)
            self._merge_foreign_venue_startup_into_recon_last()
        except Exception as e:
            self._recon_last = {
                "status": "error",
                "error": str(e)[:200],
                **_recon_snapshot_meta(self._reconciler),
            }
            self._merge_foreign_venue_startup_into_recon_last()

    def _annotate_operational_recon_notes(self, result: ReconciliationResult) -> None:
        """Surface foreign phantom_venue as operational (not silently normal)."""
        notes: list[str] = []
        if any(d.dtype == DiscrepancyType.PHANTOM_VENUE for d in result.persistent_discrepancies):
            notes.append(
                "phantom_venue may indicate foreign/pre-existing venue exposure not opened by this runner "
                "(close on venue or use isolated API keys for 24h validation)."
            )
        self._recon_last["operational_notes"] = notes

    def _merge_foreign_venue_startup_into_recon_last(self) -> None:
        """Keep startup foreign-venue exposure visible after periodic recon overwrites _recon_last."""
        if not self._foreign_venue_at_startup:
            return
        self._recon_last["status"] = "unclean"
        self._recon_last["reason"] = "foreign_venue_positions"
        if self._foreign_venue_startup_details:
            self._recon_last["foreign_venue_startup_details"] = list(
                self._foreign_venue_startup_details
            )
        startup_msg = "Foreign venue positions at startup; use a clean account or isolated API keys for 24h validation."
        merged = self._recon_last.setdefault("operational_notes", [])
        if startup_msg not in merged:
            merged.insert(0, startup_msg)

    async def _check_startup_venue_mismatch(self) -> None:
        """If the venue has open positions we do not mirror, mark recon unclean (no bootstrap)."""
        try:
            venue = await self._adapter.get_positions()
        except Exception as e:
            await logger.aerror(
                "testnet_startup_venue_check_failed",
                error=str(e)[:200],
            )
            return

        local = self._local_position_views()
        local_syms = {v.symbol for v in local}
        foreign: list[dict[str, str]] = []
        for vp in venue:
            if vp.quantity <= 0:
                continue
            if vp.symbol not in local_syms:
                foreign.append(
                    {
                        "symbol": vp.symbol,
                        "side": vp.side,
                        "quantity": str(vp.quantity),
                    }
                )

        if not foreign:
            return

        self._foreign_venue_at_startup = True
        self._foreign_venue_startup_details = foreign
        self._validation_blocked = not _allow_foreign_positions()
        venue_open = sum(1 for v in venue if v.quantity > 0)
        self._recon_last = {
            "status": "unclean",
            "reason": "foreign_venue_positions",
            "details": foreign,
            "persistent_details": foreign,
            "transient_details": [],
            "persistent_count": len(foreign),
            "transient_count": 0,
            "local_count": len(local),
            "venue_count": venue_open,
            "foreign_venue_startup_details": foreign,
            "operational_notes": [
                "Foreign venue positions at startup; use a clean account or isolated API keys for 24h validation.",
            ],
            **_recon_snapshot_meta(self._reconciler),
        }
        if self._validation_blocked:
            await logger.aerror(
                "testnet_foreign_venue_positions",
                count=len(foreign),
                details=foreign[:10],
            )
            await logger.aerror(
                "validation aborted: foreign venue positions detected",
                count=len(foreign),
                details=foreign[:10],
                override_env="CTE_ALLOW_FOREIGN_POSITIONS=1",
            )
        else:
            await logger.awarning(
                "testnet_foreign_venue_positions",
                count=len(foreign),
                details=foreign[:10],
                allow_foreign_positions=True,
            )
            await logger.awarning(
                "validation override active: foreign venue positions allowed",
                count=len(foreign),
                override_env="CTE_ALLOW_FOREIGN_POSITIONS=1",
            )

    async def _reconcile_tick(self) -> None:
        self._recon_tick += 1
        if self._recon_tick % 10 != 0:
            return
        await self._run_reconciliation()

    async def _reconcile_after_entry(self, symbol: str) -> None:
        """Immediate recon + short retries; grace window suppresses false phantom_local."""
        self._recon_grace_until[symbol] = time.monotonic() + _recon_phantom_grace_sec()
        for i in range(3):
            await self._run_reconciliation()
            if i < 2:
                await asyncio.sleep(0.15)

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
        close_ts = position.close_time if position.close_time else datetime.now(UTC)
        self._arm_reentry_cooldown(sym, close_ts, position.exit_reason)
        self._exits_recorded += 1
        self._recon_grace_until.pop(sym, None)
        await logger.ainfo(
            "testnet_position_closed",
            symbol=sym,
            pnl=str(position.realized_pnl),
            exit_reason=position.exit_reason,
            warmup_phase=position.warmup_phase,
        )

    async def _maybe_log_warmup_transition(self, sym: str, vec: Any, t: TickerState) -> None:
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
        await self._check_startup_venue_mismatch()
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

        if self._validation_blocked:
            return

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
                filled_raw = min(orez.filled_quantity, pos.quantity)
                filled_exit = _round_down_qty(sym, filled_raw, self._adapter.venue_name)
                if filled_exit <= 0:
                    await logger.aerror(
                        "testnet_exit_fill_qty_zero_after_step",
                        symbol=sym,
                        filled_raw=str(filled_raw),
                    )
                    continue
                closed = mirror.close_position_external_fill(
                    pid,
                    exit_px,
                    event_now,
                    reason,
                    detail,
                    filled_exit_quantity=filled_exit,
                    additional_exit_fees_usd=exit_fees,
                )
                if closed is not None:
                    await self._on_position_closed(closed, analytics)
                else:
                    pos_after = mirror.open_positions.get(pid)
                    if pos_after is not None and pos_after.is_open:
                        self._portfolio.update_exposure(
                            sym,
                            pos_after.entry_price * pos_after.quantity,
                            pos_after.direction,
                        )

            if not ops.is_entries_allowed:
                self._diag.record(sym, "rejected_entries_paused", "ops mode blocks entries")
                continue
            if not ops.is_symbol_enabled(sym):
                self._diag.record(sym, "rejected_symbol_disabled", "")
                continue

            rem = self._reentry_cooldown_remaining(sym, event_now)
            if rem > 0:
                self._diag.record(sym, "rejected_cooldown", f"post-exit cooldown {rem}s")
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
                self._diag.record(
                    sym,
                    "rejected_existing_position",
                    f"same-direction {scored.direction} already open",
                )
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
                self._diag.record(sym, "rejected_risk", str(assessment.reason))
                continue
            self._last_risk_approved_at = event_now

            sizer = SizingEngine(sizing_settings, self._publisher, self._portfolio.portfolio_value)
            sized = await sizer.size_order(legacy, assessment, mark)
            if sized is None:
                self._diag.record(sym, "rejected_sizing_failed", "")
                continue
            if sized.notional_usd > 0:
                self._last_nonzero_sizing_at = event_now

            qty = _round_down_qty(sym, sized.quantity, self._adapter.venue_name)
            if qty <= 0:
                self._diag.record(sym, "rejected_sizing_failed", "qty zero after step")
                continue

            rounded_notional = (qty * mark).quantize(Decimal("0.000001"))
            min_notional = Decimal(str(sizing_settings.min_order_usd))
            if rounded_notional < min_notional:
                up_qty = _round_up_qty(sym, sized.quantity, self._adapter.venue_name)
                up_notional = (up_qty * mark).quantize(Decimal("0.000001"))
                max_notional = Decimal(str(sizing_settings.max_order_usd))
                overshoot_limit = (
                    max_notional * (Decimal("1") + _entry_step_overshoot_pct())
                ).quantize(Decimal("0.000001"))
                if up_qty > 0 and up_notional >= min_notional and up_notional <= overshoot_limit:
                    qty = up_qty
                    rounded_notional = up_notional
                else:
                    self._diag.record(
                        sym,
                        "rejected_min_notional",
                        f"rounded_notional={rounded_notional} < min={min_notional}",
                    )
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
                orez = await self._await_order_settled(
                    sym,
                    req.client_order_id,
                    orez,
                    for_entry=True,
                    requested_qty=qty,
                )
            except (ExecutionError, OrderRejectedError) as e:
                self._last_venue_error = str(e)[:500]
                self._diag.record(sym, "rejected_venue_rest", str(e)[:240])
                await logger.aerror(
                    "testnet_place_order_failed",
                    symbol=sym,
                    error=str(e),
                )
                continue

            if not _entry_fill_complete(self._adapter.venue_name, sym, qty, orez):
                self._diag.record(
                    sym, "rejected_no_quote", "entry order not fully filled at settlement"
                )
                raw = orez.raw_response or {}
                await logger.awarning(
                    "testnet_entry_incomplete_fill",
                    symbol=sym,
                    requested=str(qty),
                    filled=str(orez.filled_quantity),
                    order_status=str(raw.get("orderStatus") or raw.get("status") or ""),
                    terminal_failure=_entry_order_terminal_failure(self._adapter.venue_name, orez),
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

            self._venue_entry_orders_filled += 1
            self._last_venue_error = None
            if self._first_venue_order_id is None and orez.venue_order_id:
                self._first_venue_order_id = orez.venue_order_id
            fill_px = orez.average_price
            if fill_px <= 0:
                fill_px = mark
            filled_qty = orez.filled_quantity if orez.filled_quantity > 0 else qty
            notion = fill_px * filled_qty

            await logger.ainfo(
                "testnet_entry_mirror_open_attempt",
                symbol=sym,
                venue_order_id=orez.venue_order_id or "",
                requested_qty=str(qty),
                filled_qty=str(filled_qty),
                fill_complete=True,
                mirror_open_called=True,
            )
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
                    "testnet_entry_mirror_opened",
                    symbol=sym,
                    venue_order_id=orez.venue_order_id or "",
                    position_id=opened.position_id,
                    local_qty=str(opened.quantity),
                    paper_position_created=True,
                )
                await logger.ainfo(
                    "testnet_position_opened",
                    symbol=sym,
                    tier=scored.tier.value,
                    notional=str(notion),
                    venue_order_id=orez.venue_order_id,
                    warmup_phase=entry_wp,
                )
                await self._reconcile_after_entry(sym)
            else:
                self._diag.record(sym, "rejected_no_quote", "mirror open failed")
                await logger.aerror(
                    "testnet_entry_mirror_failed",
                    symbol=sym,
                    venue_order_id=orez.venue_order_id or "",
                    requested_qty=str(qty),
                    filled_qty=str(filled_qty),
                    paper_position_created=False,
                    mirror_open_called=True,
                )
