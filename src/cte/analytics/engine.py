"""Epoch-aware analytics engine with full breakdowns.

Replaces the basic PnL tracker with a rich analytics system that:
- Tags every trade with its epoch
- Computes all metrics via pure functions (analytics/metrics.py)
- Supports drill-down by symbol, venue, tier, exit reason
- Maintains daily summary aggregates
- Exposes Prometheus metrics for real-time monitoring
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from prometheus_client import Counter, Gauge, Histogram

from cte.analytics.metrics import CompletedTrade, compute_all_metrics

if TYPE_CHECKING:
    from datetime import date

    from cte.analytics.epochs import EpochManager
    from cte.execution.position import PaperPosition

logger = structlog.get_logger(__name__)

# ── Prometheus metrics ────────────────────────────────────────
trades_total = Counter(
    "cte_analytics_trades_total", "Total trades recorded", ["epoch", "symbol", "tier"]
)
pnl_gauge = Gauge("cte_analytics_pnl_total", "Total realized PnL", ["epoch"])
equity_gauge = Gauge("cte_analytics_equity", "Current equity", ["epoch"])
win_rate_gauge = Gauge("cte_analytics_win_rate", "Win rate", ["epoch"])
drawdown_gauge = Gauge("cte_analytics_max_drawdown_pct", "Max drawdown %", ["epoch"])
expectancy_gauge = Gauge("cte_analytics_expectancy", "Expectancy per trade", ["epoch"])
profit_factor_gauge = Gauge("cte_analytics_profit_factor", "Profit factor", ["epoch"])
daily_pnl_gauge = Gauge("cte_analytics_daily_pnl", "Daily PnL", ["epoch", "date"])
trade_pnl_hist = Histogram(
    "cte_analytics_trade_pnl", "Trade PnL distribution", ["epoch"],
    buckets=[-500, -200, -100, -50, -20, 0, 20, 50, 100, 200, 500, 1000],
)


class AnalyticsEngine:
    """Epoch-aware analytics engine with full drilldown support."""

    def __init__(
        self,
        epoch_manager: EpochManager,
        initial_capital: Decimal = Decimal("10000"),
    ) -> None:
        self._epochs = epoch_manager
        self._initial_capital = initial_capital
        self._trades: list[CompletedTrade] = []
        self._equity: dict[str, Decimal] = defaultdict(lambda: initial_capital)

    def record_trade(
        self,
        position: PaperPosition,
        venue: str = "binance",
        exit_layer: int = 0,
        was_profitable_at_exit: bool = False,
        position_mode: str = "normal",
        source: str = "paper_simulated",
        warmup_phase: str | None = None,
    ) -> CompletedTrade:
        """Record a completed trade from a closed position."""
        epoch = self._epochs.active_name

        wp = warmup_phase if warmup_phase is not None else getattr(
            position, "warmup_phase", "none"
        )
        trade = CompletedTrade(
            symbol=position.symbol,
            venue=venue,
            tier=position.signal_tier,
            epoch=epoch,
            direction=position.direction,
            source=source,
            pnl=position.realized_pnl,
            exit_reason=position.exit_reason,
            exit_layer=exit_layer,
            hold_seconds=position.hold_duration_seconds,
            r_multiple=position.r_multiple,
            entry_latency_ms=position.entry_latency_ms,
            modeled_slippage_bps=float(position.modeled_slippage_bps),
            mfe_pct=position.mfe_pct,
            mae_pct=position.mae_pct,
            was_profitable_at_exit=was_profitable_at_exit,
            position_mode=position_mode,
            warmup_phase=wp,
        )

        self._trades.append(trade)
        self._equity[epoch] += position.realized_pnl

        # Prometheus
        trades_total.labels(
            epoch=epoch, symbol=position.symbol, tier=position.signal_tier
        ).inc()
        pnl_gauge.labels(epoch=epoch).set(float(self._total_pnl(epoch)))
        equity_gauge.labels(epoch=epoch).set(float(self._equity[epoch]))
        trade_pnl_hist.labels(epoch=epoch).observe(float(position.realized_pnl))

        return trade

    def get_metrics(
        self,
        epoch: str | None = None,
        symbol: str | None = None,
        tier: str | None = None,
        venue: str | None = None,
        exit_reason: str | None = None,
    ) -> dict:
        """Compute metrics for a filtered set of trades."""
        filtered = self._filter_trades(epoch, symbol, tier, venue, exit_reason)
        return compute_all_metrics(filtered, float(self._initial_capital))

    def get_daily_summary(
        self, epoch: str | None = None, target_date: date | None = None
    ) -> dict:
        """Get daily aggregated metrics."""
        filtered = self._filter_trades(epoch=epoch)
        if not filtered:
            return {}

        by_date: dict[str, list[CompletedTrade]] = defaultdict(list)
        for t in filtered:
            # Use epoch as proxy for date grouping (trades don't carry date)
            by_date[t.epoch].append(t)

        return compute_all_metrics(filtered, float(self._initial_capital))

    def get_trades(
        self,
        epoch: str | None = None,
        symbol: str | None = None,
        tier: str | None = None,
        exit_reason: str | None = None,
        source: str | None = None,
        warmup_phase: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get individual trade records for drilldown."""
        filtered = self._filter_trades(
            epoch,
            symbol,
            tier,
            exit_reason=exit_reason,
            source=source,
            warmup_phase=warmup_phase,
        )
        tail = filtered[-limit:] if limit else filtered
        # Newest first for operator journal (last recorded appears at top).
        rows: list[dict] = []
        for t in reversed(tail):
            rows.append(
                {
                    "symbol": t.symbol,
                    "venue": t.venue,
                    "tier": t.tier,
                    "epoch": t.epoch,
                    "source": t.source,
                    "pnl": str(t.pnl),
                    "exit_reason": t.exit_reason,
                    "exit_layer": t.exit_layer,
                    "hold_seconds": t.hold_seconds,
                    "r_multiple": t.r_multiple,
                    "entry_latency_ms": t.entry_latency_ms,
                    "slippage_bps": t.modeled_slippage_bps,
                    "mfe_pct": t.mfe_pct,
                    "mae_pct": t.mae_pct,
                    "was_profitable_at_exit": t.was_profitable_at_exit,
                    "position_mode": t.position_mode,
                    "warmup_phase": t.warmup_phase,
                },
            )
        return rows

    def get_epoch_comparison(self, epoch_a: str, epoch_b: str) -> dict:
        """Compare metrics between two epochs (e.g., paper vs demo)."""
        trades_a = self._filter_trades(epoch=epoch_a)
        trades_b = self._filter_trades(epoch=epoch_b)

        from cte.analytics.metrics import slippage_drift

        return {
            epoch_a: compute_all_metrics(trades_a, float(self._initial_capital)),
            epoch_b: compute_all_metrics(trades_b, float(self._initial_capital)),
            "slippage_drift": slippage_drift(trades_a, trades_b),
        }

    def update_prometheus(self, epoch: str | None = None) -> None:
        """Update all Prometheus gauges for an epoch."""
        ep = epoch or self._epochs.active_name
        trades = self._filter_trades(epoch=ep)

        from cte.analytics.metrics import (
            expectancy as calc_expectancy,
        )
        from cte.analytics.metrics import (
            max_drawdown_pct,
        )
        from cte.analytics.metrics import (
            profit_factor as calc_pf,
        )
        from cte.analytics.metrics import (
            win_rate as calc_wr,
        )

        win_rate_gauge.labels(epoch=ep).set(calc_wr(trades))
        drawdown_gauge.labels(epoch=ep).set(max_drawdown_pct(trades, float(self._initial_capital)))
        expectancy_gauge.labels(epoch=ep).set(calc_expectancy(trades))
        pf = calc_pf(trades)
        if pf is not None:
            profit_factor_gauge.labels(epoch=ep).set(pf)

    def _total_pnl(self, epoch: str) -> Decimal:
        return sum(
            (t.pnl for t in self._trades if t.epoch == epoch), Decimal("0")
        )

    def _filter_trades(
        self,
        epoch: str | None = None,
        symbol: str | None = None,
        tier: str | None = None,
        venue: str | None = None,
        exit_reason: str | None = None,
        source: str | None = None,
        warmup_phase: str | None = None,
    ) -> list[CompletedTrade]:
        result = self._trades
        if epoch:
            result = [t for t in result if t.epoch == epoch]
        if symbol:
            result = [t for t in result if t.symbol == symbol]
        if source:
            result = [t for t in result if t.source == source]
        if tier:
            result = [t for t in result if t.tier == tier]
        if venue:
            result = [t for t in result if t.venue == venue]
        if exit_reason:
            result = [t for t in result if t.exit_reason == exit_reason]
        if warmup_phase:
            result = [t for t in result if t.warmup_phase == warmup_phase]
        return result

    @property
    def total_trades(self) -> int:
        return len(self._trades)
