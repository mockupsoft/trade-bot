"""Database schema definitions for CTE.

Uses raw SQL with asyncpg. TimescaleDB hypertables for time-series data.
Standard PostgreSQL tables for reference/config data.
"""
from __future__ import annotations

SCHEMA_VERSION = "001"

CREATE_EXTENSIONS = """
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
"""

CREATE_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS cte;
"""

# ---------------------------------------------------------------------------
# Reference Tables
# ---------------------------------------------------------------------------

CREATE_SYMBOLS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.symbols (
    symbol          TEXT PRIMARY KEY,
    base_asset      TEXT NOT NULL,
    quote_asset     TEXT NOT NULL,
    tick_size       NUMERIC NOT NULL,
    lot_size        NUMERIC NOT NULL,
    min_notional    NUMERIC NOT NULL DEFAULT 10,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_VENUES_TABLE = """
CREATE TABLE IF NOT EXISTS cte.venues (
    venue_id        TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    venue_type      TEXT NOT NULL DEFAULT 'futures',
    is_primary      BOOLEAN NOT NULL DEFAULT false,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Time-Series Tables (TimescaleDB hypertables)
# ---------------------------------------------------------------------------

CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS cte.trades (
    time            TIMESTAMPTZ NOT NULL,
    event_id        UUID NOT NULL DEFAULT uuid_generate_v4(),
    venue           TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    price           NUMERIC NOT NULL,
    quantity        NUMERIC NOT NULL,
    side            TEXT NOT NULL,
    venue_trade_id  TEXT NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable('cte.trades', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON cte.trades (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_venue_time ON cte.trades (venue, time DESC);
"""

CREATE_ORDERBOOK_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.orderbook_snapshots (
    time            TIMESTAMPTZ NOT NULL,
    event_id        UUID NOT NULL DEFAULT uuid_generate_v4(),
    venue           TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    bid_prices      NUMERIC[] NOT NULL,
    bid_quantities  NUMERIC[] NOT NULL,
    ask_prices      NUMERIC[] NOT NULL,
    ask_quantities  NUMERIC[] NOT NULL,
    spread_bps      NUMERIC,
    mid_price       NUMERIC,
    sequence        BIGINT NOT NULL
);

SELECT create_hypertable('cte.orderbook_snapshots', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_ob_symbol_time
    ON cte.orderbook_snapshots (symbol, time DESC);
"""

CREATE_FEATURE_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.feature_snapshots (
    time            TIMESTAMPTZ NOT NULL,
    event_id        UUID NOT NULL DEFAULT uuid_generate_v4(),
    symbol          TEXT NOT NULL,
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    rsi             DOUBLE PRECISION,
    ema_fast        DOUBLE PRECISION,
    ema_slow        DOUBLE PRECISION,
    vwap            DOUBLE PRECISION,
    volume_24h      DOUBLE PRECISION,
    price_change_1h DOUBLE PRECISION,
    spread_bps      DOUBLE PRECISION,
    ob_imbalance    DOUBLE PRECISION,
    extra           JSONB NOT NULL DEFAULT '{}'
);

SELECT create_hypertable('cte.feature_snapshots', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_features_symbol_time
    ON cte.feature_snapshots (symbol, time DESC);
"""

# ---------------------------------------------------------------------------
# Signal & Decision Tables
# ---------------------------------------------------------------------------

CREATE_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.signals (
    time            TIMESTAMPTZ NOT NULL,
    signal_id       UUID NOT NULL DEFAULT uuid_generate_v4(),
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,
    confidence      DOUBLE PRECISION NOT NULL,
    reason_primary  TEXT NOT NULL,
    reason_factors  TEXT[] NOT NULL DEFAULT '{}',
    reason_readable TEXT NOT NULL,
    context_flags   JSONB NOT NULL DEFAULT '{}',
    features        JSONB NOT NULL DEFAULT '{}'
);

SELECT create_hypertable('cte.signals', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
    ON cte.signals (symbol, time DESC);
"""

CREATE_RISK_DECISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.risk_decisions (
    time            TIMESTAMPTZ NOT NULL,
    decision_id     UUID NOT NULL DEFAULT uuid_generate_v4(),
    signal_id       UUID NOT NULL,
    symbol          TEXT NOT NULL,
    decision        TEXT NOT NULL,
    reason          TEXT NOT NULL,
    checks          JSONB NOT NULL DEFAULT '[]'
);

SELECT create_hypertable('cte.risk_decisions', 'time', if_not_exists => TRUE);
"""

# ---------------------------------------------------------------------------
# Order & Position Tables
# ---------------------------------------------------------------------------

CREATE_ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.orders (
    time            TIMESTAMPTZ NOT NULL,
    order_id        UUID NOT NULL DEFAULT uuid_generate_v4(),
    signal_id       UUID NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    status          TEXT NOT NULL,
    requested_qty   NUMERIC NOT NULL,
    filled_qty      NUMERIC NOT NULL DEFAULT 0,
    avg_price       NUMERIC NOT NULL DEFAULT 0,
    venue           TEXT NOT NULL,
    venue_order_id  TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    fees            NUMERIC NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable('cte.orders', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_orders_signal ON cte.orders (signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON cte.orders (status, time DESC);
"""

CREATE_POSITIONS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.positions (
    position_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     NUMERIC NOT NULL,
    quantity        NUMERIC NOT NULL,
    leverage        INTEGER NOT NULL DEFAULT 1,
    signal_id       UUID NOT NULL,
    opened_at       TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    exit_reason     TEXT,
    exit_price      NUMERIC,
    realized_pnl    NUMERIC,
    highest_price   NUMERIC NOT NULL,
    lowest_price    NUMERIC NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',
    metadata        JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON cte.positions (status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON cte.positions (symbol, opened_at DESC);
"""

# ---------------------------------------------------------------------------
# Exit Records
# ---------------------------------------------------------------------------

CREATE_EXITS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.exits (
    time            TIMESTAMPTZ NOT NULL,
    exit_id         UUID NOT NULL DEFAULT uuid_generate_v4(),
    position_id     UUID NOT NULL,
    symbol          TEXT NOT NULL,
    exit_reason     TEXT NOT NULL,
    exit_price      NUMERIC NOT NULL,
    pnl             NUMERIC NOT NULL,
    hold_seconds    INTEGER NOT NULL,
    reason_detail   TEXT NOT NULL DEFAULT ''
);

SELECT create_hypertable('cte.exits', 'time', if_not_exists => TRUE);
"""

# ---------------------------------------------------------------------------
# Analytics Aggregation Tables
# ---------------------------------------------------------------------------

CREATE_DAILY_PNL_TABLE = """
CREATE TABLE IF NOT EXISTS cte.daily_pnl (
    date            DATE NOT NULL,
    symbol          TEXT NOT NULL,
    total_pnl       NUMERIC NOT NULL DEFAULT 0,
    trade_count     INTEGER NOT NULL DEFAULT 0,
    win_count       INTEGER NOT NULL DEFAULT 0,
    loss_count      INTEGER NOT NULL DEFAULT 0,
    max_drawdown    NUMERIC NOT NULL DEFAULT 0,
    sharpe_ratio    DOUBLE PRECISION,
    PRIMARY KEY (date, symbol)
);
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS cte.schema_version (
    version         TEXT PRIMARY KEY,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Continuous Aggregates (TimescaleDB)
# ---------------------------------------------------------------------------

CREATE_OHLCV_1M_AGG = """
CREATE MATERIALIZED VIEW IF NOT EXISTS cte.ohlcv_1m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', time) AS bucket,
    symbol,
    venue,
    FIRST(price, time) AS open,
    MAX(price) AS high,
    MIN(price) AS low,
    LAST(price, time) AS close,
    SUM(quantity) AS volume,
    COUNT(*) AS trade_count
FROM cte.trades
GROUP BY bucket, symbol, venue
WITH NO DATA;
"""

CREATE_OHLCV_5M_AGG = """
CREATE MATERIALIZED VIEW IF NOT EXISTS cte.ohlcv_5m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('5 minutes', time) AS bucket,
    symbol,
    venue,
    FIRST(price, time) AS open,
    MAX(price) AS high,
    MIN(price) AS low,
    LAST(price, time) AS close,
    SUM(quantity) AS volume,
    COUNT(*) AS trade_count
FROM cte.trades
GROUP BY bucket, symbol, venue
WITH NO DATA;
"""

# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

ALL_MIGRATIONS = [
    ("extensions", CREATE_EXTENSIONS),
    ("schema", CREATE_SCHEMA),
    ("symbols", CREATE_SYMBOLS_TABLE),
    ("venues", CREATE_VENUES_TABLE),
    ("trades", CREATE_TRADES_TABLE),
    ("orderbook_snapshots", CREATE_ORDERBOOK_SNAPSHOTS_TABLE),
    ("feature_snapshots", CREATE_FEATURE_SNAPSHOTS_TABLE),
    ("signals", CREATE_SIGNALS_TABLE),
    ("risk_decisions", CREATE_RISK_DECISIONS_TABLE),
    ("orders", CREATE_ORDERS_TABLE),
    ("positions", CREATE_POSITIONS_TABLE),
    ("exits", CREATE_EXITS_TABLE),
    ("daily_pnl", CREATE_DAILY_PNL_TABLE),
    ("schema_version", CREATE_SCHEMA_VERSION_TABLE),
    ("ohlcv_1m", CREATE_OHLCV_1M_AGG),
    ("ohlcv_5m", CREATE_OHLCV_5M_AGG),
]


SEED_SYMBOLS = """
INSERT INTO cte.symbols (symbol, base_asset, quote_asset, tick_size, lot_size, min_notional)
VALUES
    ('BTCUSDT', 'BTC', 'USDT', 0.10, 0.001, 10),
    ('ETHUSDT', 'ETH', 'USDT', 0.01, 0.01, 10)
ON CONFLICT (symbol) DO NOTHING;
"""

SEED_VENUES = """
INSERT INTO cte.venues (venue_id, display_name, venue_type, is_primary)
VALUES
    ('binance', 'Binance USDⓈ-M Futures', 'futures', true),
    ('bybit', 'Bybit Linear Perpetual', 'futures', false)
ON CONFLICT (venue_id) DO NOTHING;
"""
