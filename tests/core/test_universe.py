"""Tests for canonical trading universe helpers."""
from __future__ import annotations

from cte.core.universe import (
    DEFAULT_TRADING_SYMBOLS,
    binance_futures_default_streams,
    expand_legacy_engine_symbols,
    merge_market_feed_symbols,
)


def test_expand_legacy_btc_eth_to_default() -> None:
    out = expand_legacy_engine_symbols(["BTCUSDT", "ETHUSDT"])
    assert out == list(DEFAULT_TRADING_SYMBOLS)
    assert len(out) == 10


def test_expand_respects_order_eth_btc() -> None:
    out = expand_legacy_engine_symbols(["ETHUSDT", "BTCUSDT"])
    assert out == list(DEFAULT_TRADING_SYMBOLS)


def test_expand_custom_universe_unchanged() -> None:
    custom = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    assert expand_legacy_engine_symbols(custom) == custom


def test_streams_count_matches_three_per_symbol() -> None:
    streams = binance_futures_default_streams(DEFAULT_TRADING_SYMBOLS)
    assert len(streams) == 3 * len(DEFAULT_TRADING_SYMBOLS)


def test_merge_market_feed_symbols_includes_full_default_universe() -> None:
    out = merge_market_feed_symbols(["ADAUSDT"])
    assert out == list(DEFAULT_TRADING_SYMBOLS)
    assert len(out) == 10
