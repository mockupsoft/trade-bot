"""Dashboard /api/config snapshot."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")


@pytest.fixture()
def client():
    os.environ.setdefault("CTE_BINANCE_TESTNET_API_KEY", "x" * 12)
    os.environ.setdefault("CTE_BINANCE_TESTNET_API_SECRET", "y" * 12)
    from fastapi.testclient import TestClient

    from cte.dashboard.app import app

    with TestClient(app) as c:
        yield c


def test_redacted_redis_url_hides_password() -> None:
    from cte.dashboard.app import _redacted_redis_url

    out = _redacted_redis_url("redis://operator:secretpass@redis.internal:6379/0")
    assert "***" in out
    assert "secretpass" not in out
    assert _redacted_redis_url("redis://localhost:6379/0") == "redis://localhost:6379/0"


def test_config_returns_sections_and_meta(client) -> None:
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["read_only"] is True
    assert "utc" in data["meta"]
    assert "sections" in data
    assert len(data["sections"]) >= 6
    titles = {s["title"] for s in data["sections"]}
    assert "Runtime & modes" in titles
    assert "Signal engine" in titles
    runtime = next(s for s in data["sections"] if s["id"] == "runtime")
    keys = {row["key"] for row in runtime["rows"]}
    assert "engine_mode" in keys
    assert "testnet_keys" in keys
    sig = next(s for s in data["sections"] if s["id"] == "signals")
    w = next(r["value"] for r in sig["rows"] if r["key"] == "signal_weights")
    assert isinstance(w, dict)
    assert pytest.approx(sum(w.values()), rel=1e-6) == 1.0
