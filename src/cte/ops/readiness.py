"""Live readiness gate — automated checklist for phase transitions.

Before transitioning from paper→demo or demo→live, every gate in the
checklist must pass. Any failure blocks the transition.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    PENDING = "pending"


@dataclass(frozen=True)
class ReadinessGate:
    name: str
    category: str
    description: str
    status: GateStatus = GateStatus.PENDING
    value: str = ""
    threshold: str = ""
    detail: str = ""


def build_paper_to_demo_checklist(
    paper_days: int = 0,
    paper_trades: int = 0,
    crash_free_days: int = 0,
    reconciliation_clean: bool = False,
    all_tests_pass: bool = False,
    state_machine_violations: int = 0,
    api_keys_configured: bool = False,
) -> list[ReadinessGate]:
    """Checklist for paper → demo transition."""
    return [
        ReadinessGate(
            name="paper_duration", category="validation",
            description="Paper trading ran for ≥7 consecutive days",
            status=GateStatus.PASS if paper_days >= 7 else GateStatus.FAIL,
            value=str(paper_days), threshold="7",
        ),
        ReadinessGate(
            name="paper_trade_count", category="validation",
            description="At least 50 paper trades completed",
            status=GateStatus.PASS if paper_trades >= 50 else GateStatus.FAIL,
            value=str(paper_trades), threshold="50",
        ),
        ReadinessGate(
            name="crash_free", category="stability",
            description="No unhandled exceptions for ≥7 days",
            status=GateStatus.PASS if crash_free_days >= 7 else GateStatus.FAIL,
            value=str(crash_free_days), threshold="7",
        ),
        ReadinessGate(
            name="tests_pass", category="quality",
            description="All unit and integration tests pass",
            status=GateStatus.PASS if all_tests_pass else GateStatus.FAIL,
        ),
        ReadinessGate(
            name="fsm_violations", category="quality",
            description="Zero order state machine violations",
            status=GateStatus.PASS if state_machine_violations == 0 else GateStatus.FAIL,
            value=str(state_machine_violations), threshold="0",
        ),
        ReadinessGate(
            name="api_keys", category="infrastructure",
            description="Testnet API keys configured",
            status=GateStatus.PASS if api_keys_configured else GateStatus.FAIL,
        ),
    ]


def build_demo_to_live_checklist(
    demo_days: int = 0,
    demo_trades: int = 0,
    reconciliation_clean_rate: float = 0.0,
    fill_latency_p99_ms: float = 0.0,
    paper_demo_pnl_drift_pct: float = 0.0,
    slippage_drift_bps: float = 0.0,
    emergency_stop_tested: bool = False,
    manual_review_signed: bool = False,
    max_capital_configured: bool = False,
    monitoring_alerts_configured: bool = False,
) -> list[ReadinessGate]:
    """Checklist for demo → live transition (10-point gate)."""
    return [
        ReadinessGate(
            name="demo_duration", category="validation",
            description="Demo trading ran for ≥7 consecutive days",
            status=GateStatus.PASS if demo_days >= 7 else GateStatus.FAIL,
            value=str(demo_days), threshold="7",
        ),
        ReadinessGate(
            name="demo_trade_count", category="validation",
            description="50-trade acceptance test passed on testnet",
            status=GateStatus.PASS if demo_trades >= 50 else GateStatus.FAIL,
            value=str(demo_trades), threshold="50",
        ),
        ReadinessGate(
            name="reconciliation", category="validation",
            description="100% clean reconciliation for 7 days",
            status=GateStatus.PASS if reconciliation_clean_rate >= 1.0 else GateStatus.FAIL,
            value=f"{reconciliation_clean_rate:.0%}", threshold="100%",
        ),
        ReadinessGate(
            name="fill_latency", category="performance",
            description="Fill latency p99 < 5 seconds",
            status=GateStatus.PASS if fill_latency_p99_ms < 5000 else GateStatus.FAIL,
            value=f"{fill_latency_p99_ms:.0f}ms", threshold="5000ms",
        ),
        ReadinessGate(
            name="pnl_parity", category="validation",
            description="Paper-demo PnL within 5% for same signals",
            status=GateStatus.PASS if abs(paper_demo_pnl_drift_pct) < 5 else GateStatus.FAIL,
            value=f"{paper_demo_pnl_drift_pct:.1f}%", threshold="±5%",
        ),
        ReadinessGate(
            name="slippage_drift", category="validation",
            description="Slippage drift < 3 bps vs paper model",
            status=GateStatus.PASS if slippage_drift_bps < 3.0 else GateStatus.FAIL,
            value=f"{slippage_drift_bps:.1f}bps", threshold="3.0bps",
        ),
        ReadinessGate(
            name="emergency_stop", category="ops",
            description="Emergency stop tested and functional",
            status=GateStatus.PASS if emergency_stop_tested else GateStatus.FAIL,
        ),
        ReadinessGate(
            name="manual_review", category="ops",
            description="Team review and sign-off complete",
            status=GateStatus.PASS if manual_review_signed else GateStatus.FAIL,
        ),
        ReadinessGate(
            name="capital_limits", category="risk",
            description="Max capital ($100) and position ($50) configured",
            status=GateStatus.PASS if max_capital_configured else GateStatus.FAIL,
        ),
        ReadinessGate(
            name="monitoring", category="ops",
            description="24/7 monitoring alerts configured",
            status=GateStatus.PASS if monitoring_alerts_configured else GateStatus.FAIL,
        ),
    ]


def evaluate_readiness(gates: list[ReadinessGate]) -> dict:
    """Evaluate a readiness checklist. All gates must pass for go."""
    passed = sum(1 for g in gates if g.status == GateStatus.PASS)
    failed = sum(1 for g in gates if g.status == GateStatus.FAIL)
    total = len(gates)

    return {
        "ready": failed == 0,
        "passed": passed,
        "failed": failed,
        "total": total,
        "completion_pct": round(passed / total * 100, 1) if total > 0 else 0,
        "blockers": [
            {"name": g.name, "description": g.description, "value": g.value, "threshold": g.threshold}
            for g in gates if g.status == GateStatus.FAIL
        ],
        "gates": [
            {
                "name": g.name, "category": g.category,
                "description": g.description, "status": g.status.value,
                "value": g.value, "threshold": g.threshold,
            }
            for g in gates
        ],
    }
