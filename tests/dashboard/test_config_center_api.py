"""Versioned settings center API tests."""

from __future__ import annotations


def test_config_center_status_shape(dashboard_client) -> None:
    r = dashboard_client.get("/api/config/center")
    assert r.status_code == 200
    body = r.json()
    assert "backend" in body
    assert "workflow" in body
    assert "revisions" in body


def test_config_center_draft_approve_apply_flow(dashboard_client) -> None:
    create = dashboard_client.post(
        "/api/config/center/drafts",
        json={
            "name": "test-draft",
            "changes": {"CTE_SIGNALS_COOLDOWN_SECONDS": "77"},
            "note": "test",
            "created_by": "pytest",
            "role": "operator",
        },
    )
    assert create.status_code == 200
    cb = create.json()
    assert cb.get("ok") is True
    rid = cb["revision"]["revision_id"]

    approve = dashboard_client.post(
        f"/api/config/center/revisions/{rid}/approve",
        json={"actor": "approver_user", "role": "approver"},
    )
    assert approve.status_code == 200
    ab = approve.json()
    assert ab.get("ok") is True
    assert ab["revision"]["status"] == "approved"

    apply = dashboard_client.post(
        f"/api/config/center/revisions/{rid}/apply",
        json={"actor": "admin_user", "role": "admin"},
    )
    assert apply.status_code == 200
    pb = apply.json()
    assert pb.get("ok") is True
    assert pb["revision"]["status"] == "applied"


def test_config_center_rejects_non_cte_key(dashboard_client) -> None:
    r = dashboard_client.post(
        "/api/config/center/drafts",
        json={"name": "bad", "changes": {"BAD_KEY": "x"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False


def test_config_center_enforces_approval_separation(dashboard_client) -> None:
    create = dashboard_client.post(
        "/api/config/center/drafts",
        json={
            "name": "same-actor",
            "changes": {"CTE_SIGNALS_MAX_SIGNALS_PER_HOUR": "12"},
            "created_by": "alice",
            "role": "operator",
        },
    )
    rid = create.json()["revision"]["revision_id"]
    approve = dashboard_client.post(
        f"/api/config/center/revisions/{rid}/approve",
        json={"actor": "alice", "role": "approver"},
    )
    assert approve.status_code == 200
    body = approve.json()
    assert body.get("ok") is False


def test_config_center_diff_schedule_and_rollback(dashboard_client) -> None:
    create = dashboard_client.post(
        "/api/config/center/drafts",
        json={
            "name": "ops-flow",
            "changes": {"CTE_SIGNALS_COOLDOWN_SECONDS": "88"},
            "created_by": "maker",
            "role": "operator",
        },
    )
    rid = create.json()["revision"]["revision_id"]
    dashboard_client.post(
        f"/api/config/center/revisions/{rid}/approve",
        json={"actor": "checker", "role": "approver"},
    )

    diff = dashboard_client.get(f"/api/config/center/revisions/{rid}/diff")
    assert diff.status_code == 200
    db = diff.json()
    assert db.get("ok") is True
    assert isinstance(db.get("rows"), list)

    sched = dashboard_client.post(
        f"/api/config/center/revisions/{rid}/schedule",
        json={
            "actor": "admin",
            "role": "admin",
            "run_at_utc": "2099-01-01T00:00:00Z",
        },
    )
    assert sched.status_code == 200
    sb = sched.json()
    assert sb.get("ok") is True
    assert sb["revision"]["status"] == "scheduled"

    apply = dashboard_client.post(
        f"/api/config/center/revisions/{rid}/apply",
        json={"actor": "admin", "role": "admin"},
    )
    assert apply.status_code == 200
    assert apply.json().get("ok") is True

    rollback = dashboard_client.post(
        f"/api/config/center/revisions/{rid}/rollback",
        json={"actor": "admin", "role": "admin"},
    )
    assert rollback.status_code == 200
    rb = rollback.json()
    assert rb.get("ok") is True
    assert rb["revision"]["status"] == "applied"
