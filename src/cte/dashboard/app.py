"""CTE Dashboard — Binance USDⓈ-M **futures testnet** only.

- WebSocket: testnet combined stream (see ``BinanceSettings.ws_combined_url`` / ``CTE_BINANCE_WS_COMBINED_URL``).
- REST safety gate: ``CTE_BINANCE_TESTNET_API_KEY`` and ``CTE_BINANCE_TESTNET_API_SECRET`` required.
- Optional in-process **paper loop** (``CTE_DASHBOARD_PAPER_LOOP``, default on): live tickers → scoring →
  risk → sizing → paper fills → journal on close. Disable with ``0`` for tests.
- No seed trade injection; closed rows come from recorded executions (paper simulated / future demo fills).
- ``CTE_ENGINE_MODE=live`` is still blocked by ``enforce_safety``; any other value runs the testnet profile.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import structlog
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import HTMLResponse

from cte.analytics.engine import AnalyticsEngine
from cte.analytics.epochs import EpochManager, EpochMode
from cte.api.analytics_routes import router as analytics_router
from cte.api.analytics_routes import set_engine
from cte.api.health import router as health_router
from cte.core.logging import setup_logging
from cte.core.settings import EngineMode, ExecutionMode, get_settings
from cte.core.universe import (
    DEFAULT_TRADING_SYMBOLS,
    binance_futures_default_streams,
    expand_legacy_engine_symbols,
    merge_market_feed_symbols,
)
from cte.dashboard.paper_runner import (
    DashboardPaperRunner,
    _dashboard_paper_interval_sec,
    paper_loop_enabled,
)
from cte.dashboard.testnet_runner import (
    build_dashboard_venue_runner,
    venue_loop_enabled_for_settings,
)
from cte.dashboard.settings_center import DbSettingsCenter, InMemorySettingsCenter, parse_utc
from cte.market.feed import MarketDataFeed, TickerState
from cte.ops.campaign import CampaignCollector, compute_snapshot
from cte.ops.kill_switch import OperationsController
from cte.ops.readiness import (
    CampaignValidationMetrics,
    DashboardPaperToTestnetMetrics,
    EdgeProofMetrics,
    build_dashboard_paper_to_testnet_gates,
    build_edge_proof_checklist,
    build_phase5_live_gates_skipped,
    evaluate_readiness,
)
from cte.ops.safety import SystemMode, enforce_safety, print_startup_banner
from cte.ops.validation import ValidationCampaign

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

log = structlog.get_logger("dashboard")

TEMPLATE_DIR = Path(__file__).parent / "templates"

# ── Global State ──────────────────────────────────────────────
ACTIVE_TESTNET_EPOCH = "crypto_v1_demo"
# Set at startup from expanded engine symbols (see ``lifespan``).
_active_dashboard_symbols: tuple[str, ...] = DEFAULT_TRADING_SYMBOLS
# Active analytics epoch name (``crypto_v1_paper`` when ``CTE_ENGINE_MODE=paper``).
_active_dashboard_epoch: str = ACTIVE_TESTNET_EPOCH

_system_mode: SystemMode = SystemMode.DEMO
_epoch_manager = EpochManager()
_analytics_engine: AnalyticsEngine | None = None
_ops_controller = OperationsController()
_market_feed: MarketDataFeed | None = None
_feed_task: asyncio.Task | None = None
_validation_campaigns: dict[str, ValidationCampaign] = {}
_campaign_collector = CampaignCollector()
_recon_status: dict = {"status": "not_run", "mismatches": 0, "last_run": None, "details": []}
_paper_runner: DashboardPaperRunner | DashboardTestnetRunner | None = None
_paper_task: asyncio.Task | None = None
_trade_log_store: Any | None = None
_db_pool: Any | None = None
_trade_log_tasks: set[asyncio.Task] = set()
_ops_runtime_samples: deque[dict[str, Any]] = deque(maxlen=240)
_settings_center: Any | None = None
_settings_apply_tasks: dict[str, asyncio.Task] = {}
_dashboard_started_at: datetime = datetime.now(UTC)


def _resolve_mode() -> SystemMode:
    """Map ``CTE_ENGINE_MODE`` to dashboard system mode (default: paper, no API keys)."""
    raw = (os.environ.get("CTE_ENGINE_MODE") or "paper").lower()
    if raw == "live":
        return SystemMode.LIVE
    if raw == "seed":
        return SystemMode.SEED
    if raw == "demo":
        return SystemMode.DEMO
    if raw == "paper":
        return SystemMode.PAPER
    return SystemMode.PAPER


def _journal_db_enabled() -> bool:
    raw = (os.environ.get("CTE_DASHBOARD_JOURNAL_DB") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _role_allowed(role: str, allowed: set[str]) -> bool:
    return role.strip().lower() in allowed


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def _schedule_revision_apply(revision_id: str, run_at: datetime, actor: str) -> None:
    delay = max(0.0, (run_at - _now_utc()).total_seconds())
    await asyncio.sleep(delay)
    sc = _settings_center
    if sc is None:
        return
    try:
        await sc.apply(revision_id, applied_by=actor)
        await log.ainfo("settings_revision_applied_scheduled", revision_id=revision_id, actor=actor)
    except Exception as exc:
        await log.awarning(
            "settings_revision_schedule_apply_failed",
            revision_id=revision_id,
            actor=actor,
            error=str(exc)[:300],
        )
    finally:
        _settings_apply_tasks.pop(revision_id, None)


def _spawn_revision_schedule(revision_id: str, run_at: datetime, actor: str) -> None:
    existing = _settings_apply_tasks.get(revision_id)
    if existing is not None and not existing.done():
        existing.cancel()
    task = asyncio.create_task(_schedule_revision_apply(revision_id, run_at, actor))
    _settings_apply_tasks[revision_id] = task


async def _restore_pending_schedules() -> None:
    sc = _settings_center
    if sc is None:
        return
    try:
        rows = await sc.pending_schedules()
    except Exception:
        return
    now = _now_utc()
    for row in rows:
        rid = str(row.get("revision_id") or "")
        when_raw = row.get("scheduled_for")
        actor = str(row.get("scheduled_by") or "dashboard_user")
        if not rid or not when_raw:
            continue
        try:
            when = parse_utc(str(when_raw))
        except Exception:
            continue
        if when <= now:
            if rid not in _settings_apply_tasks:
                _spawn_revision_schedule(rid, now, actor)
            continue
        _spawn_revision_schedule(rid, when, actor)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global \
        _active_dashboard_epoch, \
        _active_dashboard_symbols, \
        _analytics_engine, \
        _market_feed, \
        _feed_task, \
        _system_mode, \
        _paper_runner, \
        _paper_task, \
        _trade_log_store, \
        _db_pool, \
        _trade_log_tasks, \
        _settings_center, \
        _settings_apply_tasks
    # Repo-root ``.env`` overrides stale shell exports (e.g. old testnet key placeholders).
    load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env", override=True)
    setup_logging(level="INFO", service_name="dashboard")

    _system_mode = _resolve_mode()
    if _system_mode == SystemMode.DEMO:
        os.environ["CTE_ENGINE_MODE"] = "demo"
    elif _system_mode == SystemMode.PAPER:
        os.environ["CTE_ENGINE_MODE"] = "paper"
    banner_key = "demo" if _system_mode == SystemMode.DEMO else _system_mode.value
    print_startup_banner(banner_key)

    if _system_mode == SystemMode.DEMO:
        exec_venue = (
            (os.environ.get("CTE_DASHBOARD_EXECUTION_VENUE") or "binance_testnet").strip().lower()
        )
        enforce_safety(
            "demo",
            execution_venue=exec_venue,
            binance_rest_url=os.environ.get(
                "CTE_BINANCE_TESTNET_REST_URL", "https://testnet.binancefuture.com"
            ),
            binance_api_key=os.environ.get("CTE_BINANCE_TESTNET_API_KEY", ""),
            binance_api_secret=os.environ.get("CTE_BINANCE_TESTNET_API_SECRET", ""),
            bybit_rest_url=os.environ.get("CTE_BYBIT_REST_BASE_URL", "https://api-demo.bybit.com"),
            bybit_api_key=os.environ.get("CTE_BYBIT_DEMO_API_KEY", ""),
            bybit_api_secret=os.environ.get("CTE_BYBIT_DEMO_API_SECRET", ""),
        )
    elif _system_mode == SystemMode.PAPER:
        enforce_safety("paper")

    if _system_mode == SystemMode.LIVE:
        enforce_safety("live")

    if _system_mode == SystemMode.PAPER:
        _active_dashboard_epoch = "crypto_v1_paper"
        _epoch_manager.create_epoch(
            _active_dashboard_epoch,
            EpochMode.PAPER,
            "Binance USD-M futures testnet (paper simulated)",
        )
    else:
        _active_dashboard_epoch = ACTIVE_TESTNET_EPOCH
        _epoch_manager.create_epoch(
            ACTIVE_TESTNET_EPOCH,
            EpochMode.DEMO,
            "Binance USD-M futures testnet",
        )
    _epoch_manager.activate(_active_dashboard_epoch)

    _analytics_engine = AnalyticsEngine(_epoch_manager, initial_capital=Decimal("10000"))
    set_engine(_analytics_engine)

    settings = get_settings()
    _trade_log_store = None
    _db_pool = None
    _trade_log_tasks = set()
    _settings_apply_tasks = {}
    _settings_center = InMemorySettingsCenter()
    await _settings_center.ensure_ready()
    if _journal_db_enabled() and _analytics_engine is not None:
        try:
            from cte.db.pool import DatabasePool
            from cte.db.trade_log import TradeLogStore

            _db_pool = DatabasePool(settings.database)
            await asyncio.wait_for(_db_pool.connect(), timeout=1.5)
            _trade_log_store = TradeLogStore(_db_pool)
            await _trade_log_store.ensure_ready()
            hydrated = await _trade_log_store.load_trades()
            _analytics_engine.hydrate_trades(hydrated)

            def _persist_trade_to_db(trade: Any) -> None:
                if _trade_log_store is None:
                    return
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                task = loop.create_task(_trade_log_store.insert_trade(trade))
                _trade_log_tasks.add(task)
                task.add_done_callback(_trade_log_tasks.discard)

            _analytics_engine.set_trade_persist_callback(_persist_trade_to_db)
            await log.ainfo("journal_db_ready", hydrated_trades=len(hydrated))

            _settings_center = DbSettingsCenter(_db_pool)
            await _settings_center.ensure_ready()
            await log.ainfo("settings_center_ready", backend="db")
        except Exception as exc:
            await log.awarning("journal_db_unavailable", error=str(exc)[:300])
            if _db_pool is not None:
                with contextlib.suppress(Exception):
                    await _db_pool.close()
            _db_pool = None
            _trade_log_store = None
            _settings_center = InMemorySettingsCenter()
            await _settings_center.ensure_ready()
            await log.ainfo("settings_center_ready", backend="memory")

    await _restore_pending_schedules()

    expanded = merge_market_feed_symbols(
        expand_legacy_engine_symbols(list(settings.engine.symbols)),
    )
    dash_syms = tuple(expanded)
    _active_dashboard_symbols = dash_syms
    streams = binance_futures_default_streams(dash_syms)
    _market_feed = MarketDataFeed(
        ws_url=settings.binance.ws_combined_url,
        streams=streams,
        symbols=dash_syms,
    )
    _feed_task = asyncio.create_task(_market_feed.start())
    await log.ainfo(
        "market_feed_started",
        mode="testnet",
        ws_url=settings.binance.ws_combined_url,
        symbol_count=len(dash_syms),
        stream_count=len(streams),
    )

    if paper_loop_enabled():
        if venue_loop_enabled_for_settings(settings):
            _paper_runner = build_dashboard_venue_runner(
                settings=settings,
                market_feed=lambda: _market_feed,
                analytics_engine=lambda: _analytics_engine,
                ops_controller=lambda: _ops_controller,
                symbols=dash_syms,
            )
            _paper_interval = _dashboard_paper_interval_sec()
            _paper_task = asyncio.create_task(
                _paper_runner.run_forever(interval_sec=_paper_interval)
            )
            await log.ainfo(
                "dashboard_venue_runner_scheduled",
                interval_sec=_paper_interval,
                execution_mode="testnet",
            )
        elif (
            settings.engine.mode == EngineMode.DEMO
            and settings.execution.mode == ExecutionMode.TESTNET
        ):
            v = (os.environ.get("CTE_DASHBOARD_EXECUTION_VENUE") or "binance_testnet").strip()
            print(
                "\n  ABORT: CTE_ENGINE_MODE=demo and CTE_EXECUTION_MODE=testnet require "
                f"valid API credentials for CTE_DASHBOARD_EXECUTION_VENUE={v!r}.\n",
                file=sys.stderr,
            )
            raise SystemExit(1)
        else:
            _paper_runner = DashboardPaperRunner(
                settings=settings,
                market_feed=lambda: _market_feed,
                analytics_engine=lambda: _analytics_engine,
                ops_controller=lambda: _ops_controller,
                symbols=dash_syms,
            )
            _paper_interval = _dashboard_paper_interval_sec()
            _paper_task = asyncio.create_task(
                _paper_runner.run_forever(interval_sec=_paper_interval)
            )
            await log.ainfo("paper_runner_scheduled", interval_sec=_paper_interval)
    else:
        await log.ainfo("paper_runner_disabled", reason="CTE_DASHBOARD_PAPER_LOOP")

    await log.ainfo("dashboard_ready", mode="testnet")

    yield

    # Shutdown
    if _paper_runner:
        _paper_runner.stop()
    if _paper_task:
        _paper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _paper_task
    _paper_task = None
    _paper_runner = None
    if _market_feed:
        _market_feed.stop()
    if _feed_task:
        _feed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _feed_task
    if _trade_log_tasks:
        with contextlib.suppress(Exception):
            await asyncio.gather(*list(_trade_log_tasks), return_exceptions=True)
        _trade_log_tasks.clear()
    if _settings_apply_tasks:
        for t in list(_settings_apply_tasks.values()):
            t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*list(_settings_apply_tasks.values()), return_exceptions=True)
        _settings_apply_tasks.clear()
    if _db_pool is not None:
        with contextlib.suppress(Exception):
            await _db_pool.close()
        _db_pool = None
    _trade_log_store = None
    _settings_center = None
    await log.ainfo("dashboard_stopped")


app = FastAPI(title="CTE Dashboard", version="0.1.0", lifespan=lifespan)
app.include_router(health_router, prefix="/api/dashboard")
app.include_router(analytics_router)


# ── Pages ─────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(
        content=(TEMPLATE_DIR / "index.html").read_text(),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/dashboard/meta")
async def dashboard_meta() -> dict[str, str]:
    """Process fingerprint for debugging wrong-port / stale servers on :8080."""
    return {
        "service": "cte.dashboard",
        "market_profile": "binance_usdm_testnet",
        "active_epoch": _active_dashboard_epoch,
    }


@app.get("/api/paper/status")
async def paper_loop_status() -> dict[str, Any]:
    """Paper trading loop counters (dashboard in-process pipeline)."""
    if not paper_loop_enabled():
        return {
            "enabled": False,
            "runner_active": False,
            "hint": "Set CTE_DASHBOARD_PAPER_LOOP=1 (default) to run tick→signal→risk→paper.",
        }
    if _paper_runner is None:
        return {
            "enabled": paper_loop_enabled(),
            "runner_active": False,
            "hint": "Runner not initialized (startup pending or failed).",
        }
    out: dict[str, Any] = _paper_runner.status_dict()
    out["enabled"] = True
    out["runner_active"] = True
    out["paper_loop_env"] = os.environ.get("CTE_DASHBOARD_PAPER_LOOP", "1")
    return out


@app.get("/api/paper/positions")
async def paper_open_positions() -> dict[str, Any]:
    """Open LONG paper positions (bid/ask fills, not venue margin)."""
    if _paper_runner is None:
        return {"positions": [], "meta": {"note": "paper runner off or disabled"}}
    return {"positions": _paper_runner.open_positions_payload()}


@app.get("/api/paper/warmup")
async def paper_warmup_snapshot() -> dict[str, Any]:
    """Per-symbol mid counts, warmup gate, ETA to full threshold (dashboard paper)."""
    if _paper_runner is None:
        return {"error": "paper runner not running", "symbols": {}}
    return _paper_runner.warmup_snapshot()


@app.get("/api/paper/entry-diagnostics")
async def paper_entry_diagnostics() -> dict[str, Any]:
    """Blocked entry reasons, last 20 attempts, attempt/eligible counters."""
    if _paper_runner is None:
        return {"error": "paper runner not running"}
    return _paper_runner.entry_diagnostics_payload()


# ── Market Data API ───────────────────────────────────────────


def _empty_ticker_payload() -> dict[str, object]:
    """Placeholder row when the feed is down or a symbol has not ticked yet."""
    return {
        "last_price": "0",
        "best_bid": "0",
        "best_ask": "0",
        "mark_price": "0",
        "bid_qty": "0",
        "ask_qty": "0",
        "spread_bps": 0.0,
        "age_ms": 999999,
        "is_stale": True,
        "trade_count_1m": 0,
        "volume_1m": "0",
    }


def _serialize_ticker(t: TickerState) -> dict[str, object]:
    """JSON-serialize a live ``TickerState``."""
    return {
        "last_price": str(t.last_price),
        "best_bid": str(t.best_bid),
        "best_ask": str(t.best_ask),
        "mark_price": str(t.mark_price),
        "bid_qty": str(t.bid_qty),
        "ask_qty": str(t.ask_qty),
        "spread_bps": round(t.spread_bps, 2),
        "age_ms": t.age_ms,
        "is_stale": t.is_stale,
        "trade_count_1m": t.trade_count_1m,
        "volume_1m": str(t.volume_1m),
    }


def _build_market_tickers_payload() -> dict[str, object]:
    """Always return v1 symbols so the dashboard grid is never empty."""
    settings = get_settings()
    stream_url = str(settings.binance.ws_combined_url)
    base_rows = {sym: _empty_ticker_payload() for sym in _active_dashboard_symbols}
    if not _market_feed:
        return {
            "source": "none",
            "mode": "testnet",
            "tickers": base_rows,
            "stream_url": stream_url,
            "feed_ready": False,
        }
    tickers: dict[str, dict[str, object]] = {}
    warm_by_sym: dict[str, Any] = {}
    if _paper_runner is not None:
        warm_by_sym = _paper_runner.warmup_snapshot().get("symbols", {})
    for sym in _active_dashboard_symbols:
        t = _market_feed.tickers.get(sym)
        row: dict[str, object] = _serialize_ticker(t) if t else _empty_ticker_payload()
        if sym in warm_by_sym:
            row["warmup"] = warm_by_sym[sym]
        tickers[sym] = row
    for sym, t in _market_feed.tickers.items():
        if sym not in tickers:
            row = _serialize_ticker(t)
            if sym in warm_by_sym:
                row["warmup"] = warm_by_sym[sym]
            tickers[sym] = row
    return {
        "source": "binance_testnet",
        "mode": "testnet",
        "tickers": tickers,
        "stream_url": _market_feed.stream_url,
        "feed_ready": True,
    }


@app.get("/api/market/tickers")
async def market_tickers():
    """Live ticker data for all symbols."""
    return _build_market_tickers_payload()


@app.get("/api/market/health")
async def market_health():
    """Market data feed health status."""
    if not _market_feed:
        settings = get_settings()
        return {
            "connected": False,
            "mode": "testnet",
            "detail": "Feed not initialized",
            "messages_total": 0,
            "reconnect_count": 0,
            "errors_total": 0,
            "latency_ms": 0.0,
            "uptime_seconds": 0.0,
            "last_message_age_ms": None,
            "stream_url": str(settings.binance.ws_combined_url),
            "symbols": {},
        }
    h = _market_feed.health
    now_ms = int(time.time() * 1000)
    last_age: int | None = None
    if h.last_message_ms:
        last_age = max(0, now_ms - h.last_message_ms)
    return {
        "connected": h.connected,
        "mode": "testnet",
        "messages_total": h.messages_total,
        "reconnect_count": h.reconnect_count,
        "errors_total": h.errors_total,
        "latency_ms": round(h.latency_ms, 1),
        "uptime_seconds": round(h.uptime_seconds, 1),
        "last_message_age_ms": last_age,
        "stream_url": _market_feed.stream_url,
        "symbols": h.symbols,
    }


# ── Ops API ───────────────────────────────────────────────────


def _v1_operations_policy() -> dict[str, object]:
    """Static PRD alignment for the Operations UI (matches .cursorrules / phased plan)."""
    s = get_settings()
    return {
        "direction": s.engine.direction.value,
        "symbols": expand_legacy_engine_symbols(list(s.engine.symbols)),
        "venues": {
            "primary": "binance_usdm_futures_testnet",
            "secondary_context": "bybit_v5_public_testnet",
        },
        "max_leverage": 3,
        "live_wallet_v1": False,
        "execution_surface": "paper_demo_testnet",
        "risk_manager": "absolute_veto_over_signals",
        "signal_reason_payload": "required_per_trade_decision",
        "inter_service_bus": "redis_streams_cte_prefix",
        "whale_news_role": "context_only_not_primary_trigger",
        "dashboard_note": (
            "This UI drives the in-process ops controller. Distributed services "
            "must mirror state via Redis Streams (cte:{module}:{event_type})."
        ),
    }


def _ops_runtime_feed_status() -> dict[str, Any]:
    if not _market_feed:
        return {
            "connected": False,
            "reconnect_count": 0,
            "errors_total": 0,
            "last_message_age_ms": None,
            "uptime_seconds": 0.0,
            "latency_ms": 0.0,
        }
    h = _market_feed.health
    now_ms = int(time.time() * 1000)
    age_ms = None
    if h.last_message_ms:
        age_ms = max(0, now_ms - h.last_message_ms)
    return {
        "connected": bool(h.connected),
        "reconnect_count": int(h.reconnect_count),
        "errors_total": int(h.errors_total),
        "last_message_age_ms": age_ms,
        "uptime_seconds": round(float(h.uptime_seconds), 1),
        "latency_ms": round(max(0.0, float(h.latency_ms)), 1),
    }


def _append_ops_runtime_sample() -> None:
    feed = _ops_runtime_feed_status()
    _ops_runtime_samples.append(
        {
            "ts": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "reconciliation_mismatches": int(_recon_status.get("mismatches") or 0),
            "reconnect_count": int(feed.get("reconnect_count") or 0),
            "feed_errors_total": int(feed.get("errors_total") or 0),
            "feed_connected": bool(feed.get("connected")),
        }
    )


def _slo_target_float(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _slo_item(*, actual: float, target: float, higher_is_better: bool, unit: str) -> dict[str, Any]:
    ok = actual >= target if higher_is_better else actual <= target
    delta = (actual - target) if higher_is_better else (target - actual)
    return {
        "actual": round(actual, 4),
        "target": round(target, 4),
        "unit": unit,
        "status": "ok" if ok else "breach",
        "delta_vs_target": round(delta, 4),
    }


def _build_slo_status() -> dict[str, Any]:
    metrics = (
        _analytics_engine.get_metrics(epoch=_active_dashboard_epoch) if _analytics_engine else {}
    )
    paper_status = _paper_runner.status_dict() if _paper_runner is not None else {}
    feed = _ops_runtime_feed_status()
    latest = _campaign_collector.latest

    samples = list(_ops_runtime_samples)[-60:]
    if samples:
        connected_count = sum(1 for s in samples if bool(s.get("feed_connected")))
        uptime_pct = connected_count / len(samples) * 100.0
    else:
        uptime_pct = 100.0 if bool(feed.get("connected")) else 0.0

    decision_latency_ms = float(metrics.get("avg_latency_ms") or 0.0)
    avg_slippage_bps = float(metrics.get("avg_slippage_bps") or 0.0)
    venue_orders = paper_status.get("venue_order_metrics") or {}
    sent = int(venue_orders.get("entry_orders_sent") or 0)
    filled = int(venue_orders.get("entry_orders_filled") or 0)
    fill_rate = (filled / sent) if sent > 0 else 1.0
    reject_rate = float(latest.reject_rate) if latest is not None else 0.0

    targets = {
        "uptime_pct": _slo_target_float("CTE_SLO_UPTIME_PCT", 99.0),
        "decision_latency_ms": _slo_target_float("CTE_SLO_DECISION_LATENCY_MS", 250.0),
        "slippage_bps": _slo_target_float("CTE_SLO_SLIPPAGE_BPS", 8.0),
        "fill_rate": _slo_target_float("CTE_SLO_FILL_RATE", 0.95),
        "rejection_rate": _slo_target_float("CTE_SLO_REJECTION_RATE", 0.35),
    }

    kpis = {
        "uptime": _slo_item(
            actual=uptime_pct,
            target=targets["uptime_pct"],
            higher_is_better=True,
            unit="pct",
        ),
        "decision_latency": _slo_item(
            actual=decision_latency_ms,
            target=targets["decision_latency_ms"],
            higher_is_better=False,
            unit="ms",
        ),
        "fill_quality_slippage": _slo_item(
            actual=avg_slippage_bps,
            target=targets["slippage_bps"],
            higher_is_better=False,
            unit="bps",
        ),
        "fill_quality_fill_rate": _slo_item(
            actual=fill_rate,
            target=targets["fill_rate"],
            higher_is_better=True,
            unit="ratio",
        ),
        "rejection_rate": _slo_item(
            actual=reject_rate,
            target=targets["rejection_rate"],
            higher_is_better=False,
            unit="ratio",
        ),
    }
    breach_count = sum(1 for v in kpis.values() if v.get("status") == "breach")
    return {
        "meta": {
            "utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "runtime_seconds": round(
                (datetime.now(UTC) - _dashboard_started_at).total_seconds(), 1
            ),
            "breach_count": breach_count,
        },
        "targets": targets,
        "kpis": kpis,
        "raw": {
            "samples": len(samples),
            "entry_orders_sent": sent,
            "entry_orders_filled": filled,
            "campaign_reject_rate": reject_rate,
        },
    }


async def _build_release_status() -> dict[str, Any]:
    commit = (os.environ.get("CTE_RELEASE_COMMIT") or "unknown").strip() or "unknown"
    image = (os.environ.get("CTE_RELEASE_IMAGE") or "unknown").strip() or "unknown"
    tag = (os.environ.get("CTE_RELEASE_TAG") or "local").strip() or "local"
    profile = (os.environ.get("CTE_DEPLOY_PROFILE") or "prod").strip() or "prod"
    rollback_from = (os.environ.get("CTE_RELEASE_ROLLBACK_FROM") or "").strip()

    last_settings_rollback: dict[str, Any] | None = None
    if _settings_center is not None:
        try:
            revisions = await _settings_center.list_revisions(limit=60)
            for rev in revisions:
                if rev.get("status") == "applied" and rev.get("supersedes_revision_id"):
                    last_settings_rollback = {
                        "revision_id": rev.get("revision_id"),
                        "rolled_back_to": rev.get("supersedes_revision_id"),
                        "applied_at": rev.get("applied_at"),
                        "actor": rev.get("applied_by") or rev.get("created_by"),
                    }
                    break
        except Exception:
            last_settings_rollback = None

    started = _dashboard_started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "service": "analytics",
        "app_version": app.version,
        "deploy_profile": profile,
        "commit": commit,
        "image": image,
        "image_tag": tag,
        "last_deploy_at": started,
        "uptime_seconds": round((datetime.now(UTC) - _dashboard_started_at).total_seconds(), 1),
        "rollback": {
            "release_rollback_from": rollback_from or None,
            "last_settings_rollback": last_settings_rollback,
        },
    }


def _ops_entry_diag_snapshot() -> dict[str, Any]:
    if _paper_runner is None:
        return {"global_counts": {}, "last_blocked": []}
    try:
        return _paper_runner.entry_diagnostics_payload()
    except Exception:
        return {"global_counts": {}, "last_blocked": []}


def _parse_risk_failed_checks(detail: str) -> list[str]:
    if not detail:
        return []
    marker = "Failed checks:"
    body = detail
    if marker in detail:
        body = detail.split(marker, 1)[1]
    parts = [p.strip() for p in body.split(",")]
    return [p for p in parts if p]


def _build_risk_veto_summary(entry_diag: dict[str, Any]) -> dict[str, Any]:
    global_counts = entry_diag.get("global_counts") or {}
    last_blocked = entry_diag.get("last_blocked") or []
    vetoes = [
        x
        for x in last_blocked
        if isinstance(x, dict) and str(x.get("reason") or "") == "rejected_risk"
    ]
    check_counter: Counter[str] = Counter()
    recent_vetoes: list[dict[str, str]] = []
    for row in vetoes:
        detail = str(row.get("detail") or "")
        checks = _parse_risk_failed_checks(detail)
        for c in checks:
            check_counter[c] += 1
        recent_vetoes.append(
            {
                "ts": str(row.get("ts") or ""),
                "symbol": str(row.get("symbol") or ""),
                "detail": detail or "Risk veto",
            }
        )
    top_checks = [{"check": k, "count": n} for k, n in check_counter.most_common(6)]
    return {
        "total_rejections": int(global_counts.get("rejected_risk") or 0),
        "top_failed_checks": top_checks,
        "recent_vetoes": list(reversed(recent_vetoes[-12:])),
    }


def _build_incident_feed(
    feed_status: dict[str, Any],
    entry_diag: dict[str, Any],
    paper_status: dict[str, Any],
) -> list[dict[str, Any]]:
    incidents: list[dict[str, Any]] = []
    now_iso = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    alerts = _build_alerts_status()
    for rule in alerts.get("rules", []):
        if str(rule.get("state") or "") != "firing":
            continue
        incidents.append(
            {
                "ts": str((alerts.get("meta") or {}).get("utc") or now_iso),
                "severity": str(rule.get("severity") or "warning"),
                "category": "alert_rule",
                "title": str(rule.get("title") or "Alert firing"),
                "detail": str(rule.get("detail") or rule.get("condition") or ""),
            }
        )

    if int(_recon_status.get("mismatches") or 0) > 0:
        incidents.append(
            {
                "ts": now_iso,
                "severity": "critical",
                "category": "reconciliation",
                "title": "Reconciliation mismatches detected",
                "detail": f"mismatches={int(_recon_status.get('mismatches') or 0)}",
            }
        )

    if str(paper_status.get("last_error") or ""):
        incidents.append(
            {
                "ts": now_iso,
                "severity": "critical",
                "category": "runner",
                "title": "Runner error",
                "detail": str(paper_status.get("last_error"))[:260],
            }
        )
    if str(paper_status.get("venue_last_error") or ""):
        incidents.append(
            {
                "ts": now_iso,
                "severity": "warning",
                "category": "venue",
                "title": "Venue execution warning",
                "detail": str(paper_status.get("venue_last_error"))[:260],
            }
        )

    for row in entry_diag.get("last_blocked") or []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "")
        if reason not in {"rejected_risk", "rejected_venue_rest", "rejected_unknown_gate"}:
            continue
        severity = "warning" if reason == "rejected_risk" else "critical"
        incidents.append(
            {
                "ts": str(row.get("ts") or now_iso),
                "severity": severity,
                "category": "entry_block",
                "title": reason,
                "detail": str(row.get("detail") or "")[:260],
                "symbol": str(row.get("symbol") or ""),
            }
        )

    if not bool(feed_status.get("connected")):
        incidents.append(
            {
                "ts": now_iso,
                "severity": "critical",
                "category": "market_feed",
                "title": "Market feed disconnected",
                "detail": "No live packets from market feed.",
            }
        )

    incidents.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    return incidents[:40]


def _build_ops_panel_snapshot() -> dict[str, Any]:
    _append_ops_runtime_sample()
    feed_status = _ops_runtime_feed_status()
    entry_diag = _ops_entry_diag_snapshot()
    paper_status = _paper_runner.status_dict() if _paper_runner is not None else {}
    trend = list(_ops_runtime_samples)[-60:]
    return {
        "meta": {
            "utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "runner_active": _paper_runner is not None,
        },
        "incident_feed": _build_incident_feed(feed_status, entry_diag, paper_status),
        "last_errors": {
            "runner_last_error": str(paper_status.get("last_error") or ""),
            "venue_last_error": str(paper_status.get("venue_last_error") or ""),
            "feed_errors_total": int(feed_status.get("errors_total") or 0),
        },
        "reconnect_status": feed_status,
        "reconciliation": {
            "current": {
                "status": str(_recon_status.get("status") or "not_run"),
                "mismatches": int(_recon_status.get("mismatches") or 0),
                "last_run": _recon_status.get("last_run"),
            },
            "trend": trend,
        },
        "risk_veto": _build_risk_veto_summary(entry_diag),
    }


def _runbook_scenario_no_entries(
    paper_status: dict[str, Any], entry_diag: dict[str, Any]
) -> dict[str, Any]:
    ticks_ok = int(paper_status.get("ticks_ok") or 0)
    entries_total = int(paper_status.get("entries_total") or 0)
    open_positions = int(paper_status.get("open_positions") or 0)
    blocked = entry_diag.get("global_counts") or {}
    dominant = max(blocked.items(), key=lambda kv: kv[1])[0] if blocked else None
    active = ticks_ok >= 40 and entries_total == 0 and open_positions == 0
    severity = "warning" if active else "ok"
    detail = "Henuz giris yok; isinma, gate (kapilar) ve risk engellerini kontrol edin."
    if dominant and int(blocked.get(dominant) or 0) > 0:
        detail = f"Dominant block: {dominant} ({int(blocked.get(dominant) or 0)})."
    return {
        "id": "no_entries",
        "title": "Giris yok",
        "active": active,
        "severity": severity,
        "detail": detail,
        "steps": [
            "/api/paper/status ile warmup gate (isinma kapisi) ve pipeline_stall ipucunu dogrulayin.",
            "/api/paper/entry-diagnostics icinde global_counts ve last_blocked nedenlerini inceleyin.",
            "Girisler duraklatildiysa veya sembol kapaliysa Operasyon sayfasindan acin.",
        ],
        "actions": [
            {"type": "api", "label": "Paper status", "url": "/api/paper/status"},
            {"type": "api", "label": "Entry diagnostics", "url": "/api/paper/entry-diagnostics"},
            {"type": "navigate", "label": "Open Ops page", "hash": "#/ops"},
        ],
    }


def _runbook_scenario_churn(
    paper_status: dict[str, Any], metrics: dict[str, Any]
) -> dict[str, Any]:
    exits_recorded = int(paper_status.get("exits_recorded") or 0)
    total = int(metrics.get("trade_count") or 0)
    by_reason = metrics.get("count_by_exit_reason") or {}
    no_progress = int(by_reason.get("no_progress") or 0)
    churn_ratio = (no_progress / total) if total > 0 else 0.0
    active = exits_recorded >= 8 and churn_ratio >= 0.35
    return {
        "id": "churn",
        "title": "Churn (hizli kapanis/yeniden acilis)",
        "active": active,
        "severity": "warning" if active else "ok",
        "detail": f"no_progress ratio={churn_ratio:.1%} ({no_progress}/{total})",
        "steps": [
            "Trade journal filtrelerinde exit_reason=no_progress ve hold_seconds dagilimini kontrol edin.",
            "Cikis sonrasi cooldown ayarlarini inceleyin; sifirdan buyuk cooldown etkin olmali.",
            "Spread/freshness degerlerinin stabil oldugunu dogrulayin (piyasa sagligi + uyarilar).",
        ],
        "actions": [
            {"type": "navigate", "label": "Open Positions/Journal", "hash": "#/positions"},
            {
                "type": "api",
                "label": "Analytics summary",
                "url": f"/api/analytics/summary?epoch={_active_dashboard_epoch}",
            },
            {"type": "navigate", "label": "Open Alerts", "hash": "#/alerts"},
        ],
    }


def _runbook_scenario_foreign_position(paper_status: dict[str, Any]) -> dict[str, Any]:
    foreign = bool(paper_status.get("foreign_venue_detected"))
    recon = paper_status.get("reconciliation") or {}
    reason = str((recon.get("last") or {}).get("reason") or "")
    active = foreign or reason == "foreign_venue_positions"
    detail = (
        "Acilista foreign venue position (dis kaynakli pozisyon) algilandi; temiz hesap gerekli."
    )
    return {
        "id": "foreign_position",
        "title": "Foreign position (dis pozisyon) algilandi",
        "active": active,
        "severity": "critical" if active else "ok",
        "detail": detail,
        "steps": [
            "Bu calistirici tarafindan acilmayan tum venue pozisyonlarini kapatin.",
            "Validation kampanyasi icin izole API key/hesap kullanin.",
            "Hesap temizlendikten sonra strict validation'i yeniden calistirin.",
        ],
        "actions": [
            {"type": "api", "label": "Reconciliation status", "url": "/api/reconciliation/status"},
            {"type": "api", "label": "Paper status", "url": "/api/paper/status"},
            {"type": "navigate", "label": "Open Ops panel", "hash": "#/ops"},
        ],
    }


def _runbook_scenario_recon_blocked(paper_status: dict[str, Any]) -> dict[str, Any]:
    blocked = bool(paper_status.get("validation_blocked"))
    mismatches = int((_recon_status or {}).get("mismatches") or 0)
    active = blocked or mismatches > 0
    return {
        "id": "recon_blocked",
        "title": "Reconciliation (uzlastirma) bloklu",
        "active": active,
        "severity": "critical" if active else "ok",
        "detail": f"validation_blocked={blocked} · mismatches={mismatches}",
        "steps": [
            "Mismatch detaylarini inceleyin; phantom_venue ile quantity drift ayrimini yapin.",
            "Strict mode aciksa girise izin vermeden once mismatchleri temizleyin.",
            "Campaign snapshot alin ve reject/mismatch trendinin sifira dondugunu dogrulayin.",
        ],
        "actions": [
            {"type": "api", "label": "Reconciliation status", "url": "/api/reconciliation/status"},
            {"type": "api", "label": "Campaign summary", "url": "/api/campaign/summary"},
            {"type": "navigate", "label": "Open Ops panel", "hash": "#/ops"},
        ],
    }


def _build_runbook_snapshot() -> dict[str, Any]:
    paper_status = _paper_runner.status_dict() if _paper_runner is not None else {}
    entry_diag = _ops_entry_diag_snapshot()
    metrics: dict[str, Any] = {}
    if _analytics_engine is not None:
        metrics = _analytics_engine.get_metrics(epoch=_active_dashboard_epoch)
    scenarios = [
        _runbook_scenario_no_entries(paper_status, entry_diag),
        _runbook_scenario_churn(paper_status, metrics),
        _runbook_scenario_foreign_position(paper_status),
        _runbook_scenario_recon_blocked(paper_status),
    ]
    active_count = sum(1 for s in scenarios if bool(s.get("active")))
    return {
        "meta": {
            "utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "active_count": active_count,
            "hint": "Tek tik teshis aksiyonlariyla engelleyici kosullari hizlica inceleyin.",
        },
        "scenarios": scenarios,
    }


@app.get("/api/ops/status")
async def ops_status():
    status = _ops_controller.status()
    status["system_mode"] = _system_mode.value
    status["v1_policy"] = _v1_operations_policy()
    return status


@app.get("/api/ops/panel")
async def ops_panel_status() -> dict[str, Any]:
    """Operational control-room data for incidents, errors, reconnects, recon, and risk vetoes."""
    return _build_ops_panel_snapshot()


@app.get("/api/slo/status")
async def slo_status() -> dict[str, Any]:
    """SLA/SLO snapshot: uptime, latency, fill quality, rejection rate."""
    return _build_slo_status()


@app.get("/api/release/status")
async def release_status() -> dict[str, Any]:
    """Release/deploy snapshot: commit, image, deploy time, rollback hints."""
    return await _build_release_status()


@app.get("/api/runbook/snapshot")
async def runbook_snapshot() -> dict[str, Any]:
    """Operational runbook scenarios with one-click diagnosis actions."""
    return _build_runbook_snapshot()


@app.post("/api/ops/emergency_stop")
async def emergency_stop(reason: str = "Manual trigger"):
    event = _ops_controller.emergency_stop("dashboard_user", reason)
    return {"action": event.action, "reason": event.reason}


@app.post("/api/ops/pause")
async def pause_trading(reason: str = "Manual pause"):
    _ops_controller.pause_trading(reason)
    return {"mode": _ops_controller.mode.value}


@app.post("/api/ops/resume")
async def resume_trading(reason: str = "Operator resume via dashboard"):
    _ops_controller.resume_trading(reason)
    return {"mode": _ops_controller.mode.value}


@app.post("/api/ops/symbol/{symbol}/disable")
async def disable_symbol(symbol: str, reason: str = "Manual disable"):
    _ops_controller.disable_symbol(symbol.upper(), reason)
    return {"symbol": symbol.upper(), "enabled": False}


@app.post("/api/ops/symbol/{symbol}/enable")
async def enable_symbol(symbol: str, reason: str = "Operator enabled symbol"):
    _ops_controller.enable_symbol(symbol.upper(), reason)
    return {"symbol": symbol.upper(), "enabled": True}


# ── Readiness API ─────────────────────────────────────────────


def _env_truthy(key: str, default: bool = False) -> bool:
    v = (os.environ.get(key) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _readiness_int(key: str, default: int = 0) -> int:
    try:
        return int((os.environ.get(key) or str(default)).strip())
    except ValueError:
        return default


def _testnet_keys_configured() -> bool:
    k = (os.environ.get("CTE_BINANCE_TESTNET_API_KEY") or "").strip()
    s = (os.environ.get("CTE_BINANCE_TESTNET_API_SECRET") or "").strip()
    return len(k) >= 8 and len(s) >= 8


def _bybit_demo_keys_configured() -> bool:
    k = (os.environ.get("CTE_BYBIT_DEMO_API_KEY") or "").strip()
    s = (os.environ.get("CTE_BYBIT_DEMO_API_SECRET") or "").strip()
    return len(k) >= 8 and len(s) >= 8


@app.get("/api/readiness/paper_to_demo")
async def paper_to_demo_checklist():
    """v1 path: validation + testnet infra (keys, WS, safety) with declared metrics via env."""
    trades = _analytics_engine.total_trades if _analytics_engine else 0
    feed_ok = bool(_market_feed and _market_feed.health.connected)
    metrics = DashboardPaperToTestnetMetrics(
        testnet_keys=_testnet_keys_configured(),
        market_connected=feed_ok,
        v1_safe_not_live=_system_mode != SystemMode.LIVE,
        paper_trades=trades,
        paper_days=_readiness_int("CTE_READINESS_PAPER_DAYS", 0),
        crash_free_days=_readiness_int("CTE_READINESS_CRASH_FREE_DAYS", 0),
        all_tests_pass=_env_truthy("CTE_READINESS_TESTS_PASS", False),
        fsm_violations=_readiness_int("CTE_READINESS_FSM_VIOLATIONS", 0),
    )
    gates = build_dashboard_paper_to_testnet_gates(metrics)
    out = evaluate_readiness(gates)
    out["scope_note"] = (
        "Paper / validation → testnet (demo). Keys and WebSocket are live checks; "
        "paper days, crash-free streak, tests, and FSM counts are attested via env "
        "(see .env.example)."
    )
    return out


@app.get("/api/readiness/demo_to_live")
async def demo_to_live_checklist():
    """Phase 5 live gates — all SKIP in v1 (not scored; informational)."""
    gates = build_phase5_live_gates_skipped()
    out = evaluate_readiness(gates)
    out["scope_note"] = (
        "Phase 5 — live mainnet is out of v1 scope (enforce_safety). "
        "Gates remain as a future checklist; none apply until Phase 5."
    )
    return out


@app.get("/api/readiness/edge_proof")
async def edge_proof_checklist():
    gates = build_edge_proof_checklist(EdgeProofMetrics())
    return evaluate_readiness(gates)


# ── Validation API ────────────────────────────────────────────


@app.post("/api/validation/start")
async def start_validation(name: str = "campaign_1", mode: str = "paper", days: int = 7):
    campaign = ValidationCampaign(name=name, target_days=days, mode=mode)
    campaign.start()
    _validation_campaigns[name] = campaign
    return {"name": name, "status": campaign.status.value}


@app.get("/api/validation/campaigns")
async def list_campaigns():
    return [
        {
            "name": c.name,
            "status": c.status.value,
            "days": c.days_completed,
            "target": c.target_days,
        }
        for c in _validation_campaigns.values()
    ]


@app.get("/api/validation/{name}/report")
async def campaign_report(name: str):
    campaign = _validation_campaigns.get(name)
    if not campaign:
        return {"error": f"Campaign '{name}' not found"}
    return campaign.generate_report()


# ── Campaign Metrics API ──────────────────────────────────────


@app.post("/api/campaign/snapshot")
async def take_snapshot(period: str = "hourly"):
    """Take a metric snapshot from current analytics data."""
    if not _analytics_engine:
        return {"error": "Analytics not initialized"}
    trades = _analytics_engine._filter_trades()
    feed_health = _market_feed.health if _market_feed else None
    snapshot = compute_snapshot(
        trades,
        epoch=_epoch_manager.active_name,
        period=period,
        stale_event_count=feed_health.errors_total if feed_health else 0,
        reconnect_count=feed_health.reconnect_count if feed_health else 0,
        recon_mismatch_count=_recon_status.get("mismatches", 0),
        initial_capital=float(_analytics_engine._initial_capital),
    )
    _campaign_collector.add_snapshot(snapshot)
    return snapshot.to_dict()


@app.get("/api/campaign/summary")
async def campaign_summary():
    return _campaign_collector.summary()


@app.get("/api/campaign/snapshots")
async def campaign_snapshots(period: str | None = None):
    snaps = _campaign_collector.snapshots
    if period:
        snaps = [s for s in snaps if s.period == period]
    return [s.to_dict() for s in snaps[-100:]]


# ── Reconciliation API ────────────────────────────────────────


@app.get("/api/reconciliation/status")
async def reconciliation_status():
    return _recon_status


def _worst_ticker_book_age_ms() -> int | None:
    if not _market_feed:
        return None
    ages = [t.age_ms for t in _market_feed.tickers.values()]
    return max(ages) if ages else None


def _feed_packet_age_ms() -> int | None:
    if not _market_feed or not _market_feed.health.last_message_ms:
        return None
    return max(0, int(time.time() * 1000) - _market_feed.health.last_message_ms)


def _build_alerts_status() -> dict[str, Any]:
    """Operational alert rules with live evaluation from feed, analytics, and recon."""
    s = get_settings()
    h = _market_feed.health if _market_feed else None
    connected = bool(h and h.connected)
    book_age = _worst_ticker_book_age_ms()
    pkt_age = _feed_packet_age_ms()
    recon_m = int(_recon_status.get("mismatches") or 0)
    reconnects = int(h.reconnect_count) if h else 0

    metrics: dict[str, Any] = {}
    if _analytics_engine:
        metrics = _analytics_engine.get_metrics(epoch=_active_dashboard_epoch)
    dd = float(metrics.get("max_drawdown_pct") or 0.0)
    trade_count = int(metrics.get("trade_count") or 0)
    avg_slip = float(metrics.get("avg_slippage_bps") or 0.0)
    model_slip = float(s.execution.slippage_bps)

    latest = _campaign_collector.latest
    reject_rate = float(latest.reject_rate) if latest else None

    stale_warn = False
    stale_crit = False
    if not connected:
        stale_warn = True
        stale_crit = True
    else:
        if book_age is not None and book_age > 2000:
            stale_warn = True
        if pkt_age is not None and pkt_age > 2000:
            stale_warn = True
        if book_age is not None and book_age >= 5000:
            stale_crit = True
        if pkt_age is not None and pkt_age >= 5000:
            stale_crit = True

    rules: list[dict[str, Any]] = [
        {
            "id": "stale_warn",
            "title": "Stale data (warning)",
            "condition": "Book or WS packet age > 2s",
            "severity": "warning",
            "state": "firing" if stale_warn else "ok",
            "detail": _alert_stale_detail(connected, book_age, pkt_age),
        },
    ]
    rules.append(
        {
            "id": "stale_crit",
            "title": "Stale data (critical)",
            "condition": "WS offline or age ≥ 5s",
            "severity": "critical",
            "state": "firing" if stale_crit else "ok",
            "detail": _alert_stale_detail(connected, book_age, pkt_age),
        },
    )
    rules.append(
        {
            "id": "reconnect_loop",
            "title": "Reconnect loop",
            "condition": ">5 reconnects since process start",
            "severity": "warning",
            "state": "firing" if reconnects > 5 else "ok",
            "detail": f"reconnect_count={reconnects}",
        },
    )
    rules.append(
        {
            "id": "dd_warn",
            "title": "Drawdown warning",
            "condition": "max DD > 2%",
            "severity": "warning",
            "state": "firing" if dd > 0.02 else "ok",
            "detail": f"max_drawdown_pct={dd:.2%}",
        },
    )
    rules.append(
        {
            "id": "dd_halt",
            "title": "Drawdown halt",
            "condition": "max DD > 3%",
            "severity": "critical",
            "state": "firing" if dd > 0.03 else "ok",
            "detail": f"max_drawdown_pct={dd:.2%}",
        },
    )
    emerg = float(s.risk.emergency_stop_drawdown_pct)
    rules.append(
        {
            "id": "dd_emergency",
            "title": "Drawdown emergency",
            "condition": f"max DD ≥ {emerg:.0%} (risk config)",
            "severity": "critical",
            "state": "firing" if dd >= emerg else "ok",
            "detail": f"max_drawdown_pct={dd:.2%}",
        },
    )
    rules.append(
        {
            "id": "order_reject",
            "title": "Order reject spike",
            "condition": "reject_rate > 5% (last campaign snapshot)",
            "severity": "warning",
            "state": (
                "unknown" if reject_rate is None else ("firing" if reject_rate > 0.05 else "ok")
            ),
            "detail": (
                "no snapshot yet — POST /api/campaign/snapshot"
                if reject_rate is None
                else f"reject_rate={reject_rate:.2%}"
            ),
        },
    )
    rules.append(
        {
            "id": "reconciliation",
            "title": "Reconciliation",
            "condition": "any mismatch",
            "severity": "critical",
            "state": "firing" if recon_m > 0 else "ok",
            "detail": f"mismatches={recon_m}",
        },
    )
    slip_state = "unknown"
    slip_detail = "no trades in epoch"
    if trade_count > 0:
        drift = abs(avg_slip - model_slip)
        slip_state = "firing" if drift > 3.0 else "ok"
        slip_detail = (
            f"avg_slippage_bps={avg_slip:.1f} vs model={model_slip:.0f} (Δ{drift:.1f} bps)"
        )
    rules.append(
        {
            "id": "slippage_drift",
            "title": "Slippage drift",
            "condition": ">3 bps vs execution.slippage_bps model",
            "severity": "warning",
            "state": slip_state,
            "detail": slip_detail,
        },
    )

    firing = sum(1 for r in rules if r["state"] == "firing")
    return {
        "meta": {
            "utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "legend": "OK = healthy, FIRING = threshold breached, N/A = insufficient data.",
            "firing_count": firing,
        },
        "rules": rules,
    }


def _alert_stale_detail(
    connected: bool,
    book_age: int | None,
    pkt_age: int | None,
) -> str:
    parts = [f"ws={'up' if connected else 'down'}"]
    if book_age is not None:
        parts.append(f"book_age_ms={book_age}")
    if pkt_age is not None:
        parts.append(f"last_pkt_age_ms={pkt_age}")
    return " · ".join(parts)


@app.get("/api/alerts/status")
async def alerts_status():
    """Alert rulebook + live signal evaluation for the dashboard Alerts page."""
    return _build_alerts_status()


@app.get("/api/readiness/campaign")
async def campaign_readiness():
    """Readiness gates wired to REAL campaign metrics."""
    from cte.analytics.metrics import compute_phase_metrics_slice, trades_for_promotion_evidence
    from cte.ops.readiness import build_campaign_validation_checklist

    collector = _campaign_collector
    latest = collector.latest
    trades = _analytics_engine._filter_trades() if _analytics_engine else []
    seed_count = sum(1 for t in trades if t.source == "seed")
    ic = float(_analytics_engine._initial_capital) if _analytics_engine else 10000.0
    promo = trades_for_promotion_evidence(trades)
    pm = compute_phase_metrics_slice(promo, ic)
    promo_dd = float(pm["max_drawdown_pct"])
    promo_exp = float(pm["expectancy"])
    promo_n = int(pm["trade_count"])

    metrics = CampaignValidationMetrics(
        campaign_days=collector.campaign_days,
        total_trades=len(trades),
        all_recon_clean=collector.all_recon_clean,
        max_dd_observed=collector.max_dd_observed,
        avg_latency_p95_ms=collector.avg_latency_p95,
        stale_ratio=0.0,
        reject_ratio=latest.reject_rate if latest else 0.0,
        error_count=latest.error_count if latest else 0,
        expectancy=latest.expectancy if latest else 0.0,
        seed_trade_count=seed_count,
        promotion_trade_count=promo_n,
        promotion_expectancy=promo_exp,
        promotion_max_dd_observed=promo_dd,
    )
    return evaluate_readiness(build_campaign_validation_checklist(metrics))


# ── Reports ───────────────────────────────────────────────────


@app.get("/api/report/go_no_go")
async def go_no_go_report():
    from cte.ops.go_no_go import GoNoGoMetrics, build_go_no_go_report

    collector = _campaign_collector
    metrics = GoNoGoMetrics(
        campaign_days=collector.campaign_days,
        total_trades=collector.total_trades
        or (_analytics_engine.total_trades if _analytics_engine else 0),
    )
    return build_go_no_go_report(metrics)


# ── Config API ────────────────────────────────────────────────


def _redacted_redis_url(url: str) -> str:
    """Hide password in redis:// URLs for read-only UI."""
    try:
        p = urlparse(url)
        if not p.password:
            return url
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        user = f"{p.username}:" if p.username else ""
        netloc = f"{user}***@{host}{port}"
        return urlunparse((p.scheme or "redis", netloc, p.path or "", "", "", ""))
    except Exception:
        return "redis://*** (unparseable)"


def _build_config_snapshot() -> dict[str, object]:
    """Structured, non-secret settings for the dashboard Config page."""
    s = get_settings()
    weights = {
        "momentum": s.signals.w_momentum,
        "orderflow": s.signals.w_orderflow,
        "liquidation": s.signals.w_liquidation,
        "microstructure": s.signals.w_microstructure,
        "cross_venue": s.signals.w_cross_venue,
    }
    tiers = {
        "A": s.signals.tier_a_threshold,
        "B": s.signals.tier_b_threshold,
        "C": s.signals.tier_c_threshold,
    }
    sections: list[dict[str, object]] = [
        {
            "id": "runtime",
            "title": "Runtime & modes",
            "rows": [
                {
                    "key": "system_mode",
                    "label": "System mode (dashboard)",
                    "value": _system_mode.value,
                },
                {"key": "engine_mode", "label": "Engine mode", "value": s.engine.mode.value},
                {
                    "key": "execution_mode",
                    "label": "Execution mode",
                    "value": s.execution.mode.value,
                },
                {
                    "key": "dashboard_execution_venue",
                    "label": "Dashboard execution venue (REST)",
                    "value": os.environ.get("CTE_DASHBOARD_EXECUTION_VENUE") or "binance_testnet",
                },
                {
                    "key": "venue_proof_symbol",
                    "label": "Venue proof symbol (unset = all merged symbols may receive venue REST)",
                    "value": os.environ.get("CTE_DASHBOARD_VENUE_PROOF_SYMBOL")
                    or "(none - multi-symbol venue)",
                },
                {
                    "key": "testnet_keys",
                    "label": "Binance USD-M testnet API credentials",
                    "value": "configured" if _testnet_keys_configured() else "missing",
                },
                {
                    "key": "bybit_demo_keys",
                    "label": "Bybit demo API credentials",
                    "value": "configured" if _bybit_demo_keys_configured() else "missing",
                },
                {
                    "key": "dashboard_paper_loop",
                    "label": "In-process paper loop (tick→signal→risk→journal)",
                    "value": "on" if paper_loop_enabled() else "off (CTE_DASHBOARD_PAPER_LOOP)",
                },
            ],
        },
        {
            "id": "universe",
            "title": "Universe & direction",
            "rows": [
                {
                    "key": "symbols",
                    "label": "Symbols (engine; dashboard expands legacy BTC+ETH to full universe)",
                    "value": expand_legacy_engine_symbols(list(s.engine.symbols)),
                },
                {
                    "key": "market_feed_symbols",
                    "label": "Market feed symbols (merged with default 10-pair universe)",
                    "value": merge_market_feed_symbols(
                        expand_legacy_engine_symbols(list(s.engine.symbols)),
                    ),
                },
                {"key": "direction", "label": "Direction", "value": s.engine.direction.value},
                {
                    "key": "max_leverage",
                    "label": "Max leverage (cap)",
                    "value": s.engine.max_leverage,
                },
            ],
        },
        {
            "id": "binance",
            "title": "Binance (resolved URLs)",
            "rows": [
                {
                    "key": "ws_combined",
                    "label": "Combined WebSocket",
                    "value": s.binance.ws_combined_url,
                },
                {"key": "rest_base", "label": "REST base", "value": s.binance.rest_base_url},
                {
                    "key": "stream_count",
                    "label": "Default stream templates",
                    "value": len(s.binance.streams),
                },
            ],
        },
        {
            "id": "bybit",
            "title": "Bybit (demo / testnet REST)",
            "rows": [
                {
                    "key": "bybit_rest_base",
                    "label": "REST base (demo orders)",
                    "value": os.environ.get("CTE_BYBIT_REST_BASE_URL") or s.bybit.rest_base_url,
                },
                {
                    "key": "bybit_ws",
                    "label": "Public WS (connectors)",
                    "value": s.bybit.ws_base_url,
                },
            ],
        },
        {
            "id": "execution",
            "title": "Execution (paper / simulated)",
            "rows": [
                {
                    "key": "slippage_bps",
                    "label": "Slippage model (bps)",
                    "value": s.execution.slippage_bps,
                },
                {"key": "fee_bps", "label": "Taker fee (bps)", "value": s.execution.fee_bps},
                {"key": "fill_model", "label": "Fill model", "value": s.execution.fill_model},
            ],
        },
        {
            "id": "exits",
            "title": "Exit defaults",
            "rows": [
                {"key": "stop_loss_pct", "label": "Stop loss", "value": s.exits.stop_loss_pct},
                {
                    "key": "take_profit_pct",
                    "label": "Take profit",
                    "value": s.exits.take_profit_pct,
                },
                {
                    "key": "trailing_stop_pct",
                    "label": "Trailing stop",
                    "value": s.exits.trailing_stop_pct,
                },
            ],
        },
        {
            "id": "risk",
            "title": "Risk caps",
            "rows": [
                {
                    "key": "max_position_pct",
                    "label": "Max position %",
                    "value": s.risk.max_position_pct,
                },
                {
                    "key": "max_exposure_pct",
                    "label": "Max total exposure %",
                    "value": s.risk.max_total_exposure_pct,
                },
                {
                    "key": "max_daily_drawdown_pct",
                    "label": "Max daily drawdown %",
                    "value": s.risk.max_daily_drawdown_pct,
                },
            ],
        },
        {
            "id": "signals",
            "title": "Signal engine",
            "rows": [
                {"key": "signal_weights", "label": "Weights (must sum to 1)", "value": weights},
                {"key": "tier_thresholds", "label": "Tier thresholds", "value": tiers},
            ],
        },
        {
            "id": "infra",
            "title": "Infrastructure (redacted)",
            "rows": [
                {
                    "key": "redis_url",
                    "label": "Redis URL",
                    "value": _redacted_redis_url(s.redis.url),
                },
                {"key": "redis_group", "label": "Consumer group", "value": s.redis.consumer_group},
                {
                    "key": "db_host",
                    "label": "Postgres",
                    "value": f"{s.database.user}@{s.database.host}:{s.database.port}/{s.database.name}",
                },
            ],
        },
    ]
    return {
        "meta": {
            "read_only": True,
            "hint": "Values come from environment + defaults.toml. Secrets are never returned. Restart process after changing .env.",
            "utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
        "sections": sections,
    }


@app.get("/api/config")
async def get_config():
    """Read-only settings snapshot grouped for the Config UI."""
    try:
        return _build_config_snapshot()
    except Exception as exc:
        await log.awarning("config_snapshot_failed", error=str(exc))
        return {
            "meta": {
                "read_only": True,
                "hint": "Settings could not be loaded.",
                "utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            },
            "error": str(exc),
            "sections": [],
        }


class SettingsDraftRequest(BaseModel):
    name: str = "draft"
    changes: dict[str, str]
    note: str = ""
    created_by: str = "dashboard_user"
    role: str = "operator"


class SettingsActionRequest(BaseModel):
    actor: str = "dashboard_user"
    role: str = "admin"


class SettingsScheduleRequest(SettingsActionRequest):
    run_at_utc: str


def _settings_center_backend() -> str:
    if _settings_center is None:
        return "none"
    if _settings_center.__class__.__name__.lower().startswith("db"):
        return "db"
    return "memory"


def _settings_center_required() -> Any:
    if _settings_center is None:
        raise RuntimeError("settings center unavailable")
    return _settings_center


def _settings_revision_diff_rows(rev: dict[str, Any]) -> list[dict[str, Any]]:
    changes = rev.get("changes") or {}
    out: list[dict[str, Any]] = []
    for key in sorted(changes.keys()):
        before = os.environ.get(key)
        after = str(changes.get(key))
        out.append(
            {
                "key": key,
                "before": before,
                "after": after,
                "changed": str(before) != after,
            }
        )
    return out


@app.get("/api/config/center")
async def config_center_status() -> dict[str, Any]:
    """Config center overview with active revision and latest entries."""
    sc = _settings_center_required()
    active = await sc.active_revision()
    revisions = await sc.list_revisions(limit=30)
    return {
        "backend": _settings_center_backend(),
        "workflow": ["draft", "approved", "scheduled", "applied"],
        "active_revision": active,
        "revisions": revisions,
        "apply_note": "Apply updates process environment immediately; restart services to propagate to long-lived components.",
    }


@app.get("/api/config/center/revisions")
async def config_center_revisions(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    sc = _settings_center_required()
    rows = await sc.list_revisions(status=status, limit=limit)
    return {"backend": _settings_center_backend(), "items": rows}


@app.post("/api/config/center/drafts")
async def config_center_create_draft(req: SettingsDraftRequest) -> dict[str, Any]:
    sc = _settings_center_required()
    if not _role_allowed(req.role, {"operator", "approver", "admin"}):
        return {"ok": False, "error": "role is not allowed to create draft"}
    try:
        row = await sc.create_draft(
            req.changes,
            name=req.name,
            note=req.note,
            created_by=req.created_by,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "revision": row}


@app.post("/api/config/center/revisions/{revision_id}/approve")
async def config_center_approve(revision_id: str, req: SettingsActionRequest) -> dict[str, Any]:
    sc = _settings_center_required()
    if not _role_allowed(req.role, {"approver", "admin"}):
        return {"ok": False, "error": "role is not allowed to approve"}
    try:
        row = await sc.approve(revision_id, approved_by=req.actor)
    except KeyError:
        return {"ok": False, "error": "revision not found"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "revision": row}


@app.post("/api/config/center/revisions/{revision_id}/apply")
async def config_center_apply(revision_id: str, req: SettingsActionRequest) -> dict[str, Any]:
    sc = _settings_center_required()
    if not _role_allowed(req.role, {"admin"}):
        return {"ok": False, "error": "role is not allowed to apply"}
    try:
        row = await sc.apply(revision_id, applied_by=req.actor)
        existing = _settings_apply_tasks.pop(revision_id, None)
        if existing is not None and not existing.done():
            existing.cancel()
    except KeyError:
        return {"ok": False, "error": "revision not found"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "revision": row,
        "note": "Environment updated for this process. Restart services to guarantee full propagation.",
    }


@app.get("/api/config/center/revisions/{revision_id}/diff")
async def config_center_diff(revision_id: str) -> dict[str, Any]:
    sc = _settings_center_required()
    row = await sc.get_revision(revision_id)
    if row is None:
        return {"ok": False, "error": "revision not found"}
    return {
        "ok": True,
        "revision_id": revision_id,
        "status": row.get("status"),
        "rows": _settings_revision_diff_rows(row),
    }


@app.post("/api/config/center/revisions/{revision_id}/schedule")
async def config_center_schedule(revision_id: str, req: SettingsScheduleRequest) -> dict[str, Any]:
    sc = _settings_center_required()
    if not _role_allowed(req.role, {"admin"}):
        return {"ok": False, "error": "role is not allowed to schedule apply"}
    try:
        run_at = parse_utc(req.run_at_utc)
    except Exception:
        return {"ok": False, "error": "invalid run_at_utc (ISO8601 expected)"}
    if run_at <= _now_utc():
        return {"ok": False, "error": "run_at_utc must be in the future"}
    try:
        row = await sc.schedule_apply(revision_id, scheduled_for=run_at, scheduled_by=req.actor)
    except KeyError:
        return {"ok": False, "error": "revision not found"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    _spawn_revision_schedule(revision_id, run_at, req.actor)
    return {"ok": True, "revision": row}


@app.post("/api/config/center/revisions/{revision_id}/rollback")
async def config_center_rollback(revision_id: str, req: SettingsActionRequest) -> dict[str, Any]:
    sc = _settings_center_required()
    if not _role_allowed(req.role, {"admin"}):
        return {"ok": False, "error": "role is not allowed to rollback"}
    try:
        row = await sc.rollback_to(revision_id, actor=req.actor)
        existing = _settings_apply_tasks.pop(revision_id, None)
        if existing is not None and not existing.done():
            existing.cancel()
    except KeyError:
        return {"ok": False, "error": "revision not found"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "revision": row, "note": "Rollback applied to runtime environment."}
