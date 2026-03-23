"""Release/deploy status API tests."""

from __future__ import annotations


def test_release_status_shape(dashboard_client) -> None:
    r = dashboard_client.get("/api/release/status")
    assert r.status_code == 200
    body = r.json()
    assert body.get("service") == "analytics"
    assert "commit" in body
    assert "image" in body
    assert "last_deploy_at" in body
    assert "rollback" in body
