"""Shared FastAPI TestClient for dashboard tests (single lifespan / epoch)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")


@pytest.fixture(scope="session")
def dashboard_client():
    os.environ.setdefault("CTE_BINANCE_TESTNET_API_KEY", "x" * 12)
    os.environ.setdefault("CTE_BINANCE_TESTNET_API_SECRET", "y" * 12)
    from fastapi.testclient import TestClient

    from cte.dashboard.app import app

    with TestClient(app) as client:
        yield client
