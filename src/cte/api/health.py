"""Health check and metrics API endpoints.

Every CTE service mounts these routes for operational visibility.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["health"])

_start_time = time.monotonic()
_service_name = "cte"
_service_version = "0.1.0"
_component_checks: dict[str, Any] = {}


def register_health_check(name: str, check_fn: Any) -> None:
    """Register a component health check function."""
    _component_checks[name] = check_fn


@router.get("/health")
async def health() -> dict:
    """Aggregate health check for the service."""
    components = {}
    all_healthy = True

    for name, check_fn in _component_checks.items():
        try:
            result = await check_fn() if asyncio.iscoroutinefunction(check_fn) else check_fn()
            components[name] = result
            if isinstance(result, dict) and not result.get("healthy", True):
                all_healthy = False
        except Exception as e:
            components[name] = {"healthy": False, "error": str(e)}
            all_healthy = False

    return {
        "status": "healthy" if all_healthy else "degraded",
        "service": _service_name,
        "version": _service_version,
        "uptime_seconds": round(time.monotonic() - _start_time, 2),
        "components": components,
    }


@router.get("/health/live")
async def liveness() -> dict:
    """Kubernetes liveness probe. Always returns 200 if the process is running."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness() -> dict:
    """Kubernetes readiness probe. Returns 200 only if all components are healthy."""
    result = await health()
    if result["status"] != "healthy":
        return Response(content="not ready", status_code=503)
    return {"status": "ready"}


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


import asyncio  # noqa: E402 - imported at bottom to avoid circular import in registration
