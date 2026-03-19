"""Risk manager with absolute veto power.

Intercepts all signals and applies a chain of risk checks.
If any check fails, the signal is rejected with a detailed reason.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import structlog
from prometheus_client import Counter, Gauge

from cte.core.events import (
    STREAM_KEYS,
    RiskAssessmentEvent,
    RiskCheckResult,
    RiskDecision,
    SignalEvent,
    Symbol,
)
from cte.core.settings import RiskSettings
from cte.core.streams import StreamPublisher
from cte.risk.checks import (
    check_correlation,
    check_daily_drawdown,
    check_emergency_stop,
    check_max_position_size,
    check_total_exposure,
)

logger = structlog.get_logger(__name__)

risk_decisions_total = Counter(
    "cte_risk_decisions_total", "Total risk decisions", ["symbol", "decision"]
)
risk_exposure_pct = Gauge("cte_risk_exposure_pct", "Current total exposure percentage")
risk_daily_drawdown = Gauge("cte_risk_daily_drawdown_pct", "Current daily drawdown percentage")


class PortfolioState:
    """Tracks current portfolio state for risk calculations."""

    def __init__(self, initial_capital: Decimal = Decimal("10000")) -> None:
        self.portfolio_value = initial_capital
        self.current_exposure = Decimal("0")
        self.daily_pnl = Decimal("0")
        self.daily_high_water = initial_capital
        self.open_positions: dict[str, Decimal] = {}

    @property
    def daily_drawdown(self) -> float:
        if self.daily_high_water <= 0:
            return 0.0
        return float((self.daily_high_water - self.portfolio_value) / self.daily_high_water)

    @property
    def open_symbols(self) -> list[str]:
        return list(self.open_positions.keys())

    def update_exposure(self, symbol: str, notional: Decimal) -> None:
        self.open_positions[symbol] = notional
        self.current_exposure = sum(self.open_positions.values())
        risk_exposure_pct.set(
            float(self.current_exposure / self.portfolio_value)
            if self.portfolio_value > 0
            else 0
        )

    def remove_position(self, symbol: str) -> None:
        self.open_positions.pop(symbol, None)
        self.current_exposure = sum(self.open_positions.values())

    def update_daily_drawdown(self) -> None:
        risk_daily_drawdown.set(self.daily_drawdown)


class RiskManager:
    """Central risk manager with absolute veto authority."""

    def __init__(
        self,
        settings: RiskSettings,
        publisher: StreamPublisher,
        portfolio: PortfolioState | None = None,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self.portfolio = portfolio or PortfolioState()

    async def assess_signal(
        self,
        signal: SignalEvent,
        estimated_notional: Decimal,
    ) -> RiskAssessmentEvent:
        """Run all risk checks against a signal. Returns approval or rejection."""
        checks: list[RiskCheckResult] = []

        checks.append(check_emergency_stop(
            current_drawdown=self.portfolio.daily_drawdown,
            emergency_threshold=self._settings.emergency_stop_drawdown_pct,
        ))

        checks.append(check_daily_drawdown(
            current_drawdown=self.portfolio.daily_drawdown,
            max_drawdown_pct=self._settings.max_daily_drawdown_pct,
        ))

        checks.append(check_max_position_size(
            requested_notional=estimated_notional,
            portfolio_value=self.portfolio.portfolio_value,
            max_position_pct=self._settings.max_position_pct,
        ))

        checks.append(check_total_exposure(
            current_exposure=self.portfolio.current_exposure,
            new_notional=estimated_notional,
            portfolio_value=self.portfolio.portfolio_value,
            max_exposure_pct=self._settings.max_total_exposure_pct,
        ))

        checks.append(check_correlation(
            symbol=signal.symbol.value,
            open_symbols=self.portfolio.open_symbols,
            max_correlation=self._settings.max_correlation,
        ))

        failed_checks = [c for c in checks if not c.passed]

        if failed_checks:
            decision = RiskDecision.REJECTED
            reason = f"Failed checks: {', '.join(c.check_name for c in failed_checks)}"
        else:
            decision = RiskDecision.APPROVED
            reason = "All risk checks passed"

        assessment = RiskAssessmentEvent(
            signal_id=signal.event_id,
            symbol=signal.symbol,
            decision=decision,
            reason=reason,
            checks_performed=checks,
        )

        await self._publisher.publish(STREAM_KEYS["risk"], assessment)

        risk_decisions_total.labels(
            symbol=signal.symbol.value, decision=decision.value
        ).inc()

        await logger.ainfo(
            "risk_assessment",
            signal_id=str(signal.event_id),
            symbol=signal.symbol.value,
            decision=decision.value,
            reason=reason,
            failed_checks=[c.check_name for c in failed_checks],
        )

        return assessment
