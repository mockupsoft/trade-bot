"""Tests for campaign metric collection and validation gates."""
from __future__ import annotations

from decimal import Decimal

import pytest

from cte.analytics.metrics import CompletedTrade
from cte.ops.campaign import CampaignCollector, MetricSnapshot, compute_snapshot
from cte.ops.readiness import (
    CampaignValidationMetrics,
    build_campaign_validation_checklist,
    evaluate_readiness,
)


def _trade(
    pnl=10,
    source="paper_simulated",
    latency=100,
    slip=5.0,
    warmup_phase="none",
):
    return CompletedTrade(
        symbol="BTCUSDT", venue="binance", tier="A", epoch="paper",
        source=source, pnl=Decimal(str(pnl)), exit_reason="winner_trailing",
        exit_layer=4, hold_seconds=300, r_multiple=1.0, entry_latency_ms=latency,
        modeled_slippage_bps=slip, mfe_pct=0.02, mae_pct=0.005,
        was_profitable_at_exit=pnl > 0, position_mode="normal",
        warmup_phase=warmup_phase,
    )


class TestComputeSnapshot:
    def test_basic_snapshot(self):
        trades = [_trade(100), _trade(-30), _trade(50)]
        snap = compute_snapshot(trades, epoch="test", period="hourly")
        assert snap.trade_count == 3
        assert snap.win_rate == pytest.approx(2 / 3, rel=0.01)
        assert snap.net_pnl == pytest.approx(120.0)
        assert snap.period == "hourly"

    def test_empty_snapshot(self):
        snap = compute_snapshot([], epoch="test")
        assert snap.trade_count == 0
        assert snap.win_rate == 0.0

    def test_latency_percentiles(self):
        trades = [_trade(latency=i * 10) for i in range(1, 101)]
        snap = compute_snapshot(trades)
        assert snap.latency_p50_ms > 0
        assert snap.latency_p95_ms > snap.latency_p50_ms
        assert snap.latency_p99_ms >= snap.latency_p95_ms

    def test_source_breakdown(self):
        trades = [
            _trade(source="seed"),
            _trade(source="paper_simulated"),
            _trade(source="paper_simulated"),
            _trade(source="demo_exchange"),
        ]
        snap = compute_snapshot(trades)
        assert snap.seed_trades == 1
        assert snap.paper_trades == 2
        assert snap.demo_trades == 1

    def test_operational_metrics(self):
        trades = [_trade()]
        snap = compute_snapshot(trades, stale_event_count=5, reconnect_count=2,
                                recon_mismatch_count=1)
        assert snap.stale_event_count == 5
        assert snap.reconnect_count == 2
        assert snap.recon_mismatch_count == 1

    def test_to_dict(self):
        snap = compute_snapshot([_trade()], epoch="test")
        d = snap.to_dict()
        assert "trade_count" in d
        assert "latency_p95_ms" in d
        assert "source_breakdown" in d
        assert d["source_breakdown"]["paper_simulated"] == 1
        assert "promotion_trade_count" in d
        assert "warmup_phase_breakdown" in d

    def test_promotion_counts_exclude_early_warmup(self):
        trades = [
            _trade(10, warmup_phase="early"),
            _trade(10, warmup_phase="early"),
            _trade(50, warmup_phase="full"),
        ]
        snap = compute_snapshot(trades, epoch="test")
        assert snap.trade_count == 3
        assert snap.promotion_trade_count == 1
        assert snap.promotion_expectancy == pytest.approx(50.0)


class TestCampaignCollector:
    def test_add_and_retrieve(self):
        collector = CampaignCollector()
        snap = compute_snapshot([_trade()], period="hourly")
        collector.add_snapshot(snap)
        assert len(collector.snapshots) == 1
        assert collector.latest is not None

    def test_campaign_days(self):
        collector = CampaignCollector()
        for _ in range(3):
            collector.add_snapshot(MetricSnapshot(period="daily", trade_count=10))
        assert collector.campaign_days == 3

    def test_total_trades(self):
        collector = CampaignCollector()
        collector.add_snapshot(MetricSnapshot(period="daily", trade_count=15))
        collector.add_snapshot(MetricSnapshot(period="daily", trade_count=20))
        assert collector.total_trades == 35

    def test_recon_clean(self):
        collector = CampaignCollector()
        collector.add_snapshot(MetricSnapshot(recon_mismatch_count=0))
        collector.add_snapshot(MetricSnapshot(recon_mismatch_count=0))
        assert collector.all_recon_clean
        collector.add_snapshot(MetricSnapshot(recon_mismatch_count=1))
        assert not collector.all_recon_clean

    def test_summary(self):
        collector = CampaignCollector()
        collector.add_snapshot(MetricSnapshot(period="daily", trade_count=10, max_drawdown_pct=0.02))
        s = collector.summary()
        assert s["campaign_days"] == 1
        assert s["total_trades"] == 10
        assert s["latest"] is not None


base_campaign_metrics = dict(campaign_days=7, total_trades=100, all_recon_clean=True, max_dd_observed=0.01, avg_latency_p95_ms=100, stale_ratio=0.0, reject_ratio=0.0, error_count=0, expectancy=1.0, seed_trade_count=0)

class TestCampaignValidationGates:
    def test_all_pass(self):
        gates = build_campaign_validation_checklist(
            CampaignValidationMetrics(**{**base_campaign_metrics, **dict(
                campaign_days=10, total_trades=150, all_recon_clean=True,
                max_dd_observed=0.03, avg_latency_p95_ms=2000,
                stale_ratio=0.005, reject_ratio=0.02, error_count=0,
                expectancy=15.0, seed_trade_count=0,
            )})

        )
        result = evaluate_readiness(gates)
        assert result["ready"]

    def test_seed_data_blocks(self):
        gates = build_campaign_validation_checklist(
            CampaignValidationMetrics(**{**base_campaign_metrics, **dict(
                campaign_days=10, total_trades=150, all_recon_clean=True,
                seed_trade_count=5,  # seed data mixed in!
            )})

        )
        result = evaluate_readiness(gates)
        assert not result["ready"]
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "no_seed_data" in blocker_names

    def test_recon_failure_blocks(self):
        gates = build_campaign_validation_checklist(
            CampaignValidationMetrics(**{**base_campaign_metrics, **dict(
                campaign_days=10, total_trades=150, all_recon_clean=False,
            )})

        )
        result = evaluate_readiness(gates)
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "recon_integrity" in blocker_names

    def test_high_drawdown_blocks(self):
        gates = build_campaign_validation_checklist(
            CampaignValidationMetrics(**{**base_campaign_metrics, **dict(
                campaign_days=10, total_trades=150, max_dd_observed=0.08,
            )})

        )
        result = evaluate_readiness(gates)
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "max_drawdown" in blocker_names

    def test_negative_expectancy_blocks(self):
        gates = build_campaign_validation_checklist(
            CampaignValidationMetrics(**{**base_campaign_metrics, **dict(
                campaign_days=10, total_trades=150, expectancy=-5.0,
            )})

        )
        result = evaluate_readiness(gates)
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "positive_expectancy" in blocker_names

    def test_promotion_trade_count_can_fail_while_total_high(self):
        """Readiness uses promotion-only count when provided (early warmup excluded)."""
        gates = build_campaign_validation_checklist(
            CampaignValidationMetrics(**{**base_campaign_metrics, **dict(
                campaign_days=10,
                total_trades=120,
                all_recon_clean=True,
                max_dd_observed=0.02,
                promotion_trade_count=40,
                promotion_expectancy=5.0,
                promotion_max_dd_observed=0.02,
            )})

        )
        result = evaluate_readiness(gates)
        blocker_names = [b["name"] for b in result["blockers"]]
        assert "trade_count" in blocker_names
