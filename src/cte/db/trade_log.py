"""Trade journal persistence for closed trades."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

import structlog

from cte.analytics.metrics import CompletedTrade

if TYPE_CHECKING:
    from cte.db.pool import DatabasePool

logger = structlog.get_logger(__name__)

_CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS cte;"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cte.trade_log (
    time                            TIMESTAMPTZ NOT NULL,
    trade_id                        UUID NOT NULL,
    epoch                           TEXT NOT NULL,
    symbol                          TEXT NOT NULL,
    venue                           TEXT NOT NULL,
    tier                            TEXT NOT NULL,
    direction                       TEXT NOT NULL DEFAULT 'long',
    source                          TEXT NOT NULL DEFAULT 'paper_simulated',
    pnl                             NUMERIC NOT NULL,
    entry_price                     NUMERIC NOT NULL DEFAULT 0,
    exit_price                      NUMERIC NOT NULL DEFAULT 0,
    exit_reason                     TEXT NOT NULL,
    exit_layer                      INTEGER NOT NULL,
    hold_seconds                    INTEGER NOT NULL,
    r_multiple                      DOUBLE PRECISION,
    entry_latency_ms                INTEGER NOT NULL DEFAULT 0,
    slippage_bps                    DOUBLE PRECISION NOT NULL DEFAULT 0,
    mfe_pct                         DOUBLE PRECISION NOT NULL DEFAULT 0,
    mae_pct                         DOUBLE PRECISION NOT NULL DEFAULT 0,
    was_profitable                  BOOLEAN NOT NULL DEFAULT false,
    position_mode                   TEXT NOT NULL DEFAULT 'normal',
    warmup_phase                    TEXT NOT NULL DEFAULT 'none',
    execution_channel               TEXT,
    entry_reason_summary            TEXT NOT NULL DEFAULT '',
    entry_time                      TIMESTAMPTZ,
    exit_time                       TIMESTAMPTZ,
    entry_notional_usd              NUMERIC NOT NULL DEFAULT 0,
    entry_composite_score           DOUBLE PRECISION NOT NULL DEFAULT 0,
    entry_primary_score             DOUBLE PRECISION NOT NULL DEFAULT 0,
    entry_context_multiplier        DOUBLE PRECISION NOT NULL DEFAULT 1,
    entry_strongest_sub_score       TEXT NOT NULL DEFAULT '',
    entry_strongest_sub_score_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    position_id                     UUID,
    signal_id                       UUID
);
"""

_ALTERS = (
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT 'long';",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'paper_simulated';",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_price NUMERIC NOT NULL DEFAULT 0;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS exit_price NUMERIC NOT NULL DEFAULT 0;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS warmup_phase TEXT NOT NULL DEFAULT 'none';",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS execution_channel TEXT;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_reason_summary TEXT NOT NULL DEFAULT '';",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_time TIMESTAMPTZ;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS exit_time TIMESTAMPTZ;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_notional_usd NUMERIC NOT NULL DEFAULT 0;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_composite_score DOUBLE PRECISION NOT NULL DEFAULT 0;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_primary_score DOUBLE PRECISION NOT NULL DEFAULT 0;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_context_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1;",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_strongest_sub_score TEXT NOT NULL DEFAULT '';",
    "ALTER TABLE cte.trade_log ADD COLUMN IF NOT EXISTS entry_strongest_sub_score_value DOUBLE PRECISION NOT NULL DEFAULT 0;",
)

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_log_trade_id ON cte.trade_log (trade_id);",
    "CREATE INDEX IF NOT EXISTS idx_trade_log_epoch_time ON cte.trade_log (epoch, time DESC);",
    "CREATE INDEX IF NOT EXISTS idx_trade_log_symbol_time ON cte.trade_log (symbol, time DESC);",
)


def _parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    txt = value.strip()
    if not txt:
        return None
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(txt)
    except ValueError:
        return None


def _as_decimal(value: object, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _as_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value))
    except Exception:
        return default


def _as_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(str(value))
    except Exception:
        return default


def _trade_key(trade: CompletedTrade) -> str:
    return "|".join(
        (
            trade.epoch,
            trade.symbol,
            trade.venue,
            trade.tier,
            trade.direction,
            trade.source,
            trade.entry_time or "",
            trade.exit_time or "",
            str(trade.entry_price),
            str(trade.exit_price),
            str(trade.pnl),
            trade.exit_reason,
            str(trade.hold_seconds),
        )
    )


class TradeLogStore:
    """Async persistence and hydration for analytics trade journal rows."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def ensure_ready(self) -> None:
        await self._db.execute(_CREATE_SCHEMA_SQL)
        await self._db.execute(_CREATE_TABLE_SQL)
        for sql in _ALTERS:
            await self._db.execute(sql)
        for sql in _INDEXES:
            await self._db.execute(sql)

    async def insert_trade(self, trade: CompletedTrade) -> None:
        trade_id = str(uuid5(NAMESPACE_URL, _trade_key(trade)))
        ts = _parse_ts(trade.exit_time) or _parse_ts(trade.entry_time) or datetime.now(UTC)
        query = """
        INSERT INTO cte.trade_log (
            time,
            trade_id,
            epoch,
            symbol,
            venue,
            tier,
            direction,
            source,
            pnl,
            entry_price,
            exit_price,
            exit_reason,
            exit_layer,
            hold_seconds,
            r_multiple,
            entry_latency_ms,
            slippage_bps,
            mfe_pct,
            mae_pct,
            was_profitable,
            position_mode,
            warmup_phase,
            execution_channel,
            entry_reason_summary,
            entry_time,
            exit_time,
            entry_notional_usd,
            entry_composite_score,
            entry_primary_score,
            entry_context_multiplier,
            entry_strongest_sub_score,
            entry_strongest_sub_score_value
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8,
            $9, $10, $11, $12, $13, $14, $15, $16,
            $17, $18, $19, $20, $21, $22, $23, $24,
            $25, $26, $27, $28, $29, $30, $31, $32
        )
        ON CONFLICT (trade_id) DO NOTHING;
        """
        await self._db.execute(
            query,
            ts,
            trade_id,
            trade.epoch,
            trade.symbol,
            trade.venue,
            trade.tier,
            trade.direction,
            trade.source,
            trade.pnl,
            trade.entry_price,
            trade.exit_price,
            trade.exit_reason,
            trade.exit_layer,
            trade.hold_seconds,
            trade.r_multiple,
            trade.entry_latency_ms,
            trade.modeled_slippage_bps,
            trade.mfe_pct,
            trade.mae_pct,
            trade.was_profitable_at_exit,
            trade.position_mode,
            trade.warmup_phase,
            trade.execution_channel,
            trade.entry_reason_summary,
            _parse_ts(trade.entry_time),
            _parse_ts(trade.exit_time),
            trade.entry_notional_usd,
            trade.entry_composite_score,
            trade.entry_primary_score,
            trade.entry_context_multiplier,
            trade.entry_strongest_sub_score,
            trade.entry_strongest_sub_score_value,
        )

    async def load_trades(self, limit: int = 20000) -> list[CompletedTrade]:
        query = """
        SELECT
            epoch,
            symbol,
            venue,
            tier,
            direction,
            source,
            pnl,
            entry_price,
            exit_price,
            exit_reason,
            exit_layer,
            hold_seconds,
            r_multiple,
            entry_latency_ms,
            slippage_bps,
            mfe_pct,
            mae_pct,
            was_profitable,
            position_mode,
            warmup_phase,
            execution_channel,
            entry_reason_summary,
            entry_time,
            exit_time,
            entry_notional_usd,
            entry_composite_score,
            entry_primary_score,
            entry_context_multiplier,
            entry_strongest_sub_score,
            entry_strongest_sub_score_value
        FROM cte.trade_log
        ORDER BY time ASC
        LIMIT $1;
        """
        rows = await self._db.fetch(query, limit)
        out: list[CompletedTrade] = []
        for row in rows:
            d = dict(row)
            entry_dt = d.get("entry_time")
            exit_dt = d.get("exit_time")
            entry_iso = entry_dt.isoformat() if isinstance(entry_dt, datetime) else None
            exit_iso = exit_dt.isoformat() if isinstance(exit_dt, datetime) else None
            out.append(
                CompletedTrade(
                    symbol=str(d.get("symbol") or ""),
                    venue=str(d.get("venue") or ""),
                    tier=str(d.get("tier") or ""),
                    epoch=str(d.get("epoch") or ""),
                    direction=str(d.get("direction") or "long"),
                    source=str(d.get("source") or "paper_simulated"),
                    pnl=_as_decimal(d.get("pnl")),
                    exit_reason=str(d.get("exit_reason") or ""),
                    exit_layer=_as_int(d.get("exit_layer")),
                    hold_seconds=_as_int(d.get("hold_seconds")),
                    r_multiple=d.get("r_multiple"),
                    entry_latency_ms=_as_int(d.get("entry_latency_ms")),
                    modeled_slippage_bps=_as_float(d.get("slippage_bps")),
                    mfe_pct=_as_float(d.get("mfe_pct")),
                    mae_pct=_as_float(d.get("mae_pct")),
                    was_profitable_at_exit=bool(d.get("was_profitable")),
                    position_mode=str(d.get("position_mode") or "normal"),
                    entry_price=_as_decimal(d.get("entry_price")),
                    exit_price=_as_decimal(d.get("exit_price")),
                    warmup_phase=str(d.get("warmup_phase") or "none"),
                    execution_channel=(
                        str(d.get("execution_channel"))
                        if d.get("execution_channel") is not None
                        else None
                    ),
                    entry_reason_summary=str(d.get("entry_reason_summary") or ""),
                    entry_time=entry_iso,
                    exit_time=exit_iso,
                    entry_notional_usd=_as_decimal(d.get("entry_notional_usd")),
                    entry_composite_score=_as_float(d.get("entry_composite_score")),
                    entry_primary_score=_as_float(d.get("entry_primary_score")),
                    entry_context_multiplier=_as_float(d.get("entry_context_multiplier"), 1.0),
                    entry_strongest_sub_score=str(d.get("entry_strongest_sub_score") or ""),
                    entry_strongest_sub_score_value=_as_float(
                        d.get("entry_strongest_sub_score_value")
                    ),
                )
            )
        await logger.ainfo("trade_log_loaded", count=len(out))
        return out
