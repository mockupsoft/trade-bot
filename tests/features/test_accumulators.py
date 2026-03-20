"""Tests for incremental accumulator data structures."""
from __future__ import annotations

import pytest

from cte.features.accumulators import (
    MomentumHistory,
    ReturnHistory,
    RunningTotals,
    WindowState,
)
from cte.features.types import SecondBucket, empty_bucket


def _make_bucket(ts: int, price: float = 100.0, volume: float = 1.0, is_buy: bool = True) -> SecondBucket:
    """Helper to create a bucket with one trade."""
    b = empty_bucket(ts)
    b.add_trade(price, volume, is_buy)
    return b


def _make_bucket_with_spread(ts: int, spread_bps: float = 2.0) -> SecondBucket:
    b = empty_bucket(ts)
    b.add_trade(100.0, 1.0, True)
    b.add_spread(spread_bps)
    return b


def _make_bucket_with_liq(ts: int, long_vol: float = 0.0, short_vol: float = 0.0) -> SecondBucket:
    b = empty_bucket(ts)
    if long_vol > 0:
        b.add_liquidation(long_vol, is_long_liq=True)
    if short_vol > 0:
        b.add_liquidation(short_vol, is_long_liq=False)
    return b


class TestSecondBucket:
    def test_empty_bucket(self):
        b = empty_bucket(1000)
        assert b.is_empty
        assert b.trade_count == 0
        assert b.ts == 1000

    def test_add_trade_updates_ohlc(self):
        b = empty_bucket(1)
        b.add_trade(100.0, 1.0, True)
        assert b.open_price == 100.0
        assert b.close_price == 100.0
        assert b.high_price == 100.0
        assert b.low_price == 100.0
        assert b.trade_count == 1
        assert b.volume == 1.0
        assert b.buy_volume == 1.0
        assert b.sell_volume == 0.0

        b.add_trade(110.0, 0.5, False)
        assert b.open_price == 100.0
        assert b.close_price == 110.0
        assert b.high_price == 110.0
        assert b.low_price == 100.0
        assert b.trade_count == 2
        assert b.sell_volume == 0.5

        b.add_trade(95.0, 2.0, True)
        assert b.low_price == 95.0
        assert b.close_price == 95.0

    def test_vwap(self):
        b = empty_bucket(1)
        b.add_trade(100.0, 3.0, True)   # 300
        b.add_trade(200.0, 1.0, False)  # 200
        # vwap = (300 + 200) / 4 = 125
        assert b.vwap == pytest.approx(125.0)

    def test_add_spread(self):
        b = empty_bucket(1)
        b.add_spread(2.0)
        b.add_spread(4.0)
        assert b.avg_spread_bps == pytest.approx(3.0)
        assert b.last_spread_bps == 4.0

    def test_add_orderbook(self):
        b = empty_bucket(1)
        b.add_orderbook(10.0, 8.0)
        assert b.last_bid_qty == 10.0
        assert b.last_ask_qty == 8.0
        b.add_orderbook(12.0, 6.0)
        assert b.last_bid_qty == 12.0
        assert b.ob_bid_qty_sum == 22.0

    def test_add_liquidation(self):
        b = empty_bucket(1)
        b.add_liquidation(5.0, is_long_liq=True)
        b.add_liquidation(3.0, is_long_liq=False)
        assert b.liq_long_vol == 5.0
        assert b.liq_short_vol == 3.0
        assert b.liq_count == 2

    def test_copy(self):
        b = empty_bucket(1)
        b.add_trade(100.0, 1.0, True)
        c = b.copy()
        assert c.trade_count == 1
        assert c.close_price == 100.0
        c.add_trade(200.0, 1.0, False)
        assert b.trade_count == 1  # original unchanged
        assert c.trade_count == 2


class TestRunningTotals:
    def test_add_and_subtract_are_inverse(self):
        t = RunningTotals()
        b = _make_bucket(1, price=100.0, volume=2.0, is_buy=True)
        t.add(b)
        assert t.volume == pytest.approx(2.0)
        assert t.buy_volume == pytest.approx(2.0)
        assert t.trade_count == 1
        assert t.active_seconds == 1

        t.subtract(b)
        assert t.volume == pytest.approx(0.0)
        assert t.buy_volume == pytest.approx(0.0)
        assert t.trade_count == 0
        assert t.active_seconds == 0

    def test_empty_bucket_doesnt_count_as_active(self):
        t = RunningTotals()
        t.add(empty_bucket(1))
        assert t.active_seconds == 0

    def test_accumulates_liquidations(self):
        t = RunningTotals()
        b = _make_bucket_with_liq(1, long_vol=10.0, short_vol=5.0)
        t.add(b)
        assert t.liq_long_vol == pytest.approx(10.0)
        assert t.liq_short_vol == pytest.approx(5.0)


class TestWindowState:
    def test_push_and_evict(self):
        ws = WindowState(max_seconds=3)

        ws.push(_make_bucket(1, price=100.0, volume=1.0))
        ws.push(_make_bucket(2, price=110.0, volume=2.0))
        ws.push(_make_bucket(3, price=120.0, volume=3.0))
        assert ws.size == 3
        assert ws.totals.volume == pytest.approx(6.0)

        # 4th push evicts bucket 1 (volume=1.0)
        ws.push(_make_bucket(4, price=130.0, volume=4.0))
        assert ws.size == 3
        assert ws.totals.volume == pytest.approx(9.0)  # 2+3+4

    def test_first_and_last_price(self):
        ws = WindowState(max_seconds=5)
        ws.push(_make_bucket(1, price=100.0))
        ws.push(empty_bucket(2))
        ws.push(_make_bucket(3, price=120.0))

        assert ws.first_price() == 100.0
        assert ws.last_price() == 120.0

    def test_first_price_skips_empty(self):
        ws = WindowState(max_seconds=5)
        ws.push(empty_bucket(1))
        ws.push(_make_bucket(2, price=200.0))
        assert ws.first_price() == 200.0

    def test_empty_window(self):
        ws = WindowState(max_seconds=3)
        assert ws.first_price() is None
        assert ws.last_price() is None
        assert ws.latest_spread_bps() is None
        assert ws.fill_pct == 0.0
        assert not ws.is_full

    def test_fill_pct(self):
        ws = WindowState(max_seconds=4)
        ws.push(_make_bucket(1))
        ws.push(empty_bucket(2))
        ws.push(_make_bucket(3))
        ws.push(empty_bucket(4))
        assert ws.fill_pct == pytest.approx(0.5)

    def test_is_full(self):
        ws = WindowState(max_seconds=3)
        ws.push(_make_bucket(1))
        ws.push(_make_bucket(2))
        assert not ws.is_full
        ws.push(_make_bucket(3))
        assert ws.is_full

    def test_latest_spread(self):
        ws = WindowState(max_seconds=5)
        ws.push(_make_bucket_with_spread(1, spread_bps=2.0))
        ws.push(empty_bucket(2))
        ws.push(_make_bucket_with_spread(3, spread_bps=5.0))
        assert ws.latest_spread_bps() == 5.0

    def test_latest_ob_snapshot(self):
        ws = WindowState(max_seconds=3)
        b = empty_bucket(1)
        b.add_orderbook(10.0, 8.0)
        ws.push(b)
        assert ws.latest_ob_snapshot() == (10.0, 8.0)


class TestReturnHistory:
    def test_z_score_insufficient_data(self):
        rh = ReturnHistory(max_entries=100)
        for i in range(5):
            rh.push(0.01 * i)
        assert rh.z_score(0.05) is None  # < 10 samples

    def test_z_score_with_sufficient_data(self):
        rh = ReturnHistory(max_entries=100)
        for _ in range(50):
            rh.push(0.001)
        # All identical → std ≈ 0, z_score → None
        assert rh.z_score(0.001) is None

    def test_z_score_with_variance(self):
        rh = ReturnHistory(max_entries=100)
        for i in range(50):
            rh.push(float(i) * 0.001)
        z = rh.z_score(0.025)  # mid-range value
        assert z is not None
        assert -3.0 < z < 3.0

    def test_z_score_outlier(self):
        rh = ReturnHistory(max_entries=100)
        for i in range(50):
            rh.push(float(i % 5) * 0.001)
        z = rh.z_score(0.1)  # way above range
        assert z is not None
        assert z > 2.0

    def test_ring_buffer_eviction(self):
        rh = ReturnHistory(max_entries=10)
        for i in range(20):
            rh.push(float(i))
        assert rh.count == 10
        assert rh.mean == pytest.approx(14.5)  # mean of 10..19

    def test_recompute_corrects_drift(self):
        rh = ReturnHistory(max_entries=50)
        for i in range(600):
            rh.push(float(i % 7) * 0.01)
        # After 600 pushes, recompute should have fired once
        # Mean should be correct
        expected_mean = sum(float(i % 7) * 0.01 for i in range(550, 600)) / 50
        assert rh.mean == pytest.approx(expected_mean, abs=0.001)


class TestMomentumHistory:
    def test_push_and_zscore(self):
        mh = MomentumHistory(max_entries=50)
        for i in range(30):
            mh.push(float(i) * 0.1)
        z = mh.z_score(5.0)
        assert z is not None
        assert z > 0
