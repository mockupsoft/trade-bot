"""TimescaleDB persistence for streaming feature snapshots.

Batches writes and flushes periodically to avoid per-event DB overhead.
On restart, the engine can read the last snapshot to seed window state.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any

import structlog

from cte.core.events import StreamingFeatureVector, TimeframeFeatures

logger = structlog.get_logger(__name__)

INSERT_STREAMING_FEATURE = """
INSERT INTO cte.streaming_features (
    time, event_id, symbol, window_seconds,
    returns, returns_z, momentum_z,
    taker_flow_imbalance, spread_bps, spread_widening,
    ob_imbalance, liquidation_imbalance, venue_divergence_bps, vwap,
    trade_count, volume, buy_volume, sell_volume, window_fill_pct,
    execution_feasibility, whale_risk_flag, urgent_news_flag,
    freshness_composite, trade_age_ms, orderbook_age_ms,
    last_price, best_bid, best_ask, mid_price, mark_price
) VALUES (
    $1, $2, $3, $4,
    $5, $6, $7,
    $8, $9, $10,
    $11, $12, $13, $14,
    $15, $16, $17, $18, $19,
    $20, $21, $22,
    $23, $24, $25,
    $26, $27, $28, $29, $30
)
"""


class FeaturePersister:
    """Batches streaming feature vectors for periodic DB flush."""

    def __init__(self, batch_size: int = 100) -> None:
        self._batch: deque[tuple] = deque(maxlen=batch_size * 10)
        self._batch_size = batch_size

    def stage(self, vector: StreamingFeatureVector) -> None:
        """Stage a feature vector for persistence."""
        for tf in (vector.tf_10s, vector.tf_30s, vector.tf_60s, vector.tf_5m):
            row = self._make_row(vector, tf)
            self._batch.append(row)

    def _make_row(
        self, v: StreamingFeatureVector, tf: TimeframeFeatures
    ) -> tuple:
        return (
            v.timestamp,                             # $1 time
            str(v.event_id),                         # $2 event_id
            v.symbol.value,                          # $3 symbol
            tf.window_seconds,                       # $4 window_seconds
            tf.returns,                              # $5
            tf.returns_z,                            # $6
            tf.momentum_z,                           # $7
            tf.taker_flow_imbalance,                 # $8
            tf.spread_bps,                           # $9
            tf.spread_widening,                      # $10
            tf.ob_imbalance,                         # $11
            tf.liquidation_imbalance,                # $12
            tf.venue_divergence_bps,                 # $13
            tf.vwap,                                 # $14
            tf.trade_count,                          # $15
            tf.volume,                               # $16
            0.0,                                     # $17 buy_volume (from totals)
            0.0,                                     # $18 sell_volume (from totals)
            tf.window_fill_pct,                      # $19
            v.execution_feasibility,                 # $20
            v.whale_risk_flag,                       # $21
            v.urgent_news_flag,                      # $22
            v.freshness.composite,                   # $23
            v.freshness.trade_age_ms,                # $24
            v.freshness.orderbook_age_ms,            # $25
            float(v.last_price) if v.last_price else None,  # $26
            float(v.best_bid) if v.best_bid else None,      # $27
            float(v.best_ask) if v.best_ask else None,      # $28
            float(v.mid_price) if v.mid_price else None,    # $29
            float(v.mark_price) if v.mark_price else None,  # $30
        )

    async def flush(self, db_pool: Any) -> int:
        """Write staged rows to TimescaleDB. Returns number of rows written."""
        if not self._batch:
            return 0

        rows = list(self._batch)
        self._batch.clear()

        try:
            async with db_pool.acquire() as conn:
                await conn.executemany(INSERT_STREAMING_FEATURE, rows)
            await logger.ainfo("features_persisted", count=len(rows))
            return len(rows)
        except Exception:
            await logger.aexception("feature_persist_failed", count=len(rows))
            # Re-stage failed rows for retry
            for row in rows:
                self._batch.append(row)
            return 0

    @property
    def pending_count(self) -> int:
        return len(self._batch)
