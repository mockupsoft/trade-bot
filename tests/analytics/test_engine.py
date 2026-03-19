"""Tests for the epoch-aware analytics engine."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cte.analytics.engine import AnalyticsEngine
from cte.analytics.epochs import EpochManager, EpochMode
from cte.execution.position import PaperPosition


def _t(minute=0):
    return datetime(2024, 1, 1, 12, minute, 0, tzinfo=UTC)


def _closed_position(
    symbol="BTCUSDT", tier="A", entry=Decimal("50000"),
    exit=Decimal("51000"), qty=Decimal("1"), exit_reason="winner_trailing",
    slippage_bps=5.0, latency_ms=100, stop=0.025,
) -> PaperPosition:
    p = PaperPosition(
        symbol=symbol, direction="long", signal_tier=tier,
        quantity=qty, stop_loss_pct=stop,
        modeled_slippage_bps=Decimal(str(slippage_bps)),
        entry_latency_ms=latency_ms,
    )
    p.open(entry, _t())
    p.update_price(exit + Decimal("500"))  # set MFE
    p.close(exit, _t(minute=10), exit_reason)
    return p


@pytest.fixture
def epoch_mgr():
    mgr = EpochManager()
    mgr.create_epoch("crypto_v1_paper", EpochMode.PAPER)
    mgr.activate("crypto_v1_paper")
    return mgr


@pytest.fixture
def engine(epoch_mgr):
    return AnalyticsEngine(epoch_mgr)


class TestTradeRecording:
    def test_record_trade(self, engine):
        pos = _closed_position()
        trade = engine.record_trade(pos)
        assert trade.epoch == "crypto_v1_paper"
        assert trade.symbol == "BTCUSDT"
        assert trade.tier == "A"
        assert engine.total_trades == 1

    def test_multiple_trades(self, engine):
        for _ in range(5):
            engine.record_trade(_closed_position())
        assert engine.total_trades == 5


class TestMetricsComputation:
    def test_get_metrics_all(self, engine):
        engine.record_trade(_closed_position(exit=Decimal("51000")))  # +1000
        engine.record_trade(_closed_position(exit=Decimal("49500")))  # -500
        engine.record_trade(_closed_position(exit=Decimal("50500")))  # +500

        metrics = engine.get_metrics()
        assert metrics["trade_count"] == 3
        assert metrics["win_rate"] == pytest.approx(2 / 3, rel=0.01)
        assert metrics["total_pnl"] > 0

    def test_filter_by_symbol(self, engine):
        engine.record_trade(_closed_position(symbol="BTCUSDT", exit=Decimal("51000")))
        engine.record_trade(_closed_position(symbol="ETHUSDT", exit=Decimal("49000")))

        btc = engine.get_metrics(symbol="BTCUSDT")
        assert btc["trade_count"] == 1
        assert btc["total_pnl"] > 0

        eth = engine.get_metrics(symbol="ETHUSDT")
        assert eth["trade_count"] == 1
        assert eth["total_pnl"] < 0

    def test_filter_by_tier(self, engine):
        engine.record_trade(_closed_position(tier="A", exit=Decimal("51000")))
        engine.record_trade(_closed_position(tier="C", exit=Decimal("49000")))

        a_metrics = engine.get_metrics(tier="A")
        assert a_metrics["trade_count"] == 1

    def test_filter_by_exit_reason(self, engine):
        engine.record_trade(_closed_position(exit_reason="winner_trailing"))
        engine.record_trade(_closed_position(exit_reason="hard_stop", exit=Decimal("48000")))

        winners = engine.get_metrics(exit_reason="winner_trailing")
        assert winners["trade_count"] == 1


class TestEpochSupport:
    def test_epoch_filtering(self):
        mgr = EpochManager()
        mgr.create_epoch("paper", EpochMode.PAPER)
        mgr.create_epoch("demo", EpochMode.DEMO)

        eng = AnalyticsEngine(mgr)

        mgr.activate("paper")
        eng.record_trade(_closed_position(exit=Decimal("51000")))

        mgr.activate("demo")
        eng.record_trade(_closed_position(exit=Decimal("49000")))

        paper_m = eng.get_metrics(epoch="paper")
        assert paper_m["trade_count"] == 1
        assert paper_m["total_pnl"] > 0

        demo_m = eng.get_metrics(epoch="demo")
        assert demo_m["trade_count"] == 1
        assert demo_m["total_pnl"] < 0

    def test_epoch_comparison(self):
        mgr = EpochManager()
        mgr.create_epoch("paper", EpochMode.PAPER)
        mgr.create_epoch("demo", EpochMode.DEMO)
        eng = AnalyticsEngine(mgr)

        mgr.activate("paper")
        eng.record_trade(_closed_position(slippage_bps=3.0))

        mgr.activate("demo")
        eng.record_trade(_closed_position(slippage_bps=7.0))

        comparison = eng.get_epoch_comparison("paper", "demo")
        assert "paper" in comparison
        assert "demo" in comparison
        assert "slippage_drift" in comparison
        assert comparison["slippage_drift"]["drift_bps"] == pytest.approx(4.0)


class TestTradesDrilldown:
    JOURNAL_KEYS = frozenset({
        "symbol",
        "venue",
        "tier",
        "epoch",
        "source",
        "pnl",
        "exit_reason",
        "exit_layer",
        "hold_seconds",
        "r_multiple",
        "entry_latency_ms",
        "slippage_bps",
        "mfe_pct",
        "mae_pct",
        "was_profitable_at_exit",
        "position_mode",
    })

    def test_get_trades_list_newest_first(self, engine):
        engine.record_trade(_closed_position())
        engine.record_trade(_closed_position(symbol="ETHUSDT"))

        trades = engine.get_trades()
        assert len(trades) == 2
        assert trades[0]["symbol"] == "ETHUSDT"
        assert trades[1]["symbol"] == "BTCUSDT"
        assert set(trades[0].keys()) == self.JOURNAL_KEYS
        assert "pnl" in trades[0]
        assert "r_multiple" in trades[0]

    def test_get_trades_filtered(self, engine):
        engine.record_trade(_closed_position(symbol="BTCUSDT"))
        engine.record_trade(_closed_position(symbol="ETHUSDT"))

        trades = engine.get_trades(symbol="ETHUSDT")
        assert len(trades) == 1
        assert trades[0]["symbol"] == "ETHUSDT"

    def test_get_trades_filtered_by_exit_reason(self, engine):
        engine.record_trade(_closed_position(exit_reason="winner_trailing"))
        engine.record_trade(_closed_position(exit_reason="hard_stop", exit=Decimal("48000")))

        rows = engine.get_trades(exit_reason="hard_stop")
        assert len(rows) == 1
        assert rows[0]["exit_reason"] == "hard_stop"

    def test_get_trades_filtered_by_source(self, engine):
        p1 = _closed_position()
        p2 = _closed_position(exit=Decimal("49000"))
        engine.record_trade(p1, source="paper_simulated")
        engine.record_trade(p2, source="demo_exchange")

        paper = engine.get_trades(source="paper_simulated")
        assert len(paper) == 1
        assert paper[0]["source"] == "paper_simulated"

    def test_get_trades_limit(self, engine):
        for _ in range(10):
            engine.record_trade(_closed_position())
        trades = engine.get_trades(limit=3)
        assert len(trades) == 3

    def test_get_trades_respects_epoch(self, epoch_mgr):
        epoch_mgr.create_epoch("other", EpochMode.DEMO)
        eng = AnalyticsEngine(epoch_mgr)
        epoch_mgr.activate("crypto_v1_paper")
        eng.record_trade(_closed_position())
        epoch_mgr.activate("other")
        eng.record_trade(_closed_position(symbol="ETHUSDT"))

        paper_rows = eng.get_trades(epoch="crypto_v1_paper")
        assert len(paper_rows) == 1
        assert paper_rows[0]["symbol"] == "BTCUSDT"
