"""Live readiness gate — automated checklist for phase transitions.

Two levels of gates:
1. Infrastructure gates (paper→demo, demo→live) — "does the system work?"
2. Edge gates (pre-live) — "does the system actually make money?"

No phase transition without ALL gates passing.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    PENDING = "pending"


@dataclass(frozen=True)
class PerformanceMetrics:
    # Edge stability
    expectancy_overall: float = 0.0
    expectancy_low_vol: float = 0.0
    expectancy_high_vol: float = 0.0
    expectancy_trending: float = 0.0
    positive_regime_count: int = 0
    # Tier separation
    tier_a_expectancy: float = 0.0
    tier_b_expectancy: float = 0.0
    tier_c_expectancy: float = 0.0
    tier_a_better_than_b: bool = False
    tier_b_better_than_c: bool = False
    # Exit value-add
    smart_exit_pnl: float = 0.0
    flat_exit_pnl: float = 0.0
    exit_value_add_pct: float = 0.0
    # Worst-case survival
    worst_case_expectancy: float = 0.0
    worst_case_max_dd: float = 0.0
    # Kill switch accuracy
    kill_switch_false_positive_rate: float = 0.0
    kill_switch_response_ms: float = 0.0
    # Sample size
    total_trades: int = 0


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
            description="Fill latency p99 < 5 seconds (measured)",
            status=GateStatus.PASS
            if fill_latency_p99_ms > 0 and fill_latency_p99_ms < 5000
            else GateStatus.FAIL,
            value=f"{fill_latency_p99_ms:.0f}ms", threshold="<5000ms",
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
    metrics: PerformanceMetrics,
    min_trades: int = 100,
) -> list[ReadinessGate]:
    """Edge proof gates — must pass before any real capital is risked."""
    return [
        # ── Edge Stability ────────────────────────────────────
        ReadinessGate(
            name="edge_overall", category="edge_stability",
            description="Overall expectancy is positive",
            status=GateStatus.PASS if metrics.expectancy_overall > 0 else GateStatus.FAIL,
            value=f"${metrics.expectancy_overall:.2f}", threshold="> $0",
        ),
        ReadinessGate(
            name="edge_regime_count", category="edge_stability",
            description="Expectancy positive in ≥3 volatility regimes",
            status=GateStatus.PASS if metrics.positive_regime_count >= 3 else GateStatus.FAIL,
            value=str(metrics.positive_regime_count), threshold="3",
            detail=(
                f"Low-vol: ${metrics.expectancy_low_vol:.2f}, "
                f"High-vol: ${metrics.expectancy_high_vol:.2f}, "
                f"Trending: ${metrics.expectancy_trending:.2f}"
            ),
        ),
        # ── Tier Separation ───────────────────────────────────
        ReadinessGate(
            name="tier_a_gt_b", category="tier_separation",
            description="Tier A expectancy > Tier B (directionally)",
            status=GateStatus.PASS if metrics.tier_a_better_than_b else GateStatus.FAIL,
            value=f"A=${metrics.tier_a_expectancy:.2f} B=${metrics.tier_b_expectancy:.2f}",
            detail="Scoring model must rank signals correctly",
        ),
        ReadinessGate(
            name="tier_b_gt_c", category="tier_separation",
            description="Tier B expectancy > Tier C (directionally)",
            status=GateStatus.PASS if metrics.tier_b_better_than_c else GateStatus.FAIL,
            value=f"B=${metrics.tier_b_expectancy:.2f} C=${metrics.tier_c_expectancy:.2f}",
            detail="If tiers don't separate, scoring model is noise",
        ),
        # ── Exit Value-Add ────────────────────────────────────
        ReadinessGate(
            name="exit_value_add", category="exit_effectiveness",
            description="Smart exit net PnL > flat SL/TP net PnL",
            status=GateStatus.PASS if metrics.exit_value_add_pct > 0 else GateStatus.FAIL,
            value=f"+{metrics.exit_value_add_pct:.1f}%",
            detail=f"Smart: ${metrics.smart_exit_pnl:.2f} vs Flat: ${metrics.flat_exit_pnl:.2f}",
        ),
        # ── Worst-Case Survival ───────────────────────────────
        ReadinessGate(
            name="worst_case_expectancy", category="robustness",
            description="Expectancy stays positive under worst-case fills",
            status=GateStatus.PASS if metrics.worst_case_expectancy > 0 else GateStatus.FAIL,
            value=f"${metrics.worst_case_expectancy:.2f}",
            detail="2x slippage model must not collapse the edge",
        ),
        ReadinessGate(
            name="worst_case_dd", category="robustness",
            description="Worst-case max drawdown < 10%",
            status=GateStatus.PASS if metrics.worst_case_max_dd < 0.10 else GateStatus.FAIL,
            value=f"{metrics.worst_case_max_dd:.1%}", threshold="< 10%",
        ),
        # ── Kill Switch Accuracy ──────────────────────────────
        ReadinessGate(
            name="kill_switch_false_positive", category="ops_quality",
            description="Kill switch false positive rate < 20%",
            status=GateStatus.PASS if metrics.kill_switch_false_positive_rate < 0.20 else GateStatus.FAIL,
            value=f"{metrics.kill_switch_false_positive_rate:.0%}", threshold="< 20%",
            detail="Too many false positives = lost alpha from unnecessary stops",
        ),
        ReadinessGate(
            name="kill_switch_speed", category="ops_quality",
            description="Kill switch response time < 2 seconds",
            status=GateStatus.PASS if 0 < metrics.kill_switch_response_ms < 2000 else GateStatus.FAIL,
            value=f"{metrics.kill_switch_response_ms:.0f}ms", threshold="< 2000ms",
        ),
        # ── Sample Size ───────────────────────────────────────
        ReadinessGate(
            name="sample_size", category="validation",
            description=f"At least {min_trades} trades completed",
            status=GateStatus.PASS if metrics.total_trades >= min_trades else GateStatus.FAIL,
            value=str(metrics.total_trades), threshold=str(min_trades),
        ),
    ]


def evaluate_readiness(gates: list[ReadinessGate]) -> dict:
    """Evaluate a readiness checklist. SKIP gates are informational only (not scored)."""
    active = [g for g in gates if g.status != GateStatus.SKIP]
    skipped = len(gates) - len(active)
    passed = sum(1 for g in active if g.status == GateStatus.PASS)
    failed = sum(1 for g in active if g.status == GateStatus.FAIL)
    total = len(gates)
    applicable = len(active)
    ready = applicable > 0 and failed == 0 and passed == applicable

    return {
        "ready": ready,
        "passed": passed,
        "failed": failed,
        "total": total,
        "skipped": skipped,
        "applicable": applicable,
        "completion_pct": round(passed / applicable * 100, 1) if applicable > 0 else 0.0,
        "not_applicable": applicable == 0,
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


def build_dashboard_paper_to_testnet_gates(
    *,
    testnet_keys: bool,
    market_connected: bool,
    v1_safe_not_live: bool,
    paper_trades: int,
    paper_days: int,
    crash_free_days: int,
    all_tests_pass: bool,
    fsm_violations: int = 0,
) -> list[ReadinessGate]:
    """Gates for the v1 dashboard: infrastructure truth + declared validation metrics (env)."""
    return [
        ReadinessGate(
            name="testnet_api_keys",
            category="infrastructure",
            description="Binance USDⓈ-M futures testnet API key + secret configured",
            status=GateStatus.PASS if testnet_keys else GateStatus.FAIL,
            value="configured" if testnet_keys else "missing",
            threshold="non-empty",
        ),
        ReadinessGate(
            name="market_feed_ws",
            category="infrastructure",
            description="Combined testnet WebSocket feed connected (this dashboard process)",
            status=GateStatus.PASS if market_connected else GateStatus.FAIL,
            value="connected" if market_connected else "offline",
            threshold="WS healthy",
        ),
        ReadinessGate(
            name="v1_safety_profile",
            category="compliance",
            description="v1 safety: process not in LIVE mainnet mode (paper/demo/testnet only)",
            status=GateStatus.PASS if v1_safe_not_live else GateStatus.FAIL,
            value="ok" if v1_safe_not_live else "LIVE",
            threshold="not LIVE",
        ),
        ReadinessGate(
            name="paper_duration",
            category="validation",
            description="Paper validation window ≥7 days (set CTE_READINESS_PAPER_DAYS after ops sign-off)",
            status=GateStatus.PASS if paper_days >= 7 else GateStatus.FAIL,
            value=str(paper_days),
            threshold="7",
        ),
        ReadinessGate(
            name="paper_trade_count",
            category="validation",
            description="≥50 paper / simulated trades in active epoch (analytics journal)",
            status=GateStatus.PASS if paper_trades >= 50 else GateStatus.FAIL,
            value=str(paper_trades),
            threshold="50",
        ),
        ReadinessGate(
            name="crash_free",
            category="stability",
            description="No unhandled exceptions ≥7 days (set CTE_READINESS_CRASH_FREE_DAYS)",
            status=GateStatus.PASS if crash_free_days >= 7 else GateStatus.FAIL,
            value=str(crash_free_days),
            threshold="7",
        ),
        ReadinessGate(
            name="tests_pass",
            category="quality",
            description="Automated test suite green (set CTE_READINESS_TESTS_PASS=1 after pytest in CI/local)",
            status=GateStatus.PASS if all_tests_pass else GateStatus.FAIL,
            value="pass" if all_tests_pass else "not attested",
            threshold="true",
        ),
        ReadinessGate(
            name="fsm_violations",
            category="quality",
            description="Zero order state machine violations (set CTE_READINESS_FSM_VIOLATIONS)",
            status=GateStatus.PASS if fsm_violations == 0 else GateStatus.FAIL,
            value=str(fsm_violations),
            threshold="0",
        ),
    ]


_PHASE5_SKIP_DETAIL = (
    "Phase 5 only — v1 scope is paper/demo/testnet; live mainnet is blocked by enforce_safety."
)


def build_phase5_live_gates_skipped() -> list[ReadinessGate]:
    """Same headings as demo→live checklist, all SKIP (not scored in v1)."""
    return [
        ReadinessGate(
            name="demo_duration",
            category="validation",
            description="Demo trading ran for ≥7 consecutive days",
            status=GateStatus.SKIP,
            value="—",
            threshold="7",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="demo_trade_count",
            category="validation",
            description="50-trade acceptance test passed on testnet",
            status=GateStatus.SKIP,
            value="—",
            threshold="50",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="reconciliation",
            category="validation",
            description="100% clean reconciliation for 7 days",
            status=GateStatus.SKIP,
            value="—",
            threshold="100%",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="fill_latency",
            category="performance",
            description="Fill latency p99 < 5 seconds",
            status=GateStatus.SKIP,
            value="—",
            threshold="5000ms",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="pnl_parity",
            category="validation",
            description="Paper-demo PnL within 5% for same signals",
            status=GateStatus.SKIP,
            value="—",
            threshold="±5%",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="slippage_drift",
            category="validation",
            description="Slippage drift < 3 bps vs paper model",
            status=GateStatus.SKIP,
            value="—",
            threshold="3.0bps",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="emergency_stop",
            category="ops",
            description="Emergency stop tested and functional",
            status=GateStatus.SKIP,
            value="—",
            threshold="true",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="manual_review",
            category="ops",
            description="Team review and sign-off complete",
            status=GateStatus.SKIP,
            value="—",
            threshold="true",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="capital_limits",
            category="risk",
            description="Max capital ($100) and position ($50) configured",
            status=GateStatus.SKIP,
            value="—",
            threshold="true",
            detail=_PHASE5_SKIP_DETAIL,
        ),
        ReadinessGate(
            name="monitoring",
            category="ops",
            description="24/7 monitoring alerts configured",
            status=GateStatus.SKIP,
            value="—",
            threshold="true",
            detail=_PHASE5_SKIP_DETAIL,
        ),
    ]


def build_campaign_validation_checklist(
    campaign_days: int = 0,
    total_trades: int = 0,
    all_recon_clean: bool = False,
    max_dd_observed: float = 0.0,
    avg_latency_p95_ms: float = 0.0,
    stale_ratio: float = 0.0,
    reject_ratio: float = 0.0,
    error_count: int = 0,
    expectancy: float = 0.0,
    seed_trade_count: int = 0,
    *,
    promotion_trade_count: int | None = None,
    promotion_expectancy: float | None = None,
    promotion_max_dd_observed: float | None = None,
) -> list[ReadinessGate]:
    """Build gates from REAL campaign metrics (not placeholders).

    When ``promotion_*`` args are provided, edge/risk gates use **promotion evidence**
    trades only (excludes ``warmup_phase=early``). Otherwise falls back to legacy
    ``total_trades`` / ``expectancy`` / ``max_dd_observed`` for backward compatibility.
    """
    promo_n = promotion_trade_count if promotion_trade_count is not None else total_trades
    promo_exp = promotion_expectancy if promotion_expectancy is not None else expectancy
    promo_dd = promotion_max_dd_observed if promotion_max_dd_observed is not None else max_dd_observed
    return [
        ReadinessGate(
            name="campaign_duration", category="campaign",
            description="Campaign ran for >=7 days",
            status=GateStatus.PASS if campaign_days >= 7 else GateStatus.FAIL,
            value=str(campaign_days), threshold="7",
        ),
        ReadinessGate(
            name="trade_count", category="campaign",
            description=">=100 promotion-evidence trades (excludes early warmup by default)",
            status=GateStatus.PASS if promo_n >= 100 else GateStatus.FAIL,
            value=str(promo_n), threshold="100",
            detail=f"all_trades={total_trades} promotion_only={promotion_trade_count is not None}",
        ),
        ReadinessGate(
            name="no_seed_data", category="data_integrity",
            description="Zero seed trades in campaign data",
            status=GateStatus.PASS if seed_trade_count == 0 else GateStatus.FAIL,
            value=str(seed_trade_count), threshold="0",
            detail="Seed data must never mix with real validation data",
        ),
        ReadinessGate(
            name="recon_integrity", category="reconciliation",
            description="100% reconciliation clean throughout campaign",
            status=GateStatus.PASS if all_recon_clean else GateStatus.FAIL,
            value="clean" if all_recon_clean else "mismatches found",
        ),
        ReadinessGate(
            name="max_drawdown", category="risk",
            description="Max drawdown < 5% (promotion evidence DD when provided)",
            status=GateStatus.PASS if promo_dd < 0.05 else GateStatus.FAIL,
            value=f"{promo_dd:.2%}", threshold="< 5%",
        ),
        ReadinessGate(
            name="latency_p95", category="performance",
            description="Latency p95 < 5000ms",
            status=GateStatus.PASS if 0 < avg_latency_p95_ms < 5000 else GateStatus.FAIL,
            value=f"{avg_latency_p95_ms:.0f}ms", threshold="< 5000ms",
        ),
        ReadinessGate(
            name="stale_ratio", category="data_quality",
            description="Stale data ratio < 1%",
            status=GateStatus.PASS if stale_ratio < 0.01 else GateStatus.FAIL,
            value=f"{stale_ratio:.2%}", threshold="< 1%",
        ),
        ReadinessGate(
            name="reject_ratio", category="execution",
            description="Order reject ratio < 5%",
            status=GateStatus.PASS if reject_ratio < 0.05 else GateStatus.FAIL,
            value=f"{reject_ratio:.2%}", threshold="< 5%",
        ),
        ReadinessGate(
            name="no_critical_errors", category="stability",
            description="Zero critical errors during campaign",
            status=GateStatus.PASS if error_count == 0 else GateStatus.FAIL,
            value=str(error_count), threshold="0",
        ),
        ReadinessGate(
            name="positive_expectancy", category="edge",
            description="Expectancy > $0 per trade (promotion evidence when provided)",
            status=GateStatus.PASS if promo_exp > 0 else GateStatus.FAIL,
            value=f"${promo_exp:.2f}", threshold="> $0",
        ),
    ]
