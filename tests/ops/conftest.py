import pytest
from cte.ops.readiness import CampaignValidationMetrics, PaperToDemoMetrics, DemoToLiveMetrics, EdgeProofMetrics, DashboardPaperToTestnetMetrics
from cte.ops.go_no_go import GoNoGoMetrics

@pytest.fixture
def base_campaign_metrics():
    return dict(campaign_days=7, total_trades=100, all_recon_clean=True, max_dd_observed=0.01, avg_latency_p95_ms=100, stale_ratio=0.0, reject_ratio=0.0, error_count=0, expectancy=1.0, seed_trade_count=0)

@pytest.fixture
def base_go_no_go_metrics():
    return dict(uptime_pct=100.0, crash_count=0, stale_feed_events=0, reconnect_events=0, paper_pnl=100.0, demo_pnl=100.0, pnl_drift_pct=0.0, avg_slippage_paper=1.0, avg_slippage_demo=1.0, reconciliation_clean_pct=100.0, overall_expectancy=1.0, win_rate=0.5, tier_a_expectancy=2.0, tier_b_expectancy=1.0, tier_c_expectancy=0.5, smart_exit_value_add_pct=1.0, saved_losers=1, killed_winners=0, no_progress_regret_rate=0.1, runner_avg_r=2.0, max_drawdown_pct=0.01, worst_case_dd=0.02, dd_recovery_hours=1.0, positive_regime_count=3, worst_case_expectancy=0.5, campaign_days=7, total_trades=100)

@pytest.fixture
def base_edge_proof_metrics():
    return dict(total_trades=100, expectancy_overall=1.0, expectancy_low_vol=1.0, expectancy_high_vol=1.0, expectancy_trending=1.0, positive_regime_count=3, tier_a_expectancy=2.0, tier_b_expectancy=1.0, tier_c_expectancy=0.5, tier_a_better_than_b=True, tier_b_better_than_c=True, smart_exit_pnl=100.0, flat_exit_pnl=50.0, exit_value_add_pct=10.0, worst_case_expectancy=0.5, worst_case_max_dd=0.05, kill_switch_false_positive_rate=0.05, kill_switch_response_ms=100)

@pytest.fixture
def base_paper_to_demo_metrics():
    return dict(paper_days=7, paper_trades=50, crash_free_days=7, reconciliation_clean=True, all_tests_pass=True, state_machine_violations=0, api_keys_configured=True)

@pytest.fixture
def base_demo_to_live_metrics():
    return dict(demo_days=7, demo_trades=50, reconciliation_clean_rate=1.0, fill_latency_p99_ms=100.0, paper_demo_pnl_drift_pct=0.0, slippage_drift_bps=0.0, emergency_stop_tested=True, manual_review_signed=True, max_capital_configured=True, monitoring_alerts_configured=True)
