import re

with open("src/cte/dashboard/app.py") as f:
    content = f.read()

# Fix imports
content = re.sub(
r"""<<<<<<< HEAD
    CampaignValidationMetrics,
    DashboardPaperToTestnetMetrics,
    EdgeProofMetrics,
=======
>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383""",
"""    CampaignValidationMetrics,
    DashboardPaperToTestnetMetrics,
    EdgeProofMetrics,""", content)

# Fix paper_to_demo_checklist
content = re.sub(
r"""<<<<<<< HEAD
        DashboardPaperToTestnetMetrics\(
            testnet_keys=_testnet_keys_configured\(\),
            market_connected=feed_ok,
            v1_safe_not_live=_system_mode != SystemMode.LIVE,
            paper_trades=trades,
            paper_days=_readiness_int\("CTE_READINESS_PAPER_DAYS", 0\),
            crash_free_days=_readiness_int\("CTE_READINESS_CRASH_FREE_DAYS", 0\),
            all_tests_pass=_env_truthy\("CTE_READINESS_TESTS_PASS", False\),
            fsm_violations=_readiness_int\("CTE_READINESS_FSM_VIOLATIONS", 0\),
        \)
=======
        testnet_keys=_testnet_keys_configured\(\),
        market_connected=feed_ok,
        v1_safe_not_live=_system_mode != SystemMode.LIVE,
        paper_trades=trades,
        paper_days=_readiness_int\("CTE_READINESS_PAPER_DAYS", 0\),
        crash_free_days=_readiness_int\("CTE_READINESS_CRASH_FREE_DAYS", 0\),
        all_tests_pass=_env_truthy\("CTE_READINESS_TESTS_PASS", False\),
        fsm_violations=_readiness_int\("CTE_READINESS_FSM_VIOLATIONS", 0\),
>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383""",
"""        DashboardPaperToTestnetMetrics(
            testnet_keys=_testnet_keys_configured(),
            market_connected=feed_ok,
            v1_safe_not_live=_system_mode != SystemMode.LIVE,
            paper_trades=trades,
            paper_days=_readiness_int("CTE_READINESS_PAPER_DAYS", 0),
            crash_free_days=_readiness_int("CTE_READINESS_CRASH_FREE_DAYS", 0),
            all_tests_pass=_env_truthy("CTE_READINESS_TESTS_PASS", False),
            fsm_violations=_readiness_int("CTE_READINESS_FSM_VIOLATIONS", 0),
        )""", content)


# Fix edge_proof_checklist
content = re.sub(
r"""<<<<<<< HEAD
    from cte.ops.readiness import build_edge_proof_checklist
    gates = build_edge_proof_checklist\(EdgeProofMetrics\(\)\)
=======
    from cte.ops.readiness import PerformanceMetrics, build_edge_proof_checklist
    gates = build_edge_proof_checklist\(PerformanceMetrics\(\)\)
>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383""",
"""    from cte.ops.readiness import build_edge_proof_checklist
    gates = build_edge_proof_checklist(EdgeProofMetrics())""", content)


# Fix campaign_readiness
content = re.sub(
r"""<<<<<<< HEAD
            CampaignValidationMetrics\(
                campaign_days=collector.campaign_days,
                total_trades=len\(trades\),
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
            \)
=======
            campaign_days=collector.campaign_days,
            total_trades=len\(trades\),
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
>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383""",
"""            CampaignValidationMetrics(
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
            )""", content)


# Fix go_no_go_report
content = re.sub(
r"""<<<<<<< HEAD
    from cte.ops.go_no_go import GoNoGoMetrics, build_go_no_go_report
    collector = _campaign_collector
    return build_go_no_go_report\(
        GoNoGoMetrics\(
            campaign_days=collector.campaign_days,
            total_trades=collector.total_trades or \(
                _analytics_engine.total_trades if _analytics_engine else 0
            \),
        \)
=======
    from cte.ops.go_no_go import build_go_no_go_report
    collector = _campaign_collector
    return build_go_no_go_report\(
        campaign_days=collector.campaign_days,
        total_trades=collector.total_trades or \(
            _analytics_engine.total_trades if _analytics_engine else 0
        \),
>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383""",
"""    from cte.ops.go_no_go import GoNoGoMetrics, build_go_no_go_report
    collector = _campaign_collector
    return build_go_no_go_report(
        GoNoGoMetrics(
            campaign_days=collector.campaign_days,
            total_trades=collector.total_trades or (
                _analytics_engine.total_trades if _analytics_engine else 0
            ),
        )""", content)

with open("src/cte/dashboard/app.py", "w") as f:
    f.write(content)
