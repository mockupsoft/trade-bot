"""Operations control-room API shape tests."""

from __future__ import annotations


def test_ops_panel_shape(dashboard_client) -> None:
    r = dashboard_client.get("/api/ops/panel")
    assert r.status_code == 200
    body = r.json()

    assert "meta" in body
    assert "incident_feed" in body
    assert "last_errors" in body
    assert "reconnect_status" in body
    assert "reconciliation" in body
    assert "risk_veto" in body

    assert isinstance(body["incident_feed"], list)
    assert isinstance(body["reconciliation"]["trend"], list)
    assert body["risk_veto"]["total_rejections"] >= 0


def test_ops_panel_trend_accumulates_samples(dashboard_client) -> None:
    a = dashboard_client.get("/api/ops/panel").json()
    b = dashboard_client.get("/api/ops/panel").json()
    assert len(b["reconciliation"]["trend"]) >= len(a["reconciliation"]["trend"])
