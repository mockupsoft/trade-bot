"""In-process paper trading loop for the dashboard (v1).

Bridges live Binance testnet tickers → ``StreamingFeatureVector`` (tick adapter)
→ ``ScoringSignalEngine`` → ``RiskManager`` → ``SizingEngine`` →
``ExecutionEngine`` (paper) → ``AnalyticsEngine`` on position close.

This is **not** a replacement for Redis Streams in the distributed layout; it
makes the monolithic dashboard process exercise the same decision chain the
integration tests use, so the Positions journal can populate from real market
context while respecting ops toggles and risk veto.

Disable with ``CTE_DASHBOARD_PAPER_LOOP=0`` (used in pytest dashboard suite).
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import structlog

from cte.core.events import (
    DataQuality,
    FreshnessScore,
    RiskDecision,
    SignalAction,
    SignalEvent,
    StreamingFeatureVector,
    Symbol,
    TimeframeFeatures,
)
from cte.core.settings import (
    CTESettings,
    ExecutionMode,
    RiskSettings,
    SignalSettings,
    SizingSettings,
)
from cte.execution.engine import ExecutionEngine
from cte.risk.manager import PortfolioState, RiskManager
from cte.signals.engine import ScoringSignalEngine
from cte.sizing.engine import SizingEngine

if TYPE_CHECKING:
    from collections.abc import Callable

    from cte.analytics.engine import AnalyticsEngine
    from cte.execution.paper import PaperExecutionEngine
    from cte.execution.position import PaperPosition
    from cte.market.feed import MarketDataFeed, TickerState
    from cte.ops.kill_switch import OperationsController

logger = structlog.get_logger("dashboard.paper_runner")

# --- Rejection reason codes (API / dashboard) --------------------------------
REJECTION_KEYS: tuple[str, ...] = (
    "rejected_warmup",
    "rejected_spread",
    "rejected_freshness",
    "rejected_feasibility",
    "rejected_divergence",
    "rejected_tier_score",
    "rejected_cooldown",
    "rejected_hourly_limit",
    "rejected_min_notional",
    "rejected_risk",
    "rejected_symbol_disabled",
    "rejected_entries_paused",
    "rejected_existing_position",
    "rejected_no_quote",
    "rejected_venue_rest",
    "rejected_sizing_failed",
    "rejected_venue_proof_symbol",
    "rejected_unknown_gate",
)


class EntryDiagnostics:
    """Counts and last-N log for blocked paper entries (dashboard only)."""

    def __init__(self) -> None:
        self.global_counts: dict[str, int] = {k: 0 for k in REJECTION_KEYS}
        self.per_symbol: dict[str, dict[str, int]] = defaultdict(
            lambda: {k: 0 for k in REJECTION_KEYS}
        )
        self.last_blocked: deque[dict[str, Any]] = deque(maxlen=20)
        self.entry_attempts = 0
        self.eligible_signals = 0

    def record(self, symbol: str, reason: str, detail: str = "") -> None:
        if reason not in self.global_counts:
            self.global_counts[reason] = 0
        self.global_counts[reason] += 1
        row = self.per_symbol[symbol]
        if reason not in row:
            row[reason] = 0
        row[reason] += 1
        self.last_blocked.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "symbol": symbol,
                "reason": reason,
                "detail": detail[:240],
            }
        )


# Dashboard paper loop: staged warmup + lower tier-C than global defaults.
def _dashboard_warmup_mids_early() -> int:
    raw = (os.environ.get("CTE_DASHBOARD_PAPER_WARMUP_MIDS_EARLY") or "20").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 20
    return max(15, min(80, n))


def _dashboard_warmup_mids_full() -> int:
    raw = (
        os.environ.get("CTE_DASHBOARD_PAPER_WARMUP_MIDS_FULL")
        or os.environ.get("CTE_DASHBOARD_PAPER_WARMUP_MIDS")
        or "36"
    ).strip()
    try:
        n = int(raw)
    except ValueError:
        n = 36
    return max(20, min(120, n))


def _dashboard_warmup_thresholds() -> tuple[int, int]:
    """Return (early, full) mid counts; full is always > early."""
    early = _dashboard_warmup_mids_early()
    full = _dashboard_warmup_mids_full()
    if full <= early:
        full = early + 1
    return early, full


def _dashboard_signal_settings(base: SignalSettings) -> SignalSettings:
    """Tier thresholds for dashboard paper loop only (does not change Redis services)."""
    raw = (os.environ.get("CTE_DASHBOARD_PAPER_TIER_C") or "0.32").strip()
    try:
        tier_c = float(raw)
    except ValueError:
        tier_c = 0.32
    tier_c = max(0.15, min(float(base.tier_b_threshold) - 0.01, tier_c))
    return base.model_copy(update={"tier_c_threshold": tier_c})


def _dashboard_risk_settings(base: RiskSettings, symbol_count: int) -> RiskSettings:
    """Raise total exposure cap so one max-sized position per symbol can coexist in sim."""
    n = max(1, symbol_count)
    per = float(base.max_position_pct)
    needed = min(1.0, per * float(n))
    merged = max(float(base.max_total_exposure_pct), needed)
    return base.model_copy(update={"max_total_exposure_pct": merged})


def _dashboard_early_size_mult() -> Decimal:
    raw = (os.environ.get("CTE_DASHBOARD_PAPER_EARLY_SIZE_MULT") or "0.35").strip()
    try:
        m = float(raw)
    except ValueError:
        m = 0.35
    m = max(0.1, min(1.0, m))
    return Decimal(str(round(m, 4)))


def _dashboard_paper_interval_sec() -> float:
    raw = (os.environ.get("CTE_DASHBOARD_PAPER_INTERVAL_SEC") or "1.5").strip()
    try:
        s = float(raw)
    except ValueError:
        s = 1.5
    return max(0.5, min(10.0, s))


def _dashboard_stall_warn_sec() -> float:
    raw = (os.environ.get("CTE_DASHBOARD_PAPER_STALL_WARN_MINUTES") or "5").strip()
    try:
        m = float(raw)
    except ValueError:
        m = 5.0
    return max(60.0, m * 60.0)


# Engine universe (Binance USDT linear); must match ``Symbol`` enum.
_SYMBOL_MAP: dict[str, Symbol] = {s.value: s for s in Symbol}


def paper_loop_enabled() -> bool:
    raw = (os.environ.get("CTE_DASHBOARD_PAPER_LOOP") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _env_bool(key: str, default: bool) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw not in ("0", "false", "no", "off")


def _event_time_utc(t: TickerState) -> datetime:
    """Decision time from venue/trade clock; wall clock only if feed timestamps missing."""
    ms = t.last_trade_time_ms or t.last_update_ms
    if ms <= 0:
        ms = int(time.time() * 1000)
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _mid_price(t: TickerState) -> Decimal | None:
    if t.best_bid > 0 and t.best_ask > 0:
        return (t.best_bid + t.best_ask) / Decimal("2")
    if t.last_price > 0:
        return t.last_price
    return None


def _compute_momentum_z(mids: list[Decimal], lookback: int) -> float:
    arr = [float(x) for x in mids]
    if len(arr) < max(lookback + 5, 15):
        return 0.0
    if arr[-lookback] <= 0:
        return 0.0
    short_ret = arr[-1] / arr[-lookback] - 1.0
    rets: list[float] = []
    start = max(1, len(arr) - lookback - 20)
    for i in range(start, len(arr)):
        if arr[i - 1] > 0:
            rets.append(arr[i] / arr[i - 1] - 1.0)
    if len(rets) < 5:
        return max(-3.0, min(3.0, short_ret * 80.0))
    mu = statistics.mean(rets)
    sd = statistics.pstdev(rets) or 1e-9
    z = (rets[-1] - mu) / sd
    combined = z + short_ret * 40.0
    return max(-3.0, min(3.0, combined))


def _tf_block(
    window_seconds: int,
    momentum_z: float,
    returns_z: float,
    spread_bps: float,
    trade_count: int,
    volume: float,
    window_fill_pct: float,
) -> TimeframeFeatures:
    return TimeframeFeatures(
        window_seconds=window_seconds,
        returns_z=returns_z,
        momentum_z=momentum_z,
        taker_flow_imbalance=0.12,
        spread_bps=spread_bps,
        spread_widening=0.25,
        ob_imbalance=0.12,
        liquidation_imbalance=-0.35,
        venue_divergence_bps=None,
        trade_count=max(1, trade_count // (window_seconds // 15 + 1)),
        volume=max(0.01, volume / (window_seconds / 30.0 + 1.0)),
        window_fill_pct=min(1.0, window_fill_pct),
    )


def try_build_streaming_vector_from_ticker(
    symbol: Symbol,
    mids: deque[Decimal],
    t: TickerState,
    signal_settings: SignalSettings,
    *,
    early_mids: int,
    full_mids: int,
) -> tuple[StreamingFeatureVector | None, str | None]:
    """Build a feature vector; second value is rejection code when ``None``."""
    mid = _mid_price(t)
    if mid is None or mid <= 0:
        return None, "rejected_no_quote"
    spread = float(t.spread_bps)
    if spread <= 0 or t.best_bid <= 0 or t.best_ask <= 0:
        return None, "rejected_no_quote"

    age = t.age_ms
    fresh = max(0.0, min(1.0, 1.0 - min(age, 15000) / 15000.0))
    if fresh < signal_settings.gate_min_freshness:
        return None, "rejected_freshness"
    if spread > signal_settings.gate_max_spread_bps:
        return None, "rejected_spread"

    mlist = list(mids)
    n = len(mlist)
    early_ok = n >= early_mids
    full_ok = n >= full_mids
    if full_ok:
        phase = "full"
    elif early_ok:
        phase = "early"
    else:
        phase = "none"

    lb60 = max(8, min(60, len(mlist) // 2 or 8))
    z = _compute_momentum_z(mlist, lb60)
    z10 = _compute_momentum_z(mlist, max(3, min(10, len(mlist) // 6 or 3)))

    feas = 0.92 if spread < 12.0 and fresh >= 0.55 else 0.35
    if feas < signal_settings.gate_min_feasibility:
        return None, "rejected_feasibility"

    tc = t.trade_count_1m
    vol = float(t.volume_1m) if t.volume_1m > 0 else float(tc) * 0.01

    fill_base = min(1.0, len(mlist) / 120.0)

    tf10 = _tf_block(10, z10, z10 * 0.95, spread, tc, vol, fill_base * 1.1)
    tf30 = _tf_block(30, z * 0.95, z * 0.9, spread, tc, vol, fill_base)
    tf60 = _tf_block(60, z, z * 0.92, spread, tc, vol, fill_base * 0.95)
    tf5m = _tf_block(300, z * 0.85, z * 0.88, spread, tc, vol, fill_base * 0.85)

    vec = StreamingFeatureVector(
        symbol=symbol,
        tf_10s=tf10,
        tf_30s=tf30,
        tf_60s=tf60,
        tf_5m=tf5m,
        freshness=FreshnessScore(
            trade_age_ms=age,
            orderbook_age_ms=age,
            composite=fresh,
        ),
        execution_feasibility=feas,
        whale_risk_flag=False,
        urgent_news_flag=False,
        last_price=mid,
        best_bid=t.best_bid,
        best_ask=t.best_ask,
        mid_price=mid,
        mark_price=t.mark_price if t.mark_price > 0 else mid,
        data_quality=DataQuality(
            warmup_complete=full_ok,
            warmup_early_eligible=early_ok,
            warmup_mid_count=n,
            warmup_early_threshold=early_mids,
            warmup_full_threshold=full_mids,
            warmup_phase=phase,
            binance_connected=not t.is_stale,
            bybit_connected=True,
            window_fill_pct={"10s": tf10.window_fill_pct, "30s": tf30.window_fill_pct},
        ),
    )
    return vec, None


def build_streaming_vector_from_ticker(
    symbol: Symbol,
    mids: deque[Decimal],
    t: TickerState,
    signal_settings: SignalSettings,
) -> StreamingFeatureVector | None:
    """Backward-compatible wrapper (tests); prefer ``try_build_*`` for diagnostics."""
    early, full = _dashboard_warmup_thresholds()
    vec, _rej = try_build_streaming_vector_from_ticker(
        symbol, mids, t, signal_settings, early_mids=early, full_mids=full
    )
    return vec


def _has_open_position(paper: PaperExecutionEngine, symbol: str) -> bool:
    return any(pos.symbol == symbol and pos.is_open for pos in paper.open_positions.values())


def _has_open_position_same_direction(
    paper: PaperExecutionEngine,
    symbol: str,
    action: SignalAction,
) -> bool:
    """True if an open leg exists on ``symbol`` on the same side as ``action``."""
    want_long = action in (SignalAction.OPEN_LONG, SignalAction.CLOSE_SHORT)
    want_short = action in (SignalAction.OPEN_SHORT, SignalAction.CLOSE_LONG)
    for pos in paper.open_positions.values():
        if not pos.is_open or pos.symbol != symbol:
            continue
        if want_long and pos.direction == "long":
            return True
        if want_short and pos.direction == "short":
            return True
    return False


def _iso_utc(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class DashboardPaperRunner:
    """Runs signal→risk→size→paper→analytics on a fixed interval."""

    def __init__(
        self,
        *,
        settings: CTESettings,
        market_feed: Callable[[], MarketDataFeed | None],
        analytics_engine: Callable[[], AnalyticsEngine | None],
        ops_controller: Callable[[], OperationsController],
        symbols: tuple[str, ...],
    ) -> None:
        self._settings = settings
        self._market_feed = market_feed
        self._analytics_engine = analytics_engine
        self._ops = ops_controller
        self._symbols = symbols
        self._warmup_early, self._warmup_full = _dashboard_warmup_thresholds()

        self._publisher = AsyncMock()
        self._publisher.publish = AsyncMock(return_value="ok")

        self._portfolio = PortfolioState(initial_capital=Decimal("10000"))
        self._risk = RiskManager(
            _dashboard_risk_settings(settings.risk, len(symbols)),
            self._publisher,
            self._portfolio,
        )

        exec_settings = settings.execution.model_copy()
        exec_settings.mode = ExecutionMode.PAPER
        self._execution = ExecutionEngine(
            exec_settings,
            settings.exits,
            self._publisher,
            adapter=None,
        )
        self._mid_history: dict[str, deque[Decimal]] = {s: deque(maxlen=400) for s in symbols}

        # Dashboard-only: more permissive thresholds so Positions can populate under real
        # testnet liquidity (narrow spreads are rare; composite often sits just below 0.40).
        self._demo_entries = _env_bool("CTE_DASHBOARD_PAPER_DEMO_ENTRIES", True)
        raw_warm = (os.environ.get("CTE_DASHBOARD_PAPER_WARMUP_MIDS") or "").strip()
        if raw_warm:
            self._warmup_mid_samples = max(15, int(raw_warm))
        else:
            self._warmup_mid_samples = 50 if self._demo_entries else 80

        sig = _dashboard_signal_settings(settings.signals)
        raw_tier = (os.environ.get("CTE_DASHBOARD_PAPER_TIER_C_THRESHOLD") or "").strip()
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

    def stop(self) -> None:
        self._running = False

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _pipeline_stall_analysis(self) -> dict[str, Any]:
        """Explain where the entry pipeline last progressed when entries_total is zero."""
        if self._entries_total > 0:
            return {
                "stalled": False,
                "furthest_stage": "opened",
                "dominant_blocker": None,
                "hint": "Paper entries have occurred.",
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
            "pre_signal": "No tier-eligible signal yet — warmup, hard gates, tier floor, cooldown, or hourly cap.",
            "eligible_signal": "Signal passed scoring; not yet risk-approved — check correlation, exposure, drawdown vetoes.",
            "risk_approved": "Risk approved; sizing returned None or zero — check min_order / sizer.",
            "sized": "Sized order ready; execution did not fill — missing bid/ask book.",
            "execution_attempted": "execute_signal ran; if still no position, inspect paper backend / logs.",
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
        paper = self._execution.paper_backend
        open_n = 0
        if paper:
            open_n = sum(1 for p in paper.open_positions.values() if p.is_open)
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
                    "paper_stall_no_entries",
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
            "runner_class": "DashboardPaperRunner",
            "in_process_execution": "paper_simulated",
            "execution_mode": "paper",
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
            "stall": {
                "warn_after_sec": stall_sec,
                "stall_active": stall_active,
                "top_blocker": top_blocker,
            },
        }

    def warmup_snapshot(self) -> dict[str, Any]:
        """Per-symbol warmup progress for APIs."""
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
        paper = self._execution.paper_backend
        if not paper:
            return []
        out: list[dict[str, Any]] = []
        for pos in paper.open_positions.values():
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
                }
            )
        return out

    async def run_forever(self, interval_sec: float | None = None) -> None:
        self._running = True
        if interval_sec is None:
            interval_sec = _dashboard_paper_interval_sec()
        self._runner_started_mono = time.monotonic()
        await logger.ainfo(
            "paper_runner_started",
            interval_sec=interval_sec,
            symbols=list(self._symbols),
            warmup_early=self._warmup_early,
            warmup_full=self._warmup_full,
        )
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
                await logger.aexception("paper_runner_tick_failed", error=str(e))
            await asyncio.sleep(interval_sec)
        await logger.ainfo("paper_runner_stopped")

    async def _maybe_log_warmup_transition(
        self, sym: str, vec: StreamingFeatureVector | None, t: TickerState
    ) -> None:
        if vec is None:
            return
        dq = vec.data_quality
        prev = self._symbol_gate_state.get(sym, "none")
        if dq.warmup_early_eligible and prev == "none":
            self._symbol_gate_state[sym] = "ready"
            await logger.ainfo(
                "paper_symbol_warmup_ready",
                symbol=sym,
                phase=dq.warmup_phase,
                mids=dq.warmup_mid_count,
            )
        elif prev == "ready" and t.is_stale:
            self._symbol_gate_state[sym] = "degraded"
            await logger.awarning("paper_symbol_data_degraded", symbol=sym)

    async def tick(self) -> None:
        feed = self._market_feed()
        analytics = self._analytics_engine()
        if not feed or not analytics:
            return

        ops = self._ops()
        paper = self._execution.paper_backend
        if not paper:
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
            bid, ask = t.best_bid, t.best_ask
            if bid > 0 and ask > 0:
                self._execution.update_book(sym, bid, ask)

            mark = t.mark_price if t.mark_price > 0 else mid
            vec, vec_rej = try_build_streaming_vector_from_ticker(
                sym_enum,
                self._mid_history[sym],
                t,
                self._signal_settings,
                early_mids=self._warmup_early,
                full_mids=self._warmup_full,
            )
            await self._maybe_log_warmup_transition(sym, vec, t)

            closed = self._execution.update_price_and_evaluate(sym, mark, event_now, vec)
            for pos in closed:
                await self._on_position_closed(pos, analytics)

            if not ops.is_entries_allowed:
                self._diag.record(sym, "rejected_entries_paused", "ops mode blocks entries")
                continue
            if not ops.is_symbol_enabled(sym):
                self._diag.record(sym, "rejected_symbol_disabled", "")
                continue
            if _has_open_position(paper, sym):
                self._diag.record(sym, "rejected_existing_position", "")
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

            sizing_settings: SizingSettings = self._settings.sizing
            risk_settings: RiskSettings = self._settings.risk
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

            self._last_execution_attempt_at = event_now
            opened = await self._execution.execute_signal(
                scored,
                sized.quantity,
                sized.notional_usd,
                event_now,
                warmup_phase=entry_wp,
            )
            if opened is not None:
                self._portfolio.update_exposure(sym, sized.notional_usd)
                self._entries_total += 1
                if self._first_entry_mono is None and self._runner_started_mono is not None:
                    self._first_entry_mono = time.monotonic()
                    self._first_entry_ticks = self._ticks_ok
                await logger.ainfo(
                    "paper_position_opened",
                    symbol=sym,
                    tier=scored.tier.value,
                    notional=str(sized.notional_usd),
                    warmup_phase=entry_wp,
                )
            else:
                self._diag.record(sym, "rejected_no_quote", "paper fill")

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
            venue="binance",
            was_profitable_at_exit=was_prof,
            source="paper_simulated",
            warmup_phase=position.warmup_phase,
        )
        self._exits_recorded += 1
        await logger.ainfo(
            "paper_position_closed",
            symbol=sym,
            pnl=str(position.realized_pnl),
            exit_reason=position.exit_reason,
            warmup_phase=position.warmup_phase,
        )
