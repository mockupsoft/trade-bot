"""End-to-end integration test: signal → risk → sizing → execution → exit → analytics.

Wires up the complete pipeline and verifies data flows correctly
through all stages with proper provenance tracking.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from cte.analytics.engine import AnalyticsEngine
from cte.analytics.epochs import EpochManager, EpochMode
from cte.core.events import (
    DataQuality,
    FreshnessScore,
    RiskDecision,
    SignalAction,
    SignalTier,
    StreamingFeatureVector,
    Symbol,
    TimeframeFeatures,
)
from cte.core.settings import (
    ExecutionSettings,
    ExitSettings,
    RiskSettings,
    SignalSettings,
    SizingSettings,
)
from cte.core.streams import StreamPublisher
from cte.execution.fill_model import FillMode
from cte.execution.paper import PaperExecutionEngine
from cte.exits.engine import LayeredExitEngine
from cte.risk.manager import PortfolioState, RiskManager
from cte.signals.engine import ScoringSignalEngine
from cte.sizing.engine import SizingEngine


@pytest.fixture
def publisher():
    return AsyncMock(spec=StreamPublisher, publish=AsyncMock(return_value="x"))


def _t(minute=0, second=0):
    return datetime(2024, 1, 1, 12, minute, second, tzinfo=UTC)


def _bullish_features() -> StreamingFeatureVector:
    return StreamingFeatureVector(
        symbol=Symbol.BTCUSDT,
        tf_10s=TimeframeFeatures(window_seconds=10, returns_z=2.0, momentum_z=1.8,
                                  taker_flow_imbalance=0.3, spread_bps=1.0,
                                  spread_widening=0.8, ob_imbalance=0.3,
                                  venue_divergence_bps=2.0,
                                  trade_count=200, volume=10.0, window_fill_pct=1.0),
        tf_30s=TimeframeFeatures(window_seconds=30, returns_z=2.2, momentum_z=2.0,
                                  taker_flow_imbalance=0.25, spread_bps=1.0,
                                  ob_imbalance=0.25, trade_count=500, volume=25.0, window_fill_pct=1.0),
        tf_60s=TimeframeFeatures(window_seconds=60, returns_z=2.5, momentum_z=2.2,
                                  taker_flow_imbalance=0.2, spread_bps=0.8,
                                  spread_widening=0.75, ob_imbalance=0.35,
                                  liquidation_imbalance=-0.5, venue_divergence_bps=2.0,
                                  trade_count=1000, volume=50.0, window_fill_pct=1.0),
        tf_5m=TimeframeFeatures(window_seconds=300, returns_z=2.8, momentum_z=2.0,
                                 taker_flow_imbalance=0.18, spread_bps=0.8,
                                 ob_imbalance=0.28, liquidation_imbalance=-0.4,
                                 trade_count=5000, volume=250.0, window_fill_pct=0.98),
        freshness=FreshnessScore(trade_age_ms=30, orderbook_age_ms=80, composite=0.99),
        execution_feasibility=0.95,
        last_price=Decimal("65000"),
        best_bid=Decimal("64999"),
        best_ask=Decimal("65001"),
        data_quality=DataQuality(warmup_complete=True, binance_connected=True, bybit_connected=True,
                                  window_fill_pct={"10s": 1.0, "30s": 1.0, "60s": 1.0, "5m": 0.98}),
    )


class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_signal_to_analytics_happy_path(self, publisher):
        """Complete happy path: bullish signal → approved → sized → filled → exited → recorded."""
        # 1. Signal Engine
        signal_settings = SignalSettings(cooldown_seconds=0, max_signals_per_hour=100)
        signal_engine = ScoringSignalEngine(signal_settings, publisher)

        features = _bullish_features()
        signal = await signal_engine.evaluate(features)
        assert signal is not None
        assert signal.tier in (SignalTier.A, SignalTier.B)

        # 2. Risk Manager
        risk_settings = RiskSettings()
        portfolio = PortfolioState(initial_capital=Decimal("10000"))
        risk_mgr = RiskManager(risk_settings, publisher, portfolio)

        notional = Decimal("200")  # 2% of portfolio
        assessment = await risk_mgr.assess_signal(
            # Convert ScoredSignalEvent to SignalEvent-like for risk manager
            type("FakeSignal", (), {
                "event_id": signal.event_id,
                "symbol": signal.symbol,
            })(),
            notional,
        )
        assert assessment.decision == RiskDecision.APPROVED

        # 3. Sizing Engine
        sizing_settings = SizingSettings(min_order_usd=10.0, max_order_usd=500.0)
        sizer = SizingEngine(sizing_settings, publisher, Decimal("10000"))

        from cte.core.events import SignalEvent
        legacy_signal = SignalEvent(
            symbol=signal.symbol, action=SignalAction.OPEN_LONG,
            confidence=signal.composite_score,
            reason=signal.reason,
        )
        sized = await sizer.size_order(legacy_signal, assessment, Decimal("65000"))
        assert sized is not None
        assert sized.quantity > 0

        # 4. Paper Execution
        exec_settings = ExecutionSettings(slippage_bps=5, fill_delay_ms=100)
        exit_settings = ExitSettings(stop_loss_pct=0.02, take_profit_pct=0.03)
        paper = PaperExecutionEngine(exec_settings, exit_settings, publisher, FillMode.SPREAD_CROSSING)
        paper.update_book("BTCUSDT", Decimal("64999"), Decimal("65001"))

        position = paper.open_position(signal, sized.quantity, sized.notional_usd, _t())
        assert position is not None
        assert position.signal_tier in ("A", "B")
        assert position.fill_price > Decimal("65001")  # filled above ask

        # 5. Price moves up → exit engine monitors
        LayeredExitEngine()

        # Price rises to TP
        tp_price = position.entry_price * Decimal("1.04")
        paper.update_book("BTCUSDT", tp_price - 1, tp_price + 1)
        closed_positions = paper.evaluate_exits("BTCUSDT", tp_price, _t(minute=5))
        assert len(closed_positions) == 1
        closed = closed_positions[0]
        assert closed.realized_pnl > 0

        # 6. Analytics
        epoch_mgr = EpochManager()
        epoch_mgr.create_epoch("test_epoch", EpochMode.PAPER)
        epoch_mgr.activate("test_epoch")
        analytics = AnalyticsEngine(epoch_mgr)

        trade = analytics.record_trade(closed, venue="binance")
        assert trade.epoch == "test_epoch"
        assert trade.pnl > 0

        metrics = analytics.get_metrics()
        assert metrics["trade_count"] == 1
        assert metrics["win_rate"] == 1.0
        assert metrics["total_pnl"] > 0

    @pytest.mark.asyncio
    async def test_signal_rejected_by_gates(self, publisher):
        """Stale data → signal engine gates reject before scoring."""
        signal_settings = SignalSettings(cooldown_seconds=0, max_signals_per_hour=100)
        signal_engine = ScoringSignalEngine(signal_settings, publisher)

        features = StreamingFeatureVector(
            symbol=Symbol.BTCUSDT,
            tf_10s=TimeframeFeatures(window_seconds=10, trade_count=0, volume=0.0, window_fill_pct=0.0),
            tf_30s=TimeframeFeatures(window_seconds=30, trade_count=0, volume=0.0, window_fill_pct=0.0),
            tf_60s=TimeframeFeatures(window_seconds=60, spread_bps=25.0,
                                      trade_count=0, volume=0.0, window_fill_pct=0.0),
            tf_5m=TimeframeFeatures(window_seconds=300, trade_count=0, volume=0.0, window_fill_pct=0.0),
            freshness=FreshnessScore(composite=0.1),
            execution_feasibility=0.05,
            last_price=Decimal("65000"),
            data_quality=DataQuality(warmup_complete=False),
        )

        signal = await signal_engine.evaluate(features)
        assert signal is None  # gated by stale data + warmup

    @pytest.mark.asyncio
    async def test_provenance_chain(self, publisher):
        """Verify that signal metadata propagates through the entire chain."""
        signal_settings = SignalSettings(cooldown_seconds=0, max_signals_per_hour=100)
        signal_engine = ScoringSignalEngine(signal_settings, publisher)

        signal = await signal_engine.evaluate(_bullish_features())
        assert signal is not None

        exec_settings = ExecutionSettings(slippage_bps=5, fill_delay_ms=100)
        exit_settings = ExitSettings()
        paper = PaperExecutionEngine(exec_settings, exit_settings, publisher)
        paper.update_book("BTCUSDT", Decimal("64999"), Decimal("65001"))

        position = paper.open_position(signal, Decimal("0.01"), Decimal("650"), _t())

        # Signal provenance on position
        assert position.signal_tier == signal.tier.value
        assert position.composite_score == signal.composite_score
        assert position.signal_id == signal.event_id
        assert len(position.entry_reason) > 0
