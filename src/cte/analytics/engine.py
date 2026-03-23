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
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

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
    "cte_analytics_trade_pnl",
    "Trade PnL distribution",
    ["epoch"],
    buckets=[-500, -200, -100, -50, -20, 0, 20, 50, 100, 200, 500, 1000],
)


class AnalyticsEngine:
    """Epoch-aware analytics engine with full drilldown support."""

    def __init__(
        self,
        epoch_manager: EpochManager,
        initial_capital: Decimal = Decimal("10000"),
        persist_trade: Callable[[CompletedTrade], None] | None = None,
    ) -> None:
        self._epochs = epoch_manager
        self._initial_capital = initial_capital
        self._trades: list[CompletedTrade] = []
        self._equity: dict[str, Decimal] = defaultdict(lambda: initial_capital)
        self._persist_trade = persist_trade

    def set_trade_persist_callback(
        self,
        persist_trade: Callable[[CompletedTrade], None] | None,
    ) -> None:
        self._persist_trade = persist_trade

    def hydrate_trades(self, trades: list[CompletedTrade]) -> None:
        if not trades:
            return
        self._trades.extend(trades)
        for t in trades:
            self._equity[t.epoch] += t.pnl

    def record_trade(
        self,
        position: PaperPosition,
        venue: str = "binance",
        exit_layer: int = 0,
        was_profitable_at_exit: bool = False,
        position_mode: str = "normal",
        source: str = "paper_simulated",
        warmup_phase: str | None = None,
        execution_channel: str | None = None,
    ) -> CompletedTrade:
        """Record a completed trade from a closed position."""
        epoch = self._epochs.active_name

        wp = warmup_phase if warmup_phase is not None else getattr(position, "warmup_phase", "none")
        trade = CompletedTrade(
            symbol=position.symbol,
            venue=venue,
            tier=position.signal_tier,
            epoch=epoch,
            direction=position.direction,
            source=source,
            entry_price=position.entry_price,
            exit_price=position.exit_price,
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
            execution_channel=execution_channel,
            entry_reason_summary=(position.entry_reason or "")[:240],
            entry_time=position.fill_time.isoformat() if position.fill_time else None,
            exit_time=position.close_time.isoformat() if position.close_time else None,
            entry_notional_usd=(
                position.initial_notional_usd
                if position.initial_notional_usd > 0
                else position.notional_usd
            ),
            entry_composite_score=position.composite_score,
            entry_primary_score=position.primary_score,
            entry_context_multiplier=position.context_multiplier,
            entry_strongest_sub_score=position.strongest_sub_score,
            entry_strongest_sub_score_value=position.strongest_sub_score_value,
        )

        self._trades.append(trade)
        self._equity[epoch] += position.realized_pnl

        if self._persist_trade is not None:
            try:
                self._persist_trade(trade)
            except Exception as exc:
                logger.warning("analytics_trade_persist_failed", error=str(exc)[:300])

        # Prometheus
        trades_total.labels(epoch=epoch, symbol=position.symbol, tier=position.signal_tier).inc()
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

    def get_daily_summary(self, epoch: str | None = None, target_date: date | None = None) -> dict:
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
        venue: str | None = None,
        exit_reason: str | None = None,
        source: str | None = None,
        warmup_phase: str | None = None,
        direction: str | None = None,
        execution_channel: str | None = None,
        pnl_sign: str | None = None,
        hold_seconds_min: int | None = None,
        hold_seconds_max: int | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get individual trade records for drilldown."""
        filtered = self._filter_trades(
            epoch,
            symbol,
            tier,
            venue,
            exit_reason=exit_reason,
            source=source,
            warmup_phase=warmup_phase,
            direction=direction,
            execution_channel=execution_channel,
            pnl_sign=pnl_sign,
            hold_seconds_min=hold_seconds_min,
            hold_seconds_max=hold_seconds_max,
            time_from=time_from,
            time_to=time_to,
        )
        tail = filtered[-limit:] if limit else filtered
        return [self._trade_row(t) for t in reversed(tail)]

    def get_trades_paged(
        self,
        *,
        epoch: str | None = None,
        symbol: str | None = None,
        tier: str | None = None,
        exit_reason: str | None = None,
        source: str | None = None,
        warmup_phase: str | None = None,
        direction: str | None = None,
        venue: str | None = None,
        execution_channel: str | None = None,
        pnl_sign: str | None = None,
        hold_seconds_min: int | None = None,
        hold_seconds_max: int | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Get paged trade rows with total count for dashboard journal."""
        filtered = self._filter_trades(
            epoch,
            symbol,
            tier,
            venue,
            exit_reason=exit_reason,
            source=source,
            warmup_phase=warmup_phase,
            direction=direction,
            execution_channel=execution_channel,
            pnl_sign=pnl_sign,
            hold_seconds_min=hold_seconds_min,
            hold_seconds_max=hold_seconds_max,
            time_from=time_from,
            time_to=time_to,
        )
        ordered = list(reversed(filtered))
        total_count = len(ordered)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        items = [self._trade_row(t) for t in ordered[start:end]]
        total_pages = (total_count + page_size - 1) // page_size if total_count else 1
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    def _trade_row(self, t: CompletedTrade) -> dict[str, Any]:
        pnl_pct: float | None = None
        if t.entry_price > 0 and t.exit_price > 0:
            if t.direction == "short":
                pnl_pct = float((t.entry_price - t.exit_price) / t.entry_price)
            else:
                pnl_pct = float((t.exit_price - t.entry_price) / t.entry_price)

        execution_channel = (
            t.execution_channel
            if t.execution_channel
            else (
                "bybit_linear_demo"
                if t.source == "demo_exchange" and t.venue == "bybit_demo"
                else ("binance_usdm_testnet" if t.source == "demo_exchange" else "paper_simulated")
            )
        )

        return {
            "symbol": t.symbol,
            "venue": t.venue,
            "tier": t.tier,
            "epoch": t.epoch,
            "direction": t.direction,
            "source": t.source,
            "entry_price": str(t.entry_price),
            "exit_price": str(t.exit_price),
            "execution_channel": execution_channel,
            "pnl": str(t.pnl),
            "pnl_pct": pnl_pct,
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
            "entry_reason_summary": t.entry_reason_summary,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "entry_notional_usd": str(t.entry_notional_usd),
            "entry_composite_score": t.entry_composite_score,
            "entry_primary_score": t.entry_primary_score,
            "entry_context_multiplier": t.entry_context_multiplier,
            "entry_strongest_sub_score": t.entry_strongest_sub_score,
            "entry_strongest_sub_score_value": t.entry_strongest_sub_score_value,
        }

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
        return sum((t.pnl for t in self._trades if t.epoch == epoch), Decimal("0"))

    def _filter_trades(
        self,
        epoch: str | None = None,
        symbol: str | None = None,
        tier: str | None = None,
        venue: str | None = None,
        exit_reason: str | None = None,
        source: str | None = None,
        warmup_phase: str | None = None,
        direction: str | None = None,
        execution_channel: str | None = None,
        pnl_sign: str | None = None,
        hold_seconds_min: int | None = None,
        hold_seconds_max: int | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
    ) -> list[CompletedTrade]:
        def _parse_iso(ts: str | None) -> datetime | None:
            if not ts:
                return None
            raw = ts.strip()
            if not raw:
                return None
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC)
            except ValueError:
                return None

        lower_ts = _parse_iso(time_from)
        upper_ts = _parse_iso(time_to)

        def _trade_time(t: CompletedTrade) -> datetime | None:
            return _parse_iso(t.exit_time) or _parse_iso(t.entry_time)

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
        if direction:
            result = [t for t in result if t.direction == direction]
        if execution_channel:
            result = [t for t in result if (t.execution_channel or "") == execution_channel]
        if pnl_sign == "pos":
            result = [t for t in result if t.pnl > 0]
        elif pnl_sign == "neg":
            result = [t for t in result if t.pnl < 0]
        elif pnl_sign == "flat":
            result = [t for t in result if t.pnl == 0]
        if hold_seconds_min is not None:
            result = [t for t in result if t.hold_seconds >= hold_seconds_min]
        if hold_seconds_max is not None:
            result = [t for t in result if t.hold_seconds <= hold_seconds_max]
        if lower_ts is not None:
            result = [
                t for t in result if (_trade_time(t) is not None and _trade_time(t) >= lower_ts)
            ]
        if upper_ts is not None:
            result = [
                t for t in result if (_trade_time(t) is not None and _trade_time(t) <= upper_ts)
            ]
        return result

    @property
    def total_trades(self) -> int:
        return len(self._trades)
