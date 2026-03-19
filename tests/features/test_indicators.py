"""Tests for technical indicator calculations."""
from __future__ import annotations

import numpy as np
import pytest

from cte.features.indicators import (
    bid_ask_spread_bps,
    ema,
    orderbook_imbalance,
    price_change_pct,
    rsi,
    vwap,
)


class TestRSI:
    def test_rsi_insufficient_data(self):
        prices = np.array([1.0, 2.0, 3.0])
        assert rsi(prices, period=14) is None

    def test_rsi_all_gains(self):
        prices = np.arange(1, 20, dtype=np.float64)
        result = rsi(prices, period=14)
        assert result is not None
        assert result == 100.0

    def test_rsi_normal_range(self):
        np.random.seed(42)
        prices = 50000 + np.cumsum(np.random.randn(100)) * 100
        result = rsi(prices, period=14)
        assert result is not None
        assert 0 <= result <= 100

    def test_rsi_mixed_movement(self):
        prices = np.array([100, 102, 101, 103, 102, 104, 103, 105,
                          104, 103, 102, 103, 104, 105, 106], dtype=np.float64)
        result = rsi(prices, period=14)
        assert result is not None
        assert 30 < result < 70


class TestEMA:
    def test_ema_insufficient_data(self):
        prices = np.array([1.0, 2.0])
        assert ema(prices, period=12) is None

    def test_ema_exact_period(self):
        prices = np.arange(1, 13, dtype=np.float64)
        result = ema(prices, period=12)
        assert result is not None
        assert result == np.mean(prices)

    def test_ema_gives_more_weight_to_recent(self):
        prices = np.array([10.0] * 20 + [20.0] * 5, dtype=np.float64)
        result = ema(prices, period=12)
        assert result is not None
        assert result > 10.0
        assert result < 20.0


class TestVWAP:
    def test_vwap_empty(self):
        assert vwap(np.array([]), np.array([])) is None

    def test_vwap_zero_volume(self):
        assert vwap(np.array([100.0]), np.array([0.0])) is None

    def test_vwap_equal_volume(self):
        prices = np.array([100.0, 200.0, 300.0])
        volumes = np.array([1.0, 1.0, 1.0])
        result = vwap(prices, volumes)
        assert result is not None
        assert result == pytest.approx(200.0)

    def test_vwap_weighted(self):
        prices = np.array([100.0, 200.0])
        volumes = np.array([3.0, 1.0])
        result = vwap(prices, volumes)
        assert result is not None
        assert result == pytest.approx(125.0)


class TestOrderbookImbalance:
    def test_balanced_book(self):
        bids = np.array([1.0, 1.0, 1.0])
        asks = np.array([1.0, 1.0, 1.0])
        result = orderbook_imbalance(bids, asks)
        assert result is not None
        assert result == pytest.approx(0.0)

    def test_buy_pressure(self):
        bids = np.array([3.0, 3.0])
        asks = np.array([1.0, 1.0])
        result = orderbook_imbalance(bids, asks)
        assert result is not None
        assert result > 0

    def test_sell_pressure(self):
        bids = np.array([1.0])
        asks = np.array([5.0])
        result = orderbook_imbalance(bids, asks)
        assert result is not None
        assert result < 0

    def test_empty_book(self):
        result = orderbook_imbalance(np.array([]), np.array([]))
        assert result is None


class TestBidAskSpread:
    def test_normal_spread(self):
        result = bid_ask_spread_bps(50000.0, 50001.0)
        assert result is not None
        assert result == pytest.approx(0.2, rel=0.1)

    def test_zero_price(self):
        assert bid_ask_spread_bps(0, 100.0) is None


class TestPriceChange:
    def test_price_change(self):
        prices = np.array([100.0, 102.0, 105.0, 110.0], dtype=np.float64)
        result = price_change_pct(prices, lookback=3)
        assert result is not None
        assert result == pytest.approx(0.10)

    def test_insufficient_data(self):
        prices = np.array([100.0])
        assert price_change_pct(prices, lookback=5) is None
