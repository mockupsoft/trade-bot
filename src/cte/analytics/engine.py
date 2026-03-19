"""Analytics engine for post-trade analysis.

Consumes exit and order events to compute running performance metrics:
PnL, win rate, Sharpe ratio, drawdown curves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

import structlog
from prometheus_client import Gauge

from cte.core.events import ExitEvent, OrderEvent

logger = structlog.get_logger(__name__)

pnl_total_gauge = Gauge("cte_pnl_total_usd", "Total realized PnL in USD")
pnl_daily_gauge = Gauge("cte_pnl_daily_usd", "Daily realized PnL in USD")
win_rate_gauge = Gauge("cte_win_rate", "Win rate (0-1)")
sharpe_gauge = Gauge("cte_sharpe_ratio", "Rolling Sharpe ratio")
max_drawdown_gauge = Gauge("cte_max_drawdown_pct", "Maximum drawdown percentage")


@dataclass
class TradeRecord:
    """Completed trade record for analytics."""

    symbol: str
    pnl: Decimal
    exit_reason: str
    hold_seconds: int
    timestamp: datetime


@dataclass
class DailyMetrics:
    """Per-day aggregated metrics."""

    date: date
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    pnl_series: list[float] = field(default_factory=list)


class AnalyticsEngine:
    """Computes and exposes trading performance metrics."""

    def __init__(self, initial_capital: Decimal = Decimal("10000")) -> None:
        self._initial_capital = initial_capital
        self._equity = initial_capital
        self._high_water_mark = initial_capital
        self._total_pnl = Decimal("0")
        self._trades: list[TradeRecord] = []
        self._daily: dict[date, DailyMetrics] = {}

    async def record_exit(self, event: ExitEvent) -> None:
        """Process an exit event into analytics."""
        record = TradeRecord(
            symbol=event.symbol.value,
            pnl=event.pnl,
            exit_reason=event.exit_reason.value,
            hold_seconds=event.hold_duration_seconds,
            timestamp=event.timestamp,
        )
        self._trades.append(record)

        self._equity += event.pnl
        self._total_pnl += event.pnl

        if self._equity > self._high_water_mark:
            self._high_water_mark = self._equity

        today = event.timestamp.date()
        daily = self._get_daily(today)
        daily.trades += 1
        daily.total_pnl += event.pnl
        daily.pnl_series.append(float(event.pnl))

        if event.pnl > 0:
            daily.wins += 1
        else:
            daily.losses += 1

        self._update_prometheus()

        await logger.ainfo(
            "trade_recorded",
            symbol=event.symbol.value,
            pnl=str(event.pnl),
            exit_reason=event.exit_reason.value,
            total_pnl=str(self._total_pnl),
            equity=str(self._equity),
        )

    def _get_daily(self, d: date) -> DailyMetrics:
        if d not in self._daily:
            self._daily[d] = DailyMetrics(date=d)
        return self._daily[d]

    def _update_prometheus(self) -> None:
        pnl_total_gauge.set(float(self._total_pnl))
        win_rate_gauge.set(self.win_rate)
        max_drawdown_gauge.set(self.max_drawdown_pct)

        today = datetime.now(timezone.utc).date()
        daily = self._daily.get(today)
        if daily:
            pnl_daily_gauge.set(float(daily.total_pnl))

    @property
    def win_rate(self) -> float:
        if not self._trades:
            return 0.0
        wins = sum(1 for t in self._trades if t.pnl > 0)
        return wins / len(self._trades)

    @property
    def total_trades(self) -> int:
        return len(self._trades)

    @property
    def max_drawdown_pct(self) -> float:
        if self._high_water_mark <= 0:
            return 0.0
        return float((self._high_water_mark - self._equity) / self._high_water_mark)

    @property
    def sharpe_ratio(self) -> float | None:
        """Annualized Sharpe ratio from trade PnLs."""
        if len(self._trades) < 2:
            return None

        import numpy as np

        returns = np.array([float(t.pnl) for t in self._trades])
        mean_ret = np.mean(returns)
        std_ret = np.std(returns)

        if std_ret == 0:
            return None

        trades_per_day = max(1, len(self._trades) / max(1, len(self._daily)))
        annualization = (365 * trades_per_day) ** 0.5

        return float((mean_ret / std_ret) * annualization)

    def summary(self) -> dict:
        """Return a summary dictionary for API consumption."""
        return {
            "total_pnl": str(self._total_pnl),
            "equity": str(self._equity),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4) if self.sharpe_ratio else None,
            "high_water_mark": str(self._high_water_mark),
        }
