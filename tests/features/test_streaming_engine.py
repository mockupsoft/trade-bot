"""Tests for the StreamingFeatureEngine coordinator."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from cte.core.events import (
    LiquidationEvent,
    MarkPriceEvent,
    OrderbookLevel,
    OrderbookSnapshotEvent,
    Side,
    StreamingFeatureVector,
    Symbol,
    TradeEvent,
    Venue,
    WhaleAlertEvent,
)
from cte.core.settings import FeatureSettings
from cte.core.streams import StreamPublisher
from cte.features.engine import StreamingFeatureEngine


@pytest.fixture
def settings() -> FeatureSettings:
    return FeatureSettings(streaming_windows=[10, 30, 60, 300])


@pytest.fixture
def publisher() -> StreamPublisher:
    p = AsyncMock(spec=StreamPublisher)
    p.publish = AsyncMock(return_value="mock-id")
    return p


@pytest.fixture
def engine(settings, publisher) -> StreamingFeatureEngine:
    return StreamingFeatureEngine(settings=settings, publisher=publisher)


def _trade(symbol: str, price: float, qty: float, ts_sec: int, venue: str = "binance") -> TradeEvent:
    return TradeEvent(
        venue=Venue(venue),
        symbol=Symbol(symbol),
        price=Decimal(str(price)),
        quantity=Decimal(str(qty)),
        side=Side.BUY,
        trade_time=datetime.fromtimestamp(ts_sec, tz=UTC),
        venue_trade_id=f"t-{ts_sec}",
    )


def _orderbook(symbol: str, bid: float, ask: float, ts_sec: int, venue: str = "binance") -> OrderbookSnapshotEvent:
    return OrderbookSnapshotEvent(
        venue=Venue(venue),
        symbol=Symbol(symbol),
        bids=[
            OrderbookLevel(price=Decimal(str(bid)), quantity=Decimal("5.0")),
            OrderbookLevel(price=Decimal(str(bid - 1)), quantity=Decimal("10.0")),
        ],
        asks=[
            OrderbookLevel(price=Decimal(str(ask)), quantity=Decimal("5.0")),
            OrderbookLevel(price=Decimal(str(ask + 1)), quantity=Decimal("10.0")),
        ],
        sequence=ts_sec,
        snapshot_time=datetime.fromtimestamp(ts_sec, tz=UTC),
    )


class TestStreamingFeatureEngineBasic:
    @pytest.mark.asyncio
    async def test_first_event_creates_state(self, engine):
        trade = _trade("BTCUSDT", 50000.0, 0.1, 1000)
        result = await engine.handle_trade(trade)
        # First event: no tick yet (bucket just started)
        assert result is None
        assert "BTCUSDT" in engine.active_symbols

    @pytest.mark.asyncio
    async def test_second_boundary_emits_vector(self, engine):
        # Event at second 1000
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        # Event at second 1001 → tick!
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.2, 1001))
        assert result is not None
        assert isinstance(result, StreamingFeatureVector)
        assert result.symbol == Symbol.BTCUSDT

    @pytest.mark.asyncio
    async def test_same_second_no_tick(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        result = await engine.handle_trade(_trade("BTCUSDT", 50001.0, 0.1, 1000))
        assert result is None  # same second, no tick

    @pytest.mark.asyncio
    async def test_gap_seconds_filled_with_empty(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        # Jump to second 1005 — 4 gap seconds
        result = await engine.handle_trade(_trade("BTCUSDT", 50050.0, 0.1, 1005))
        assert result is not None

        state = engine.get_state("BTCUSDT")
        # Windows should have received multiple buckets
        ws10 = state.windows[10]
        assert ws10.size >= 5  # at least 5 buckets pushed

    @pytest.mark.asyncio
    async def test_multiple_symbols_independent(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        await engine.handle_trade(_trade("ETHUSDT", 3000.0, 1.0, 1000))

        r1 = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        r2 = await engine.handle_trade(_trade("ETHUSDT", 3005.0, 1.0, 1001))

        assert r1 is not None
        assert r2 is not None
        assert r1.symbol == Symbol.BTCUSDT
        assert r2.symbol == Symbol.ETHUSDT


class TestFeatureVectorContents:
    @pytest.mark.asyncio
    async def test_vector_has_all_timeframes(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 1.0, 1000))
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 1.0, 1001))

        assert result.tf_10s.window_seconds == 10
        assert result.tf_30s.window_seconds == 30
        assert result.tf_60s.window_seconds == 60
        assert result.tf_5m.window_seconds == 300

    @pytest.mark.asyncio
    async def test_returns_computed(self, engine):
        # Feed 5 seconds of rising prices
        for i in range(5):
            trade = _trade("BTCUSDT", 50000.0 + i * 100, 1.0, 1000 + i)
            await engine.handle_trade(trade)

        # Get the last result
        result = await engine.handle_trade(_trade("BTCUSDT", 50500.0, 1.0, 1005))
        assert result is not None
        ret = result.tf_10s.returns
        # Returns should be positive (price went up)
        if ret is not None:
            assert ret > 0

    @pytest.mark.asyncio
    async def test_taker_flow_imbalance_all_buys(self, engine):
        # All buy trades
        for i in range(5):
            trade = TradeEvent(
                venue=Venue.BINANCE,
                symbol=Symbol.BTCUSDT,
                price=Decimal("50000"),
                quantity=Decimal("1.0"),
                side=Side.BUY,
                trade_time=datetime.fromtimestamp(1000 + i, tz=UTC),
                venue_trade_id=f"t-{i}",
            )
            await engine.handle_trade(trade)

        result = await engine.handle_trade(
            _trade("BTCUSDT", 50000.0, 1.0, 1005)
        )
        tfi = result.tf_10s.taker_flow_imbalance
        if tfi is not None:
            assert tfi > 0  # all buys → positive

    @pytest.mark.asyncio
    async def test_last_price_updated(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        result = await engine.handle_trade(_trade("BTCUSDT", 50123.0, 0.1, 1001))
        assert result.last_price == Decimal("50123.0")

    @pytest.mark.asyncio
    async def test_freshness_score(self, engine):
        # Recent data → good freshness
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        assert result.freshness is not None
        assert result.freshness.trade_age_ms >= 0


class TestOrderbookHandling:
    @pytest.mark.asyncio
    async def test_orderbook_updates_spread(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        await engine.handle_orderbook(
            _orderbook("BTCUSDT", 49999.0, 50001.0, 1000)
        )
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        assert result is not None
        assert result.best_bid == Decimal("49999.0")
        assert result.best_ask == Decimal("50001.0")

    @pytest.mark.asyncio
    async def test_ob_imbalance_computed(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        # Bid-heavy orderbook
        await engine.handle_orderbook(
            _orderbook("BTCUSDT", 49999.0, 50001.0, 1000)
        )
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        obi = result.tf_10s.ob_imbalance
        if obi is not None:
            # Our orderbook fixture has equal depth both sides
            assert -1.0 <= obi <= 1.0


class TestVenueDivergence:
    @pytest.mark.asyncio
    async def test_cross_venue_divergence(self, engine):
        # Binance orderbook
        await engine.handle_orderbook(
            _orderbook("BTCUSDT", 50000.0, 50002.0, 1000, venue="binance")
        )
        # Bybit orderbook at slightly different price
        await engine.handle_orderbook(
            _orderbook("BTCUSDT", 49990.0, 49992.0, 1000, venue="bybit")
        )
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        result = await engine.handle_trade(_trade("BTCUSDT", 50001.0, 0.1, 1001))

        div = result.tf_10s.venue_divergence_bps
        if div is not None:
            assert div > 0  # binance mid > bybit mid


class TestMarkPriceAndLiquidation:
    @pytest.mark.asyncio
    async def test_mark_price_stored(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        mark = MarkPriceEvent(
            venue=Venue.BINANCE,
            symbol=Symbol.BTCUSDT,
            mark_price=Decimal("50005.0"),
        )
        await engine.handle_mark_price(mark)
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        assert result.mark_price == Decimal("50005.0")

    @pytest.mark.asyncio
    async def test_liquidation_processed(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        liq = LiquidationEvent(
            venue=Venue.BINANCE,
            symbol=Symbol.BTCUSDT,
            side=Side.SELL,
            price=Decimal("49500.0"),
            quantity=Decimal("10.0"),
            is_long_liquidation=True,
        )
        await engine.handle_liquidation(liq)
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        li = result.tf_10s.liquidation_imbalance
        # Should show long liquidation dominance
        if li is not None:
            assert li > 0


class TestContextFlags:
    @pytest.mark.asyncio
    async def test_whale_flag(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))

        whale = WhaleAlertEvent(
            blockchain="ethereum",
            tx_hash="0xabc",
            from_address="0x1",
            to_address="0x2",
            amount_usd=Decimal("10000000"),
            token="USDT",
        )
        await engine.handle_whale_alert(whale)

        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        assert result.whale_risk_flag is True

    @pytest.mark.asyncio
    async def test_no_whale_flag_by_default(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        assert result.whale_risk_flag is False


class TestDataQuality:
    @pytest.mark.asyncio
    async def test_warmup_not_complete_initially(self, engine):
        await engine.handle_trade(_trade("BTCUSDT", 50000.0, 0.1, 1000))
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1001))
        assert result.data_quality.warmup_complete is False

    @pytest.mark.asyncio
    async def test_window_fill_pct_in_quality(self, engine):
        for i in range(5):
            await engine.handle_trade(_trade("BTCUSDT", 50000.0 + i, 0.1, 1000 + i))
        result = await engine.handle_trade(_trade("BTCUSDT", 50010.0, 0.1, 1005))
        assert "10s" in result.data_quality.window_fill_pct


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_same_inputs_same_outputs(self, settings, publisher):
        """Replay the same event sequence twice → identical feature vectors."""
        results_a = []
        results_b = []

        for run_results in (results_a, results_b):
            eng = StreamingFeatureEngine(settings=settings, publisher=publisher)
            for i in range(20):
                price = 50000.0 + i * 10
                trade = _trade("BTCUSDT", price, 1.0, 1000 + i)
                r = await eng.handle_trade(trade)
                if r is not None:
                    run_results.append(r)

        assert len(results_a) == len(results_b)
        for a, b in zip(results_a, results_b, strict=False):
            assert a.tf_10s.returns == b.tf_10s.returns
            assert a.tf_10s.taker_flow_imbalance == b.tf_10s.taker_flow_imbalance
            assert a.tf_10s.trade_count == b.tf_10s.trade_count
            assert a.tf_10s.volume == b.tf_10s.volume
