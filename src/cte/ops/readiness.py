"""Live readiness gate — automated checklist for phase transitions.

Two levels of gates:
1. Infrastructure gates (paper→demo, demo→live) — "does the system work?"
2. Edge gates (pre-live) — "does the system actually make money?"

No phase transition without ALL gates passing.
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
            status=GateStatus.PASS if 0 < fill_latency_p99_ms < 5000 else GateStatus.FAIL,
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
            status=GateStatus.PASS if 0 <= slippage_drift_bps < 3.0 else GateStatus.FAIL,
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


# ══════════════════════════════════════════════════════════════
# EDGE PROOF GATES — "Does the system actually make money?"
# ══════════════════════════════════════════════════════════════

def build_edge_proof_checklist(
    # Edge stability
    expectancy_overall: float = 0.0,
    expectancy_low_vol: float = 0.0,
    expectancy_high_vol: float = 0.0,
    expectancy_trending: float = 0.0,
    positive_regime_count: int = 0,
    # Tier separation
    tier_a_expectancy: float = 0.0,
    tier_b_expectancy: float = 0.0,
    tier_c_expectancy: float = 0.0,
    tier_a_better_than_b: bool = False,
    tier_b_better_than_c: bool = False,
    # Exit value-add
    smart_exit_pnl: float = 0.0,
    flat_exit_pnl: float = 0.0,
    exit_value_add_pct: float = 0.0,
    # Worst-case survival
    worst_case_expectancy: float = 0.0,
    worst_case_max_dd: float = 0.0,
    # Kill switch accuracy
    kill_switch_false_positive_rate: float = 0.0,
    kill_switch_response_ms: float = 0.0,
) -> list[ReadinessGate]:
    """Edge proof gates — must pass before any real capital is risked."""
    return [
        # ── Edge Stability ────────────────────────────────────
        ReadinessGate(
            name="edge_overall", category="edge_stability",
            description="Overall expectancy is positive",
            status=GateStatus.PASS if expectancy_overall > 0 else GateStatus.FAIL,
            value=f"${expectancy_overall:.2f}", threshold="> $0",
        ),
        ReadinessGate(
            name="edge_regime_count", category="edge_stability",
            description="Expectancy positive in ≥3 volatility regimes",
            status=GateStatus.PASS if positive_regime_count >= 3 else GateStatus.FAIL,
            value=str(positive_regime_count), threshold="3",
            detail=(
                f"Low-vol: ${expectancy_low_vol:.2f}, "
                f"High-vol: ${expectancy_high_vol:.2f}, "
                f"Trending: ${expectancy_trending:.2f}"
            ),
        ),
        # ── Tier Separation ───────────────────────────────────
        ReadinessGate(
            name="tier_a_gt_b", category="tier_separation",
            description="Tier A expectancy > Tier B (directionally)",
            status=GateStatus.PASS if tier_a_better_than_b else GateStatus.FAIL,
            value=f"A=${tier_a_expectancy:.2f} B=${tier_b_expectancy:.2f}",
            detail="Scoring model must rank signals correctly",
        ),
        ReadinessGate(
            name="tier_b_gt_c", category="tier_separation",
            description="Tier B expectancy > Tier C (directionally)",
            status=GateStatus.PASS if tier_b_better_than_c else GateStatus.FAIL,
            value=f"B=${tier_b_expectancy:.2f} C=${tier_c_expectancy:.2f}",
            detail="If tiers don't separate, scoring model is noise",
        ),
        # ── Exit Value-Add ────────────────────────────────────
        ReadinessGate(
            name="exit_value_add", category="exit_effectiveness",
            description="Smart exit net PnL > flat SL/TP net PnL",
            status=GateStatus.PASS if exit_value_add_pct > 0 else GateStatus.FAIL,
            value=f"+{exit_value_add_pct:.1f}%",
            detail=f"Smart: ${smart_exit_pnl:.2f} vs Flat: ${flat_exit_pnl:.2f}",
        ),
        # ── Worst-Case Survival ───────────────────────────────
        ReadinessGate(
            name="worst_case_expectancy", category="robustness",
            description="Expectancy stays positive under worst-case fills",
            status=GateStatus.PASS if worst_case_expectancy > 0 else GateStatus.FAIL,
            value=f"${worst_case_expectancy:.2f}",
            detail="2x slippage model must not collapse the edge",
        ),
        ReadinessGate(
            name="worst_case_dd", category="robustness",
            description="Worst-case max drawdown < 10%",
            status=GateStatus.PASS if worst_case_max_dd < 0.10 else GateStatus.FAIL,
            value=f"{worst_case_max_dd:.1%}", threshold="< 10%",
        ),
        # ── Kill Switch Accuracy ──────────────────────────────
        ReadinessGate(
            name="kill_switch_false_positive", category="ops_quality",
            description="Kill switch false positive rate < 20%",
            status=GateStatus.PASS if kill_switch_false_positive_rate < 0.20 else GateStatus.FAIL,
            value=f"{kill_switch_false_positive_rate:.0%}", threshold="< 20%",
            detail="Too many false positives = lost alpha from unnecessary stops",
        ),
        ReadinessGate(
            name="kill_switch_speed", category="ops_quality",
            description="Kill switch response time < 2 seconds",
            status=GateStatus.PASS if 0 < kill_switch_response_ms < 2000 else GateStatus.FAIL,
            value=f"{kill_switch_response_ms:.0f}ms", threshold="< 2000ms",
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
            {
                "name": g.name, "category": g.category,
                "description": g.description,
                "value": g.value, "threshold": g.threshold,
                "detail": g.detail,
            }
            for g in gates if g.status == GateStatus.FAIL
        ],
        "gates": [
            {
                "name": g.name, "category": g.category,
                "description": g.description, "status": g.status.value,
                "value": g.value, "threshold": g.threshold, "detail": g.detail,
            }
            for g in gates
        ],
    }
