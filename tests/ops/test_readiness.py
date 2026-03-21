"""Tests for readiness gates and performance metrics."""
from __future__ import annotations

import pytest


base_paper_to_demo_metrics = dict(paper_days=7, paper_trades=50, crash_free_days=7, reconciliation_clean=True, all_tests_pass=True, state_machine_violations=0, api_keys_configured=True)

base_demo_to_live_metrics = dict(demo_days=7, demo_trades=50, reconciliation_clean_rate=1.0, fill_latency_p99_ms=100.0, paper_demo_pnl_drift_pct=0.0, slippage_drift_bps=0.0, emergency_stop_tested=True, manual_review_signed=True, max_capital_configured=True, monitoring_alerts_configured=True)

base_edge_proof_metrics = dict(total_trades=100, expectancy_overall=1.0, expectancy_low_vol=1.0, expectancy_high_vol=1.0, expectancy_trending=1.0, positive_regime_count=3, tier_a_expectancy=2.0, tier_b_expectancy=1.0, tier_c_expectancy=0.5, tier_a_better_than_b=True, tier_b_better_than_c=True, smart_exit_pnl=100.0, flat_exit_pnl=50.0, exit_value_add_pct=10.0, worst_case_expectancy=0.5, worst_case_max_dd=0.05, kill_switch_false_positive_rate=0.05, kill_switch_response_ms=100)

base_go_no_go_metrics = dict(uptime_pct=100.0, crash_count=0, stale_feed_events=0, reconnect_events=0, paper_pnl=100.0, demo_pnl=100.0, pnl_drift_pct=0.0, avg_slippage_paper=1.0, avg_slippage_demo=1.0, reconciliation_clean_pct=100.0, overall_expectancy=1.0, win_rate=0.5, profit_factor=2.0, tier_a_expectancy=2.0, tier_b_expectancy=1.0, tier_c_expectancy=0.5, smart_exit_value_add_pct=1.0, saved_losers=1, killed_winners=0, no_progress_regret_rate=0.1, runner_avg_r=2.0, max_drawdown_pct=0.01, worst_case_dd=0.02, dd_recovery_hours=1.0, positive_regime_count=3, worst_case_expectancy=0.5, campaign_days=7, total_trades=100)

from cte.ops.readiness import (
    EdgeProofMetrics,
    GateStatus,
    build_edge_proof_checklist,
    evaluate_readiness,
)


class TestEdgeProofChecklist:
    def test_edge_proof_all_pass(self):
        """Happy path: all gates passing."""
        metrics = EdgeProofMetrics(**{**base_edge_proof_metrics, **dict(
            expectancy_overall=15.0,
            expectancy_low_vol=5.0,
            expectancy_high_vol=10.0,
            expectancy_trending=20.0,
            positive_regime_count=3,
            tier_a_expectancy=25.0,
            tier_b_expectancy=10.0,
            tier_c_expectancy=2.0,
            tier_a_better_than_b=True,
            tier_b_better_than_c=True,
            smart_exit_pnl=500.0,
            flat_exit_pnl=350.0,
            exit_value_add_pct=42.8,
            worst_case_expectancy=5.0,
            worst_case_max_dd=0.06,
            kill_switch_false_positive_rate=0.10,
            kill_switch_response_ms=500,
            total_trades=150,
        )})
        gates = build_edge_proof_checklist(metrics)
        result = evaluate_readiness(gates)

        assert result["ready"] is True
        assert result["passed"] == 10  # 9 original + 1 sample size
        assert result["failed"] == 0
        assert result["applicable"] == 10

    @pytest.mark.parametrize("field, value, gate_name", [
        ("expectancy_overall", -1.0, "edge_overall"),
        ("positive_regime_count", 2, "edge_regime_count"),
        ("tier_a_better_than_b", False, "tier_a_gt_b"),
        ("tier_b_better_than_c", False, "tier_b_gt_c"),
        ("exit_value_add_pct", -5.0, "exit_value_add"),
        ("worst_case_expectancy", -2.0, "worst_case_expectancy"),
        ("worst_case_max_dd", 0.15, "worst_case_dd"),
        ("kill_switch_false_positive_rate", 0.25, "kill_switch_false_positive"),
        ("kill_switch_response_ms", 3000, "kill_switch_speed"),
        ("total_trades", 50, "sample_size"),
    ])
    def test_edge_proof_individual_failures(self, field, value, gate_name):
        """Test each gate failing individually."""
        # Start with all-pass metrics
        base_metrics = {
            "expectancy_overall": 1.0,
            "positive_regime_count": 3,
            "tier_a_better_than_b": True,
            "tier_b_better_than_c": True,
            "exit_value_add_pct": 1.0,
            "worst_case_expectancy": 1.0,
            "worst_case_max_dd": 0.05,
            "kill_switch_false_positive_rate": 0.05,
            "kill_switch_response_ms": 500,
            "total_trades": 200,
        }
        # Override one field to cause failure
        base_metrics[field] = value
        metrics = EdgeProofMetrics(**{**base_edge_proof_metrics, **dict(**base_metrics)})

        gates = build_edge_proof_checklist(metrics)
        result = evaluate_readiness(gates)

        assert result["ready"] is False
        blocker_names = [b["name"] for b in result["blockers"]]
        assert gate_name in blocker_names

    def test_edge_proof_boundaries(self):
        """Test threshold boundary conditions."""
        # Overall expectancy must be > 0
        metrics_zero_exp = EdgeProofMetrics(**{**base_edge_proof_metrics, **dict(expectancy_overall=0.0, total_trades=200)})
        gates = build_edge_proof_checklist(metrics_zero_exp)
        assert any(g.name == "edge_overall" and g.status == GateStatus.FAIL for g in gates)

        # Sample size must be >= min_trades
        metrics_border_trades = EdgeProofMetrics(**{**base_edge_proof_metrics, **dict(total_trades=100, expectancy_overall=1.0)})
        gates = build_edge_proof_checklist(metrics_border_trades)
        assert any(g.name == "sample_size" and g.status == GateStatus.PASS for g in gates)

        metrics_just_below = EdgeProofMetrics(**{**base_edge_proof_metrics, **dict(total_trades=99, expectancy_overall=1.0)})
        gates = build_edge_proof_checklist(metrics_just_below)
        assert any(g.name == "sample_size" and g.status == GateStatus.FAIL for g in gates)

        # Kill switch speed must be > 0 and < 2000
        metrics_zero_ms = EdgeProofMetrics(**{**base_edge_proof_metrics, **dict(kill_switch_response_ms=0, expectancy_overall=1.0)})
        gates = build_edge_proof_checklist(metrics_zero_ms)
        assert any(g.name == "kill_switch_speed" and g.status == GateStatus.FAIL for g in gates)

        metrics_fast = EdgeProofMetrics(**{**base_edge_proof_metrics, **dict(kill_switch_response_ms=1, expectancy_overall=1.0)})
        gates = build_edge_proof_checklist(metrics_fast)
        assert any(g.name == "kill_switch_speed" and g.status == GateStatus.PASS for g in gates)
