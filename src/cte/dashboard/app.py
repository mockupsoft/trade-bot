"""CTE Dashboard — Binance USDⓈ-M **futures testnet** only.

- WebSocket: testnet combined stream (see ``BinanceSettings.ws_combined_url`` / ``CTE_BINANCE_WS_COMBINED_URL``).
- REST safety gate: ``CTE_BINANCE_TESTNET_API_KEY`` and ``CTE_BINANCE_TESTNET_API_SECRET`` required.
- No seed / synthetic trade injection; analytics fill from real recorded trades only.
- ``CTE_ENGINE_MODE=live`` is still blocked by ``enforce_safety``; any other value runs the testnet profile.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import structlog
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from cte.analytics.engine import AnalyticsEngine
from cte.analytics.epochs import EpochManager, EpochMode
from cte.api.analytics_routes import router as analytics_router
from cte.api.analytics_routes import set_engine
from cte.api.health import router as health_router
from cte.core.logging import setup_logging
from cte.core.settings import get_settings
from cte.market.feed import MarketDataFeed, TickerState
from cte.ops.campaign import CampaignCollector, compute_snapshot
from cte.ops.kill_switch import OperationsController
from cte.ops.readiness import (
    build_dashboard_paper_to_testnet_gates,
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
DASHBOARD_MARKET_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")

_system_mode: SystemMode = SystemMode.DEMO
_epoch_manager = EpochManager()
_analytics_engine: AnalyticsEngine | None = None
_ops_controller = OperationsController()
_market_feed: MarketDataFeed | None = None
_feed_task: asyncio.Task | None = None
_validation_campaigns: dict[str, ValidationCampaign] = {}
_campaign_collector = CampaignCollector()
_recon_status: dict = {"status": "not_run", "mismatches": 0, "last_run": None, "details": []}


def _resolve_mode() -> SystemMode:
    """Return LIVE only when explicitly requested (then safety blocks startup)."""
    raw = (os.environ.get("CTE_ENGINE_MODE") or "demo").lower()
    if raw == "live":
        return SystemMode.LIVE
    return SystemMode.DEMO


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _analytics_engine, _market_feed, _feed_task, _system_mode
    setup_logging(level="INFO", service_name="dashboard")

    _system_mode = _resolve_mode()
    if _system_mode == SystemMode.DEMO:
        os.environ["CTE_ENGINE_MODE"] = "demo"
    banner_key = "demo" if _system_mode == SystemMode.DEMO else _system_mode.value
    print_startup_banner(banner_key)

    if _system_mode == SystemMode.DEMO:
        enforce_safety(
            "demo",
            binance_rest_url=os.environ.get("CTE_BINANCE_TESTNET_REST_URL", "https://testnet.binancefuture.com"),
            binance_api_key=os.environ.get("CTE_BINANCE_TESTNET_API_KEY", ""),
            binance_api_secret=os.environ.get("CTE_BINANCE_TESTNET_API_SECRET", ""),
        )

    if _system_mode == SystemMode.LIVE:
        enforce_safety("live")

    _epoch_manager.create_epoch(
        ACTIVE_TESTNET_EPOCH,
        EpochMode.DEMO,
        "Binance USD-M futures testnet",
    )
    _epoch_manager.activate(ACTIVE_TESTNET_EPOCH)

    _analytics_engine = AnalyticsEngine(_epoch_manager, initial_capital=Decimal("10000"))
    set_engine(_analytics_engine)

    settings = get_settings()
    _market_feed = MarketDataFeed(ws_url=settings.binance.ws_combined_url)
    _feed_task = asyncio.create_task(_market_feed.start())
    await log.ainfo(
        "market_feed_started",
        mode="testnet",
        ws_url=settings.binance.ws_combined_url,
    )

    await log.ainfo("dashboard_ready", mode="testnet")

    yield

    # Shutdown
    if _market_feed:
        _market_feed.stop()
    if _feed_task:
        _feed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _feed_task
    await log.ainfo("dashboard_stopped")


app = FastAPI(title="CTE Dashboard", version="0.1.0", lifespan=lifespan)
app.include_router(health_router, prefix="/api/dashboard")
app.include_router(analytics_router)


# ── Pages ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=(TEMPLATE_DIR / "index.html").read_text())


@app.get("/api/dashboard/meta")
async def dashboard_meta() -> dict[str, str]:
    """Process fingerprint for debugging wrong-port / stale servers on :8080."""
    return {
        "service": "cte.dashboard",
        "market_profile": "binance_usdm_testnet",
    }


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
    base_rows = {sym: _empty_ticker_payload() for sym in DASHBOARD_MARKET_SYMBOLS}
    if not _market_feed:
        return {
            "source": "none",
            "mode": "testnet",
            "tickers": base_rows,
            "stream_url": stream_url,
            "feed_ready": False,
        }
    tickers: dict[str, dict[str, object]] = {}
    for sym in DASHBOARD_MARKET_SYMBOLS:
        t = _market_feed.tickers.get(sym)
        tickers[sym] = _serialize_ticker(t) if t else _empty_ticker_payload()
    for sym, t in _market_feed.tickers.items():
        if sym not in tickers:
            tickers[sym] = _serialize_ticker(t)
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
    return {
        "direction": "long_only",
        "symbols": ["BTCUSDT", "ETHUSDT"],
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


@app.get("/api/ops/status")
async def ops_status():
    status = _ops_controller.status()
    status["system_mode"] = _system_mode.value
    status["v1_policy"] = _v1_operations_policy()
    return status


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


@app.get("/api/readiness/paper_to_demo")
async def paper_to_demo_checklist():
    """v1 path: validation + testnet infra (keys, WS, safety) with declared metrics via env."""
    trades = _analytics_engine.total_trades if _analytics_engine else 0
    feed_ok = bool(_market_feed and _market_feed.health.connected)
    gates = build_dashboard_paper_to_testnet_gates(
        testnet_keys=_testnet_keys_configured(),
        market_connected=feed_ok,
        v1_safe_not_live=_system_mode != SystemMode.LIVE,
        paper_trades=trades,
        paper_days=_readiness_int("CTE_READINESS_PAPER_DAYS", 0),
        crash_free_days=_readiness_int("CTE_READINESS_CRASH_FREE_DAYS", 0),
        all_tests_pass=_env_truthy("CTE_READINESS_TESTS_PASS", False),
        fsm_violations=_readiness_int("CTE_READINESS_FSM_VIOLATIONS", 0),
    )
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
    from cte.ops.readiness import build_edge_proof_checklist
    gates = build_edge_proof_checklist()
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
        {"name": c.name, "status": c.status.value, "days": c.days_completed, "target": c.target_days}
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
        trades, epoch=_epoch_manager.active_name, period=period,
        stale_event_count=feed_health.errors_total if feed_health else 0,
        reconnect_count=feed_health.reconnect_count if feed_health else 0,
        recon_mismatch_count=_recon_status.get("mismatches", 0),
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
        metrics = _analytics_engine.get_metrics(epoch=ACTIVE_TESTNET_EPOCH)
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
                "unknown"
                if reject_rate is None
                else ("firing" if reject_rate > 0.05 else "ok")
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
        slip_detail = f"avg_slippage_bps={avg_slip:.1f} vs model={model_slip:.0f} (Δ{drift:.1f} bps)"
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
    from cte.ops.readiness import build_campaign_validation_checklist
    collector = _campaign_collector
    latest = collector.latest
    trades = _analytics_engine._filter_trades() if _analytics_engine else []
    seed_count = sum(1 for t in trades if t.source == "seed")
    return evaluate_readiness(build_campaign_validation_checklist(
        campaign_days=collector.campaign_days,
        total_trades=collector.total_trades,
        all_recon_clean=collector.all_recon_clean,
        max_dd_observed=collector.max_dd_observed,
        avg_latency_p95_ms=collector.avg_latency_p95,
        stale_ratio=0.0,
        reject_ratio=latest.reject_rate if latest else 0.0,
        error_count=latest.error_count if latest else 0,
        expectancy=latest.expectancy if latest else 0.0,
        seed_trade_count=seed_count,
    ))


# ── Reports ───────────────────────────────────────────────────

@app.get("/api/report/go_no_go")
async def go_no_go_report():
    from cte.ops.go_no_go import build_go_no_go_report
    collector = _campaign_collector
    return build_go_no_go_report(
        campaign_days=collector.campaign_days,
        total_trades=collector.total_trades or (
            _analytics_engine.total_trades if _analytics_engine else 0
        ),
    )


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
                {"key": "system_mode", "label": "System mode (dashboard)", "value": _system_mode.value},
                {"key": "engine_mode", "label": "Engine mode", "value": s.engine.mode.value},
                {"key": "execution_mode", "label": "Execution mode", "value": s.execution.mode.value},
                {"key": "testnet_keys", "label": "Testnet API credentials", "value": "configured" if _testnet_keys_configured() else "missing"},
            ],
        },
        {
            "id": "universe",
            "title": "Universe & direction",
            "rows": [
                {"key": "symbols", "label": "Symbols", "value": list(s.engine.symbols)},
                {"key": "direction", "label": "Direction", "value": s.engine.direction.value},
                {"key": "max_leverage", "label": "Max leverage (cap)", "value": s.engine.max_leverage},
            ],
        },
        {
            "id": "binance",
            "title": "Binance (resolved URLs)",
            "rows": [
                {"key": "ws_combined", "label": "Combined WebSocket", "value": s.binance.ws_combined_url},
                {"key": "rest_base", "label": "REST base", "value": s.binance.rest_base_url},
                {"key": "stream_count", "label": "Default stream templates", "value": len(s.binance.streams)},
            ],
        },
        {
            "id": "execution",
            "title": "Execution (paper / simulated)",
            "rows": [
                {"key": "slippage_bps", "label": "Slippage model (bps)", "value": s.execution.slippage_bps},
                {"key": "fee_bps", "label": "Taker fee (bps)", "value": s.execution.fee_bps},
                {"key": "fill_model", "label": "Fill model", "value": s.execution.fill_model},
            ],
        },
        {
            "id": "exits",
            "title": "Exit defaults",
            "rows": [
                {"key": "stop_loss_pct", "label": "Stop loss", "value": s.exits.stop_loss_pct},
                {"key": "take_profit_pct", "label": "Take profit", "value": s.exits.take_profit_pct},
                {"key": "trailing_stop_pct", "label": "Trailing stop", "value": s.exits.trailing_stop_pct},
            ],
        },
        {
            "id": "risk",
            "title": "Risk caps",
            "rows": [
                {"key": "max_position_pct", "label": "Max position %", "value": s.risk.max_position_pct},
                {"key": "max_exposure_pct", "label": "Max total exposure %", "value": s.risk.max_total_exposure_pct},
                {"key": "max_daily_drawdown_pct", "label": "Max daily drawdown %", "value": s.risk.max_daily_drawdown_pct},
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
                {"key": "redis_url", "label": "Redis URL", "value": _redacted_redis_url(s.redis.url)},
                {"key": "redis_group", "label": "Consumer group", "value": s.redis.consumer_group},
                {"key": "db_host", "label": "Postgres", "value": f"{s.database.user}@{s.database.host}:{s.database.port}/{s.database.name}"},
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
