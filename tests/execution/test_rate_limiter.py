"""Tests for token bucket rate limiter."""
from __future__ import annotations

import pytest

from cte.execution.rate_limiter import RateLimiterConfig, TokenBucketRateLimiter


@pytest.fixture
def limiter():
    config = RateLimiterConfig(
        max_tokens=10,
        refill_interval_sec=1.0,
        min_tokens_for_order=2,
        backoff_base_sec=0.1,
        backoff_max_sec=1.0,
    )
    return TokenBucketRateLimiter(config)


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_acquire_immediate(self, limiter):
        wait = await limiter.acquire(1)
        assert wait == 0.0
        assert limiter.available_tokens < 10

    @pytest.mark.asyncio
    async def test_acquire_multiple(self, limiter):
        for _ in range(9):
            await limiter.acquire(1)
        assert limiter.available_tokens < 2

    @pytest.mark.asyncio
    async def test_has_capacity(self, limiter):
        assert limiter.has_capacity  # 10 tokens > min_tokens=2
        for _ in range(9):
            await limiter.acquire(1)
        assert not limiter.has_capacity  # < 2 remaining

    def test_report_429_drains_bucket(self, limiter):
        limiter.report_429()
        assert limiter.available_tokens < 0.1  # near-zero (tiny refill between calls)

    @pytest.mark.asyncio
    async def test_large_weight_acquire(self, limiter):
        wait = await limiter.acquire(5)
        assert wait == 0.0
        assert limiter.available_tokens < 6


class TestReconcilerBasic:
    """Basic reconciliation tests without real venue."""

    @pytest.mark.asyncio
    async def test_clean_reconciliation(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from cte.execution.adapter import ExecutionAdapter, VenuePosition
        from cte.execution.reconciliation import LocalPositionView, PositionReconciler

        adapter = AsyncMock(spec=ExecutionAdapter)
        adapter.get_positions.return_value = [
            VenuePosition(symbol="BTCUSDT", side="long", quantity=Decimal("1")),
        ]

        local = [LocalPositionView(symbol="BTCUSDT", side="long", quantity=Decimal("1"))]
        recon = PositionReconciler()
        result = await recon.reconcile(adapter, local)
        assert result.is_clean
        assert len(result.discrepancies) == 0
        assert result.persistent_discrepancies == []
        assert result.transient_discrepancies == []

    @pytest.mark.asyncio
    async def test_phantom_local(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from cte.execution.adapter import ExecutionAdapter
        from cte.execution.reconciliation import (
            DiscrepancyType,
            LocalPositionView,
            PositionReconciler,
        )

        adapter = AsyncMock(spec=ExecutionAdapter)
        adapter.get_positions.return_value = []

        local = [LocalPositionView(symbol="BTCUSDT", side="long", quantity=Decimal("1"))]
        result = await PositionReconciler().reconcile(adapter, local)
        assert not result.is_clean
        assert result.discrepancies[0].dtype == DiscrepancyType.PHANTOM_LOCAL
        assert len(result.persistent_discrepancies) == 1
        assert result.transient_discrepancies == []

    @pytest.mark.asyncio
    async def test_phantom_local_transient_grace(self):
        from unittest.mock import patch

        from decimal import Decimal
        from unittest.mock import AsyncMock

        from cte.execution.adapter import ExecutionAdapter
        from cte.execution.reconciliation import (
            DiscrepancyType,
            LocalPositionView,
            PositionReconciler,
        )

        adapter = AsyncMock(spec=ExecutionAdapter)
        adapter.get_positions.return_value = []

        local = [LocalPositionView(symbol="BTCUSDT", side="long", quantity=Decimal("1"))]
        with patch("cte.execution.reconciliation.time.monotonic", return_value=100.0):
            result = await PositionReconciler().reconcile(
                adapter,
                local,
                grace_until_mono={"BTCUSDT": 200.0},
            )
        assert result.is_clean
        assert result.discrepancies[0].dtype == DiscrepancyType.PHANTOM_LOCAL_TRANSIENT
        assert result.persistent_discrepancies == []
        assert len(result.transient_discrepancies) == 1

        with patch("cte.execution.reconciliation.time.monotonic", return_value=250.0):
            result2 = await PositionReconciler().reconcile(
                adapter,
                local,
                grace_until_mono={"BTCUSDT": 200.0},
            )
        assert not result2.is_clean
        assert result2.discrepancies[0].dtype == DiscrepancyType.PHANTOM_LOCAL

    @pytest.mark.asyncio
    async def test_phantom_venue(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from cte.execution.adapter import ExecutionAdapter, VenuePosition
        from cte.execution.reconciliation import DiscrepancyType, PositionReconciler

        adapter = AsyncMock(spec=ExecutionAdapter)
        adapter.get_positions.return_value = [
            VenuePosition(symbol="ETHUSDT", side="long", quantity=Decimal("5")),
        ]
        result = await PositionReconciler().reconcile(adapter, [])
        assert not result.is_clean
        assert result.discrepancies[0].dtype == DiscrepancyType.PHANTOM_VENUE

    @pytest.mark.asyncio
    async def test_quantity_mismatch(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from cte.execution.adapter import ExecutionAdapter, VenuePosition
        from cte.execution.reconciliation import (
            DiscrepancyType,
            LocalPositionView,
            PositionReconciler,
        )

        adapter = AsyncMock(spec=ExecutionAdapter)
        adapter.get_positions.return_value = [
            VenuePosition(symbol="BTCUSDT", side="long", quantity=Decimal("2")),
        ]
        local = [LocalPositionView(symbol="BTCUSDT", side="long", quantity=Decimal("1"))]
        result = await PositionReconciler().reconcile(adapter, local)
        assert not result.is_clean
        assert result.discrepancies[0].dtype == DiscrepancyType.QUANTITY_MISMATCH

    @pytest.mark.asyncio
    async def test_quantity_mismatch_strict_tolerance_zero(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from cte.execution.adapter import ExecutionAdapter, VenuePosition
        from cte.execution.reconciliation import (
            DiscrepancyType,
            LocalPositionView,
            PositionReconciler,
        )

        adapter = AsyncMock(spec=ExecutionAdapter)
        adapter.get_positions.return_value = [
            VenuePosition(symbol="BTCUSDT", side="long", quantity=Decimal("1.0001")),
        ]
        local = [LocalPositionView(symbol="BTCUSDT", side="long", quantity=Decimal("1"))]
        r = PositionReconciler(tolerance_pct=0.0)
        assert r.tolerance_pct == 0.0
        result = await r.reconcile(adapter, local)
        assert not result.is_clean
        assert result.discrepancies[0].dtype == DiscrepancyType.QUANTITY_MISMATCH

    @pytest.mark.asyncio
    async def test_quantity_match_exact_under_zero_tolerance(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from cte.execution.adapter import ExecutionAdapter, VenuePosition
        from cte.execution.reconciliation import LocalPositionView, PositionReconciler

        adapter = AsyncMock(spec=ExecutionAdapter)
        adapter.get_positions.return_value = [
            VenuePosition(symbol="BTCUSDT", side="long", quantity=Decimal("1")),
        ]
        local = [LocalPositionView(symbol="BTCUSDT", side="long", quantity=Decimal("1"))]
        result = await PositionReconciler(tolerance_pct=0.0).reconcile(adapter, local)
        assert result.is_clean

    @pytest.mark.asyncio
    async def test_side_mismatch(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from cte.execution.adapter import ExecutionAdapter, VenuePosition
        from cte.execution.reconciliation import (
            DiscrepancyType,
            LocalPositionView,
            PositionReconciler,
        )

        adapter = AsyncMock(spec=ExecutionAdapter)
        adapter.get_positions.return_value = [
            VenuePosition(symbol="BTCUSDT", side="short", quantity=Decimal("1")),
        ]
        local = [LocalPositionView(symbol="BTCUSDT", side="long", quantity=Decimal("1"))]
        result = await PositionReconciler().reconcile(adapter, local)
        assert not result.is_clean
        assert result.discrepancies[0].dtype == DiscrepancyType.SIDE_MISMATCH
