"""CTE Dashboard — Mode-aware operations platform.

Modes (``CTE_ENGINE_MODE``):
  seed  = UI preview with fake data (no WebSocket)
  paper = Binance USDⓈ-M **public** WebSocket + empty analytics until trades exist
  demo  = same feed + Binance **testnet** keys required (safety gate)
  live  = disabled in v1

Run locally::

    CTE_ENGINE_MODE=paper cte-dashboard

Docker: set ``CTE_DASHBOARD_MODE`` for the ``analytics`` service (defaults to ``paper``).
See ``docs/DASHBOARD_MODES.md`` for seed / paper / demo setup and verification curls.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from cte.analytics.engine import AnalyticsEngine
from cte.analytics.epochs import EpochManager, EpochMode
from cte.api.analytics_routes import router as analytics_router
from cte.api.analytics_routes import set_engine
from cte.api.health import router as health_router
from cte.core.logging import setup_logging
from cte.market.feed import MarketDataFeed
from cte.ops.campaign import CampaignCollector, compute_snapshot
from cte.ops.kill_switch import OperationsController
from cte.ops.readiness import (
    build_demo_to_live_checklist,
    build_paper_to_demo_checklist,
    evaluate_readiness,
)
from cte.ops.safety import SystemMode, enforce_safety, print_startup_banner
from cte.ops.validation import ValidationCampaign

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

log = structlog.get_logger("dashboard")

TEMPLATE_DIR = Path(__file__).parent / "templates"

# ── Global State ──────────────────────────────────────────────
_system_mode: SystemMode = SystemMode.SEED
_epoch_manager = EpochManager()
_analytics_engine: AnalyticsEngine | None = None
_ops_controller = OperationsController()
_market_feed: MarketDataFeed | None = None
_feed_task: asyncio.Task | None = None
_validation_campaigns: dict[str, ValidationCampaign] = {}
_campaign_collector = CampaignCollector()
_recon_status: dict = {"status": "not_run", "mismatches": 0, "last_run": None, "details": []}


def _resolve_mode() -> SystemMode:
    raw = os.environ.get("CTE_ENGINE_MODE", "seed").lower()
    try:
        return SystemMode(raw)
    except ValueError:
        return SystemMode.SEED


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _analytics_engine, _market_feed, _feed_task, _system_mode
    setup_logging(level="INFO", service_name="dashboard")

    _system_mode = _resolve_mode()
    print_startup_banner(_system_mode.value)

    # Safety checks for demo mode
    if _system_mode == SystemMode.DEMO:
        enforce_safety(
            "demo",
            binance_rest_url=os.environ.get("CTE_BINANCE_TESTNET_REST_URL", "https://testnet.binancefuture.com"),
            binance_api_key=os.environ.get("CTE_BINANCE_TESTNET_API_KEY", ""),
            binance_api_secret=os.environ.get("CTE_BINANCE_TESTNET_API_SECRET", ""),
        )

    if _system_mode == SystemMode.LIVE:
        enforce_safety("live")

    # Epochs
    for name, mode, desc in [
        ("crypto_v1_paper", EpochMode.PAPER, "Paper trading phase"),
        ("crypto_v1_demo", EpochMode.DEMO, "Testnet demo phase"),
        ("crypto_v1_live", EpochMode.LIVE, "Minimal live trading"),
        ("crypto_v1_shadow_short", EpochMode.SHADOW, "Shadow short experiment"),
    ]:
        _epoch_manager.create_epoch(name, mode, desc)

    epoch_name = {
        SystemMode.SEED: "crypto_v1_paper",
        SystemMode.PAPER: "crypto_v1_paper",
        SystemMode.DEMO: "crypto_v1_demo",
        SystemMode.LIVE: "crypto_v1_live",
    }[_system_mode]
    _epoch_manager.activate(epoch_name)

    _analytics_engine = AnalyticsEngine(_epoch_manager, initial_capital=Decimal("10000"))
    set_engine(_analytics_engine)

    # Seed mode: inject fake data for UI preview
    if _system_mode == SystemMode.SEED:
        from cte.dashboard.seed import inject_seed_data
        count = inject_seed_data(_analytics_engine)
        await log.ainfo("seed_data_injected", trades=count, mode="seed")

    # Paper & Demo: start live market data feed
    if _system_mode in (SystemMode.PAPER, SystemMode.DEMO):
        _market_feed = MarketDataFeed()
        _feed_task = asyncio.create_task(_market_feed.start())
        await log.ainfo("market_feed_started", mode=_system_mode.value)

    await log.ainfo("dashboard_ready", mode=_system_mode.value)

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


# ── Market Data API ───────────────────────────────────────────

@app.get("/api/market/tickers")
async def market_tickers():
    """Live ticker data for all symbols."""
    if not _market_feed:
        return {"source": "none", "mode": _system_mode.value, "tickers": {}}
    return {
        "source": "binance_ws",
        "mode": _system_mode.value,
        "tickers": {
            sym: {
                "last_price": str(t.last_price),
                "best_bid": str(t.best_bid),
                "best_ask": str(t.best_ask),
                "mark_price": str(t.mark_price),
                "spread_bps": round(t.spread_bps, 2),
                "age_ms": t.age_ms,
                "is_stale": t.is_stale,
                "trade_count_1m": t.trade_count_1m,
            }
            for sym, t in _market_feed.tickers.items()
        },
    }


@app.get("/api/market/health")
async def market_health():
    """Market data feed health status."""
    if not _market_feed:
        return {"connected": False, "mode": _system_mode.value, "detail": "No feed in this mode"}
    h = _market_feed.health
    return {
        "connected": h.connected,
        "mode": _system_mode.value,
        "messages_total": h.messages_total,
        "reconnect_count": h.reconnect_count,
        "errors_total": h.errors_total,
        "latency_ms": round(h.latency_ms, 1),
        "uptime_seconds": round(h.uptime_seconds, 1),
        "symbols": h.symbols,
    }


# ── Ops API ───────────────────────────────────────────────────

@app.get("/api/ops/status")
async def ops_status():
    status = _ops_controller.status()
    status["system_mode"] = _system_mode.value
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
async def resume_trading():
    _ops_controller.resume_trading()
    return {"mode": _ops_controller.mode.value}


@app.post("/api/ops/symbol/{symbol}/disable")
async def disable_symbol(symbol: str, reason: str = "Manual disable"):
    _ops_controller.disable_symbol(symbol.upper(), reason)
    return {"symbol": symbol.upper(), "enabled": False}


@app.post("/api/ops/symbol/{symbol}/enable")
async def enable_symbol(symbol: str):
    _ops_controller.enable_symbol(symbol.upper())
    return {"symbol": symbol.upper(), "enabled": True}


# ── Readiness API ─────────────────────────────────────────────

@app.get("/api/readiness/paper_to_demo")
async def paper_to_demo_checklist():
    gates = build_paper_to_demo_checklist(
        paper_days=_analytics_engine.total_trades // 10 if _analytics_engine else 0,
        paper_trades=_analytics_engine.total_trades if _analytics_engine else 0,
    )
    return evaluate_readiness(gates)


@app.get("/api/readiness/demo_to_live")
async def demo_to_live_checklist():
    gates = build_demo_to_live_checklist()
    return evaluate_readiness(gates)


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

@app.get("/api/config")
async def get_config():
    from cte.core.settings import get_settings
    try:
        s = get_settings()
        return {
            "system_mode": _system_mode.value,
            "engine_mode": s.engine.mode.value,
            "symbols": s.engine.symbols,
            "max_leverage": s.engine.max_leverage,
            "execution_mode": s.execution.mode.value,
            "slippage_bps": s.execution.slippage_bps,
            "fill_model": s.execution.fill_model,
            "stop_loss_pct": s.exits.stop_loss_pct,
            "take_profit_pct": s.exits.take_profit_pct,
            "trailing_stop_pct": s.exits.trailing_stop_pct,
            "risk_max_position_pct": s.risk.max_position_pct,
            "risk_max_exposure_pct": s.risk.max_total_exposure_pct,
            "risk_max_drawdown_pct": s.risk.max_daily_drawdown_pct,
            "signal_weights": {
                "momentum": s.signals.w_momentum,
                "orderflow": s.signals.w_orderflow,
                "liquidation": s.signals.w_liquidation,
                "microstructure": s.signals.w_microstructure,
                "cross_venue": s.signals.w_cross_venue,
            },
            "tier_thresholds": {
                "A": s.signals.tier_a_threshold,
                "B": s.signals.tier_b_threshold,
                "C": s.signals.tier_c_threshold,
            },
        }
    except Exception:
        return {"error": "Settings not available", "system_mode": _system_mode.value}
