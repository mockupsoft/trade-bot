"""Research page: /api/analytics/summary tier filter contract."""
from __future__ import annotations


def test_summary_without_tier_ok(dashboard_client) -> None:
    r = dashboard_client.get("/api/analytics/summary", params={"epoch": "crypto_v1_demo"})
    assert r.status_code == 200
    body = r.json()
    assert "trade_count" in body
    assert "runner_outcomes" in body


def test_summary_tier_abc_ok(dashboard_client) -> None:
    for tier in ("A", "B", "C"):
        r = dashboard_client.get(
            "/api/analytics/summary",
            params={"epoch": "crypto_v1_demo", "tier": tier},
        )
        assert r.status_code == 200, tier
        assert "trade_count" in r.json()


def test_summary_invalid_tier_rejected(dashboard_client) -> None:
    r = dashboard_client.get(
        "/api/analytics/summary",
        params={"epoch": "crypto_v1_demo", "tier": "Z"},
    )
    assert r.status_code == 422
