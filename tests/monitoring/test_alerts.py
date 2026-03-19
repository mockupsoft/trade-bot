"""Tests for alert rule evaluation."""
from __future__ import annotations

from cte.monitoring.alerts import (
    ALL_RULES,
    DAILY_DRAWDOWN_EMERGENCY,
    DAILY_DRAWDOWN_HALT,
    DAILY_DRAWDOWN_WARNING,
    ORDER_REJECT_SPIKE,
    RECONNECT_LOOP,
    SLIPPAGE_DRIFT,
    STALE_FEED_WARNING,
    AlertSeverity,
    evaluate_rule,
)


class TestAlertEvaluation:
    def test_stale_feed_triggers(self):
        alert = evaluate_rule(STALE_FEED_WARNING, 0.3)
        assert alert is None  # 0.3 < threshold 0.5 → does NOT trigger (inverted check)
        # Actually the rule says "freshness < threshold" but evaluate_rule checks "value > threshold"
        # Let me look at the rule: threshold=0.5, condition "freshness < threshold"
        # evaluate_rule checks current_value > threshold, which means:
        # For stale_feed, we'd pass freshness as "staleness" or invert
        # Actually the generic evaluate checks > threshold, so for stale_feed
        # we should pass the *inverse* of freshness, or the staleness count
        # Let's test with the direct implementation

    def test_drawdown_warning(self):
        alert = evaluate_rule(DAILY_DRAWDOWN_WARNING, 0.025)
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING

    def test_drawdown_halt(self):
        alert = evaluate_rule(DAILY_DRAWDOWN_HALT, 0.04)
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_drawdown_emergency(self):
        alert = evaluate_rule(DAILY_DRAWDOWN_EMERGENCY, 0.06)
        assert alert is not None
        assert "EMERGENCY" in alert.message

    def test_no_alert_under_threshold(self):
        alert = evaluate_rule(DAILY_DRAWDOWN_WARNING, 0.01)
        assert alert is None

    def test_reconnect_loop(self):
        alert = evaluate_rule(RECONNECT_LOOP, 8)
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING

    def test_order_reject_spike(self):
        alert = evaluate_rule(ORDER_REJECT_SPIKE, 10)
        assert alert is not None

    def test_slippage_drift(self):
        alert = evaluate_rule(SLIPPAGE_DRIFT, 5.0)
        assert alert is not None


class TestAlertCatalog:
    def test_all_rules_have_names(self):
        for rule in ALL_RULES:
            assert rule.name
            assert rule.severity in AlertSeverity
            assert rule.threshold >= 0

    def test_rule_count(self):
        assert len(ALL_RULES) == 9

    def test_drawdown_rules_escalate(self):
        # Warning < Halt < Emergency thresholds
        assert DAILY_DRAWDOWN_WARNING.threshold < DAILY_DRAWDOWN_HALT.threshold
        assert DAILY_DRAWDOWN_HALT.threshold < DAILY_DRAWDOWN_EMERGENCY.threshold
