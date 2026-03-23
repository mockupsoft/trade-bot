"""Tests for PaperPosition state machine and analytics."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from cte.execution.position import PaperPosition, PositionStatus


def _utc(year=2024, month=1, day=1, hour=12, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


class TestPositionLifecycle:
    def test_pending_to_open(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long")
        assert pos.status == PositionStatus.PENDING

        pos.open(Decimal("50000"), _utc())
        assert pos.status == PositionStatus.OPEN
        assert pos.entry_price == Decimal("50000")
        assert pos.fill_price == Decimal("50000")

    def test_open_to_closed(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("51000"), _utc(second=30), "take_profit", "Gain 2%")
        assert pos.status == PositionStatus.CLOSED
        assert pos.exit_price == Decimal("51000")
        assert pos.exit_reason == "take_profit"

    def test_short_position_lifecycle(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="short", quantity=Decimal("1"))
        assert pos.status == PositionStatus.PENDING

        pos.open(Decimal("50000"), _utc())
        assert pos.status == PositionStatus.OPEN
        assert pos.entry_price == Decimal("50000")

        pos.close(Decimal("49000"), _utc(second=30), "take_profit", "Gain 2%")
        assert pos.status == PositionStatus.CLOSED
        assert pos.realized_pnl == Decimal("1000")

    def test_cannot_open_twice(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long")
        pos.open(Decimal("50000"), _utc())
        with pytest.raises(ValueError, match="Cannot open"):
            pos.open(Decimal("50000"), _utc())

    def test_cannot_close_pending(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long")
        with pytest.raises(ValueError, match="Cannot close"):
            pos.close(Decimal("50000"), _utc(), "test")

    def test_state_transitions_recorded(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("51000"), _utc(second=30), "take_profit")
        assert len(pos.state_transitions) == 2
        assert pos.state_transitions[0][0] == "pending"
        assert pos.state_transitions[0][1] == "open"
        assert pos.state_transitions[1][1] == "closed"


class TestPartialVenueExit:
    def test_partial_reduce_then_close_accumulates_pnl(self):
        pos = PaperPosition(
            symbol="BTCUSDT",
            direction="long",
            quantity=Decimal("1"),
            estimated_fees_usd=Decimal("0"),
            stop_loss_pct=0.02,
        )
        pos.open(Decimal("50000"), _utc())
        pos.apply_external_partial_reduce(
            Decimal("0.4"),
            Decimal("51000"),
            _utc(second=10),
            additional_exit_fees_usd=Decimal("0"),
        )
        assert pos.status == PositionStatus.REDUCED
        assert pos.quantity == Decimal("0.6")
        assert pos.realized_pnl == Decimal("400")  # (51000-50000)*0.4
        pos.close(Decimal("52000"), _utc(second=30), "take_profit")
        # 400 + (52000-50000)*0.6 = 400 + 1200 = 1600
        assert pos.realized_pnl == Decimal("1600")


class TestPnL:
    def test_long_winning_trade(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("2"))
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("51000"), _utc(second=30), "take_profit")
        assert pos.realized_pnl == Decimal("2000")  # (51000-50000)*2, minus small fee
        assert pos.is_winner

    def test_long_losing_trade(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("49000"), _utc(second=30), "stop_loss")
        assert pos.realized_pnl < 0

    def test_unrealized_pnl_updates(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())
        pos.update_price(Decimal("50500"))
        assert pos.unrealized_pnl == Decimal("500")

    def test_short_unrealized_pnl_updates(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="short", quantity=Decimal("2"))
        pos.open(Decimal("50000"), _utc())
        pos.update_price(Decimal("49000"))
        assert pos.unrealized_pnl == Decimal("2000")  # (50000 - 49000) * 2
        pos.update_price(Decimal("51000"))
        assert pos.unrealized_pnl == Decimal("-2000")

    def test_short_realized_pnl_at_close(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="short", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())
        # close at 49000 -> gross 1000
        pos.close(Decimal("49000"), _utc(), "tp")
        assert pos.realized_pnl == Decimal("1000")

    def test_fees_deducted(self):
        pos = PaperPosition(
            symbol="BTCUSDT", direction="long", quantity=Decimal("1"),
            estimated_fees_usd=Decimal("10"),
        )
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("50100"), _utc(second=30), "take_profit")
        # PnL = 100 - 10 fees = 90
        assert pos.realized_pnl == Decimal("90")


class TestMFEMAE:
    def test_mfe_tracks_best(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())

        pos.update_price(Decimal("51000"))  # +2%
        pos.update_price(Decimal("50500"))  # pull back
        pos.update_price(Decimal("50200"))

        assert pos.mfe_pct == pytest.approx(0.02)  # 51000 was the best
        assert pos.highest_price == Decimal("51000")

    def test_mae_tracks_worst(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())

        pos.update_price(Decimal("49000"))  # -2%
        pos.update_price(Decimal("49500"))  # recovery

        assert pos.mae_pct == pytest.approx(0.02)
        assert pos.lowest_price == Decimal("49000")

    def test_mfe_mae_usd(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("0.1"))
        pos.open(Decimal("50000"), _utc())

        pos.update_price(Decimal("52000"))  # +4% → mfe_usd = 0.04 * 50000 * 0.1 = 200
        assert pos.mfe_usd == Decimal("200.0")

    def test_short_mfe_mae_tracking(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="short", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())

        # Move to 49000 (+2% MFE, 0 MAE for short)
        pos.update_price(Decimal("49000"))
        assert pos.mfe_pct == pytest.approx(0.02)
        assert pos.mae_pct == 0.0

        # Move to 52000 (MFE stays 2%, MAE hits 4% for short)
        pos.update_price(Decimal("52000"))
        assert pos.mfe_pct == pytest.approx(0.02)
        assert pos.mae_pct == pytest.approx(0.04)

        assert pos.mfe_usd == Decimal("1000")
        assert pos.mae_usd == Decimal("2000")

    def test_no_updates_when_closed(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("51000"), _utc(second=10), "tp")

        old_mfe = pos.mfe_pct
        pos.update_price(Decimal("55000"))  # should not update
        assert pos.mfe_pct == old_mfe


class TestRMultiple:
    def test_r_multiple_positive(self):
        pos = PaperPosition(
            symbol="BTCUSDT", direction="long", quantity=Decimal("1"),
            stop_loss_pct=0.02,
        )
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("52000"), _utc(second=30), "take_profit")
        # Risk = 50000 * 0.02 * 1 = 1000
        # PnL = 2000
        # R = 2000 / 1000 = 2.0
        assert pos.r_multiple == pytest.approx(2.0)

    def test_r_multiple_negative(self):
        pos = PaperPosition(
            symbol="BTCUSDT", direction="long", quantity=Decimal("1"),
            stop_loss_pct=0.02,
        )
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("49000"), _utc(second=30), "stop_loss")
        assert pos.r_multiple is not None
        assert pos.r_multiple < 0

    def test_r_multiple_none_without_stop(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        pos.open(Decimal("50000"), _utc())
        pos.close(Decimal("51000"), _utc(second=30), "manual")
        assert pos.r_multiple is None


class TestEntryLatency:
    def test_latency_calculated(self):
        signal_time = _utc(second=0)
        fill_time = _utc(second=0) + timedelta(milliseconds=150)

        pos = PaperPosition(symbol="BTCUSDT", signal_time=signal_time)
        pos.open(Decimal("50000"), fill_time)
        assert pos.entry_latency_ms == 150

    def test_hold_duration(self):
        pos = PaperPosition(symbol="BTCUSDT", direction="long", quantity=Decimal("1"))
        fill_time = _utc(minute=0)
        close_time = _utc(minute=5)
        pos.open(Decimal("50000"), fill_time)
        pos.close(Decimal("50100"), close_time, "timeout")
        assert pos.hold_duration_seconds == 300


class TestSerialization:
    def test_to_dict_complete(self):
        pos = PaperPosition(
            symbol="BTCUSDT", direction="long", quantity=Decimal("1"),
            signal_tier="A", entry_reason="Strong momentum", composite_score=0.82,
            stop_loss_pct=0.02,
        )
        pos.open(Decimal("50000"), _utc())
        pos.update_price(Decimal("51000"))
        pos.close(Decimal("50800"), _utc(second=30), "trailing_stop", "Drawdown from high")

        d = pos.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert d["signal_tier"] == "A"
        assert d["status"] == "closed"
        assert d["exit_reason"] == "trailing_stop"
        assert float(d["mfe_pct"]) > 0
        assert d["r_multiple"] is not None
