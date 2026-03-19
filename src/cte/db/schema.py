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
    direction       TEXT NOT NULL DEFAULT 'long',
    status          TEXT NOT NULL DEFAULT 'open',

    -- Signal provenance
    signal_id       UUID NOT NULL,
    signal_tier     TEXT NOT NULL DEFAULT '',
    entry_reason    TEXT NOT NULL DEFAULT '',
    composite_score DOUBLE PRECISION DEFAULT 0,

    -- Fill details
    entry_price     NUMERIC NOT NULL,
    fill_price      NUMERIC NOT NULL,
    quantity        NUMERIC NOT NULL,
    notional_usd    NUMERIC NOT NULL DEFAULT 0,
    leverage        INTEGER NOT NULL DEFAULT 1,

    -- Slippage and cost
    signal_price        NUMERIC,
    modeled_slippage_bps NUMERIC DEFAULT 0,
    effective_spread_bps NUMERIC DEFAULT 0,
    fill_model_used     TEXT DEFAULT '',
    estimated_fees_usd  NUMERIC DEFAULT 0,

    -- Timing
    signal_time         TIMESTAMPTZ,
    opened_at           TIMESTAMPTZ NOT NULL,
    closed_at           TIMESTAMPTZ,
    entry_latency_ms    INTEGER DEFAULT 0,
    modeled_fill_latency_ms INTEGER DEFAULT 0,

    -- Risk
    stop_loss_pct       DOUBLE PRECISION DEFAULT 0,
    take_profit_pct     DOUBLE PRECISION DEFAULT 0,
    stop_distance_usd   NUMERIC DEFAULT 0,

    -- Excursion analytics
    highest_price   NUMERIC NOT NULL,
    lowest_price    NUMERIC NOT NULL,
    mfe_pct         DOUBLE PRECISION DEFAULT 0,
    mae_pct         DOUBLE PRECISION DEFAULT 0,
    mfe_usd         NUMERIC DEFAULT 0,
    mae_usd         NUMERIC DEFAULT 0,

    -- PnL
    realized_pnl    NUMERIC,
    unrealized_pnl  NUMERIC DEFAULT 0,

    -- Exit
    exit_price      NUMERIC,
    exit_reason     TEXT,
    exit_detail     TEXT DEFAULT '',
    r_multiple      DOUBLE PRECISION,

    -- State history
    state_transitions JSONB NOT NULL DEFAULT '[]',
    metadata        JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON cte.positions (status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON cte.positions (symbol, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_positions_signal ON cte.positions (signal_id);
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

CREATE_EPOCH_DAILY_SUMMARY_TABLE = """
CREATE TABLE IF NOT EXISTS cte.epoch_daily_summary (
    date                DATE NOT NULL,
    epoch               TEXT NOT NULL,
    symbol              TEXT NOT NULL DEFAULT '_all',
    venue               TEXT NOT NULL DEFAULT '_all',
    tier                TEXT NOT NULL DEFAULT '_all',

    -- Counts
    trade_count         INTEGER NOT NULL DEFAULT 0,
    win_count           INTEGER NOT NULL DEFAULT 0,
    loss_count          INTEGER NOT NULL DEFAULT 0,

    -- PnL
    gross_profit        NUMERIC NOT NULL DEFAULT 0,
    gross_loss          NUMERIC NOT NULL DEFAULT 0,
    net_pnl             NUMERIC NOT NULL DEFAULT 0,
    avg_win             NUMERIC NOT NULL DEFAULT 0,
    avg_loss            NUMERIC NOT NULL DEFAULT 0,

    -- Ratios
    win_rate            DOUBLE PRECISION DEFAULT 0,
    expectancy          DOUBLE PRECISION DEFAULT 0,
    profit_factor       DOUBLE PRECISION,
    max_drawdown_pct    DOUBLE PRECISION DEFAULT 0,
    sharpe_ratio        DOUBLE PRECISION,

    -- Exit analysis
    saved_losers        INTEGER NOT NULL DEFAULT 0,
    killed_winners      INTEGER NOT NULL DEFAULT 0,
    no_progress_count   INTEGER NOT NULL DEFAULT 0,
    runner_count        INTEGER NOT NULL DEFAULT 0,

    -- Execution quality
    avg_hold_seconds    DOUBLE PRECISION DEFAULT 0,
    avg_r_multiple      DOUBLE PRECISION,
    avg_slippage_bps    DOUBLE PRECISION DEFAULT 0,
    avg_latency_ms      DOUBLE PRECISION DEFAULT 0,

    PRIMARY KEY (date, epoch, symbol, venue, tier)
);
"""

CREATE_TRADE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS cte.trade_log (
    time                TIMESTAMPTZ NOT NULL,
    trade_id            UUID NOT NULL DEFAULT uuid_generate_v4(),
    epoch               TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    venue               TEXT NOT NULL,
    tier                TEXT NOT NULL,
    pnl                 NUMERIC NOT NULL,
    exit_reason         TEXT NOT NULL,
    exit_layer          INTEGER NOT NULL,
    hold_seconds        INTEGER NOT NULL,
    r_multiple          DOUBLE PRECISION,
    entry_latency_ms    INTEGER NOT NULL DEFAULT 0,
    slippage_bps        DOUBLE PRECISION NOT NULL DEFAULT 0,
    mfe_pct             DOUBLE PRECISION NOT NULL DEFAULT 0,
    mae_pct             DOUBLE PRECISION NOT NULL DEFAULT 0,
    was_profitable      BOOLEAN NOT NULL DEFAULT false,
    position_mode       TEXT NOT NULL DEFAULT 'normal',
    position_id         UUID,
    signal_id           UUID
);

SELECT create_hypertable('cte.trade_log', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_trade_log_epoch ON cte.trade_log (epoch, time DESC);
CREATE INDEX IF NOT EXISTS idx_trade_log_symbol ON cte.trade_log (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_trade_log_exit ON cte.trade_log (exit_reason, time DESC);
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS cte.schema_version (
    version         TEXT PRIMARY KEY,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Streaming Features (multi-timeframe)
# ---------------------------------------------------------------------------

CREATE_STREAMING_FEATURES_TABLE = """
CREATE TABLE IF NOT EXISTS cte.streaming_features (
    time                TIMESTAMPTZ NOT NULL,
    event_id            UUID NOT NULL DEFAULT uuid_generate_v4(),
    symbol              TEXT NOT NULL,
    window_seconds      INTEGER NOT NULL,

    -- Core features
    returns             DOUBLE PRECISION,
    returns_z           DOUBLE PRECISION,
    momentum_z          DOUBLE PRECISION,
    taker_flow_imbalance DOUBLE PRECISION,
    spread_bps          DOUBLE PRECISION,
    spread_widening     DOUBLE PRECISION,
    ob_imbalance        DOUBLE PRECISION,
    liquidation_imbalance DOUBLE PRECISION,
    venue_divergence_bps DOUBLE PRECISION,
    vwap                DOUBLE PRECISION,

    -- Volume & activity
    trade_count         INTEGER NOT NULL DEFAULT 0,
    volume              DOUBLE PRECISION NOT NULL DEFAULT 0,
    buy_volume          DOUBLE PRECISION NOT NULL DEFAULT 0,
    sell_volume         DOUBLE PRECISION NOT NULL DEFAULT 0,
    window_fill_pct     DOUBLE PRECISION NOT NULL DEFAULT 0,

    -- Scalar / cross-timeframe (stored with window_seconds=0)
    execution_feasibility DOUBLE PRECISION,
    whale_risk_flag     BOOLEAN DEFAULT false,
    urgent_news_flag    BOOLEAN DEFAULT false,

    -- Freshness
    freshness_composite DOUBLE PRECISION,
    trade_age_ms        INTEGER,
    orderbook_age_ms    INTEGER,

    -- Raw reference values
    last_price          NUMERIC,
    best_bid            NUMERIC,
    best_ask            NUMERIC,
    mid_price           NUMERIC,
    mark_price          NUMERIC
);

SELECT create_hypertable('cte.streaming_features', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_sf_symbol_window_time
    ON cte.streaming_features (symbol, window_seconds, time DESC);
"""

CREATE_LIQUIDATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS cte.liquidations (
    time            TIMESTAMPTZ NOT NULL,
    event_id        UUID NOT NULL DEFAULT uuid_generate_v4(),
    venue           TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    price           NUMERIC NOT NULL,
    quantity        NUMERIC NOT NULL,
    is_long_liq     BOOLEAN NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable('cte.liquidations', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_liq_symbol_time ON cte.liquidations (symbol, time DESC);
"""

CREATE_MARK_PRICES_TABLE = """
CREATE TABLE IF NOT EXISTS cte.mark_prices (
    time            TIMESTAMPTZ NOT NULL,
    venue           TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    mark_price      NUMERIC NOT NULL,
    index_price     NUMERIC,
    funding_rate    NUMERIC
);

SELECT create_hypertable('cte.mark_prices', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_mark_symbol_time ON cte.mark_prices (symbol, time DESC);
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
    ("epoch_daily_summary", CREATE_EPOCH_DAILY_SUMMARY_TABLE),
    ("trade_log", CREATE_TRADE_LOG_TABLE),
    ("schema_version", CREATE_SCHEMA_VERSION_TABLE),
    ("streaming_features", CREATE_STREAMING_FEATURES_TABLE),
    ("liquidations", CREATE_LIQUIDATIONS_TABLE),
    ("mark_prices", CREATE_MARK_PRICES_TABLE),
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
