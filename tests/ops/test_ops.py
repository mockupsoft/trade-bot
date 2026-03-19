"""Tests for operational controls, readiness gates, and validation campaigns."""
from __future__ import annotations

from datetime import date

import pytest

from cte.ops.kill_switch import OperationsController, TradingMode
from cte.ops.readiness import (
    GateStatus,
    build_demo_to_live_checklist,
    build_paper_to_demo_checklist,
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

        ctrl.resume_trading()
        assert ctrl.mode == TradingMode.ACTIVE
        assert ctrl.is_entries_allowed

    def test_symbol_toggle(self):
        ctrl = OperationsController()
        assert ctrl.is_symbol_enabled("BTCUSDT")

        ctrl.disable_symbol("BTCUSDT", "test")
        assert not ctrl.is_symbol_enabled("BTCUSDT")

        ctrl.enable_symbol("BTCUSDT")
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
        ctrl.resume_trading()
        ctrl.emergency_stop("test", "stop")
        status = ctrl.status()
        assert len(status["mode_history"]) == 3


class TestReadinessGate:
    def test_paper_to_demo_all_pass(self):
        gates = build_paper_to_demo_checklist(
            paper_days=10, paper_trades=100, crash_free_days=10,
            all_tests_pass=True, state_machine_violations=0,
            api_keys_configured=True,
        )
        result = evaluate_readiness(gates)
        assert result["ready"]
        assert result["failed"] == 0

    def test_paper_to_demo_fails(self):
        gates = build_paper_to_demo_checklist(paper_days=3, paper_trades=10)
        result = evaluate_readiness(gates)
        assert not result["ready"]
        assert result["failed"] > 0
        assert len(result["blockers"]) > 0

    def test_demo_to_live_all_pass(self):
        gates = build_demo_to_live_checklist(
            demo_days=10, demo_trades=60,
            reconciliation_clean_rate=1.0,
            fill_latency_p99_ms=2000,
            paper_demo_pnl_drift_pct=2.0,
            slippage_drift_bps=1.5,
            emergency_stop_tested=True,
            manual_review_signed=True,
            max_capital_configured=True,
            monitoring_alerts_configured=True,
        )
        result = evaluate_readiness(gates)
        assert result["ready"]
        assert result["completion_pct"] == 100.0

    def test_demo_to_live_blocks_on_latency(self):
        gates = build_demo_to_live_checklist(
            demo_days=10, demo_trades=60,
            reconciliation_clean_rate=1.0,
            fill_latency_p99_ms=8000,  # > 5000ms threshold
        )
        result = evaluate_readiness(gates)
        assert not result["ready"]
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "fill_latency" in blocker_names


class TestValidationCampaign:
    def test_campaign_lifecycle(self):
        c = ValidationCampaign(name="test", target_days=3, mode="paper")
        assert c.status == CampaignStatus.PLANNED

        c.start()
        assert c.status == CampaignStatus.RUNNING

        for i in range(3):
            c.add_snapshot(DailySnapshot(
                date=date(2024, 1, i + 1),
                trade_count=10, win_rate=0.6, net_pnl=50.0,
                reconciliation_clean=True,
            ))

        c.complete()
        assert c.status == CampaignStatus.COMPLETED
        assert c.is_target_reached

    def test_campaign_report(self):
        c = ValidationCampaign(name="report_test", target_days=2)
        c.start()
        c.add_snapshot(DailySnapshot(
            date=date(2024, 1, 1), trade_count=15, win_rate=0.6,
            net_pnl=100.0, max_drawdown_pct=0.02,
            reconciliation_clean=True,
        ))
        c.add_snapshot(DailySnapshot(
            date=date(2024, 1, 2), trade_count=12, win_rate=0.5,
            net_pnl=-30.0, max_drawdown_pct=0.03,
            reconciliation_clean=True,
        ))
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
        c.add_snapshot(DailySnapshot(
            date=date(2024, 1, 1), exceptions_caught=3,
            max_drawdown_pct=0.08,
        ))
        c.complete()

        report = c.generate_report()
        assert not report["go_no_go"]["ready"]
        assert len(report["go_no_go"]["blockers"]) >= 2
