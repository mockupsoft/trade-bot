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
from collections import deque
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
    from collections.abc import Callable, Iterable

    from cte.analytics.engine import AnalyticsEngine
    from cte.execution.paper import PaperExecutionEngine
    from cte.execution.position import PaperPosition
    from cte.market.feed import MarketDataFeed, TickerState
    from cte.ops.kill_switch import OperationsController

logger = structlog.get_logger("dashboard.paper_runner")

# v1 symbols only
_SYMBOL_MAP: dict[str, Symbol] = {
    "BTCUSDT": Symbol.BTCUSDT,
    "ETHUSDT": Symbol.ETHUSDT,
}


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


def _compute_momentum_z(mids: Iterable[Decimal], lookback: int) -> float:
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


def build_streaming_vector_from_ticker(
    symbol: Symbol,
    mids: deque[Decimal],
    t: TickerState,
    signal_settings: SignalSettings,
    *,
    warmup_mid_samples: int = 80,
) -> StreamingFeatureVector | None:
    """Build a feature vector from rolling mids + latest ticker (LONG-only adapter)."""
    mid = _mid_price(t)
    if mid is None or mid <= 0:
        return None
    spread = float(t.spread_bps)
    if spread <= 0 or t.best_bid <= 0 or t.best_ask <= 0:
        return None

    age = t.age_ms
    fresh = max(0.0, min(1.0, 1.0 - min(age, 15000) / 15000.0))
    if fresh < signal_settings.gate_min_freshness:
        return None
    if spread > signal_settings.gate_max_spread_bps:
        return None

    mlist = list(mids)
    lb60 = max(8, min(60, len(mlist) // 2 or 8))
    z = _compute_momentum_z(mlist, lb60)
    z10 = _compute_momentum_z(mlist, max(3, min(10, len(mlist) // 6 or 3)))

    warmup_ok = len(mlist) >= warmup_mid_samples
    feas = 0.92 if spread < 12.0 and fresh >= 0.55 else 0.35
    if feas < signal_settings.gate_min_feasibility:
        return None

    tc = t.trade_count_1m
    vol = float(t.volume_1m) if t.volume_1m > 0 else float(tc) * 0.01

    fill_base = min(1.0, len(mlist) / 120.0)

    tf10 = _tf_block(10, z10, z10 * 0.95, spread, tc, vol, fill_base * 1.1)
    tf30 = _tf_block(30, z * 0.95, z * 0.9, spread, tc, vol, fill_base)
    tf60 = _tf_block(60, z, z * 0.92, spread, tc, vol, fill_base * 0.95)
    tf5m = _tf_block(300, z * 0.85, z * 0.88, spread, tc, vol, fill_base * 0.85)

    return StreamingFeatureVector(
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
            warmup_complete=warmup_ok,
            binance_connected=not t.is_stale,
            bybit_connected=True,
            window_fill_pct={"10s": tf10.window_fill_pct, "30s": tf30.window_fill_pct},
        ),
    )


def _has_open_long(paper: PaperExecutionEngine, symbol: str) -> bool:
    return any(pos.symbol == symbol and pos.is_open for pos in paper.open_positions.values())


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

        self._publisher = AsyncMock()
        self._publisher.publish = AsyncMock(return_value="ok")

        self._portfolio = PortfolioState(initial_capital=Decimal("10000"))
        self._risk = RiskManager(settings.risk, self._publisher, self._portfolio)

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

        sig = settings.signals.model_copy()
        raw_tier = (os.environ.get("CTE_DASHBOARD_PAPER_TIER_C_THRESHOLD") or "").strip()
        if raw_tier:
            sig = sig.model_copy(update={"tier_c_threshold": float(raw_tier)})
        elif self._demo_entries:
            sig = sig.model_copy(
                update={"tier_c_threshold": min(sig.tier_c_threshold, 0.36)},
            )
        self._signal_settings = sig
        self._signal_engine = ScoringSignalEngine(sig, self._publisher)

        self._running = False
        self._last_error: str | None = None
        self._ticks_ok = 0
        self._entries_total = 0
        self._exits_recorded = 0
        self._last_skip: dict[str, str] = {}

    def stop(self) -> None:
        self._running = False

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def status_dict(self) -> dict[str, Any]:
        paper = self._execution.paper_backend
        open_n = 0
        if paper:
            open_n = sum(1 for p in paper.open_positions.values() if p.is_open)
        return {
            "ticks_ok": self._ticks_ok,
            "entries_total": self._entries_total,
            "exits_recorded": self._exits_recorded,
            "open_positions": open_n,
            "last_error": self._last_error,
            "demo_entries": self._demo_entries,
            "warmup_mid_samples": self._warmup_mid_samples,
            "tier_c_threshold": self._signal_settings.tier_c_threshold,
            "mids_buffered": {s: len(self._mid_history[s]) for s in self._symbols},
            "last_skip_by_symbol": dict(self._last_skip),
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
                    "entry_reason": (pos.entry_reason or "")[:500],
                    "opened_at": pos.fill_time.isoformat() if pos.fill_time else "",
                }
            )
        return out

    async def run_forever(self, interval_sec: float = 2.0) -> None:
        self._running = True
        await logger.ainfo(
            "paper_runner_started",
            interval_sec=interval_sec,
            symbols=list(self._symbols),
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
            closed = self._execution.update_price_and_evaluate(sym, mark, event_now)
            for pos in closed:
                await self._on_position_closed(pos, analytics)

            if not ops.is_entries_allowed:
                self._last_skip[sym] = "ops_not_active"
                continue
            if not ops.is_symbol_enabled(sym):
                self._last_skip[sym] = "symbol_disabled"
                continue
            if _has_open_long(paper, sym):
                self._last_skip[sym] = "already_long"
                continue

            vec = build_streaming_vector_from_ticker(
                sym_enum,
                self._mid_history[sym],
                t,
                self._signal_settings,
                warmup_mid_samples=self._warmup_mid_samples,
            )
            if vec is None:
                n_mids = len(self._mid_history[sym])
                if n_mids < self._warmup_mid_samples:
                    self._last_skip[sym] = f"warmup_{n_mids}/{self._warmup_mid_samples}_mids"
                else:
                    self._last_skip[sym] = "no_feature_vector"
                continue

            scored = await self._signal_engine.evaluate(vec)
            if scored is None:
                self._last_skip[sym] = "signal_gated_or_below_tier"
                continue

            legacy = SignalEvent(
                symbol=scored.symbol,
                action=SignalAction.OPEN_LONG,
                confidence=scored.composite_score,
                reason=scored.reason,
            )

            sizing_settings: SizingSettings = self._settings.sizing
            risk_settings: RiskSettings = self._settings.risk
            est = min(
                Decimal(str(sizing_settings.max_order_usd)),
                self._portfolio.portfolio_value * Decimal(str(risk_settings.max_position_pct)),
            )
            if est < Decimal(str(sizing_settings.min_order_usd)):
                self._last_skip[sym] = "sizing_below_min_order"
                continue

            assessment = await self._risk.assess_signal(legacy, est)
            if assessment.decision != RiskDecision.APPROVED:
                self._last_skip[sym] = "risk_veto"
                continue

            sizer = SizingEngine(sizing_settings, self._publisher, self._portfolio.portfolio_value)
            sized = await sizer.size_order(legacy, assessment, mark)
            if sized is None:
                self._last_skip[sym] = "sizing_failed"
                continue

            opened = await self._execution.execute_signal(
                scored, sized.quantity, sized.notional_usd, event_now
            )
            if opened is not None:
                self._portfolio.update_exposure(sym, sized.notional_usd)
                self._entries_total += 1
                self._last_skip[sym] = "ok_opened"
                await logger.ainfo(
                    "paper_position_opened",
                    symbol=sym,
                    tier=scored.tier.value,
                    notional=str(sized.notional_usd),
                )
            else:
                self._last_skip[sym] = "paper_fill_rejected_no_book"

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
        )
        self._exits_recorded += 1
        await logger.ainfo(
            "paper_position_closed",
            symbol=sym,
            pnl=str(position.realized_pnl),
            exit_reason=position.exit_reason,
        )
