"""Validation campaign orchestrator.

Manages a multi-day validation run that collects evidence for go/no-go decisions.
Produces daily snapshots and a final campaign report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum


class CampaignStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"


@dataclass
class DailySnapshot:
    """One day's validation data."""
    date: date
    trade_count: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    reconciliation_clean: bool = True
    feed_stale_events: int = 0
    reconnect_events: int = 0
    order_rejects: int = 0
    fsm_violations: int = 0
    avg_slippage_bps: float = 0.0
    avg_latency_ms: float = 0.0
    exceptions_caught: int = 0
    notes: str = ""


@dataclass
class ValidationCampaign:
    """A multi-day validation campaign with daily snapshots."""

    name: str
    target_days: int = 7
    mode: str = "paper"       # paper | demo | parallel
    status: CampaignStatus = CampaignStatus.PLANNED
    started_at: datetime | None = None
    ended_at: datetime | None = None
    snapshots: list[DailySnapshot] = field(default_factory=list)

    def start(self) -> None:
        self.status = CampaignStatus.RUNNING
        self.started_at = datetime.now(UTC)

    def add_snapshot(self, snapshot: DailySnapshot) -> None:
        self.snapshots.append(snapshot)

    def complete(self) -> None:
        self.status = CampaignStatus.COMPLETED
        self.ended_at = datetime.now(UTC)

    def abort(self, reason: str) -> None:
        self.status = CampaignStatus.ABORTED
        self.ended_at = datetime.now(UTC)
        if self.snapshots:
            self.snapshots[-1].notes = f"ABORTED: {reason}"

    @property
    def days_completed(self) -> int:
        return len(self.snapshots)

    @property
    def is_target_reached(self) -> bool:
        return self.days_completed >= self.target_days

    def generate_report(self) -> dict:
        """Generate the final campaign report."""
        if not self.snapshots:
            return {"status": "no_data"}

        total_trades = sum(s.trade_count for s in self.snapshots)
        total_pnl = sum(s.net_pnl for s in self.snapshots)
        avg_wr = sum(s.win_rate for s in self.snapshots) / len(self.snapshots)
        max_dd = max(s.max_drawdown_pct for s in self.snapshots)
        all_recon_clean = all(s.reconciliation_clean for s in self.snapshots)
        total_stale = sum(s.feed_stale_events for s in self.snapshots)
        total_reconnects = sum(s.reconnect_events for s in self.snapshots)
        total_rejects = sum(s.order_rejects for s in self.snapshots)
        total_fsm = sum(s.fsm_violations for s in self.snapshots)
        total_exceptions = sum(s.exceptions_caught for s in self.snapshots)
        avg_slip = (
            sum(s.avg_slippage_bps for s in self.snapshots) / len(self.snapshots)
        )

        # Go/No-Go assessment
        blockers = []
        if not self.is_target_reached:
            blockers.append(f"Only {self.days_completed}/{self.target_days} days completed")
        if not all_recon_clean:
            blockers.append("Reconciliation had discrepancies")
        if total_fsm > 0:
            blockers.append(f"{total_fsm} state machine violations")
        if total_exceptions > 0:
            blockers.append(f"{total_exceptions} unhandled exceptions")
        if max_dd > 0.05:
            blockers.append(f"Max drawdown {max_dd:.1%} exceeds 5%")

        return {
            "campaign": self.name,
            "mode": self.mode,
            "status": self.status.value,
            "days_completed": self.days_completed,
            "target_days": self.target_days,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "summary": {
                "total_trades": total_trades,
                "total_pnl": round(total_pnl, 2),
                "avg_win_rate": round(avg_wr, 4),
                "max_drawdown_pct": round(max_dd, 4),
                "avg_slippage_bps": round(avg_slip, 2),
                "reconciliation_all_clean": all_recon_clean,
                "total_stale_events": total_stale,
                "total_reconnects": total_reconnects,
                "total_order_rejects": total_rejects,
                "total_fsm_violations": total_fsm,
                "total_exceptions": total_exceptions,
            },
            "go_no_go": {
                "ready": len(blockers) == 0,
                "blockers": blockers,
            },
            "daily_snapshots": [
                {
                    "date": s.date.isoformat(),
                    "trades": s.trade_count,
                    "pnl": round(s.net_pnl, 2),
                    "win_rate": round(s.win_rate, 4),
                    "max_dd": round(s.max_drawdown_pct, 4),
                    "recon_clean": s.reconciliation_clean,
                    "stale": s.feed_stale_events,
                    "reconnects": s.reconnect_events,
                    "rejects": s.order_rejects,
                    "exceptions": s.exceptions_caught,
                }
                for s in self.snapshots
            ],
        }
