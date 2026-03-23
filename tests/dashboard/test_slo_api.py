"""SLA/SLO API contract tests."""

from __future__ import annotations


def test_slo_status_shape(dashboard_client) -> None:
    r = dashboard_client.get("/api/slo/status")
    assert r.status_code == 200
    body = r.json()
    assert "meta" in body
    assert "targets" in body
    assert "kpis" in body
    assert "raw" in body


def test_slo_status_contains_required_kpis(dashboard_client) -> None:
    body = dashboard_client.get("/api/slo/status").json()
    kpis = body.get("kpis") or {}
    for key in (
        "uptime",
        "decision_latency",
        "fill_quality_slippage",
        "fill_quality_fill_rate",
        "rejection_rate",
    ):
        assert key in kpis
        item = kpis[key]
        assert "actual" in item
        assert "target" in item
        assert item.get("status") in {"ok", "breach"}
