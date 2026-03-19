"""HTTP contract for trade journal (Positions UI) via analytics router."""
from __future__ import annotations


def test_trades_list_ok(dashboard_client) -> None:
    """Journal endpoint returns a list of dict rows."""
    r = dashboard_client.get("/api/analytics/trades", params={"epoch": "crypto_v1_demo", "limit": 50})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert all(isinstance(x, dict) for x in data)


def test_trades_limit_validation(dashboard_client) -> None:
    r = dashboard_client.get("/api/analytics/trades", params={"limit": 0})
    assert r.status_code == 422
    r2 = dashboard_client.get("/api/analytics/trades", params={"limit": 501})
    assert r2.status_code == 422


def test_trades_returns_journal_shape_after_record(dashboard_client) -> None:
    from datetime import UTC, datetime
    from decimal import Decimal

    from cte.dashboard import app as dash
    from cte.execution.position import PaperPosition

    eng = dash._analytics_engine
    assert eng is not None

    def _t():
        return datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)

    p = PaperPosition(
        symbol="BTCUSDT",
        direction="long",
        signal_tier="B",
        quantity=Decimal("1"),
        stop_loss_pct=0.02,
        modeled_slippage_bps=Decimal("5"),
        entry_latency_ms=50,
    )
    p.open(Decimal("50000"), _t())
    p.close(Decimal("50500"), _t(), "winner_trailing")
    eng.record_trade(p, venue="binance", source="paper_simulated")

    r = dashboard_client.get(
        "/api/analytics/trades",
        params={"epoch": "crypto_v1_demo", "limit": 10},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    row = next(x for x in rows if x["symbol"] == "BTCUSDT" and x["tier"] == "B")
    assert row["venue"] == "binance"
    assert row["epoch"] == "crypto_v1_demo"
    assert row["source"] == "paper_simulated"
    assert row["exit_reason"] == "winner_trailing"
    assert "was_profitable_at_exit" in row
