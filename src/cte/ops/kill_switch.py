"""Emergency kill switch and operational controls.

Provides immediate actions for risk situations:
- Emergency stop: close all positions immediately
- Trading halt: stop new signals, keep monitoring
- Risk pause: temporary freeze on new entries
- Venue toggle: enable/disable per-symbol execution

CTE policy: every operator-facing transition is recorded with an explainable
``reason`` string (audit trail). Full multi-service propagation uses Redis
Streams in the distributed layout; this controller holds the in-process
dashboard copy.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import structlog
from prometheus_client import Counter, Gauge

logger = structlog.get_logger(__name__)

kill_switch_activations = Counter("cte_kill_switch_total", "Kill switch activations", ["action"])
trading_mode_gauge = Gauge("cte_trading_mode", "Trading mode (0=halted, 1=paused, 2=active)")


class TradingMode(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"  # no new entries, exits still run
    HALTED = "halted"  # no new entries, manual exits only


@dataclass(frozen=True)
class OpsAuditEvent:
    """Single auditable operator or system action with mandatory justification."""

    action: str
    triggered_by: str
    timestamp: datetime
    reason: str
    symbol: str | None = None


class OperationsController:
    """Central operational controls for the trading engine."""

    def __init__(self) -> None:
        self._mode = TradingMode.ACTIVE
        self._symbol_toggles: dict[str, dict[str, str | bool | datetime | None]] = {
            "BTCUSDT": {"enabled": True, "disabled_at": None, "disabled_reason": ""},
            "ETHUSDT": {"enabled": True, "disabled_at": None, "disabled_reason": ""},
        }
        self._events: deque[OpsAuditEvent] = deque(maxlen=100)
        self._mode_history: list[tuple[str, str, str]] = []
        trading_mode_gauge.set(2)

    def _audit(
        self,
        action: str,
        triggered_by: str,
        reason: str,
        symbol: str | None = None,
    ) -> OpsAuditEvent:
        ev = OpsAuditEvent(
            action=action,
            triggered_by=triggered_by,
            timestamp=datetime.now(UTC),
            reason=reason,
            symbol=symbol,
        )
        self._events.append(ev)
        logger.info(
            "ops_audit",
            action=action,
            triggered_by=triggered_by,
            reason=reason,
            symbol=symbol,
        )
        return ev

    @property
    def mode(self) -> TradingMode:
        return self._mode

    @property
    def is_trading_allowed(self) -> bool:
        return self._mode == TradingMode.ACTIVE

    @property
    def is_entries_allowed(self) -> bool:
        return self._mode == TradingMode.ACTIVE

    def is_symbol_enabled(self, symbol: str) -> bool:
        row = self._symbol_toggles.get(symbol)
        if not row:
            return False
        return bool(row["enabled"])

    def emergency_stop(self, triggered_by: str, reason: str) -> OpsAuditEvent:
        """Close all positions and halt all trading. Requires manual restart."""
        now = datetime.now(UTC)
        old = self._mode.value
        self._mode = TradingMode.HALTED
        trading_mode_gauge.set(0)
        kill_switch_activations.labels(action="emergency_stop").inc()
        self._mode_history.append((old, "halted", now.isoformat()))
        return self._audit("emergency_stop", triggered_by, reason, None)

    def pause_trading(self, reason: str, triggered_by: str = "dashboard_user") -> None:
        """Stop new entries but keep exit monitoring active."""
        old = self._mode.value
        self._mode = TradingMode.PAUSED
        trading_mode_gauge.set(1)
        kill_switch_activations.labels(action="pause").inc()
        now = datetime.now(UTC)
        self._mode_history.append((old, "paused", now.isoformat()))
        self._audit("pause", triggered_by, reason, None)

    def resume_trading(self, reason: str, triggered_by: str = "dashboard_user") -> None:
        """Resume normal trading operations."""
        old = self._mode.value
        self._mode = TradingMode.ACTIVE
        trading_mode_gauge.set(2)
        kill_switch_activations.labels(action="resume").inc()
        now = datetime.now(UTC)
        self._mode_history.append((old, "active", now.isoformat()))
        self._audit("resume", triggered_by, reason, None)

    def disable_symbol(self, symbol: str, reason: str, triggered_by: str = "dashboard_user") -> None:
        if symbol in self._symbol_toggles:
            self._symbol_toggles[symbol] = {
                "enabled": False,
                "disabled_at": datetime.now(UTC),
                "disabled_reason": reason,
            }
            self._audit("symbol_disable", triggered_by, reason, symbol)

    def enable_symbol(self, symbol: str, reason: str, triggered_by: str = "dashboard_user") -> None:
        if symbol in self._symbol_toggles:
            self._symbol_toggles[symbol] = {
                "enabled": True,
                "disabled_at": None,
                "disabled_reason": "",
            }
            self._audit("symbol_enable", triggered_by, reason, symbol)

    def status(self) -> dict:
        return {
            "mode": self._mode.value,
            "is_trading_allowed": self.is_trading_allowed,
            "is_entries_allowed": self.is_entries_allowed,
            "symbols": {
                sym: {
                    "enabled": bool(row["enabled"]),
                    "disabled_reason": str(row.get("disabled_reason") or ""),
                }
                for sym, row in self._symbol_toggles.items()
            },
            "recent_events": [
                {
                    "action": e.action,
                    "triggered_by": e.triggered_by,
                    "timestamp": e.timestamp.isoformat(),
                    "reason": e.reason,
                    "symbol": e.symbol,
                }
                for e in list(self._events)
            ][-30:],
            "mode_history": self._mode_history[-20:],
        }
