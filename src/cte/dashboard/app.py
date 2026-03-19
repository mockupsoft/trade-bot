"""CTE Dashboard — Professional multi-page operations + research platform.

Three surfaces:
A. Monitoring Dashboard — KPIs, charts, trade journal
B. Operations Console — kill switch, venue health, reconciliation, mode control
C. Research Console — score distributions, tier comparison, exit attribution

Run: uvicorn cte.dashboard.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from cte.analytics.engine import AnalyticsEngine
from cte.analytics.epochs import EpochManager, EpochMode
from cte.api.analytics_routes import router as analytics_router
from cte.api.analytics_routes import set_engine
from cte.api.health import router as health_router
from cte.core.logging import setup_logging
from cte.ops.kill_switch import OperationsController
from cte.ops.readiness import (
    build_demo_to_live_checklist,
    build_paper_to_demo_checklist,
    evaluate_readiness,
)
from cte.ops.validation import ValidationCampaign

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

TEMPLATE_DIR = Path(__file__).parent / "templates"

_epoch_manager = EpochManager()
_analytics_engine: AnalyticsEngine | None = None
_ops_controller = OperationsController()
_validation_campaigns: dict[str, ValidationCampaign] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _analytics_engine
    setup_logging(level="INFO", service_name="dashboard")

    for name, mode, desc in [
        ("crypto_v1_paper", EpochMode.PAPER, "Paper trading phase"),
        ("crypto_v1_demo", EpochMode.DEMO, "Testnet demo phase"),
        ("crypto_v1_live", EpochMode.LIVE, "Minimal live trading"),
        ("crypto_v1_shadow_short", EpochMode.SHADOW, "Shadow short experiment"),
    ]:
        _epoch_manager.create_epoch(name, mode, desc)
    _epoch_manager.activate("crypto_v1_paper")

    _analytics_engine = AnalyticsEngine(_epoch_manager, initial_capital=Decimal("10000"))
    set_engine(_analytics_engine)
    yield


app = FastAPI(title="CTE Dashboard", version="0.1.0", lifespan=lifespan)
app.include_router(health_router, prefix="/api/dashboard")
app.include_router(analytics_router)


# ── Pages ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=(TEMPLATE_DIR / "index.html").read_text())


# ── Ops API ───────────────────────────────────────────────────

@app.get("/api/ops/status")
async def ops_status():
    return _ops_controller.status()


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


# ── Edge Proof API ────────────────────────────────────────────

@app.get("/api/readiness/edge_proof")
async def edge_proof_checklist():
    from cte.ops.readiness import build_edge_proof_checklist
    gates = build_edge_proof_checklist()
    return evaluate_readiness(gates)


@app.get("/api/report/go_no_go")
async def go_no_go_report():
    from cte.ops.go_no_go import build_go_no_go_report
    return build_go_no_go_report(
        campaign_days=0,
        total_trades=_analytics_engine.total_trades if _analytics_engine else 0,
    )


# ── Config API (read-only) ────────────────────────────────────

@app.get("/api/config")
async def get_config():
    from cte.core.settings import get_settings
    try:
        s = get_settings()
        return {
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
        return {"error": "Settings not available"}
