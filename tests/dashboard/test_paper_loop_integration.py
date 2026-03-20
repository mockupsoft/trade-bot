"""End-to-end paper runner: feed quote + warmup + scoring opens a position (BTC only)."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from cte.analytics.engine import AnalyticsEngine
from cte.analytics.epochs import EpochManager, EpochMode
from cte.core.settings import CTESettings
from cte.dashboard.paper_runner import DashboardPaperRunner
from cte.market.feed import TickerState
from cte.ops.kill_switch import OperationsController

pytest.importorskip("fastapi")


@pytest.mark.asyncio
async def test_paper_runner_opens_long_after_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strong uptrend + permissive tier floor → at least one paper entry."""
    monkeypatch.setenv("CTE_DASHBOARD_PAPER_DEMO_ENTRIES", "1")
    monkeypatch.setenv("CTE_DASHBOARD_PAPER_WARMUP_MIDS", "20")
    monkeypatch.setenv("CTE_DASHBOARD_PAPER_TIER_C_THRESHOLD", "0.25")

    settings = CTESettings()
    epoch = EpochManager()
    epoch.create_epoch("paper_loop_test", EpochMode.DEMO, "integration")
    epoch.activate("paper_loop_test")
    analytics = AnalyticsEngine(epoch, initial_capital=Decimal("10000"))
    ops = OperationsController()

    price = Decimal("50000")
    base_ms = int(time.time() * 1000)

    class FakeFeed:
        def __init__(self) -> None:
            self._tick = 0

        def get_ticker(self, symbol: str) -> TickerState | None:
            if symbol != "BTCUSDT":
                return None
            self._tick += 1
            # Gentle drift: avoids same-tick TP so we can assert an open leg or a closed journal row.
            price_local = price + Decimal(self._tick) * Decimal("2")
            ms = base_ms + self._tick * 1000
            t = TickerState(
                symbol="BTCUSDT",
                last_price=price_local,
                best_bid=price_local - Decimal("5"),
                best_ask=price_local + Decimal("5"),
                mark_price=price_local,
                last_update_ms=ms,
                last_trade_time_ms=ms,
            )
            return t

    feed = FakeFeed()

    runner = DashboardPaperRunner(
        settings=settings,
        market_feed=lambda: feed,
        analytics_engine=lambda: analytics,
        ops_controller=lambda: ops,
        symbols=("BTCUSDT",),
    )

    for _ in range(200):
        await runner.tick()

    st = runner.status_dict()
    assert st["entries_total"] >= 1, st.get("last_skip_by_symbol", {})
    assert st["open_positions"] >= 1 or st["exits_recorded"] >= 1
    if st["open_positions"] >= 1:
        assert runner.open_positions_payload()
