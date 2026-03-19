"""Tests for pure-function feature computations."""
from __future__ import annotations

import pytest

from cte.features.accumulators import MomentumHistory, ReturnHistory, WindowState
from cte.features.formulas import (
    compute_execution_feasibility,
    compute_freshness,
    compute_liquidation_imbalance,
    compute_momentum_z,
    compute_ob_imbalance,
    compute_returns,
    compute_returns_z,
    compute_spread_bps,
    compute_spread_widening,
    compute_taker_flow_imbalance,
    compute_urgent_news_flag,
    compute_venue_divergence_bps,
    compute_vwap,
    compute_whale_risk_flag,
)
from cte.features.types import SecondBucket, VenueState, empty_bucket


def _filled_window(
    max_seconds: int,
    price_start: float = 100.0,
    price_step: float = 0.1,
    volume: float = 1.0,
    buy_ratio: float = 0.5,
) -> WindowState:
    """Build a WindowState filled with incrementing trades."""
    ws = WindowState(max_seconds=max_seconds)
    for i in range(max_seconds):
        b = empty_bucket(1000 + i)
        price = price_start + i * price_step
        is_buy = (i % 2 == 0) if buy_ratio == 0.5 else True
        b.add_trade(price, volume, is_buy)
        ws.push(b)
    return ws


class TestReturns:
    def test_positive_return(self):
        ws = _filled_window(10, price_start=100.0, price_step=1.0)
        ret = compute_returns(ws)
        assert ret is not None
        assert ret == pytest.approx(9.0 / 100.0)  # (109-100)/100

    def test_negative_return(self):
        ws = _filled_window(10, price_start=100.0, price_step=-0.5)
        ret = compute_returns(ws)
        assert ret is not None
        assert ret < 0

    def test_empty_window(self):
        ws = WindowState(max_seconds=10)
        assert compute_returns(ws) is None

    def test_single_bucket(self):
        ws = WindowState(max_seconds=10)
        b = empty_bucket(1)
        b.add_trade(100.0, 1.0, True)
        ws.push(b)
        assert compute_returns(ws) == pytest.approx(0.0)  # first==last


class TestReturnsZ:
    def test_none_when_no_history(self):
        rh = ReturnHistory(max_entries=100)
        assert compute_returns_z(0.01, rh) is None

    def test_with_history(self):
        rh = ReturnHistory(max_entries=100)
        for i in range(50):
            rh.push(0.001 * (i % 10))
        z = compute_returns_z(0.005, rh)
        assert z is not None

    def test_none_for_none_return(self):
        rh = ReturnHistory(max_entries=100)
        assert compute_returns_z(None, rh) is None


class TestMomentumZ:
    def test_with_buy_heavy_window(self):
        ws = _filled_window(10, buy_ratio=1.0)  # all buys
        mh = MomentumHistory(max_entries=50)
        for _ in range(30):
            mh.push(0.0)  # history of zero flow
        z = compute_momentum_z(ws, mh)
        # Current flow is all-buy, history is zero → should be positive z
        if z is not None:
            assert z > 0


class TestTakerFlowImbalance:
    def test_all_buys(self):
        ws = WindowState(max_seconds=3)
        for i in range(3):
            b = empty_bucket(i)
            b.add_trade(100.0, 1.0, is_buy=True)
            ws.push(b)
        tfi = compute_taker_flow_imbalance(ws)
        assert tfi is not None
        assert tfi == pytest.approx(1.0)

    def test_all_sells(self):
        ws = WindowState(max_seconds=3)
        for i in range(3):
            b = empty_bucket(i)
            b.add_trade(100.0, 1.0, is_buy=False)
            ws.push(b)
        tfi = compute_taker_flow_imbalance(ws)
        assert tfi == pytest.approx(-1.0)

    def test_balanced(self):
        ws = WindowState(max_seconds=2)
        b1 = empty_bucket(0)
        b1.add_trade(100.0, 5.0, True)
        b2 = empty_bucket(1)
        b2.add_trade(100.0, 5.0, False)
        ws.push(b1)
        ws.push(b2)
        tfi = compute_taker_flow_imbalance(ws)
        assert tfi == pytest.approx(0.0)

    def test_empty(self):
        ws = WindowState(max_seconds=3)
        ws.push(empty_bucket(0))
        assert compute_taker_flow_imbalance(ws) is None


class TestSpread:
    def test_spread_bps(self):
        ws = WindowState(max_seconds=3)
        for i in range(3):
            b = empty_bucket(i)
            b.add_trade(100.0, 1.0, True)
            b.add_spread(float(i + 1))
            ws.push(b)
        # Latest bucket spread = 3.0
        assert compute_spread_bps(ws) == 3.0

    def test_spread_widening_above_one(self):
        ws = WindowState(max_seconds=4)
        for i in range(3):
            b = empty_bucket(i)
            b.add_trade(100.0, 1.0, True)
            b.add_spread(2.0)  # avg = 2.0
            ws.push(b)
        b = empty_bucket(3)
        b.add_trade(100.0, 1.0, True)
        b.add_spread(4.0)  # current = 4.0, avg ≈ 2.5
        ws.push(b)
        widening = compute_spread_widening(ws)
        assert widening is not None
        assert widening > 1.0  # spread widened

    def test_spread_widening_below_one(self):
        ws = WindowState(max_seconds=3)
        for i in range(2):
            b = empty_bucket(i)
            b.add_trade(100.0, 1.0, True)
            b.add_spread(5.0)
            ws.push(b)
        b = empty_bucket(2)
        b.add_trade(100.0, 1.0, True)
        b.add_spread(2.0)  # tightened
        ws.push(b)
        widening = compute_spread_widening(ws)
        assert widening is not None
        assert widening < 1.0

    def test_no_spread_data(self):
        ws = WindowState(max_seconds=3)
        ws.push(_make_trade_bucket(0))
        assert compute_spread_bps(ws) is None


class TestOrderbookImbalance:
    def test_bid_heavy(self):
        ws = WindowState(max_seconds=3)
        b = empty_bucket(0)
        b.add_orderbook(10.0, 5.0)
        ws.push(b)
        obi = compute_ob_imbalance(ws)
        assert obi is not None
        assert obi > 0  # bid > ask

    def test_ask_heavy(self):
        ws = WindowState(max_seconds=3)
        b = empty_bucket(0)
        b.add_orderbook(3.0, 9.0)
        ws.push(b)
        obi = compute_ob_imbalance(ws)
        assert obi is not None
        assert obi < 0

    def test_balanced(self):
        ws = WindowState(max_seconds=3)
        b = empty_bucket(0)
        b.add_orderbook(5.0, 5.0)
        ws.push(b)
        obi = compute_ob_imbalance(ws)
        assert obi == pytest.approx(0.0)

    def test_no_orderbook(self):
        ws = WindowState(max_seconds=3)
        ws.push(empty_bucket(0))
        assert compute_ob_imbalance(ws) is None


class TestLiquidationImbalance:
    def test_long_heavy(self):
        ws = WindowState(max_seconds=3)
        b = empty_bucket(0)
        b.add_liquidation(10.0, is_long_liq=True)
        b.add_liquidation(2.0, is_long_liq=False)
        ws.push(b)
        li = compute_liquidation_imbalance(ws)
        assert li is not None
        assert li > 0  # more long liqs (bearish)

    def test_no_liquidations(self):
        ws = WindowState(max_seconds=3)
        ws.push(empty_bucket(0))
        assert compute_liquidation_imbalance(ws) is None


class TestVenueDivergence:
    def test_divergence_when_binance_higher(self):
        binance = VenueState()
        bybit = VenueState()
        binance.update_book(100.0, 100.2, 1000)
        bybit.update_book(99.8, 100.0, 1000)
        div = compute_venue_divergence_bps(binance, bybit)
        assert div is not None
        assert div > 0  # binance mid > bybit mid

    def test_zero_divergence(self):
        binance = VenueState()
        bybit = VenueState()
        binance.update_book(100.0, 100.0, 1000)
        bybit.update_book(100.0, 100.0, 1000)
        div = compute_venue_divergence_bps(binance, bybit)
        assert div == pytest.approx(0.0)

    def test_stale_venue(self):
        binance = VenueState()
        bybit = VenueState()
        binance.update_book(100.0, 100.2, 1000)
        assert compute_venue_divergence_bps(binance, bybit) is None


class TestFreshness:
    def test_fully_fresh(self):
        now = 10_000
        result = compute_freshness(
            now_ms=now,
            last_trade_ms=now - 100,
            last_ob_ms=now - 200,
            binance_ms=now - 100,
            bybit_ms=now - 500,
        )
        assert result["composite"] > 0.9

    def test_stale_data(self):
        now = 100_000
        result = compute_freshness(
            now_ms=now,
            last_trade_ms=now - 60_000,  # 60s old
            last_ob_ms=now - 60_000,
            binance_ms=now - 60_000,
            bybit_ms=now - 60_000,
        )
        assert result["composite"] == 0.0

    def test_no_data(self):
        result = compute_freshness(
            now_ms=10_000,
            last_trade_ms=0,
            last_ob_ms=0,
            binance_ms=0,
            bybit_ms=0,
        )
        assert result["composite"] == 0.0


class TestExecutionFeasibility:
    def test_good_conditions(self):
        feas = compute_execution_feasibility(
            spread_bps=1.0,
            ob_bid_qty=2.0,
            ob_ask_qty=2.0,
            freshness_composite=0.99,
            symbol="BTCUSDT",
        )
        assert feas is not None
        assert feas > 0.8

    def test_wide_spread_tanks_score(self):
        feas = compute_execution_feasibility(
            spread_bps=18.0,
            ob_bid_qty=2.0,
            ob_ask_qty=2.0,
            freshness_composite=0.99,
            symbol="BTCUSDT",
        )
        assert feas is not None
        assert feas < 0.2

    def test_no_depth_tanks_score(self):
        feas = compute_execution_feasibility(
            spread_bps=1.0,
            ob_bid_qty=0.001,
            ob_ask_qty=0.001,
            freshness_composite=0.99,
            symbol="BTCUSDT",
        )
        assert feas is not None
        assert feas < 0.1

    def test_none_spread(self):
        assert compute_execution_feasibility(
            None, 1.0, 1.0, 0.99, "BTCUSDT"
        ) is None


class TestWhaleRiskFlag:
    def test_recent_whale(self):
        now = 100_000
        assert compute_whale_risk_flag(now - 30_000, now) is True

    def test_old_whale(self):
        now = 100_000
        assert compute_whale_risk_flag(now - 7_200_000, now) is False  # 2h ago

    def test_no_whale(self):
        assert compute_whale_risk_flag(0, 100_000) is False


class TestUrgentNewsFlag:
    def test_recent_news(self):
        now = 100_000
        assert compute_urgent_news_flag(now - 60_000, now) is True  # 1min ago

    def test_old_news(self):
        now = 100_000
        assert compute_urgent_news_flag(now - 7_200_000, now) is False

    def test_no_news(self):
        assert compute_urgent_news_flag(0, 100_000) is False


class TestVWAP:
    def test_simple_vwap(self):
        ws = WindowState(max_seconds=3)
        for i in range(3):
            b = empty_bucket(i)
            b.add_trade(100.0 + i * 10, 1.0, True)  # 100, 110, 120 x 1.0 each
            ws.push(b)
        v = compute_vwap(ws)
        assert v is not None
        assert v == pytest.approx(110.0)

    def test_volume_weighted(self):
        ws = WindowState(max_seconds=2)
        b1 = empty_bucket(0)
        b1.add_trade(100.0, 3.0, True)   # 300
        ws.push(b1)
        b2 = empty_bucket(1)
        b2.add_trade(200.0, 1.0, True)   # 200
        ws.push(b2)
        v = compute_vwap(ws)
        assert v == pytest.approx(125.0)  # 500/4

    def test_empty(self):
        ws = WindowState(max_seconds=3)
        ws.push(empty_bucket(0))
        assert compute_vwap(ws) is None


# ── Helper ──────────────────────────────────────────────────────

def _make_trade_bucket(ts: int) -> SecondBucket:
    b = empty_bucket(ts)
    b.add_trade(100.0, 1.0, True)
    return b
