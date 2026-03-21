"""Tests for operational controls, readiness gates, and validation campaigns."""

from __future__ import annotations

from datetime import date

from cte.ops.go_no_go import GoNoGoMetrics, build_go_no_go_report
from cte.ops.kill_switch import OperationsController, TradingMode
from cte.ops.readiness import (
    PerformanceMetrics,
    PaperToDemoMetrics,
    DemoToLiveMetrics,
    DashboardPaperToTestnetMetrics,
    build_dashboard_paper_to_testnet_gates,
    build_demo_to_live_checklist,
    build_edge_proof_checklist,
    build_paper_to_demo_checklist,
    build_phase5_live_gates_skipped,
    evaluate_readiness,
)
from cte.ops.validation import CampaignStatus, DailySnapshot, ValidationCampaign


class TestKillSwitch:
    def test_initial_state_active(self):
        ctrl = OperationsController()
        assert ctrl.mode == TradingMode.ACTIVE
        assert ctrl.is_trading_allowed
        assert ctrl.is_entries_allowed

    def test_emergency_stop(self):
        ctrl = OperationsController()
        event = ctrl.emergency_stop("test", "Unit test")
        assert ctrl.mode == TradingMode.HALTED
        assert not ctrl.is_trading_allowed
        assert event.action == "emergency_stop"

    def test_pause_and_resume(self):
        ctrl = OperationsController()
        ctrl.pause_trading("test pause")
        assert ctrl.mode == TradingMode.PAUSED
        assert not ctrl.is_entries_allowed

        ctrl.resume_trading("test resume")
        assert ctrl.mode == TradingMode.ACTIVE
        assert ctrl.is_entries_allowed

    def test_symbol_toggle(self):
        ctrl = OperationsController()
        assert ctrl.is_symbol_enabled("BTCUSDT")

        ctrl.disable_symbol("BTCUSDT", "test")
        assert not ctrl.is_symbol_enabled("BTCUSDT")

        ctrl.enable_symbol("BTCUSDT", "test enable")
        assert ctrl.is_symbol_enabled("BTCUSDT")

    def test_status_output(self):
        ctrl = OperationsController()
        ctrl.emergency_stop("test", "Test reason")
        status = ctrl.status()
        assert status["mode"] == "halted"
        assert len(status["recent_events"]) == 1
        assert "BTCUSDT" in status["symbols"]

    def test_mode_history_tracked(self):
        ctrl = OperationsController()
        ctrl.pause_trading("pause")
        ctrl.resume_trading("resume")
        ctrl.emergency_stop("test", "stop")
        status = ctrl.status()
        assert len(status["mode_history"]) == 3

    def test_audit_log_covers_pause_resume_and_symbol_toggles(self):
        ctrl = OperationsController()
        ctrl.pause_trading("p")
        ctrl.resume_trading("r")
        ctrl.disable_symbol("BTCUSDT", "maintenance")
        ctrl.enable_symbol("BTCUSDT", "clear")
        events = ctrl.status()["recent_events"]
        actions = [e["action"] for e in events]
        assert actions == ["pause", "resume", "symbol_disable", "symbol_enable"]


class TestDashboardReadinessGates:
    def test_phase5_all_skipped(self) -> None:
        gates = build_phase5_live_gates_skipped()
        r = evaluate_readiness(gates)
        assert r["applicable"] == 0
        assert r["skipped"] == 10
        assert r["not_applicable"] is True
        assert not r["ready"]
        assert r["passed"] == 0
        assert r["failed"] == 0

    def test_dashboard_paper_to_testnet_all_pass(self) -> None:
        gates = build_dashboard_paper_to_testnet_gates(
            DashboardPaperToTestnetMetrics(
                testnet_keys=True,
                market_connected=True,
                v1_safe_not_live=True,
                paper_trades=100,
                paper_days=10,
                crash_free_days=10,
                all_tests_pass=True,
                fsm_violations=0,
            )
        )
        r = evaluate_readiness(gates)
        assert r["ready"]
        assert r["applicable"] == 8
        assert r["skipped"] == 0


class TestReadinessGate:
    def test_paper_to_demo_all_pass(self):
        gates = build_paper_to_demo_checklist(
            PaperToDemoMetrics(
                paper_days=10,
                paper_trades=100,
                crash_free_days=10,
                reconciliation_clean=True,
                all_tests_pass=True,
                state_machine_violations=0,
                api_keys_configured=True,
            )
        )
        result = evaluate_readiness(gates)
        assert result["ready"]
        assert result["failed"] == 0

    def test_paper_to_demo_fails(self):
        gates = build_paper_to_demo_checklist(
            PaperToDemoMetrics(
                paper_days=3,
                paper_trades=10,
                crash_free_days=0,
                reconciliation_clean=False,
                all_tests_pass=False,
                state_machine_violations=0,
                api_keys_configured=False,
            )
        )

        result = evaluate_readiness(gates)
        assert not result["ready"]
        assert result["failed"] > 0
        assert len(result["blockers"]) > 0

    def test_demo_to_live_all_pass(self):
        gates = build_demo_to_live_checklist(
            DemoToLiveMetrics(
                demo_days=10,
                demo_trades=60,
                reconciliation_clean_rate=1.0,
                fill_latency_p99_ms=2000,
                paper_demo_pnl_drift_pct=2.0,
                slippage_drift_bps=1.5,
                emergency_stop_tested=True,
                manual_review_signed=True,
                max_capital_configured=True,
                monitoring_alerts_configured=True,
            )
        )
        result = evaluate_readiness(gates)
        assert result["ready"]
        assert result["completion_pct"] == 100.0

    def test_demo_to_live_blocks_on_latency(self):
        gates = build_demo_to_live_checklist(
            DemoToLiveMetrics(
                demo_days=10,
                demo_trades=60,
                reconciliation_clean_rate=1.0,
                fill_latency_p99_ms=8000,
                paper_demo_pnl_drift_pct=2.0,
                slippage_drift_bps=1.5,
                emergency_stop_tested=True,
                manual_review_signed=True,
                max_capital_configured=True,
                monitoring_alerts_configured=True,
            )
        )
        result = evaluate_readiness(gates)
        assert not result["ready"]
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "fill_latency" in blocker_names


class TestEdgeProofGates:
    def test_all_pass(self):
        metrics = PerformanceMetrics(
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
            total_trades=100,
        )
        gates = build_edge_proof_checklist(metrics)
        result = evaluate_readiness(gates)
        assert result["ready"]

    def test_tier_separation_fail(self):
        metrics = PerformanceMetrics(
            expectancy_overall=10.0,
            expectancy_low_vol=5.0,
            expectancy_high_vol=10.0,
            expectancy_trending=20.0,
            positive_regime_count=3,
            tier_a_expectancy=5.0,
            tier_b_expectancy=15.0,
            tier_c_expectancy=2.0,
            tier_a_better_than_b=False,
            tier_b_better_than_c=True,
            smart_exit_pnl=500.0,
            flat_exit_pnl=350.0,
            exit_value_add_pct=42.8,
            worst_case_expectancy=5.0,
            worst_case_max_dd=0.06,
            kill_switch_false_positive_rate=0.10,
            kill_switch_response_ms=500.0,
            total_trades=100,
        )
        gates = build_edge_proof_checklist(metrics)
        result = evaluate_readiness(gates)
        assert not result["ready"]
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "tier_a_gt_b" in blocker_names

    def test_worst_case_survival_fail(self):
        metrics = PerformanceMetrics(
            expectancy_overall=10.0,
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
            worst_case_expectancy=-5.0,
            worst_case_max_dd=0.15,
            kill_switch_false_positive_rate=0.10,
            kill_switch_response_ms=500.0,
            total_trades=100,
        )
        gates = build_edge_proof_checklist(metrics)
        result = evaluate_readiness(gates)
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "worst_case_expectancy" in blocker_names
        assert "worst_case_dd" in blocker_names

    def test_edge_regime_count(self):
        metrics = PerformanceMetrics(
            expectancy_overall=10.0,
            expectancy_low_vol=5.0,
            expectancy_high_vol=10.0,
            expectancy_trending=20.0,
            positive_regime_count=1,
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
            kill_switch_response_ms=500.0,
            total_trades=100,
        )
        gates = build_edge_proof_checklist(metrics)
        result = evaluate_readiness(gates)
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "edge_regime_count" in blocker_names


class TestGoNoGoReport:
    def test_go_report(self):
        report = build_go_no_go_report(
            GoNoGoMetrics(
                uptime_pct=99.9,
                crash_count=0,
                stale_feed_events=1,
                reconnect_events=2,
                paper_pnl=500,
                demo_pnl=480,
                pnl_drift_pct=4.0,
                avg_slippage_paper=4.0,
                avg_slippage_demo=5.5,
                reconciliation_clean_pct=100,
                overall_expectancy=15.0,
                win_rate=0.58,
                profit_factor=1.8,
                tier_a_expectancy=25.0,
                tier_b_expectancy=10.0,
                tier_c_expectancy=3.0,
                smart_exit_value_add_pct=15.0,
                saved_losers=12,
                killed_winners=3,
                no_progress_regret_rate=0.2,
                runner_avg_r=2.5,
                max_drawdown_pct=0.025,
                worst_case_dd=0.06,
                dd_recovery_hours=4,
                positive_regime_count=3,
                worst_case_expectancy=8.0,
                campaign_days=7,
                total_trades=120,
            )
        )
        assert report["final_verdict"] == "GO"
        assert report["overall_score"] > 60
        assert len(report["critical_blockers"]) == 0

    def test_no_go_negative_expectancy(self):
        report = build_go_no_go_report(
            GoNoGoMetrics(
                uptime_pct=99.9,
                crash_count=0,
                stale_feed_events=1,
                reconnect_events=2,
                paper_pnl=500.0,
                demo_pnl=480.0,
                pnl_drift_pct=4.0,
                avg_slippage_paper=4.0,
                avg_slippage_demo=5.5,
                reconciliation_clean_pct=100.0,
                overall_expectancy=-10.0,
                win_rate=0.35,
                profit_factor=0.6,
                tier_a_expectancy=-5,
                tier_b_expectancy=-8,
                tier_c_expectancy=-15,
                smart_exit_value_add_pct=15.0,
                saved_losers=12,
                killed_winners=3,
                no_progress_regret_rate=0.2,
                runner_avg_r=2.5,
                max_drawdown_pct=0.08,
                worst_case_dd=0.15,
                dd_recovery_hours=4.0,
                positive_regime_count=0,
                worst_case_expectancy=-20.0,
                campaign_days=7,
                total_trades=120,
            )
        )
        assert report["final_verdict"] == "NO-GO"
        assert len(report["critical_blockers"]) > 0

    def test_conditional_go(self):
        report = build_go_no_go_report(
            GoNoGoMetrics(
                uptime_pct=99.5,
                crash_count=0,
                stale_feed_events=1,
                reconnect_events=2,
                paper_pnl=500.0,
                demo_pnl=480.0,
                pnl_drift_pct=4.0,
                avg_slippage_paper=4.0,
                avg_slippage_demo=5.5,
                reconciliation_clean_pct=100,
                overall_expectancy=8.0,
                win_rate=0.52,
                profit_factor=1.3,
                tier_a_expectancy=12.0,
                tier_b_expectancy=5.0,
                tier_c_expectancy=2.0,
                smart_exit_value_add_pct=-2.0,
                saved_losers=12,
                killed_winners=3,
                no_progress_regret_rate=0.2,
                runner_avg_r=2.5,
                max_drawdown_pct=0.02,
                worst_case_dd=0.05,
                dd_recovery_hours=4.0,
                positive_regime_count=3,
                worst_case_expectancy=3.0,
                campaign_days=7,
                total_trades=120,
            )
        )
        assert report["final_verdict"] in ("CONDITIONAL-GO", "GO")

    def test_report_has_all_sections(self):
        report = build_go_no_go_report(
            GoNoGoMetrics(
                uptime_pct=99.9,
                crash_count=0,
                stale_feed_events=1,
                reconnect_events=2,
                paper_pnl=500.0,
                demo_pnl=480.0,
                pnl_drift_pct=4.0,
                avg_slippage_paper=4.0,
                avg_slippage_demo=5.5,
                reconciliation_clean_pct=100.0,
                overall_expectancy=15.0,
                win_rate=0.58,
                profit_factor=1.8,
                tier_a_expectancy=25.0,
                tier_b_expectancy=10.0,
                tier_c_expectancy=3.0,
                smart_exit_value_add_pct=15.0,
                saved_losers=12,
                killed_winners=3,
                no_progress_regret_rate=0.2,
                runner_avg_r=2.5,
                max_drawdown_pct=0.025,
                worst_case_dd=0.06,
                dd_recovery_hours=4.0,
                positive_regime_count=3,
                worst_case_expectancy=8.0,
                campaign_days=7,
                total_trades=120,
            )
        )
        section_names = [s["name"] for s in report["sections"]]
        assert "system_health" in section_names
        assert "execution_reality" in section_names
        assert "signal_quality" in section_names
        assert "exit_effectiveness" in section_names
        assert "risk_behavior" in section_names
        assert "edge_stability" in section_names


class TestValidationCampaign:
    def test_campaign_lifecycle(self):
        c = ValidationCampaign(name="test", target_days=3, mode="paper")
        assert c.status == CampaignStatus.PLANNED

        c.start()
        assert c.status == CampaignStatus.RUNNING

        for i in range(3):
            c.add_snapshot(
                DailySnapshot(
                    date=date(2024, 1, i + 1),
                    trade_count=10,
                    win_rate=0.6,
                    net_pnl=50.0,
                    reconciliation_clean=True,
                )
            )

        c.complete()
        assert c.status == CampaignStatus.COMPLETED
        assert c.is_target_reached

    def test_campaign_report(self):
        c = ValidationCampaign(name="report_test", target_days=2)
        c.start()
        c.add_snapshot(
            DailySnapshot(
                date=date(2024, 1, 1),
                trade_count=15,
                win_rate=0.6,
                net_pnl=100.0,
                max_drawdown_pct=0.02,
                reconciliation_clean=True,
            )
        )
        c.add_snapshot(
            DailySnapshot(
                date=date(2024, 1, 2),
                trade_count=12,
                win_rate=0.5,
                net_pnl=-30.0,
                max_drawdown_pct=0.03,
                reconciliation_clean=True,
            )
        )
        c.complete()

        report = c.generate_report()
        assert report["campaign"] == "report_test"
        assert report["summary"]["total_trades"] == 27
        assert report["summary"]["total_pnl"] == 70.0
        assert report["summary"]["reconciliation_all_clean"]
        assert report["go_no_go"]["ready"]
        assert len(report["daily_snapshots"]) == 2

    def test_campaign_abort(self):
        c = ValidationCampaign(name="abort_test")
        c.start()
        c.add_snapshot(DailySnapshot(date=date(2024, 1, 1)))
        c.abort("Critical failure")
        assert c.status == CampaignStatus.ABORTED

    def test_report_with_blockers(self):
        c = ValidationCampaign(name="blocker_test", target_days=7)
        c.start()
        c.add_snapshot(
            DailySnapshot(
                date=date(2024, 1, 1),
                exceptions_caught=3,
                max_drawdown_pct=0.08,
            )
        )
        c.complete()

        report = c.generate_report()
        assert not report["go_no_go"]["ready"]
        assert len(report["go_no_go"]["blockers"]) >= 2
