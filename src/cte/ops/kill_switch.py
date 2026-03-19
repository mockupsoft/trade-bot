"""Emergency kill switch and operational controls.

Provides immediate actions for risk situations:
- Emergency stop: close all positions immediately
- Trading halt: stop new signals, keep monitoring
- Risk pause: temporary freeze on new entries
- Venue toggle: enable/disable per-symbol execution
"""
from __future__ import annotations

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
    PAUSED = "paused"       # no new entries, exits still run
    HALTED = "halted"       # no new entries, manual exits only


@dataclass
class KillSwitchEvent:
    action: str
    triggered_by: str
    timestamp: datetime
    reason: str
    positions_closed: int = 0


@dataclass
class SymbolToggle:
    enabled: bool = True
    disabled_at: datetime | None = None
    disabled_reason: str = ""


class OperationsController:
    """Central operational controls for the trading engine."""

    def __init__(self) -> None:
        self._mode = TradingMode.ACTIVE
        self._symbol_toggles: dict[str, SymbolToggle] = {
            "BTCUSDT": SymbolToggle(),
            "ETHUSDT": SymbolToggle(),
        }
        self._events: list[KillSwitchEvent] = []
        self._mode_history: list[tuple[str, str, str]] = []
        trading_mode_gauge.set(2)

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
        toggle = self._symbol_toggles.get(symbol)
        return toggle.enabled if toggle else False

    def emergency_stop(self, triggered_by: str, reason: str) -> KillSwitchEvent:
        """Close all positions and halt all trading. Requires manual restart."""
        now = datetime.now(UTC)
        old = self._mode.value
        self._mode = TradingMode.HALTED
        trading_mode_gauge.set(0)
        kill_switch_activations.labels(action="emergency_stop").inc()

        event = KillSwitchEvent(
            action="emergency_stop",
            triggered_by=triggered_by,
            timestamp=now,
            reason=reason,
        )
        self._events.append(event)
        self._mode_history.append((old, "halted", now.isoformat()))
        return event

    def pause_trading(self, reason: str) -> None:
        """Stop new entries but keep exit monitoring active."""
        old = self._mode.value
        self._mode = TradingMode.PAUSED
        trading_mode_gauge.set(1)
        kill_switch_activations.labels(action="pause").inc()
        now = datetime.now(UTC)
        self._mode_history.append((old, "paused", now.isoformat()))

    def resume_trading(self) -> None:
        """Resume normal trading operations."""
        old = self._mode.value
        self._mode = TradingMode.ACTIVE
        trading_mode_gauge.set(2)
        kill_switch_activations.labels(action="resume").inc()
        now = datetime.now(UTC)
        self._mode_history.append((old, "active", now.isoformat()))

    def disable_symbol(self, symbol: str, reason: str) -> None:
        if symbol in self._symbol_toggles:
            self._symbol_toggles[symbol] = SymbolToggle(
                enabled=False,
                disabled_at=datetime.now(UTC),
                disabled_reason=reason,
            )

    def enable_symbol(self, symbol: str) -> None:
        if symbol in self._symbol_toggles:
            self._symbol_toggles[symbol] = SymbolToggle(enabled=True)

    def status(self) -> dict:
        return {
            "mode": self._mode.value,
            "is_trading_allowed": self.is_trading_allowed,
            "is_entries_allowed": self.is_entries_allowed,
            "symbols": {
                sym: {"enabled": t.enabled, "disabled_reason": t.disabled_reason}
                for sym, t in self._symbol_toggles.items()
            },
            "recent_events": [
                {
                    "action": e.action,
                    "triggered_by": e.triggered_by,
                    "timestamp": e.timestamp.isoformat(),
                    "reason": e.reason,
                }
                for e in self._events[-10:]
            ],
            "mode_history": self._mode_history[-20:],
        }
