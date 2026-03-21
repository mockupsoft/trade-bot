"""GO/NO-GO report framework — final decision document before live trading.

Produces a structured report with 7 sections that answers:
"Should we risk real capital on this system?"

This is not a dashboard metric. This is an investment decision document.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ReportSection:
    name: str
    verdict: str       # "pass" | "fail" | "warning" | "insufficient_data"
    score: float       # 0-100
    findings: list[str]
    recommendations: list[str]


@dataclass(frozen=True)
class GoNoGoMetrics:
    # System health
    uptime_pct: float
    crash_count: int
    stale_feed_events: int
    reconnect_events: int
    # Execution reality
    paper_pnl: float
    demo_pnl: float
    pnl_drift_pct: float
    avg_slippage_paper: float
    avg_slippage_demo: float
    reconciliation_clean_pct: float
    # Signal quality
    overall_expectancy: float
    win_rate: float
    tier_a_expectancy: float
    tier_b_expectancy: float
    tier_c_expectancy: float
    # Exit effectiveness
    smart_exit_value_add_pct: float
    saved_losers: int
    killed_winners: int
    no_progress_regret_rate: float
    runner_avg_r: float
    # Risk behavior
    max_drawdown_pct: float
    worst_case_dd: float
    dd_recovery_hours: float
    # Edge stability
    positive_regime_count: int
    worst_case_expectancy: float
    # Campaign stats
    campaign_days: int
    total_trades: int
    profit_factor: float | None = None


def build_go_no_go_report(
    metrics: GoNoGoMetrics,

) -> dict:
    """Build the complete GO/NO-GO report."""

    sections = []

    m = metrics
    # ── 1. System Health ──────────────────────────────────────
    health_findings = []
    health_score = 100
    if m.uptime_pct < 99.0:
        health_score -= 30
        health_findings.append(f"Uptime {m.uptime_pct:.1f}% below 99% target")
    if m.crash_count > 0:
        health_score -= 20 * m.crash_count
        health_findings.append(f"{m.crash_count} crashes during validation")
    if m.stale_feed_events > 5:
        health_score -= 10
        health_findings.append(f"{m.stale_feed_events} stale feed events")

    if not health_findings:
        health_findings.append("All systems healthy during validation period")

    sections.append(ReportSection(
        name="system_health", score=max(0, health_score),
        verdict="pass" if health_score >= 70 else "fail",
        findings=health_findings,
        recommendations=["Monitor uptime continuously"] if health_score < 100 else [],
    ))

    # ── 2. Execution Reality ──────────────────────────────────
    exec_findings = []
    exec_score = 100
    slip_drift = m.avg_slippage_demo - m.avg_slippage_paper
    if abs(m.pnl_drift_pct) > 5:
        exec_score -= 40
        exec_findings.append(f"PnL drift {m.pnl_drift_pct:.1f}% exceeds ±5% tolerance")
    if slip_drift > 3:
        exec_score -= 30
        exec_findings.append(f"Slippage drift {slip_drift:.1f} bps above paper model")
    if m.reconciliation_clean_pct < 100:
        exec_score -= 30
        exec_findings.append(f"Reconciliation clean rate {m.reconciliation_clean_pct:.0f}%")

    if not exec_findings:
        exec_findings.append("Paper and demo execution closely aligned")

    sections.append(ReportSection(
        name="execution_reality", score=max(0, exec_score),
        verdict="pass" if exec_score >= 70 else "fail",
        findings=exec_findings,
        recommendations=["Recalibrate fill model"] if slip_drift > 2 else [],
    ))

    # ── 3. Signal Quality ─────────────────────────────────────
    sig_findings = []
    sig_score = 50  # start neutral
    if m.overall_expectancy > 0:
        sig_score += 25
        sig_findings.append(f"Positive expectancy: ${m.overall_expectancy:.2f}/trade")
    else:
        sig_score -= 50
        sig_findings.append(f"NEGATIVE expectancy: ${m.overall_expectancy:.2f}/trade")

    if m.profit_factor and m.profit_factor > 1.5:
        sig_score += 15
        sig_findings.append(f"Strong profit factor: {m.profit_factor:.2f}")
    elif m.profit_factor and m.profit_factor > 1.0:
        sig_score += 5
        sig_findings.append(f"Marginal profit factor: {m.profit_factor:.2f}")

    if m.tier_a_expectancy > m.tier_b_expectancy > m.tier_c_expectancy:

        sig_score += 10
        sig_findings.append("Tier separation correct: A > B > C")
    else:
        sig_findings.append("WARNING: Tier separation violated")

    sections.append(ReportSection(
        name="signal_quality", score=max(0, min(100, sig_score)),
        verdict="pass" if sig_score >= 60 else "fail" if sig_score < 40 else "warning",
        findings=sig_findings,
        recommendations=[],
    ))

    # ── 4. Exit Effectiveness ─────────────────────────────────
    exit_findings = []
    exit_score = 50
    if m.smart_exit_value_add_pct > 0:
        exit_score += 25
        exit_findings.append(f"Smart exit adds {m.smart_exit_value_add_pct:.1f}% vs flat SL/TP")
    else:
        exit_findings.append("Smart exit underperforms flat SL/TP")

    if m.no_progress_regret_rate < 0.3:
        exit_score += 15
        exit_findings.append(f"Low no-progress regret: {m.no_progress_regret_rate:.0%}")
    elif m.no_progress_regret_rate > 0.5:
        exit_score -= 10
        exit_findings.append(f"High no-progress regret: {m.no_progress_regret_rate:.0%} — timer too aggressive")

    if m.runner_avg_r > 2.0:
        exit_score += 10
        exit_findings.append(f"Runner mode effective: avg {m.runner_avg_r:.1f}R")


    sections.append(ReportSection(
        name="exit_effectiveness", score=max(0, min(100, exit_score)),
        verdict="pass" if exit_score >= 60 else "warning",
        findings=exit_findings,
        recommendations=[],
    ))

    # ── 5. Risk Behavior ──────────────────────────────────────
    risk_findings = []
    risk_score = 100
    if m.max_drawdown_pct > 0.05:
        risk_score -= 40
        risk_findings.append(f"Max drawdown {m.max_drawdown_pct:.1%} exceeds 5%")
    elif m.max_drawdown_pct > 0.03:
        risk_score -= 15
        risk_findings.append(f"Max drawdown {m.max_drawdown_pct:.1%} near 3% warning")
    else:
        risk_findings.append(f"Max drawdown controlled at {m.max_drawdown_pct:.1%}")

    if m.worst_case_dd > 0.10:
        risk_score -= 30
        risk_findings.append(f"Worst-case DD {m.worst_case_dd:.1%} exceeds 10%")


    sections.append(ReportSection(
        name="risk_behavior", score=max(0, risk_score),
        verdict="pass" if risk_score >= 70 else "fail",
        findings=risk_findings,
        recommendations=[],
    ))

    # ── 6. Edge Stability ─────────────────────────────────────
    edge_findings = []
    edge_score = 0
    if m.positive_regime_count >= 3:
        edge_score += 60
        edge_findings.append(f"Edge positive in {m.positive_regime_count} regimes")
    elif m.positive_regime_count >= 2:
        edge_score += 30
        edge_findings.append(f"Edge positive in only {m.positive_regime_count} regimes (need 3+)")
    else:
        edge_findings.append(f"Edge fragile: positive in only {m.positive_regime_count} regime(s)")

    if m.worst_case_expectancy > 0:
        edge_score += 40
        edge_findings.append(f"Worst-case expectancy positive: ${m.worst_case_expectancy:.2f}")
    else:
        edge_findings.append(f"Worst-case expectancy NEGATIVE: ${m.worst_case_expectancy:.2f}")


    sections.append(ReportSection(
        name="edge_stability", score=max(0, min(100, edge_score)),
        verdict="pass" if edge_score >= 70 else "fail" if edge_score < 40 else "warning",
        findings=edge_findings,
        recommendations=[],
    ))

    # ── 7. GO / NO-GO ────────────────────────────────────────
    section_verdicts = [s.verdict for s in sections]
    any_fail = "fail" in section_verdicts
    any_warning = "warning" in section_verdicts
    avg_score = sum(s.score for s in sections) / len(sections) if sections else 0

    if any_fail or (any_warning and avg_score < 65):
        final_verdict = "NO-GO"
    elif any_warning:
        final_verdict = "CONDITIONAL-GO"
    else:
        final_verdict = "GO"

    return {
        "report_title": "CTE GO/NO-GO Decision Report",
        "generated_at": datetime.now(UTC).isoformat(),
        "campaign_days": m.campaign_days,
        "total_trades": m.total_trades,

        "final_verdict": final_verdict,
        "overall_score": round(avg_score, 1),
        "sections": [
            {
                "name": s.name,
                "verdict": s.verdict,
                "score": s.score,
                "findings": s.findings,
                "recommendations": s.recommendations,
            }
            for s in sections
        ],
        "critical_blockers": [
            s.name for s in sections if s.verdict == "fail"
        ],
        "warnings": [
            s.name for s in sections if s.verdict == "warning"
        ],
    }
