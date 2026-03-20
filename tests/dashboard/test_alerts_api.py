"""Dashboard /api/alerts/status."""
from __future__ import annotations


def test_alerts_status_shape(dashboard_client) -> None:
    r = dashboard_client.get("/api/alerts/status")
    assert r.status_code == 200
    data = r.json()
    assert "meta" in data
    assert "rules" in data
    assert isinstance(data["rules"], list)
    assert len(data["rules"]) >= 8
    ids = {rule["id"] for rule in data["rules"]}
    assert "stale_warn" in ids
    assert "reconciliation" in ids
    for rule in data["rules"]:
        assert rule["state"] in ("ok", "firing", "unknown")
        assert rule["severity"] in ("warning", "critical")
