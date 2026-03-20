"""Paper loop HTTP surface (loop disabled in dashboard test session)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def test_paper_status_disabled_in_tests(dashboard_client):
    r = dashboard_client.get("/api/paper/status")
    assert r.status_code == 200
    body = r.json()
    assert body.get("enabled") is False


def test_paper_positions_empty_without_runner(dashboard_client):
    r = dashboard_client.get("/api/paper/positions")
    assert r.status_code == 200
    assert r.json().get("positions") == []


def test_paper_warmup_without_runner(dashboard_client):
    r = dashboard_client.get("/api/paper/warmup")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body


def test_paper_entry_diagnostics_without_runner(dashboard_client):
    r = dashboard_client.get("/api/paper/entry-diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
