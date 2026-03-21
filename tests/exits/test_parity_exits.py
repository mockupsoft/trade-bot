from datetime import UTC, datetime
from decimal import Decimal

from cte.execution.position import PaperPosition
from cte.exits.config import TIER_A_PROFILE, TIER_C_PROFILE
from cte.exits.layers import (
    ExitContext,
    PositionExitState,
    check_layer2_thesis_failure,
    check_layer4_winner_protection,
    check_layer5_runner,
)
from tests.exits.test_layers import _features


def _t(minute=0, second=0):
    return datetime(2024, 1, 1, 12, minute, second, tzinfo=UTC)

def _short_pos(entry=Decimal("50000"), qty=Decimal("1"), tier="A", stop=0.025, fill_time=None) -> PaperPosition:
    p = PaperPosition(
        symbol="BTCUSDT", direction="short", signal_tier=tier,
        quantity=qty, stop_loss_pct=stop,
    )
    p.open(entry, fill_time or _t())
    return p

def _ctx(pos, current_price, now=None, features=None) -> ExitContext:
    pos.update_price(current_price)
    return ExitContext(
        position=pos,
        current_price=current_price,
        now=now or _t(),
        features=features,
    )

class TestLayer2ThesisFailureParity:
    def test_short_tfi_flip_positive(self):
        pos = _short_pos(tier="C")
        state = PositionExitState()
        # For shorts, positive TFI (buy dominant) means thesis fails
        feat = _features(tfi=0.3, returns_z=-1.0)
        ctx = _ctx(pos, Decimal("49000"), _t(), feat)
        r = check_layer2_thesis_failure(ctx, TIER_C_PROFILE, state)
        assert r.triggered
        assert "TFI=0.30 > 0.1" in r.detail

    def test_short_momentum_surge(self):
        pos = _short_pos(tier="C")
        state = PositionExitState()
        # For shorts, momentum surging upward (e.g. returns_z > 1.5) means thesis fails
        feat = _features(returns_z=2.0, tfi=-0.1)
        ctx = _ctx(pos, Decimal("51000"), _t(), feat)
        r = check_layer2_thesis_failure(ctx, TIER_C_PROFILE, state)
        assert r.triggered
        assert "returns_z=2.00 > 1.0" in r.detail

    def test_short_liquidation_shift(self):
        pos = _short_pos(tier="C")
        state = PositionExitState()
        # For shorts, negative liquidation imbalance (shorts liquidated -> bullish squeeze) means thesis fails
        feat = _features(liq_imb=-0.4)
        ctx = _ctx(pos, Decimal("51000"), _t(), feat)
        r = check_layer2_thesis_failure(ctx, TIER_C_PROFILE, state)
        assert r.triggered
        assert "liq_imbalance=-0.40 < -0.3" in r.detail

class TestLayer4WinnerProtectionParity:
    def test_short_winner_protection_trailing_from_low(self):
        pos = _short_pos(entry=Decimal("50000"))
        state = PositionExitState()
        profile = TIER_A_PROFILE

        # Move price down to make it a winner (gain > 1.5% -> e.g. price drops to 48000 = 4% gain)
        ctx1 = _ctx(pos, Decimal("48000"), _t(minute=5))
        r1 = check_layer4_winner_protection(ctx1, profile, state)
        assert not r1.triggered
        assert state.position_mode == "winner_protection"
        assert pos.lowest_price == Decimal("48000")

        # Price rebounds up. Drawdown from low is calculated as (current - low) / low.
        # Trailing pct for Tier A winner is 2.0% (0.020)
        # 48000 * 1.02 = 48960. A price above 48960 will trigger.
        ctx2 = _ctx(pos, Decimal("49000"), _t(minute=10))
        r2 = check_layer4_winner_protection(ctx2, profile, state)
        assert r2.triggered
        assert "winner_trailing" in r2.exit_reason
        assert "low 48000" in r2.detail

class TestLayer5RunnerParity:
    def test_short_runner_trailing_from_low(self):
        pos = _short_pos(entry=Decimal("50000"))
        state = PositionExitState()
        profile = TIER_A_PROFILE

        # Move price way down to make it a runner (gain > 3.0% -> price drops to 45000 = 10% gain)
        ctx1 = _ctx(pos, Decimal("45000"), _t(minute=5))
        r1 = check_layer5_runner(ctx1, profile, state)
        assert not r1.triggered
        assert state.position_mode == "runner"
        assert pos.lowest_price == Decimal("45000")

        # Price rebounds up. Runner trailing is 3.5% (0.035) for Tier A
        # 45000 * 1.035 = 46575
        ctx2 = _ctx(pos, Decimal("46600"), _t(minute=10))
        r2 = check_layer5_runner(ctx2, profile, state)
        assert r2.triggered
        assert "runner_trailing" in r2.exit_reason
        assert "low 45000" in r2.detail

    def test_short_runner_downgrade(self):
        pos = _short_pos(entry=Decimal("50000"))
        state = PositionExitState(position_mode="runner")
        profile = TIER_A_PROFILE

        # Momentum surges positive for short -> runner downgraded
        feat = _features(returns_z=1.6)
        ctx = _ctx(pos, Decimal("45000"), _t(minute=10), feat)
        r = check_layer5_runner(ctx, profile, state)
        assert not r.triggered
        assert state.position_mode == "winner_protection"
        assert "Runner downgraded" in r.detail
