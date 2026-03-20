"""Feature adapter for dashboard paper loop (no FastAPI lifespan)."""
from __future__ import annotations

import time
from collections import deque
from decimal import Decimal

import pytest

from cte.core.events import Symbol
from cte.core.settings import SignalSettings
from cte.dashboard.paper_runner import (
    _dashboard_signal_settings,
    _dashboard_warmup_thresholds,
    build_streaming_vector_from_ticker,
)
from cte.market.feed import TickerState


def test_build_streaming_vector_warm_passes_gates() -> None:
    sig = SignalSettings()
    now_ms = int(time.time() * 1000)
    t = TickerState(
        symbol="BTCUSDT",
        best_bid=Decimal("100"),
        best_ask=Decimal("100.05"),
        last_price=Decimal("100.025"),
        mark_price=Decimal("100.025"),
        last_update_ms=now_ms,
    )
    mids: deque[Decimal] = deque(
        [Decimal("99") + Decimal(i) / Decimal(200) for i in range(100)],
        maxlen=400,
    )
    vec = build_streaming_vector_from_ticker(Symbol.BTCUSDT, mids, t, sig)
    assert vec is not None
    assert vec.data_quality.warmup_complete
    assert vec.freshness.composite >= sig.gate_min_freshness


def test_dashboard_warmup_thresholds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CTE_DASHBOARD_PAPER_WARMUP_MIDS", raising=False)
    monkeypatch.delenv("CTE_DASHBOARD_PAPER_WARMUP_MIDS_FULL", raising=False)
    monkeypatch.delenv("CTE_DASHBOARD_PAPER_WARMUP_MIDS_EARLY", raising=False)
    early, full = _dashboard_warmup_thresholds()
    assert early == 20
    assert full == 36
    assert full > early


def test_dashboard_signal_settings_lowers_tier_c(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CTE_DASHBOARD_PAPER_TIER_C", raising=False)
    base = SignalSettings()
    tuned = _dashboard_signal_settings(base)
    assert tuned.tier_c_threshold < base.tier_b_threshold
    assert tuned.tier_c_threshold == pytest.approx(0.32)
