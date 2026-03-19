"""CTE Dashboard — Professional monitoring UI served by FastAPI.

Serves a single-page dashboard with real-time auto-refreshing data.
No build step required — uses Tailwind CSS, Chart.js, and Alpine.js via CDN.

Run: uvicorn cte.dashboard.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from cte.analytics.engine import AnalyticsEngine
from cte.analytics.epochs import EpochManager, EpochMode
from cte.api.analytics_routes import router as analytics_router, set_engine
from cte.api.health import router as health_router
from cte.core.logging import setup_logging

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

_epoch_manager = EpochManager()
_analytics_engine: AnalyticsEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _analytics_engine
    setup_logging(level="INFO", service_name="dashboard")

    _epoch_manager.create_epoch("crypto_v1_paper", EpochMode.PAPER, "Paper trading phase")
    _epoch_manager.create_epoch("crypto_v1_demo", EpochMode.DEMO, "Testnet demo phase")
    _epoch_manager.create_epoch("crypto_v1_live", EpochMode.LIVE, "Minimal live trading")
    _epoch_manager.create_epoch(
        "crypto_v1_shadow_short", EpochMode.SHADOW, "Shadow short-selling experiment"
    )
    _epoch_manager.activate("crypto_v1_paper")

    _analytics_engine = AnalyticsEngine(_epoch_manager, initial_capital=Decimal("10000"))
    set_engine(_analytics_engine)

    yield


app = FastAPI(
    title="CTE Dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router, prefix="/api/dashboard")
app.include_router(analytics_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the main dashboard page."""
    template = TEMPLATE_DIR / "index.html"
    return HTMLResponse(content=template.read_text())


@app.get("/api/dashboard/info")
async def info() -> dict:
    """Dashboard metadata."""
    return {
        "service": "CTE Dashboard",
        "version": "0.1.0",
        "epochs": [
            {"name": e.name, "mode": e.mode.value, "active": e.is_active}
            for e in _epoch_manager.list_epochs()
        ],
        "total_trades": _analytics_engine.total_trades if _analytics_engine else 0,
    }
