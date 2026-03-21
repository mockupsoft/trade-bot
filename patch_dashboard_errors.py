import re

with open("src/cte/dashboard/app.py") as f:
    content = f.read()

# Add try...except blocks for paper_to_demo_checklist
content = re.sub(
r"""async def paper_to_demo_checklist\(\):
    \"\"\"v1 path: validation \+ testnet infra \(keys, WS, safety\) with declared metrics via env\.\"\"\"
    trades = _analytics_engine\.total_trades if _analytics_engine else 0
    feed_ok = bool\(_market_feed and _market_feed\.health\.connected\)
    gates = build_dashboard_paper_to_testnet_gates\(
        DashboardPaperToTestnetMetrics\(
            testnet_keys=_testnet_keys_configured\(\),
            market_connected=feed_ok,
            v1_safe_not_live=_system_mode != SystemMode\.LIVE,
            paper_trades=trades,
            paper_days=_readiness_int\("CTE_READINESS_PAPER_DAYS", 0\),
            crash_free_days=_readiness_int\("CTE_READINESS_CRASH_FREE_DAYS", 0\),
            all_tests_pass=_env_truthy\("CTE_READINESS_TESTS_PASS", False\),
            fsm_violations=_readiness_int\("CTE_READINESS_FSM_VIOLATIONS", 0\),
        \)
    \)
    out = evaluate_readiness\(gates\)
    out\["scope_note"\] = \(
        "Paper / validation → testnet \(demo\)\. Keys and WebSocket are live checks; "
        "paper days, crash-free streak, tests, and FSM counts are attested via env "
        "\(see \.env\.example\)\."
    \)
    return out""",
"""async def paper_to_demo_checklist():
    \"\"\"v1 path: validation + testnet infra (keys, WS, safety) with declared metrics via env.\"\"\"
    try:
        trades = _analytics_engine.total_trades if _analytics_engine else 0
        feed_ok = bool(_market_feed and _market_feed.health.connected)
        gates = build_dashboard_paper_to_testnet_gates(
            DashboardPaperToTestnetMetrics(
                testnet_keys=_testnet_keys_configured(),
                market_connected=feed_ok,
                v1_safe_not_live=_system_mode != SystemMode.LIVE,
                paper_trades=trades,
                paper_days=_readiness_int("CTE_READINESS_PAPER_DAYS", 0),
                crash_free_days=_readiness_int("CTE_READINESS_CRASH_FREE_DAYS", 0),
                all_tests_pass=_env_truthy("CTE_READINESS_TESTS_PASS", False),
                fsm_violations=_readiness_int("CTE_READINESS_FSM_VIOLATIONS", 0),
            )
        )
        out = evaluate_readiness(gates)
        out["scope_note"] = (
            "Paper / validation → testnet (demo). Keys and WebSocket are live checks; "
            "paper days, crash-free streak, tests, and FSM counts are attested via env "
            "(see .env.example)."
        )
        return out
    except ValueError as exc:
        return {
            "ready": False,
            "completion_pct": 0.0,
            "failed": 1,
            "blockers": [{"name": "validation_error", "reason": str(exc)}],
            "gates": [],
            "scope_note": "Failed to evaluate readiness due to missing required metrics."
        }""", content)

# Add try...except blocks for demo_to_live_checklist
content = re.sub(
r"""async def demo_to_live_checklist\(\):
    \"\"\"Phase 5 live gates — all SKIP in v1 \(not scored; informational\)\.\"\"\"
    gates = build_phase5_live_gates_skipped\(\)
    out = evaluate_readiness\(gates\)
    out\["scope_note"\] = \(
        "Phase 5 — live mainnet is out of v1 scope \(enforce_safety\)\. "
        "Gates remain as a future checklist; none apply until Phase 5\."
    \)
    return out""",
"""async def demo_to_live_checklist():
    \"\"\"Phase 5 live gates — all SKIP in v1 (not scored; informational).\"\"\"
    try:
        gates = build_phase5_live_gates_skipped()
        out = evaluate_readiness(gates)
        out["scope_note"] = (
            "Phase 5 — live mainnet is out of v1 scope (enforce_safety). "
            "Gates remain as a future checklist; none apply until Phase 5."
        )
        return out
    except ValueError as exc:
        return {
            "ready": False,
            "completion_pct": 0.0,
            "failed": 1,
            "blockers": [{"name": "validation_error", "reason": str(exc)}],
            "gates": [],
            "scope_note": "Failed to evaluate readiness due to missing required metrics."
        }""", content)

# Add try...except blocks for edge_proof_checklist
content = re.sub(
r"""async def edge_proof_checklist\(\):
    from cte.ops.readiness import build_edge_proof_checklist
    gates = build_edge_proof_checklist\(EdgeProofMetrics\(
        total_trades=0,
        expectancy_overall=0\.0,
        expectancy_low_vol=0\.0,
        expectancy_high_vol=0\.0,
        expectancy_trending=0\.0,
        positive_regime_count=0,
        tier_a_expectancy=0\.0,
        tier_b_expectancy=0\.0,
        tier_c_expectancy=0\.0,
        tier_a_better_than_b=False,
        tier_b_better_than_c=False,
        smart_exit_pnl=0\.0,
        flat_exit_pnl=0\.0,
        exit_value_add_pct=0\.0,
        worst_case_expectancy=0\.0,
        worst_case_max_dd=0\.0,
        kill_switch_false_positive_rate=0\.0,
        kill_switch_response_ms=0\.0
    \)\)
    return evaluate_readiness\(gates\)""",
"""async def edge_proof_checklist():
    from cte.ops.readiness import build_edge_proof_checklist
    try:
        gates = build_edge_proof_checklist(EdgeProofMetrics(
            total_trades=0,
            expectancy_overall=0.0,
            expectancy_low_vol=0.0,
            expectancy_high_vol=0.0,
            expectancy_trending=0.0,
            positive_regime_count=0,
            tier_a_expectancy=0.0,
            tier_b_expectancy=0.0,
            tier_c_expectancy=0.0,
            tier_a_better_than_b=False,
            tier_b_better_than_c=False,
            smart_exit_pnl=0.0,
            flat_exit_pnl=0.0,
            exit_value_add_pct=0.0,
            worst_case_expectancy=0.0,
            worst_case_max_dd=0.0,
            kill_switch_false_positive_rate=0.0,
            kill_switch_response_ms=0.0
        ))
        return evaluate_readiness(gates)
    except ValueError as exc:
        return {
            "ready": False,
            "completion_pct": 0.0,
            "failed": 1,
            "blockers": [{"name": "validation_error", "reason": str(exc)}],
            "gates": [],
            "scope_note": "Failed to evaluate readiness due to missing required metrics."
        }""", content)

# Add try...except blocks for campaign_readiness
content = re.sub(
r"""async def campaign_readiness\(\):
    \"\"\"Readiness gates wired to REAL campaign metrics\.\"\"\"
    from cte.analytics.metrics import compute_phase_metrics_slice, trades_for_promotion_evidence
    from cte.ops.readiness import build_campaign_validation_checklist

    collector = _campaign_collector
    latest = collector\.latest
    trades = _analytics_engine\._filter_trades\(\) if _analytics_engine else \[\]
    seed_count = sum\(1 for t in trades if t\.source == "seed"\)
    ic = float\(_analytics_engine\._initial_capital\) if _analytics_engine else 10000\.0
    promo = trades_for_promotion_evidence\(trades\)
    pm = compute_phase_metrics_slice\(promo, ic\)
    promo_dd = float\(pm\["max_drawdown_pct"\]\)
    promo_exp = float\(pm\["expectancy"\]\)
    promo_n = int\(pm\["trade_count"\]\)
    return evaluate_readiness\(
        build_campaign_validation_checklist\(
            CampaignValidationMetrics\(
                campaign_days=collector\.campaign_days,
                total_trades=len\(trades\),
                all_recon_clean=collector\.all_recon_clean,
                max_dd_observed=collector\.max_dd_observed,
                avg_latency_p95_ms=collector\.avg_latency_p95,
                stale_ratio=0\.0,
                reject_ratio=latest\.reject_rate if latest else 0\.0,
                error_count=latest\.error_count if latest else 0,
                expectancy=latest\.expectancy if latest else 0\.0,
                seed_trade_count=seed_count,
                promotion_trade_count=promo_n,
                promotion_expectancy=promo_exp,
                promotion_max_dd_observed=promo_dd,
            \)
        \)
    \)""",
"""async def campaign_readiness():
    \"\"\"Readiness gates wired to REAL campaign metrics.\"\"\"
    from cte.analytics.metrics import compute_phase_metrics_slice, trades_for_promotion_evidence
    from cte.ops.readiness import build_campaign_validation_checklist

    try:
        collector = _campaign_collector
        latest = collector.latest
        trades = _analytics_engine._filter_trades() if _analytics_engine else []
        seed_count = sum(1 for t in trades if t.source == "seed")
        ic = float(_analytics_engine._initial_capital) if _analytics_engine else 10000.0
        promo = trades_for_promotion_evidence(trades)
        pm = compute_phase_metrics_slice(promo, ic)
        promo_dd = float(pm["max_drawdown_pct"])
        promo_exp = float(pm["expectancy"])
        promo_n = int(pm["trade_count"])
        return evaluate_readiness(
            build_campaign_validation_checklist(
                CampaignValidationMetrics(
                    campaign_days=collector.campaign_days,
                    total_trades=len(trades),
                    all_recon_clean=collector.all_recon_clean,
                    max_dd_observed=collector.max_dd_observed,
                    avg_latency_p95_ms=collector.avg_latency_p95,
                    stale_ratio=0.0,
                    reject_ratio=latest.reject_rate if latest else 0.0,
                    error_count=latest.error_count if latest else 0,
                    expectancy=latest.expectancy if latest else 0.0,
                    seed_trade_count=seed_count,
                    promotion_trade_count=promo_n,
                    promotion_expectancy=promo_exp,
                    promotion_max_dd_observed=promo_dd,
                )
            )
        )
    except ValueError as exc:
        return {
            "ready": False,
            "completion_pct": 0.0,
            "failed": 1,
            "blockers": [{"name": "validation_error", "reason": str(exc)}],
            "gates": [],
            "scope_note": "Failed to evaluate readiness due to missing required metrics."
        }""", content)


# Add try...except blocks for go_no_go_report
content = re.sub(
r"""async def go_no_go_report\(\):
    from cte.ops.go_no_go import GoNoGoMetrics, build_go_no_go_report
    collector = _campaign_collector
    return build_go_no_go_report\(
        GoNoGoMetrics\(
            uptime_pct=0\.0,
            crash_count=0,
            stale_feed_events=0,
            reconnect_events=0,
            paper_pnl=0\.0,
            demo_pnl=0\.0,
            pnl_drift_pct=0\.0,
            avg_slippage_paper=0\.0,
            avg_slippage_demo=0\.0,
            reconciliation_clean_pct=0\.0,
            overall_expectancy=0\.0,
            win_rate=0\.0,
            profit_factor=None,
            tier_a_expectancy=0\.0,
            tier_b_expectancy=0\.0,
            tier_c_expectancy=0\.0,
            smart_exit_value_add_pct=0\.0,
            saved_losers=0,
            killed_winners=0,
            no_progress_regret_rate=0\.0,
            runner_avg_r=0\.0,
            max_drawdown_pct=0\.0,
            worst_case_dd=0\.0,
            dd_recovery_hours=0\.0,
            positive_regime_count=0,
            worst_case_expectancy=0\.0,
            campaign_days=collector\.campaign_days,
            total_trades=collector\.total_trades or \(
                _analytics_engine\.total_trades if _analytics_engine else 0
            \),
        \)
    \)""",
"""async def go_no_go_report():
    from cte.ops.go_no_go import GoNoGoMetrics, build_go_no_go_report
    try:
        collector = _campaign_collector
        return build_go_no_go_report(
            GoNoGoMetrics(
                uptime_pct=0.0,
                crash_count=0,
                stale_feed_events=0,
                reconnect_events=0,
                paper_pnl=0.0,
                demo_pnl=0.0,
                pnl_drift_pct=0.0,
                avg_slippage_paper=0.0,
                avg_slippage_demo=0.0,
                reconciliation_clean_pct=0.0,
                overall_expectancy=0.0,
                win_rate=0.0,
                profit_factor=None,
                tier_a_expectancy=0.0,
                tier_b_expectancy=0.0,
                tier_c_expectancy=0.0,
                smart_exit_value_add_pct=0.0,
                saved_losers=0,
                killed_winners=0,
                no_progress_regret_rate=0.0,
                runner_avg_r=0.0,
                max_drawdown_pct=0.0,
                worst_case_dd=0.0,
                dd_recovery_hours=0.0,
                positive_regime_count=0,
                worst_case_expectancy=0.0,
                campaign_days=collector.campaign_days,
                total_trades=collector.total_trades or (
                    _analytics_engine.total_trades if _analytics_engine else 0
                ),
            )
        )
    except ValueError as exc:
        return {
            "final_verdict": "NO-GO",
            "overall_score": 0,
            "critical_blockers": [f"Validation Error: {exc}"],
            "sections": []
        }""", content)


with open("src/cte/dashboard/app.py", "w") as f:
    f.write(content)
