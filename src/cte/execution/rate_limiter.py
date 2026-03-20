"""Token bucket rate limiter with exponential backoff.

Each exchange has specific rate limits:
- Binance Futures: 2400 request weight/minute, individual endpoint weights vary
- Bybit: 120 requests/minute per IP, some endpoints have lower limits

The limiter tracks tokens and provides async wait for availability.
If the bucket is empty, it waits (with backoff) rather than sending
and getting 429'd.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class RateLimiterConfig:
    """Rate limiter parameters for a specific venue."""
    max_tokens: int = 120           # max requests per window
    refill_interval_sec: float = 60.0  # window duration
    min_tokens_for_order: int = 5   # reserve for critical ops
    backoff_base_sec: float = 0.5
    backoff_max_sec: float = 30.0


BINANCE_LIMITS = RateLimiterConfig(
    max_tokens=2400,
    refill_interval_sec=60.0,
    min_tokens_for_order=10,
    backoff_base_sec=0.5,
    backoff_max_sec=30.0,
)

BYBIT_LIMITS = RateLimiterConfig(
    max_tokens=120,
    refill_interval_sec=60.0,
    min_tokens_for_order=5,
    backoff_base_sec=1.0,
    backoff_max_sec=30.0,
)


class TokenBucketRateLimiter:
    """Async token bucket rate limiter.

    Tokens refill continuously. If tokens are exhausted, callers
    wait with exponential backoff until tokens are available.
    """

    def __init__(self, config: RateLimiterConfig) -> None:
        self._config = config
        self._tokens: float = float(config.max_tokens)
        self._last_refill: float = time.monotonic()
        self._consecutive_waits: int = 0
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int = 1) -> float:
        """Acquire tokens. Returns wait time in seconds (0 if immediate).

        If insufficient tokens, waits with backoff.
        """
        async with self._lock:
            self._refill()

            if self._tokens >= weight:
                self._tokens -= weight
                self._consecutive_waits = 0
                return 0.0

            wait_time = self._calculate_wait(weight)
            self._consecutive_waits += 1

        await asyncio.sleep(wait_time)

        async with self._lock:
            self._refill()
            self._tokens = max(0, self._tokens - weight)
            return wait_time

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        refill_rate = self._config.max_tokens / self._config.refill_interval_sec
        self._tokens = min(
            float(self._config.max_tokens),
            self._tokens + elapsed * refill_rate,
        )
        self._last_refill = now

    def _calculate_wait(self, needed: int) -> float:
        """Calculate wait time with exponential backoff."""
        deficit = needed - self._tokens
        refill_rate = self._config.max_tokens / self._config.refill_interval_sec
        natural_wait = deficit / refill_rate if refill_rate > 0 else 1.0

        backoff = min(
            self._config.backoff_base_sec * (2 ** self._consecutive_waits),
            self._config.backoff_max_sec,
        )

        return max(natural_wait, backoff)

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens

    @property
    def has_capacity(self) -> bool:
        return self.available_tokens >= self._config.min_tokens_for_order

    def report_429(self) -> None:
        """Called when a 429 response is received. Drains bucket."""
        self._tokens = 0
        self._consecutive_waits += 2
