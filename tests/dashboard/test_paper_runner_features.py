"""Feature adapter for dashboard paper loop (no FastAPI lifespan)."""
from __future__ import annotations

import time
from collections import deque
from decimal import Decimal

from cte.core.events import Symbol
from cte.core.settings import SignalSettings
from cte.dashboard.paper_runner import build_streaming_vector_from_ticker
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
    mids50 = deque([Decimal("100") + Decimal(i) / Decimal(200) for i in range(50)], maxlen=400)
    vec50 = build_streaming_vector_from_ticker(
        Symbol.BTCUSDT, mids50, t, sig, warmup_mid_samples=50
    )
    assert vec50 is not None
    assert vec50.data_quality.warmup_complete
