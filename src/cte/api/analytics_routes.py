"""Analytics API endpoints for dashboard drilldowns.

All endpoints return JSON suitable for Grafana JSON datasource
or direct frontend consumption.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# The engine is injected via app.state at startup
_engine = None


def set_engine(engine: Any) -> None:
    global _engine
    _engine = engine


@router.get("/summary")
async def summary(
    epoch: str | None = None,
    symbol: str | None = None,
    tier: Annotated[
        Literal["A", "B", "C"] | None,
        Query(description="Signal tier filter (v1 tiers A/B/C only)."),
    ] = None,
) -> dict:
    """Full metrics summary with optional filters (Research + Overview)."""
    if not _engine:
        return {"error": "Analytics engine not initialized"}
    return _engine.get_metrics(epoch=epoch, symbol=symbol, tier=tier)


@router.get("/pnl/daily")
async def daily_pnl(epoch: str | None = None) -> dict:
    """Daily PnL breakdown."""
    if not _engine:
        return {"error": "Analytics engine not initialized"}
    return _engine.get_daily_summary(epoch=epoch)


@router.get("/trades")
async def trades(
    epoch: str | None = None,
    symbol: str | None = None,
    tier: str | None = None,
    exit_reason: str | None = None,
    source: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    """Individual trade records for drilldown (newest first).

    Filter by ``source``: ``seed`` | ``paper_simulated`` | ``demo_exchange``.
    v1 symbols: BTCUSDT, ETHUSDT (enforced by execution stack; journal is read-only).
    """
    if not _engine:
        return []
    return _engine.get_trades(
        epoch=epoch, symbol=symbol, tier=tier, exit_reason=exit_reason,
        source=source, limit=limit,
    )


@router.get("/breakdown/exit_reason")
async def by_exit_reason(epoch: str | None = None) -> dict:
    """PnL and count breakdown by exit reason."""
    if not _engine:
        return {}
    metrics = _engine.get_metrics(epoch=epoch)
    return {
        "pnl_by_exit_reason": metrics.get("pnl_by_exit_reason", {}),
        "count_by_exit_reason": metrics.get("count_by_exit_reason", {}),
    }


@router.get("/breakdown/tier")
async def by_tier(epoch: str | None = None) -> dict:
    """PnL breakdown by signal tier."""
    if not _engine:
        return {}
    metrics = _engine.get_metrics(epoch=epoch)
    return {"pnl_by_tier": metrics.get("pnl_by_tier", {})}


@router.get("/breakdown/symbol")
async def by_symbol(epoch: str | None = None) -> dict:
    """PnL breakdown by symbol."""
    if not _engine:
        return {}
    metrics = _engine.get_metrics(epoch=epoch)
    return {"pnl_by_symbol": metrics.get("pnl_by_symbol", {})}


@router.get("/exit_analysis/saved_losers")
async def saved_losers(epoch: str | None = None) -> dict:
    """Saved losers count and details."""
    if not _engine:
        return {}
    metrics = _engine.get_metrics(epoch=epoch)
    return {"saved_losers": metrics.get("saved_losers", 0)}


@router.get("/exit_analysis/killed_winners")
async def killed_winners(epoch: str | None = None) -> dict:
    """Killed winners count and details."""
    if not _engine:
        return {}
    metrics = _engine.get_metrics(epoch=epoch)
    return {"killed_winners": metrics.get("killed_winners", 0)}


@router.get("/exit_analysis/no_progress_regret")
async def no_progress_regret(epoch: str | None = None) -> dict:
    """No-progress exit regret analysis."""
    if not _engine:
        return {}
    metrics = _engine.get_metrics(epoch=epoch)
    return metrics.get("no_progress_regret", {})


@router.get("/exit_analysis/runner_outcomes")
async def runner_outcomes(epoch: str | None = None) -> dict:
    """Runner mode outcome analysis."""
    if not _engine:
        return {}
    metrics = _engine.get_metrics(epoch=epoch)
    return metrics.get("runner_outcomes", {})


@router.get("/compare")
async def compare_epochs(epoch_a: str, epoch_b: str) -> dict:
    """Compare metrics between two epochs (e.g., paper vs demo)."""
    if not _engine:
        return {}
    return _engine.get_epoch_comparison(epoch_a, epoch_b)


@router.get("/epochs")
async def list_epochs() -> list[dict]:
    """List all registered epochs."""
    if not _engine:
        return []
    return [
        {
            "name": ep.name,
            "mode": ep.mode.value,
            "started_at": ep.started_at.isoformat(),
            "ended_at": ep.ended_at.isoformat() if ep.ended_at else None,
            "is_active": ep.is_active,
            "duration_hours": round(ep.duration_hours, 2),
        }
        for ep in _engine._epochs.list_epochs()
    ]
