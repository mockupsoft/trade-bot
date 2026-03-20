"""Tests for the event normalizer."""
from __future__ import annotations

from decimal import Decimal

import pytest

from cte.core.events import (
    RawOrderbookEvent,
    RawTradeEvent,
    Side,
    Symbol,
    Venue,
)
from cte.core.exceptions import DataValidationError
from cte.normalizer.engine import EventNormalizer


class TestTradeNormalization:
    @pytest.mark.asyncio
    async def test_normalize_binance_trade(self, mock_publisher, sample_raw_trade):
        normalizer = EventNormalizer(mock_publisher)
        result = await normalizer.normalize_trade(sample_raw_trade)

        assert result is not None
        assert result.symbol == Symbol.BTCUSDT
        assert result.price == Decimal("50000.50")
        assert result.quantity == Decimal("0.001")
        assert result.side == Side.BUY
        assert result.venue == Venue.BINANCE

    @pytest.mark.asyncio
    async def test_normalize_buyer_maker_is_sell(self, mock_publisher):
        raw = RawTradeEvent(
            venue=Venue.BINANCE,
            symbol_raw="ETHUSDT",
            price="3000.00",
            quantity="1.0",
            trade_id="789",
            trade_time=1700000000000,
            is_buyer_maker=True,
        )
        normalizer = EventNormalizer(mock_publisher)
        result = await normalizer.normalize_trade(raw)

        assert result is not None
        assert result.side == Side.SELL

    @pytest.mark.asyncio
    async def test_normalize_unknown_symbol_returns_none(self, mock_publisher):
        raw = RawTradeEvent(
            venue=Venue.BINANCE,
            symbol_raw="NOTLISTEDUSDT",
            price="0.1",
            quantity="100",
            trade_id="999",
            trade_time=1700000000000,
            is_buyer_maker=False,
        )
        normalizer = EventNormalizer(mock_publisher)
        result = await normalizer.normalize_trade(raw)
        assert result is None

    @pytest.mark.asyncio
    async def test_normalize_invalid_price_raises(self, mock_publisher):
        raw = RawTradeEvent(
            venue=Venue.BINANCE,
            symbol_raw="BTCUSDT",
            price="not_a_number",
            quantity="1.0",
            trade_id="111",
            trade_time=1700000000000,
            is_buyer_maker=False,
        )
        normalizer = EventNormalizer(mock_publisher)
        with pytest.raises(DataValidationError):
            await normalizer.normalize_trade(raw)

    @pytest.mark.asyncio
    async def test_normalize_zero_price_raises(self, mock_publisher):
        raw = RawTradeEvent(
            venue=Venue.BINANCE,
            symbol_raw="BTCUSDT",
            price="0",
            quantity="1.0",
            trade_id="222",
            trade_time=1700000000000,
            is_buyer_maker=False,
        )
        normalizer = EventNormalizer(mock_publisher)
        with pytest.raises(DataValidationError):
            await normalizer.normalize_trade(raw)

    @pytest.mark.asyncio
    async def test_normalize_publishes_to_stream(self, mock_publisher, sample_raw_trade):
        normalizer = EventNormalizer(mock_publisher)
        await normalizer.normalize_trade(sample_raw_trade)
        assert mock_publisher.publish.called
        stream_key = mock_publisher.publish.call_args_list[0][0][0]
        assert stream_key == "cte:market:trade"


class TestOrderbookNormalization:
    @pytest.mark.asyncio
    async def test_normalize_orderbook(self, mock_publisher):
        raw = RawOrderbookEvent(
            venue=Venue.BINANCE,
            symbol_raw="BTCUSDT",
            event_type="snapshot",
            bids=[["50000", "1.0"], ["49999", "2.0"]],
            asks=[["50001", "0.5"], ["50002", "1.5"]],
            update_id=1000,
            venue_timestamp=1700000000000,
        )
        normalizer = EventNormalizer(mock_publisher)
        result = await normalizer.normalize_orderbook(raw)

        assert result is not None
        assert result.symbol == Symbol.BTCUSDT
        assert len(result.bids) == 2
        assert len(result.asks) == 2
        assert result.bids[0].price == Decimal("50000")
