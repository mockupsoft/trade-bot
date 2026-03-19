"""FastAPI application factory for CTE services.

Each service (connector, normalizer, feature engine, etc.) creates
its own FastAPI app using this factory, then mounts service-specific routers.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from cte.api.health import router as health_router
from cte.core.logging import setup_logging
from cte.core.settings import CTESettings, get_settings


def create_app(
    service_name: str,
    settings: CTESettings | None = None,
    lifespan: object | None = None,
) -> FastAPI:
    """Create a FastAPI application for a CTE service."""
    if settings is None:
        settings = get_settings()

    setup_logging(level=settings.engine.log_level, service_name=service_name)

    app = FastAPI(
        title=f"CTE – {service_name}",
        version="0.1.0",
        docs_url=f"/api/{service_name}/docs",
        openapi_url=f"/api/{service_name}/openapi.json",
        lifespan=lifespan,
    )

    app.include_router(health_router, prefix=f"/api/{service_name}")
    app.state.settings = settings

    return app


@asynccontextmanager
async def default_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Default lifespan with logging setup/teardown."""
    import structlog
    log = structlog.get_logger("lifespan")
    await log.ainfo("service_starting", service=app.title)
    yield
    await log.ainfo("service_stopping", service=app.title)
