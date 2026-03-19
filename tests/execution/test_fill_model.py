"""Tests for fill model computations."""
from __future__ import annotations

from decimal import Decimal

import pytest

from cte.execution.fill_model import BookLevel, FillMode, FillResult, compute_fill


class TestSpreadCrossingFill:
    def test_buy_fills_above_ask(self):
        result = compute_fill(
            side="buy", quantity=Decimal("1"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=5,
        )
        assert result.fill_price > Decimal("50002")
        assert result.model_used == FillMode.SPREAD_CROSSING

    def test_sell_fills_below_bid(self):
        result = compute_fill(
            side="sell", quantity=Decimal("1"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=5,
        )
        assert result.fill_price < Decimal("50000")

    def test_zero_slippage_fills_at_touch(self):
        result = compute_fill(
            side="buy", quantity=Decimal("1"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=0,
        )
        assert result.fill_price == Decimal("50002.00")

    def test_sell_zero_slippage(self):
        result = compute_fill(
            side="sell", quantity=Decimal("1"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=0,
        )
        assert result.fill_price == Decimal("50000.00")

    def test_slippage_bps_tracked(self):
        result = compute_fill(
            side="buy", quantity=Decimal("1"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=10,
        )
        assert result.slippage_bps > 0

    def test_buy_always_worse_than_sell(self):
        buy = compute_fill("buy", Decimal("1"), Decimal("50000"), Decimal("50002"), 5)
        sell = compute_fill("sell", Decimal("1"), Decimal("50000"), Decimal("50002"), 5)
        assert buy.fill_price > sell.fill_price

    def test_invalid_book_raises(self):
        with pytest.raises(ValueError):
            compute_fill("buy", Decimal("1"), Decimal("0"), Decimal("50002"), 5)


class TestVWAPDepthFill:
    def test_small_order_fills_at_best(self):
        levels = [
            BookLevel(price=Decimal("50002"), quantity=Decimal("10")),
            BookLevel(price=Decimal("50005"), quantity=Decimal("10")),
        ]
        result = compute_fill(
            side="buy", quantity=Decimal("1"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=0,
            mode=FillMode.VWAP_DEPTH,
            book_levels=levels,
        )
        # Small order fills entirely at first level
        assert result.fill_price == Decimal("50002.00")
        assert result.model_used == FillMode.VWAP_DEPTH

    def test_large_order_walks_book(self):
        levels = [
            BookLevel(price=Decimal("50002"), quantity=Decimal("5")),
            BookLevel(price=Decimal("50010"), quantity=Decimal("5")),
        ]
        result = compute_fill(
            side="buy", quantity=Decimal("10"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=0,
            mode=FillMode.VWAP_DEPTH,
            book_levels=levels,
        )
        # VWAP of (5*50002 + 5*50010) / 10 = 50006
        assert result.fill_price == Decimal("50006.00")

    def test_order_exceeds_book_depth(self):
        levels = [
            BookLevel(price=Decimal("50002"), quantity=Decimal("2")),
        ]
        result = compute_fill(
            side="buy", quantity=Decimal("10"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=0,
            mode=FillMode.VWAP_DEPTH,
            book_levels=levels,
        )
        # Fill remaining at worst known price
        assert result.fill_price >= Decimal("50002")

    def test_falls_back_to_spread_without_levels(self):
        result = compute_fill(
            side="buy", quantity=Decimal("1"),
            best_bid=Decimal("50000"), best_ask=Decimal("50002"),
            slippage_bps=5,
            mode=FillMode.VWAP_DEPTH,
            book_levels=None,
        )
        # Falls back to spread crossing when no levels given
        assert result.model_used == FillMode.SPREAD_CROSSING


class TestWorstCaseFill:
    def test_double_slippage(self):
        normal = compute_fill(
            "buy", Decimal("1"), Decimal("50000"), Decimal("50002"), 5,
            mode=FillMode.SPREAD_CROSSING,
        )
        worst = compute_fill(
            "buy", Decimal("1"), Decimal("50000"), Decimal("50002"), 5,
            mode=FillMode.WORST_CASE,
        )
        assert worst.fill_price > normal.fill_price
        assert worst.model_used == FillMode.WORST_CASE

    def test_sell_worst_case(self):
        normal = compute_fill(
            "sell", Decimal("1"), Decimal("50000"), Decimal("50002"), 5,
            mode=FillMode.SPREAD_CROSSING,
        )
        worst = compute_fill(
            "sell", Decimal("1"), Decimal("50000"), Decimal("50002"), 5,
            mode=FillMode.WORST_CASE,
        )
        assert worst.fill_price < normal.fill_price
