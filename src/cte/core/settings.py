"""CTE configuration management using Pydantic Settings.

Loads configuration from environment variables, .env files, and defaults.toml.
Environment variables take highest precedence.
"""
from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineMode(StrEnum):
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


class Direction(StrEnum):
    LONG_ONLY = "long_only"


class SizingMethod(StrEnum):
    FIXED_FRACTION = "fixed_fraction"
    KELLY = "kelly"


class ExecutionMode(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class EngineSettings(BaseSettings):
    mode: EngineMode = EngineMode.PAPER
    symbols: list[str] = Field(default=["BTCUSDT", "ETHUSDT"])
    direction: Direction = Direction.LONG_ONLY
    max_leverage: int = Field(default=3, ge=1, le=5)
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_prefix="CTE_ENGINE_")


class BinanceSettings(BaseSettings):
    ws_base_url: str = "wss://fstream.binance.com"
    ws_combined_url: str = "wss://fstream.binance.com/stream"
    rest_base_url: str = "https://fapi.binance.com"
    testnet_ws_url: str = "wss://stream.binancefuture.com"
    testnet_rest_url: str = "https://testnet.binancefuture.com"
    streams: list[str] = Field(default=[
        "btcusdt@trade",
        "btcusdt@depth20@100ms",
        "ethusdt@trade",
        "ethusdt@depth20@100ms",
    ])
    ping_interval_sec: int = 180
    reconnect_base_sec: float = 1.0
    reconnect_max_sec: float = 60.0
    max_connections_per_ip: int = 5

    model_config = SettingsConfigDict(env_prefix="CTE_BINANCE_")


class BybitSettings(BaseSettings):
    ws_base_url: str = "wss://stream.bybit.com/v5/public/linear"
    testnet_ws_url: str = "wss://stream-testnet.bybit.com/v5/public/linear"
    rest_base_url: str = "https://api.bybit.com"
    topics: list[str] = Field(default=[
        "publicTrade.BTCUSDT",
        "publicTrade.ETHUSDT",
        "orderbook.50.BTCUSDT",
        "orderbook.50.ETHUSDT",
    ])
    ping_interval_sec: int = 20
    reconnect_base_sec: float = 1.0
    reconnect_max_sec: float = 60.0
    max_subscriptions_per_connection: int = 10

    model_config = SettingsConfigDict(env_prefix="CTE_BYBIT_")


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379/0"
    max_connections: int = 20
    stream_max_len: int = 100_000
    consumer_group: str = "cte-engine"
    consumer_name: str = "cte-worker-1"
    block_ms: int = 5000

    model_config = SettingsConfigDict(env_prefix="CTE_REDIS_")


class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    name: str = "cte"
    user: str = "cte"
    password: str = ""
    min_pool_size: int = 5
    max_pool_size: int = 20
    statement_cache_size: int = 100

    model_config = SettingsConfigDict(env_prefix="CTE_DB_")

    @property
    def dsn(self) -> str:
        pw = f":{self.password}" if self.password else ""
        return f"postgresql://{self.user}{pw}@{self.host}:{self.port}/{self.name}"


class FeatureSettings(BaseSettings):
    # Legacy indicator settings (used by old FeatureEngine for backward compat)
    rsi_period: int = 14
    ema_fast_period: int = 12
    ema_slow_period: int = 26
    vwap_window_minutes: int = 60
    volume_profile_bins: int = 50
    window_size_minutes: int = 240

    # Streaming feature engine settings
    streaming_windows: list[int] = Field(default=[10, 30, 60, 300])
    emit_interval_seconds: int = 1
    zscore_min_samples: int = 10
    warmup_seconds: int = 300
    persist_interval_seconds: int = 10
    whale_lookback_minutes: int = 60
    news_lookback_minutes: int = 30

    model_config = SettingsConfigDict(env_prefix="CTE_FEATURES_")


class SignalSettings(BaseSettings):
    # Legacy settings (kept for backward compat with old SignalEngine)
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    cooldown_seconds: int = 300
    max_signals_per_hour: int = 10

    # Scoring signal engine weights (must sum to 1.0)
    w_momentum: float = 0.35
    w_orderflow: float = 0.25
    w_liquidation: float = 0.10
    w_microstructure: float = 0.20
    w_cross_venue: float = 0.10

    # Tier thresholds
    tier_a_threshold: float = 0.72
    tier_b_threshold: float = 0.55
    tier_c_threshold: float = 0.40

    # Hard gate thresholds
    gate_min_freshness: float = 0.5
    gate_max_spread_bps: float = 15.0
    gate_max_divergence_bps: float = 50.0
    gate_min_feasibility: float = 0.3

    model_config = SettingsConfigDict(env_prefix="CTE_SIGNALS_")


class RiskSettings(BaseSettings):
    max_position_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    max_total_exposure_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    max_daily_drawdown_pct: float = Field(default=0.03, ge=0.0, le=1.0)
    max_correlation: float = Field(default=0.85, ge=0.0, le=1.0)
    emergency_stop_drawdown_pct: float = Field(default=0.05, ge=0.0, le=1.0)

    model_config = SettingsConfigDict(env_prefix="CTE_RISK_")


class SizingSettings(BaseSettings):
    method: SizingMethod = SizingMethod.FIXED_FRACTION
    fixed_fraction_pct: float = Field(default=0.02, ge=0.0, le=1.0)
    kelly_half: bool = True
    min_order_usd: float = 10.0
    max_order_usd: float = 1000.0

    model_config = SettingsConfigDict(env_prefix="CTE_SIZING_")


class ExecutionSettings(BaseSettings):
    mode: ExecutionMode = ExecutionMode.PAPER
    slippage_bps: int = 5
    fill_delay_ms: int = 100
    max_retries: int = 3
    retry_delay_sec: float = 1.0
    fill_model: str = "spread_crossing"   # spread_crossing | vwap_depth | worst_case
    fee_bps: int = 4                      # taker fee in basis points

    model_config = SettingsConfigDict(env_prefix="CTE_EXECUTION_")


class ExitSettings(BaseSettings):
    trailing_stop_pct: float = Field(default=0.015, ge=0.0)
    take_profit_pct: float = Field(default=0.03, ge=0.0)
    stop_loss_pct: float = Field(default=0.02, ge=0.0)
    max_hold_minutes: int = 1440
    invalidation_check_interval_sec: int = 30

    model_config = SettingsConfigDict(env_prefix="CTE_EXITS_")


class MonitoringSettings(BaseSettings):
    prometheus_port: int = 9090
    health_check_interval_sec: int = 30
    metrics_export_interval_sec: int = 15

    model_config = SettingsConfigDict(env_prefix="CTE_MONITORING_")


class CTESettings(BaseSettings):
    """Root settings aggregating all subsystem configurations."""

    engine: EngineSettings = Field(default_factory=EngineSettings)
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    bybit: BybitSettings = Field(default_factory=BybitSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    signals: SignalSettings = Field(default_factory=SignalSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    sizing: SizingSettings = Field(default_factory=SizingSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    exits: ExitSettings = Field(default_factory=ExitSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)

    model_config = SettingsConfigDict(env_prefix="CTE_")

    @model_validator(mode="after")
    def validate_execution_mode_matches_engine(self) -> Self:
        if self.engine.mode == EngineMode.PAPER and self.execution.mode != ExecutionMode.PAPER:
            msg = "Engine mode is 'paper' but execution mode is not 'paper'"
            raise ValueError(msg)
        if self.engine.mode == EngineMode.LIVE and self.execution.mode != ExecutionMode.LIVE:
            msg = "Engine mode is 'live' but execution mode is not 'live'"
            raise ValueError(msg)
        return self

    @classmethod
    def from_toml(cls, path: Path | None = None) -> CTESettings:
        """Load settings from TOML file, then overlay env vars."""
        if path is None:
            path = Path(__file__).parent.parent.parent.parent / "config" / "defaults.toml"
        if path.exists():
            with open(path, "rb") as f:
                _data = tomllib.load(f)
        return cls()


def get_settings() -> CTESettings:
    """Factory function for dependency injection."""
    return CTESettings()
