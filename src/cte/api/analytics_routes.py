"""Analytics API endpoints for dashboard drilldowns.

All endpoints return JSON suitable for Grafana JSON datasource
or direct frontend consumption.
"""

from __future__ import annotations

import csv
from io import StringIO
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# The engine is injected via app.state at startup
_engine = None


def _pdf_escape(txt: str) -> str:
    return txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _simple_pdf(lines: list[str]) -> bytes:
    # Minimal single-page PDF generator (dependency-free).
    content_parts = ["BT", "/F1 10 Tf", "40 800 Td", "14 TL"]
    for idx, line in enumerate(lines[:48]):
        if idx > 0:
            content_parts.append("T*")
        content_parts.append(f"({_pdf_escape(line)}) Tj")
    content_parts.append("ET")
    stream = "\n".join(content_parts).encode("latin-1", errors="replace")

    objs: list[bytes] = []
    objs.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objs.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objs.append(
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
    )
    objs.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objs.append(
        f"5 0 obj << /Length {len(stream)} >> stream\n".encode("ascii")
        + stream
        + b"\nendstream endobj\n"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_start = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(out)


def _collect_trades_for_export(**kwargs: Any) -> list[dict[str, Any]]:
    if not _engine:
        return []
    page = 1
    size = 200
    items: list[dict[str, Any]] = []
    while True:
        chunk = _engine.get_trades_paged(page=page, page_size=size, **kwargs)
        rows = list(chunk.get("items") or [])
        items.extend(rows)
        if page >= int(chunk.get("total_pages") or 1):
            break
        page += 1
    return items


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
    venue: str | None = None,
    direction: str | None = None,
    execution_channel: str | None = None,
    pnl_sign: Annotated[
        Literal["pos", "neg", "flat"] | None,
        Query(description="Filter by pnl sign: pos | neg | flat"),
    ] = None,
    hold_seconds_min: int | None = Query(default=None, ge=0),
    hold_seconds_max: int | None = Query(default=None, ge=0),
    time_from: str | None = Query(
        default=None, description="ISO8601 UTC, e.g. 2026-03-23T00:00:00Z"
    ),
    time_to: str | None = Query(default=None, description="ISO8601 UTC, e.g. 2026-03-23T23:59:59Z"),
    warmup_phase: str | None = Query(
        default=None,
        description="Filter by staged warmup: none | early | full",
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    """Individual trade records for drilldown (newest first).

    Filter by ``source``: ``seed`` | ``paper_simulated`` | ``demo_exchange``.
    Configured engine symbols (default: 10 Binance USDT linear majors; journal is read-only).
    """
    if not _engine:
        return []
    return _engine.get_trades(
        epoch=epoch,
        symbol=symbol,
        tier=tier,
        venue=venue,
        exit_reason=exit_reason,
        source=source,
        warmup_phase=warmup_phase,
        direction=direction,
        execution_channel=execution_channel,
        pnl_sign=pnl_sign,
        hold_seconds_min=hold_seconds_min,
        hold_seconds_max=hold_seconds_max,
        time_from=time_from,
        time_to=time_to,
        limit=limit,
    )


@router.get("/trades/paged")
async def trades_paged(
    epoch: str | None = None,
    symbol: str | None = None,
    tier: str | None = None,
    exit_reason: str | None = None,
    source: str | None = None,
    venue: str | None = None,
    direction: str | None = None,
    execution_channel: str | None = None,
    pnl_sign: Annotated[
        Literal["pos", "neg", "flat"] | None,
        Query(description="Filter by pnl sign: pos | neg | flat"),
    ] = None,
    hold_seconds_min: int | None = Query(default=None, ge=0),
    hold_seconds_max: int | None = Query(default=None, ge=0),
    time_from: str | None = Query(default=None, description="ISO8601 UTC"),
    time_to: str | None = Query(default=None, description="ISO8601 UTC"),
    warmup_phase: str | None = Query(
        default=None,
        description="Filter by staged warmup: none | early | full",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> dict[str, Any]:
    """Paged trade records with total count for the dashboard journal."""
    if not _engine:
        return {
            "items": [],
            "page": page,
            "page_size": page_size,
            "total_count": 0,
            "total_pages": 1,
        }
    return _engine.get_trades_paged(
        epoch=epoch,
        symbol=symbol,
        tier=tier,
        venue=venue,
        exit_reason=exit_reason,
        source=source,
        warmup_phase=warmup_phase,
        direction=direction,
        execution_channel=execution_channel,
        pnl_sign=pnl_sign,
        hold_seconds_min=hold_seconds_min,
        hold_seconds_max=hold_seconds_max,
        time_from=time_from,
        time_to=time_to,
        page=page,
        page_size=page_size,
    )


@router.get("/trades/export.csv")
async def trades_export_csv(
    epoch: str | None = None,
    symbol: str | None = None,
    tier: str | None = None,
    exit_reason: str | None = None,
    source: str | None = None,
    venue: str | None = None,
    direction: str | None = None,
    execution_channel: str | None = None,
    pnl_sign: Literal["pos", "neg", "flat"] | None = None,
    hold_seconds_min: int | None = Query(default=None, ge=0),
    hold_seconds_max: int | None = Query(default=None, ge=0),
    time_from: str | None = None,
    time_to: str | None = None,
    warmup_phase: str | None = None,
) -> Response:
    rows = _collect_trades_for_export(
        epoch=epoch,
        symbol=symbol,
        tier=tier,
        venue=venue,
        exit_reason=exit_reason,
        source=source,
        warmup_phase=warmup_phase,
        direction=direction,
        execution_channel=execution_channel,
        pnl_sign=pnl_sign,
        hold_seconds_min=hold_seconds_min,
        hold_seconds_max=hold_seconds_max,
        time_from=time_from,
        time_to=time_to,
    )
    columns = [
        "epoch",
        "symbol",
        "venue",
        "tier",
        "direction",
        "source",
        "execution_channel",
        "pnl",
        "pnl_pct",
        "r_multiple",
        "exit_reason",
        "hold_seconds",
        "entry_notional_usd",
        "entry_time",
        "exit_time",
    ]
    buff = StringIO()
    writer = csv.DictWriter(buff, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in columns})
    return Response(
        content=buff.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="trade_journal.csv"'},
    )


@router.get("/trades/export.pdf")
async def trades_export_pdf(
    epoch: str | None = None,
    symbol: str | None = None,
    tier: str | None = None,
    exit_reason: str | None = None,
    source: str | None = None,
    venue: str | None = None,
    direction: str | None = None,
    execution_channel: str | None = None,
    pnl_sign: Literal["pos", "neg", "flat"] | None = None,
    hold_seconds_min: int | None = Query(default=None, ge=0),
    hold_seconds_max: int | None = Query(default=None, ge=0),
    time_from: str | None = None,
    time_to: str | None = None,
    warmup_phase: str | None = None,
) -> Response:
    rows = _collect_trades_for_export(
        epoch=epoch,
        symbol=symbol,
        tier=tier,
        venue=venue,
        exit_reason=exit_reason,
        source=source,
        warmup_phase=warmup_phase,
        direction=direction,
        execution_channel=execution_channel,
        pnl_sign=pnl_sign,
        hold_seconds_min=hold_seconds_min,
        hold_seconds_max=hold_seconds_max,
        time_from=time_from,
        time_to=time_to,
    )
    lines = [
        "CTE Trade Journal Export",
        f"Rows: {len(rows)}",
        "symbol | tier | pnl | exit_reason | hold_seconds | source",
    ]
    for r in rows[:44]:
        lines.append(
            f"{r.get('symbol', '')} | {r.get('tier', '')} | {r.get('pnl', '')} | {r.get('exit_reason', '')} | {r.get('hold_seconds', '')} | {r.get('source', '')}"
        )
    pdf = _simple_pdf(lines)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="trade_journal.pdf"'},
    )


@router.get("/trades/attribution")
async def trades_attribution(
    epoch: str | None = None,
    symbol: str | None = None,
    tier: str | None = None,
    exit_reason: str | None = None,
    source: str | None = None,
    venue: str | None = None,
    direction: str | None = None,
    execution_channel: str | None = None,
    pnl_sign: Literal["pos", "neg", "flat"] | None = None,
    hold_seconds_min: int | None = Query(default=None, ge=0),
    hold_seconds_max: int | None = Query(default=None, ge=0),
    time_from: str | None = None,
    time_to: str | None = None,
    warmup_phase: str | None = None,
) -> dict[str, Any]:
    if not _engine:
        return {"items": [], "totals": {}}
    metrics = _engine.get_metrics(
        epoch=epoch,
        symbol=symbol,
        tier=tier,
        venue=venue,
        exit_reason=exit_reason,
    )
    rows = _collect_trades_for_export(
        epoch=epoch,
        symbol=symbol,
        tier=tier,
        venue=venue,
        exit_reason=exit_reason,
        source=source,
        warmup_phase=warmup_phase,
        direction=direction,
        execution_channel=execution_channel,
        pnl_sign=pnl_sign,
        hold_seconds_min=hold_seconds_min,
        hold_seconds_max=hold_seconds_max,
        time_from=time_from,
        time_to=time_to,
    )
    pnl_by_source: dict[str, float] = {}
    pnl_by_venue: dict[str, float] = {}
    pnl_by_direction: dict[str, float] = {}
    for r in rows:
        pnl = float(r.get("pnl") or 0.0)
        s = str(r.get("source") or "unknown")
        v = str(r.get("venue") or "unknown")
        d = str(r.get("direction") or "unknown")
        pnl_by_source[s] = round(pnl_by_source.get(s, 0.0) + pnl, 4)
        pnl_by_venue[v] = round(pnl_by_venue.get(v, 0.0) + pnl, 4)
        pnl_by_direction[d] = round(pnl_by_direction.get(d, 0.0) + pnl, 4)
    return {
        "totals": {
            "trade_count": len(rows),
            "total_pnl": metrics.get("total_pnl", 0.0),
            "win_rate": metrics.get("win_rate", 0.0),
            "expectancy": metrics.get("expectancy", 0.0),
        },
        "pnl_by_tier": metrics.get("pnl_by_tier", {}),
        "pnl_by_symbol": metrics.get("pnl_by_symbol", {}),
        "pnl_by_exit_reason": metrics.get("pnl_by_exit_reason", {}),
        "pnl_by_source": pnl_by_source,
        "pnl_by_venue": pnl_by_venue,
        "pnl_by_direction": pnl_by_direction,
    }


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
    """PnL and per-tier expectancy / win_rate (validation audit)."""
    if not _engine:
        return {}
    metrics = _engine.get_metrics(epoch=epoch)
    return {
        "pnl_by_tier": metrics.get("pnl_by_tier", {}),
        "metrics_by_tier": metrics.get("metrics_by_tier", {}),
        "tier_pnl_consistency": metrics.get("tier_pnl_consistency", {}),
    }


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
