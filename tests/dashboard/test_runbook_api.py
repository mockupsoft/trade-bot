"""Runbook API shape and scenario coverage tests."""

from __future__ import annotations


def test_runbook_snapshot_shape(dashboard_client) -> None:
    r = dashboard_client.get("/api/runbook/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert "meta" in body
    assert "scenarios" in body
    assert isinstance(body["scenarios"], list)


def test_runbook_contains_core_scenarios(dashboard_client) -> None:
    body = dashboard_client.get("/api/runbook/snapshot").json()
    ids = {str(x.get("id")) for x in body.get("scenarios", [])}
    assert "no_entries" in ids
    assert "churn" in ids
    assert "foreign_position" in ids
    assert "recon_blocked" in ids
