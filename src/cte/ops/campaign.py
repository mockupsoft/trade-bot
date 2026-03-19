"""Campaign metric collection for validation snapshots.

Collects hourly and daily snapshots of all key validation metrics.
These snapshots feed the readiness gates with real data instead of placeholders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cte.analytics.metrics import CompletedTrade


@dataclass
class MetricSnapshot:
    """Point-in-time snapshot of all validation metrics."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    period: str = "hourly"  # "hourly" | "daily"
    epoch: str = ""

    # Trade metrics
    trade_count: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_slippage_bps: float = 0.0

    # Execution quality
    reject_count: int = 0
    reject_rate: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0

    # Operational health
    stale_event_count: int = 0
    reconnect_count: int = 0
    recon_mismatch_count: int = 0
    error_count: int = 0

    # Source breakdown
    seed_trades: int = 0
    paper_trades: int = 0
    demo_trades: int = 0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "period": self.period,
            "epoch": self.epoch,
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 4),
            "expectancy": round(self.expectancy, 2),
            "gross_pnl": round(self.gross_pnl, 2),
            "net_pnl": round(self.net_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "avg_slippage_bps": round(self.avg_slippage_bps, 2),
            "reject_rate": round(self.reject_rate, 4),
            "latency_p50_ms": round(self.latency_p50_ms, 1),
            "latency_p95_ms": round(self.latency_p95_ms, 1),
            "latency_p99_ms": round(self.latency_p99_ms, 1),
            "stale_event_count": self.stale_event_count,
            "reconnect_count": self.reconnect_count,
            "recon_mismatch_count": self.recon_mismatch_count,
            "source_breakdown": {
                "seed": self.seed_trades,
                "paper_simulated": self.paper_trades,
                "demo_exchange": self.demo_trades,
            },
        }


def compute_snapshot(
    trades: list[CompletedTrade],
    epoch: str = "",
    period: str = "hourly",
    reject_count: int = 0,
    stale_event_count: int = 0,
    reconnect_count: int = 0,
    recon_mismatch_count: int = 0,
    error_count: int = 0,
) -> MetricSnapshot:
    """Compute a metric snapshot from a list of trades + operational counters."""
    if not trades:
        return MetricSnapshot(period=period, epoch=epoch,
                              stale_event_count=stale_event_count,
                              reconnect_count=reconnect_count,
                              recon_mismatch_count=recon_mismatch_count,
                              error_count=error_count)

    wins = sum(1 for t in trades if t.pnl > 0)
    total = len(trades)
    gross_profit = sum(float(t.pnl) for t in trades if t.pnl > 0)
    gross_loss = abs(sum(float(t.pnl) for t in trades if t.pnl < 0))
    net = sum(float(t.pnl) for t in trades)

    # Drawdown
    equity = 10000.0
    peak = equity
    max_dd = 0.0
    for t in trades:
        equity += float(t.pnl)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Latency percentiles
    latencies = sorted(t.entry_latency_ms for t in trades if t.entry_latency_ms > 0)
    p50 = _percentile(latencies, 50) if latencies else 0
    p95 = _percentile(latencies, 95) if latencies else 0
    p99 = _percentile(latencies, 99) if latencies else 0

    slippages = [t.modeled_slippage_bps for t in trades]

    return MetricSnapshot(
        period=period,
        epoch=epoch,
        trade_count=total,
        win_rate=wins / total if total > 0 else 0,
        expectancy=net / total if total > 0 else 0,
        gross_pnl=gross_profit - gross_loss + gross_loss,  # = gross_profit (for gross field)
        net_pnl=net,
        max_drawdown_pct=max_dd,
        avg_slippage_bps=sum(slippages) / len(slippages) if slippages else 0,
        reject_count=reject_count,
        reject_rate=reject_count / (total + reject_count) if (total + reject_count) > 0 else 0,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        latency_p99_ms=p99,
        stale_event_count=stale_event_count,
        reconnect_count=reconnect_count,
        recon_mismatch_count=recon_mismatch_count,
        error_count=error_count,
        seed_trades=sum(1 for t in trades if t.source == "seed"),
        paper_trades=sum(1 for t in trades if t.source == "paper_simulated"),
        demo_trades=sum(1 for t in trades if t.source == "demo_exchange"),
    )


class CampaignCollector:
    """Collects and stores periodic metric snapshots."""

    def __init__(self) -> None:
        self._snapshots: list[MetricSnapshot] = []

    def add_snapshot(self, snapshot: MetricSnapshot) -> None:
        self._snapshots.append(snapshot)

    @property
    def snapshots(self) -> list[MetricSnapshot]:
        return list(self._snapshots)

    @property
    def latest(self) -> MetricSnapshot | None:
        return self._snapshots[-1] if self._snapshots else None

    def daily_snapshots(self) -> list[MetricSnapshot]:
        return [s for s in self._snapshots if s.period == "daily"]

    def hourly_snapshots(self) -> list[MetricSnapshot]:
        return [s for s in self._snapshots if s.period == "hourly"]

    @property
    def campaign_days(self) -> int:
        return len(self.daily_snapshots())

    @property
    def total_trades(self) -> int:
        daily = self.daily_snapshots()
        return sum(s.trade_count for s in daily)

    @property
    def all_recon_clean(self) -> bool:
        return all(s.recon_mismatch_count == 0 for s in self._snapshots)

    @property
    def max_dd_observed(self) -> float:
        if not self._snapshots:
            return 0.0
        return max(s.max_drawdown_pct for s in self._snapshots)

    @property
    def avg_latency_p95(self) -> float:
        vals = [s.latency_p95_ms for s in self._snapshots if s.latency_p95_ms > 0]
        return sum(vals) / len(vals) if vals else 0.0

    def summary(self) -> dict:
        return {
            "snapshot_count": len(self._snapshots),
            "campaign_days": self.campaign_days,
            "total_trades": self.total_trades,
            "all_recon_clean": self.all_recon_clean,
            "max_dd_observed": round(self.max_dd_observed, 4),
            "avg_latency_p95_ms": round(self.avg_latency_p95, 1),
            "latest": self.latest.to_dict() if self.latest else None,
        }


def _percentile(sorted_data: list, pct: float) -> float:
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * pct / 100
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return float(sorted_data[-1])
    d = k - f
    return float(sorted_data[f]) + d * (float(sorted_data[c]) - float(sorted_data[f]))
