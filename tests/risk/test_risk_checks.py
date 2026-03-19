"""Tests for risk check implementations."""
from __future__ import annotations

from decimal import Decimal

from cte.risk.checks import (
    check_correlation,
    check_daily_drawdown,
    check_emergency_stop,
    check_max_position_size,
    check_total_exposure,
)


class TestMaxPositionSize:
    def test_within_limit(self):
        result = check_max_position_size(
            requested_notional=Decimal("400"),
            portfolio_value=Decimal("10000"),
            max_position_pct=0.05,
        )
        assert result.passed
        assert result.value == 0.04

    def test_exceeds_limit(self):
        result = check_max_position_size(
            requested_notional=Decimal("600"),
            portfolio_value=Decimal("10000"),
            max_position_pct=0.05,
        )
        assert not result.passed

    def test_zero_portfolio(self):
        result = check_max_position_size(
            requested_notional=Decimal("100"),
            portfolio_value=Decimal("0"),
            max_position_pct=0.05,
        )
        assert not result.passed


class TestTotalExposure:
    def test_within_limit(self):
        result = check_total_exposure(
            current_exposure=Decimal("500"),
            new_notional=Decimal("300"),
            portfolio_value=Decimal("10000"),
            max_exposure_pct=0.15,
        )
        assert result.passed

    def test_exceeds_limit(self):
        result = check_total_exposure(
            current_exposure=Decimal("1000"),
            new_notional=Decimal("600"),
            portfolio_value=Decimal("10000"),
            max_exposure_pct=0.15,
        )
        assert not result.passed


class TestDailyDrawdown:
    def test_within_limit(self):
        result = check_daily_drawdown(
            current_drawdown=0.02,
            max_drawdown_pct=0.03,
        )
        assert result.passed

    def test_exceeds_limit(self):
        result = check_daily_drawdown(
            current_drawdown=0.04,
            max_drawdown_pct=0.03,
        )
        assert not result.passed


class TestCorrelation:
    def test_no_open_positions(self):
        result = check_correlation("BTCUSDT", [], 0.85)
        assert result.passed

    def test_correlated_pair(self):
        result = check_correlation("ETHUSDT", ["BTCUSDT"], 0.80)
        assert not result.passed

    def test_uncorrelated(self):
        result = check_correlation("BTCUSDT", ["UNKNOWN"], 0.85)
        assert result.passed


class TestEmergencyStop:
    def test_normal(self):
        result = check_emergency_stop(0.03, 0.05)
        assert result.passed

    def test_emergency(self):
        result = check_emergency_stop(0.06, 0.05)
        assert not result.passed
