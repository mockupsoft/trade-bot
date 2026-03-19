"""Tests for the LayeredExitEngine coordinator."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from cte.core.events import (
    DataQuality,
    FreshnessScore,
    StreamingFeatureVector,
    Symbol,
    TimeframeFeatures,
)
from cte.execution.position import PaperPosition
from cte.exits.engine import LayeredExitEngine


def _t(minute=0, second=0):
    return datetime(2024, 1, 1, 12, minute, second, tzinfo=UTC)


def _pos(entry=Decimal("50000"), tier="A", stop=0.025, fill_time=None):
    p = PaperPosition(
        symbol="BTCUSDT", direction="long", signal_tier=tier,
        quantity=Decimal("1"), stop_loss_pct=stop,
    )
    p.open(entry, fill_time or _t())
    return p


def _feat(freshness=0.98, spread=1.2, tfi=0.1, returns_z=1.0, liq_imb=None):
    return StreamingFeatureVector(
        symbol=Symbol.BTCUSDT,
        tf_10s=TimeframeFeatures(window_seconds=10, trade_count=100, volume=5.0, window_fill_pct=1.0),
        tf_30s=TimeframeFeatures(window_seconds=30, trade_count=300, volume=15.0, window_fill_pct=1.0),
        tf_60s=TimeframeFeatures(
            window_seconds=60, taker_flow_imbalance=tfi, returns_z=returns_z,
            spread_bps=spread, liquidation_imbalance=liq_imb,
            trade_count=600, volume=30.0, window_fill_pct=1.0,
        ),
        tf_5m=TimeframeFeatures(window_seconds=300, trade_count=3000, volume=150.0, window_fill_pct=0.95),
        freshness=FreshnessScore(composite=freshness),
        last_price=Decimal("50000"),
        data_quality=DataQuality(warmup_complete=True),
    )


class TestPriorityOrder:
    def test_hard_risk_overrides_everything(self):
        engine = LayeredExitEngine()
        pos = _pos()
        # Price crashed 5% AND thesis failed AND stale data
        feat = _feat(freshness=0.1, tfi=-0.5, returns_z=-3.0)
        decision = engine.evaluate(pos, Decimal("47500"), _t(second=5), feat)
        assert decision.should_exit
        assert decision.exit_layer == 1

    def test_thesis_failure_before_no_progress(self):
        engine = LayeredExitEngine()
        pos = _pos(tier="C", fill_time=_t())
        feat = _feat(tfi=-0.5, returns_z=-2.0)
        # 10 min, no progress, AND thesis failed
        decision = engine.evaluate(pos, Decimal("50050"), _t(minute=10), feat)
        assert decision.should_exit
        assert decision.exit_layer == 2

    def test_no_exit_when_all_clear(self):
        engine = LayeredExitEngine()
        pos = _pos(fill_time=_t())
        feat = _feat()
        decision = engine.evaluate(pos, Decimal("50100"), _t(minute=2), feat)
        assert not decision.should_exit


class TestExplainability:
    def test_decision_has_all_layers(self):
        engine = LayeredExitEngine()
        pos = _pos(fill_time=_t())
        feat = _feat()
        decision = engine.evaluate(pos, Decimal("50100"), _t(minute=2), feat)
        assert len(decision.all_layers) > 0
        layer_names = {layer_result.layer_name for layer_result in decision.all_layers}
        assert "hard_risk" in layer_names

    def test_exit_has_reason_detail(self):
        engine = LayeredExitEngine()
        pos = _pos()
        decision = engine.evaluate(pos, Decimal("48000"), _t(second=5))
        assert decision.should_exit
        assert len(decision.exit_detail) > 0
        assert decision.exit_reason == "hard_stop"

    def test_position_mode_tracked(self):
        engine = LayeredExitEngine()
        pos = _pos(fill_time=_t())
        feat = _feat()
        # Move to profit → winner mode
        pos.update_price(Decimal("51000"))
        decision = engine.evaluate(pos, Decimal("51000"), _t(minute=5), feat)
        assert decision.position_mode in ("normal", "winner_protection", "runner")


class TestPositionModeProgression:
    def test_normal_to_winner(self):
        engine = LayeredExitEngine()
        pos = _pos(fill_time=_t())

        # Initially normal
        d1 = engine.evaluate(pos, Decimal("50100"), _t(minute=1))
        assert d1.position_mode == "normal"

        # Profit grows → winner
        pos.update_price(Decimal("50600"))
        d2 = engine.evaluate(pos, Decimal("50600"), _t(minute=3))
        assert d2.position_mode == "winner_protection"

    def test_winner_to_runner(self):
        engine = LayeredExitEngine()
        pos = _pos(fill_time=_t())

        # Profit grows past runner threshold (2.5% for Tier A)
        pos.update_price(Decimal("51500"))
        d = engine.evaluate(pos, Decimal("51500"), _t(minute=5))
        assert d.position_mode == "runner"

    def test_runner_downgrade_on_momentum_collapse(self):
        engine = LayeredExitEngine()
        pos = _pos(fill_time=_t())

        # Enter runner mode
        pos.update_price(Decimal("51500"))
        engine.evaluate(pos, Decimal("51500"), _t(minute=5))

        # Momentum collapses
        feat = _feat(returns_z=-2.0)
        d = engine.evaluate(pos, Decimal("51500"), _t(minute=6), feat)
        assert d.position_mode == "winner_protection"


class TestNoProgressByTier:
    def test_tier_a_patient(self):
        engine = LayeredExitEngine()
        pos = _pos(tier="A", fill_time=_t())

        # At 10 min, no progress — but Tier A has 15 min budget
        d = engine.evaluate(pos, Decimal("50050"), _t(minute=10))
        assert not d.should_exit

        # At 16 min, still no progress → exit
        d = engine.evaluate(pos, Decimal("50050"), _t(minute=16))
        assert d.should_exit
        assert d.exit_reason == "no_progress"

    def test_tier_c_impatient(self):
        engine = LayeredExitEngine()
        pos = _pos(tier="C", fill_time=_t())

        # At 5 min, Tier C budget is 4 min → exit
        d = engine.evaluate(pos, Decimal("50050"), _t(minute=5))
        assert d.should_exit
        assert d.exit_reason == "no_progress"

    def test_runner_suspends_no_progress(self):
        engine = LayeredExitEngine()
        pos = _pos(tier="A", fill_time=_t())

        # Enter runner mode
        pos.update_price(Decimal("51500"))
        engine.evaluate(pos, Decimal("51500"), _t(minute=3))

        # 30 min later, price consolidating but still a runner → no exit
        d = engine.evaluate(pos, Decimal("51300"), _t(minute=30))
        assert not d.should_exit


class TestAnalyticsHooks:
    def test_profitable_exit_flagged(self):
        engine = LayeredExitEngine()
        pos = _pos(tier="C", fill_time=_t())
        feat = _feat(tfi=-0.5, returns_z=-2.0)

        # Thesis fails while position is profitable
        d = engine.evaluate(pos, Decimal("50200"), _t(minute=1), feat)
        assert d.should_exit
        assert d.was_profitable_at_exit
        assert d.exit_gain_pct > 0

    def test_losing_exit_flagged(self):
        engine = LayeredExitEngine()
        pos = _pos()
        d = engine.evaluate(pos, Decimal("48000"), _t(second=5))
        assert d.should_exit
        assert not d.was_profitable_at_exit

    def test_hold_seconds_and_r(self):
        engine = LayeredExitEngine()
        pos = _pos(fill_time=_t())
        d = engine.evaluate(pos, Decimal("48000"), _t(minute=2))
        assert d.hold_seconds == 120
        assert d.current_r is not None


class TestDeterministicReplay:
    def test_same_sequence_same_decisions(self):
        results = []
        for _ in range(2):
            eng = LayeredExitEngine()
            pos = _pos(fill_time=_t())
            feat = _feat()

            tick_decisions = []
            prices = [50100, 50300, 50800, 51200, 51000, 50500]
            for i, p in enumerate(prices):
                d = eng.evaluate(pos, Decimal(str(p)), _t(minute=i + 1), feat)
                tick_decisions.append((d.should_exit, d.exit_reason, d.position_mode))

            results.append(tick_decisions)

        assert results[0] == results[1]

    def test_cleanup_removes_state(self):
        engine = LayeredExitEngine()
        pos = _pos(fill_time=_t())
        engine.evaluate(pos, Decimal("50100"), _t(minute=1))
        assert pos.position_id in engine._states
        engine.cleanup(pos.position_id)
        assert pos.position_id not in engine._states
