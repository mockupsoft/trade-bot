"""Tests for 5-layer exit checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cte.core.events import (
    DataQuality, FreshnessScore, StreamingFeatureVector,
    Symbol, TimeframeFeatures,
)
from cte.execution.position import PaperPosition, PositionStatus
from cte.exits.config import TIER_A_PROFILE, TIER_B_PROFILE, TIER_C_PROFILE, TierExitProfile
from cte.exits.layers import (
    ExitContext, PositionExitState,
    check_layer1_hard_risk, check_layer2_thesis_failure,
    check_layer3_no_progress, check_layer4_winner_protection,
    check_layer5_runner,
)


def _t(minute=0, second=0):
    return datetime(2024, 1, 1, 12, minute, second, tzinfo=timezone.utc)


def _pos(entry=Decimal("50000"), qty=Decimal("1"), tier="A", stop=0.025,
         fill_time=None, direction="long") -> PaperPosition:
    p = PaperPosition(
        symbol="BTCUSDT", direction=direction, signal_tier=tier,
        quantity=qty, stop_loss_pct=stop,
    )
    p.open(entry, fill_time or _t())
    return p


def _features(freshness=0.98, spread=1.2, tfi=0.1, returns_z=1.0,
              liq_imb=None, warmup=True) -> StreamingFeatureVector:
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
        data_quality=DataQuality(warmup_complete=warmup),
    )


def _ctx(pos, price, now, features=None):
    return ExitContext(position=pos, current_price=price, now=now, features=features)


class TestLayer1HardRisk:
    def test_hard_stop_triggers(self):
        pos = _pos(entry=Decimal("50000"))
        ctx = _ctx(pos, Decimal("48500"), _t(second=30))  # -3% > 2.5%
        r = check_layer1_hard_risk(ctx, TIER_A_PROFILE)
        assert r.triggered
        assert r.exit_reason == "hard_stop"

    def test_within_stop_passes(self):
        pos = _pos(entry=Decimal("50000"))
        ctx = _ctx(pos, Decimal("49000"), _t(second=10))  # -2% < 2.5%
        r = check_layer1_hard_risk(ctx, TIER_A_PROFILE)
        assert not r.triggered

    def test_stale_data_triggers(self):
        pos = _pos()
        feat = _features(freshness=0.1)
        ctx = _ctx(pos, Decimal("50000"), _t(second=5), feat)
        r = check_layer1_hard_risk(ctx, TIER_A_PROFILE)
        assert r.triggered
        assert r.exit_reason == "stale_data"

    def test_spread_blowout_triggers(self):
        pos = _pos()
        feat = _features(spread=25.0)
        ctx = _ctx(pos, Decimal("50000"), _t(second=5), feat)
        r = check_layer1_hard_risk(ctx, TIER_A_PROFILE)
        assert r.triggered
        assert r.exit_reason == "spread_blowout"

    def test_no_features_only_checks_price(self):
        pos = _pos(entry=Decimal("50000"))
        ctx = _ctx(pos, Decimal("50000"), _t(second=5))
        r = check_layer1_hard_risk(ctx, TIER_A_PROFILE)
        assert not r.triggered


class TestLayer2ThesisFailure:
    def test_tfi_flip_with_confirmation(self):
        pos = _pos()
        state = PositionExitState()
        profile = TIER_A_PROFILE  # needs 3 confirmations

        feat = _features(tfi=-0.3, returns_z=1.0)
        for i in range(2):
            ctx = _ctx(pos, Decimal("50000"), _t(second=i), feat)
            r = check_layer2_thesis_failure(ctx, profile, state)
            assert not r.triggered
        ctx = _ctx(pos, Decimal("50000"), _t(second=3), feat)
        r = check_layer2_thesis_failure(ctx, profile, state)
        assert r.triggered
        assert r.exit_reason == "thesis_failure"

    def test_tier_c_single_confirm(self):
        pos = _pos(tier="C")
        state = PositionExitState()
        feat = _features(tfi=-0.3)
        ctx = _ctx(pos, Decimal("50000"), _t(), feat)
        r = check_layer2_thesis_failure(ctx, TIER_C_PROFILE, state)
        assert r.triggered  # Tier C = 1 confirmation

    def test_momentum_collapse(self):
        pos = _pos(tier="C")
        state = PositionExitState()
        feat = _features(returns_z=-2.0, tfi=0.1)
        ctx = _ctx(pos, Decimal("50000"), _t(), feat)
        r = check_layer2_thesis_failure(ctx, TIER_C_PROFILE, state)
        assert r.triggered

    def test_liq_shift_for_long(self):
        pos = _pos(tier="C", direction="long")
        state = PositionExitState()
        feat = _features(liq_imb=0.5, tfi=0.1, returns_z=1.0)
        ctx = _ctx(pos, Decimal("50000"), _t(), feat)
        r = check_layer2_thesis_failure(ctx, TIER_C_PROFILE, state)
        assert r.triggered

    def test_reset_on_recovery(self):
        pos = _pos()
        state = PositionExitState()
        profile = TIER_A_PROFILE

        bad = _features(tfi=-0.3)
        ctx = _ctx(pos, Decimal("50000"), _t(second=0), bad)
        check_layer2_thesis_failure(ctx, profile, state)
        assert state.thesis_fail_count == 1

        good = _features(tfi=0.2)
        ctx = _ctx(pos, Decimal("50000"), _t(second=1), good)
        check_layer2_thesis_failure(ctx, profile, state)
        assert state.thesis_fail_count == 0

    def test_no_features_passes(self):
        pos = _pos()
        state = PositionExitState()
        ctx = _ctx(pos, Decimal("50000"), _t())
        r = check_layer2_thesis_failure(ctx, TIER_A_PROFILE, state)
        assert not r.triggered


class TestLayer3NoProgress:
    def test_no_progress_triggers_after_budget(self):
        pos = _pos(tier="A", fill_time=_t())
        state = PositionExitState()
        # 20 minutes later, only 0.1% gain
        ctx = _ctx(pos, Decimal("50050"), _t(minute=20))
        r = check_layer3_no_progress(ctx, TIER_A_PROFILE, state)
        assert r.triggered
        assert r.exit_reason == "no_progress"

    def test_within_budget_passes(self):
        pos = _pos(tier="A", fill_time=_t())
        state = PositionExitState()
        ctx = _ctx(pos, Decimal("50050"), _t(minute=5))  # only 5 min, budget=15
        r = check_layer3_no_progress(ctx, TIER_A_PROFILE, state)
        assert not r.triggered

    def test_sufficient_progress_passes(self):
        pos = _pos(tier="A", fill_time=_t())
        state = PositionExitState()
        ctx = _ctx(pos, Decimal("50300"), _t(minute=20))  # 0.6% gain > 0.3%
        r = check_layer3_no_progress(ctx, TIER_A_PROFILE, state)
        assert not r.triggered

    def test_tier_c_shorter_budget(self):
        pos = _pos(tier="C", fill_time=_t())
        state = PositionExitState()
        ctx = _ctx(pos, Decimal("50050"), _t(minute=5))  # 5 min > C budget of 4 min
        r = check_layer3_no_progress(ctx, TIER_C_PROFILE, state)
        assert r.triggered

    def test_suspended_in_runner_mode(self):
        pos = _pos(fill_time=_t())
        state = PositionExitState(position_mode="runner")
        far_future = _t() + __import__("datetime").timedelta(hours=1)
        ctx = _ctx(pos, Decimal("50050"), far_future)
        r = check_layer3_no_progress(ctx, TIER_A_PROFILE, state)
        assert not r.triggered
        assert "runner mode" in r.detail


class TestLayer4WinnerProtection:
    def test_activates_on_profit(self):
        pos = _pos(entry=Decimal("50000"), fill_time=_t())
        state = PositionExitState()
        # Up 1.5% → qualifies for winner
        ctx = _ctx(pos, Decimal("50750"), _t(minute=5))
        r = check_layer4_winner_protection(ctx, TIER_A_PROFILE, state)
        assert state.position_mode == "winner_protection"
        assert not r.triggered  # activated but no trailing stop hit yet

    def test_trailing_from_high_triggers(self):
        pos = _pos(entry=Decimal("50000"), fill_time=_t())
        # Push price up enough to qualify as winner (≥1% gain)
        pos.update_price(Decimal("51500"))  # +3% gain, high watermark set
        state = PositionExitState(position_mode="winner_protection")

        # Current price still qualifies as winner (above 1% gain)
        # but drawdown from high exceeds 2% trailing
        drop_price = Decimal("50400")  # ~2.14% below 51500, still +0.8% from entry
        # Need drop >= 2% from 51500: 51500 * 0.98 = 50470
        # But also need gain_pct >= 1% → price >= 50500
        # Use price just above 50500 but > 2% down from 51500
        # 51500 * (1 - 0.02) = 50470 → this is < 50500 so gain < 1%
        # This means the position wouldn't qualify as winner at this price
        # Solution: use higher entry context so the math works
        # Let's set high to 52000, drop to 50900 (2.1% down, still 1.8% up from entry)
        pos2 = _pos(entry=Decimal("50000"), fill_time=_t())
        pos2.update_price(Decimal("52000"))  # high watermark
        state2 = PositionExitState(position_mode="winner_protection")

        drop = Decimal("50900")  # 2.1% below 52000, 1.8% above 50000
        ctx = _ctx(pos2, drop, _t(minute=10))
        r = check_layer4_winner_protection(ctx, TIER_A_PROFILE, state2)
        assert r.triggered
        assert r.exit_reason == "winner_trailing"

    def test_not_triggered_when_not_winner(self):
        pos = _pos(entry=Decimal("50000"), fill_time=_t())
        state = PositionExitState()
        ctx = _ctx(pos, Decimal("50200"), _t(minute=5))  # only 0.4% gain
        r = check_layer4_winner_protection(ctx, TIER_A_PROFILE, state)
        assert not r.triggered
        assert state.position_mode == "normal"

    def test_defers_to_runner(self):
        pos = _pos(entry=Decimal("50000"), fill_time=_t())
        state = PositionExitState(position_mode="runner")
        ctx = _ctx(pos, Decimal("51000"), _t(minute=5))
        r = check_layer4_winner_protection(ctx, TIER_A_PROFILE, state)
        assert not r.triggered
        assert "runner" in r.detail.lower()


class TestLayer5Runner:
    def test_activates_on_big_profit(self):
        pos = _pos(entry=Decimal("50000"), stop=0.025, fill_time=_t())
        state = PositionExitState(position_mode="winner_protection")
        # Up 3% → qualifies for runner (activation_pct=2.5% for Tier A)
        ctx = _ctx(pos, Decimal("51500"), _t(minute=10))
        r = check_layer5_runner(ctx, TIER_A_PROFILE, state)
        assert state.position_mode == "runner"
        assert not r.triggered  # activated but trailing not hit

    def test_runner_trailing_triggers(self):
        pos = _pos(entry=Decimal("50000"), stop=0.025, fill_time=_t())
        pos.update_price(Decimal("53000"))  # high watermark
        state = PositionExitState(position_mode="runner")

        # Price still qualifies as runner (≥2.5% gain) but drop ≥3.5% from high
        # 53000 * (1-0.035) = 51145. Gain from 50000 = 2.29% < 2.5% → not runner
        # Need: gain ≥ 2.5% AND drawdown ≥ 3.5%
        # Set higher peak: 55000. 55000 * 0.965 = 53075. gain = 6.15% → qualifies
        pos2 = _pos(entry=Decimal("50000"), stop=0.025, fill_time=_t())
        pos2.update_price(Decimal("55000"))  # high watermark
        state2 = PositionExitState(position_mode="runner")

        drop = Decimal("52800")  # 4% below 55000, 5.6% above entry → still runner
        ctx = _ctx(pos2, drop, _t(minute=30))
        r = check_layer5_runner(ctx, TIER_A_PROFILE, state2)
        assert r.triggered
        assert r.exit_reason == "runner_trailing"

    def test_momentum_collapse_downgrades(self):
        pos = _pos(entry=Decimal("50000"), stop=0.025, fill_time=_t())
        state = PositionExitState(position_mode="runner")
        feat = _features(returns_z=-2.0)
        ctx = _ctx(pos, Decimal("51500"), _t(minute=10), feat)
        r = check_layer5_runner(ctx, TIER_A_PROFILE, state)
        assert state.position_mode == "winner_protection"
        assert "downgraded" in r.detail

    def test_not_runner_yet(self):
        pos = _pos(entry=Decimal("50000"), fill_time=_t())
        state = PositionExitState()
        ctx = _ctx(pos, Decimal("50500"), _t(minute=5))  # only 1%
        r = check_layer5_runner(ctx, TIER_A_PROFILE, state)
        assert not r.triggered
        assert state.position_mode == "normal"
