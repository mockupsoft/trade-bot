"""Alert rule definitions for the CTE monitoring system.

Each rule is a pure function: takes current metric values → alert or not.
Rules are evaluated by a periodic alert checker, not by Prometheus
(though Prometheus alerting rules mirror these for redundancy).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class AlertRule:
    """Definition of an alert rule."""
    name: str
    severity: AlertSeverity
    condition: str
    threshold: float
    message_template: str


@dataclass(frozen=True)
class AlertEvent:
    """A fired alert."""
    rule_name: str
    severity: AlertSeverity
    message: str
    current_value: float
    threshold: float


# ── Alert Rule Catalog ────────────────────────────────────────

STALE_FEED_WARNING = AlertRule(
    name="stale_feed",
    severity=AlertSeverity.WARNING,
    condition="freshness_composite < threshold",
    threshold=0.5,
    message_template="Data freshness {value:.2f} below {threshold} for {symbol}",
)

STALE_FEED_CRITICAL = AlertRule(
    name="stale_feed_critical",
    severity=AlertSeverity.CRITICAL,
    condition="freshness_composite < threshold for > 5min",
    threshold=0.3,
    message_template="CRITICAL: Data stale for >5min, freshness={value:.2f}",
)

RECONNECT_LOOP = AlertRule(
    name="reconnect_loop",
    severity=AlertSeverity.WARNING,
    condition="reconnect_count_5min > threshold",
    threshold=5,
    message_template="Venue {venue} reconnected {value:.0f} times in 5min",
)

DAILY_DRAWDOWN_WARNING = AlertRule(
    name="daily_drawdown_warning",
    severity=AlertSeverity.WARNING,
    condition="daily_drawdown_pct > threshold",
    threshold=0.02,
    message_template="Daily drawdown {value:.2%} exceeds warning at {threshold:.2%}",
)

DAILY_DRAWDOWN_HALT = AlertRule(
    name="daily_drawdown_halt",
    severity=AlertSeverity.CRITICAL,
    condition="daily_drawdown_pct > threshold",
    threshold=0.03,
    message_template="HALT: Daily drawdown {value:.2%} exceeds {threshold:.2%}",
)

DAILY_DRAWDOWN_EMERGENCY = AlertRule(
    name="daily_drawdown_emergency",
    severity=AlertSeverity.CRITICAL,
    condition="daily_drawdown_pct > threshold",
    threshold=0.05,
    message_template="EMERGENCY: Close all positions. Drawdown {value:.2%}",
)

ORDER_REJECT_SPIKE = AlertRule(
    name="order_reject_spike",
    severity=AlertSeverity.WARNING,
    condition="reject_count_1h > threshold",
    threshold=5,
    message_template="Order rejections: {value:.0f} in last hour",
)

SLIPPAGE_DRIFT = AlertRule(
    name="slippage_drift",
    severity=AlertSeverity.WARNING,
    condition="live_slippage_bps - paper_slippage_bps > threshold",
    threshold=3.0,
    message_template="Slippage drift: live {value:.1f} bps above paper model",
)

RECONCILIATION_FAILURE = AlertRule(
    name="reconciliation_failure",
    severity=AlertSeverity.CRITICAL,
    condition="reconciliation_discrepancies > 0",
    threshold=0,
    message_template="Position reconciliation found {value:.0f} discrepancies",
)

ALL_RULES = [
    STALE_FEED_WARNING,
    STALE_FEED_CRITICAL,
    RECONNECT_LOOP,
    DAILY_DRAWDOWN_WARNING,
    DAILY_DRAWDOWN_HALT,
    DAILY_DRAWDOWN_EMERGENCY,
    ORDER_REJECT_SPIKE,
    SLIPPAGE_DRIFT,
    RECONCILIATION_FAILURE,
]


def evaluate_rule(rule: AlertRule, current_value: float) -> AlertEvent | None:
    """Evaluate a single alert rule. Returns AlertEvent if triggered."""
    if rule.name in ("reconciliation_failure",):
        triggered = current_value > rule.threshold
    else:
        triggered = current_value > rule.threshold

    if not triggered:
        return None

    return AlertEvent(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message_template.format(
            value=current_value, threshold=rule.threshold,
            symbol="", venue="",
        ),
        current_value=current_value,
        threshold=rule.threshold,
    )


PROMETHEUS_ALERT_RULES_YAML = """
groups:
  - name: cte_alerts
    rules:
      - alert: CTEStaleData
        expr: cte_sf_window_fill_pct{window="60s"} < 0.5
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Data freshness degraded for {{ $labels.symbol }}"

      - alert: CTEStaleDataCritical
        expr: cte_sf_window_fill_pct{window="60s"} < 0.3
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "CRITICAL: Data stale >5min for {{ $labels.symbol }}"

      - alert: CTEReconnectLoop
        expr: increase(cte_ws_reconnects_total[5m]) > 5
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.venue }} reconnected {{ $value }} times in 5min"

      - alert: CTEDailyDrawdownWarning
        expr: cte_analytics_max_drawdown_pct > 0.02
        labels:
          severity: warning
        annotations:
          summary: "Daily drawdown {{ $value | humanizePercentage }}"

      - alert: CTEDailyDrawdownHalt
        expr: cte_analytics_max_drawdown_pct > 0.03
        labels:
          severity: critical
        annotations:
          summary: "HALT new positions: drawdown {{ $value | humanizePercentage }}"

      - alert: CTEDailyDrawdownEmergency
        expr: cte_analytics_max_drawdown_pct > 0.05
        labels:
          severity: critical
        annotations:
          summary: "EMERGENCY: Close all positions. Drawdown {{ $value | humanizePercentage }}"

      - alert: CTEOrderRejectSpike
        expr: increase(cte_binance_requests_total{status="error"}[1h]) > 5
        labels:
          severity: warning
        annotations:
          summary: "{{ $value }} order rejections in last hour"

      - alert: CTEReconciliationFailure
        expr: cte_recon_discrepancies_total > 0
        labels:
          severity: critical
        annotations:
          summary: "Position reconciliation discrepancy detected"
"""
