"""Tests for live market data feed."""
from __future__ import annotations

from decimal import Decimal

import orjson
import pytest

from cte.market.feed import MarketDataFeed, TickerState


class TestTickerState:
    def test_spread_bps(self):
        t = TickerState(best_bid=Decimal("50000"), best_ask=Decimal("50002"))
        assert t.spread_bps == pytest.approx(0.4, rel=0.1)

    def test_stale_detection(self):
        t = TickerState(last_update_ms=0)
        assert t.is_stale

    def test_fresh_data(self):
        import time
        t = TickerState(last_update_ms=int(time.time() * 1000))
        assert not t.is_stale

    def test_zero_spread(self):
        t = TickerState(best_bid=Decimal("0"), best_ask=Decimal("0"))
        assert t.spread_bps == 0.0


class TestMarketDataFeed:
    def test_initialization(self):
        feed = MarketDataFeed()
        assert "BTCUSDT" in feed.tickers
        assert "ETHUSDT" in feed.tickers

    def test_ws_url_explicit_arg(self):
        feed = MarketDataFeed(ws_url="wss://example.test/stream")
        assert feed._ws_url == "wss://example.test/stream"

    def test_ws_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CTE_MARKET_WS_URL", "wss://custom.example/stream")
        feed = MarketDataFeed()
        assert feed._ws_url == "wss://custom.example/stream"
        monkeypatch.delenv("CTE_MARKET_WS_URL", raising=False)

    def test_health_initial(self):
        feed = MarketDataFeed()
        h = feed.health
        assert not h.connected
        assert h.messages_total == 0

    def test_process_trade_message(self):
        feed = MarketDataFeed()
        msg = orjson.dumps({
            "stream": "btcusdt@trade",
            "data": {"p": "65432.10", "q": "0.5", "T": 1700000000000},
        })
        feed._process_message(msg)
        t = feed.get_ticker("BTCUSDT")
        assert t is not None
        assert t.last_price == Decimal("65432.10")
        assert t.trade_count_1m == 1

    def test_process_depth_message(self):
        feed = MarketDataFeed()
        msg = orjson.dumps({
            "stream": "btcusdt@depth5@100ms",
            "data": {
                "b": [["65430.00", "2.5"], ["65429.00", "1.0"]],
                "a": [["65432.00", "1.8"], ["65433.00", "3.0"]],
            },
        })
        feed._process_message(msg)
        t = feed.get_ticker("BTCUSDT")
        assert t.best_bid == Decimal("65430.00")
        assert t.best_ask == Decimal("65432.00")

    def test_process_mark_price(self):
        feed = MarketDataFeed()
        msg = orjson.dumps({
            "stream": "btcusdt@markPrice@1s",
            "data": {"p": "65431.50", "E": 1700000000000},
        })
        feed._process_message(msg)
        t = feed.get_ticker("BTCUSDT")
        assert t.mark_price == Decimal("65431.50")

    def test_unknown_symbol_ignored(self):
        feed = MarketDataFeed()
        msg = orjson.dumps({
            "stream": "dogeusdt@trade",
            "data": {"p": "0.1", "q": "1000", "T": 1700000000000},
        })
        feed._process_message(msg)
        assert feed.health.errors_total == 0

    def test_message_count_increments(self):
        feed = MarketDataFeed()
        for i in range(5):
            msg = orjson.dumps({
                "stream": "ethusdt@trade",
                "data": {"p": str(3000 + i), "q": "1.0", "T": 1700000000000},
            })
            feed._process_message(msg)
        assert feed.health.messages_total == 5
        assert feed.get_ticker("ETHUSDT").trade_count_1m == 5

    def test_build_url(self):
        feed = MarketDataFeed()
        url = feed._build_url()
        assert "btcusdt@trade" in url
        assert "ethusdt@trade" in url
        assert url.startswith("wss://")

    def test_health_with_symbols(self):
        feed = MarketDataFeed()
        msg = orjson.dumps({
            "stream": "btcusdt@trade",
            "data": {"p": "65000", "q": "0.1", "T": 1700000000000},
        })
        feed._process_message(msg)
        h = feed.health
        assert "BTCUSDT" in h.symbols
        assert h.symbols["BTCUSDT"]["last_price"] == "65000"
