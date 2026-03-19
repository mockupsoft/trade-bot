"""Technical indicator calculations.

Pure functions operating on numpy arrays. No side effects, no I/O.
These are the building blocks for the feature engine.
"""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def rsi(prices: NDArray[np.float64], period: int = 14) -> float | None:
    """Relative Strength Index.

    Returns None if insufficient data (< period + 1 prices).
    """
    if len(prices) < period + 1:
        return None

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def ema(prices: NDArray[np.float64], period: int) -> float | None:
    """Exponential Moving Average (last value).

    Returns None if insufficient data (< period prices).
    """
    if len(prices) < period:
        return None

    multiplier = 2.0 / (period + 1)
    ema_val = float(np.mean(prices[:period]))

    for price in prices[period:]:
        ema_val = (float(price) - ema_val) * multiplier + ema_val

    return ema_val


def vwap(
    prices: NDArray[np.float64],
    volumes: NDArray[np.float64],
) -> float | None:
    """Volume Weighted Average Price.

    Returns None if no volume data.
    """
    if len(prices) == 0 or len(volumes) == 0:
        return None

    total_volume = np.sum(volumes)
    if total_volume == 0:
        return None

    return float(np.sum(prices * volumes) / total_volume)


def orderbook_imbalance(
    bid_quantities: NDArray[np.float64],
    ask_quantities: NDArray[np.float64],
) -> float | None:
    """Orderbook imbalance ratio: (bid_vol - ask_vol) / (bid_vol + ask_vol).

    Range: [-1, 1]. Positive = buy pressure, negative = sell pressure.
    """
    bid_vol = np.sum(bid_quantities)
    ask_vol = np.sum(ask_quantities)
    total = bid_vol + ask_vol

    if total == 0:
        return None

    return float((bid_vol - ask_vol) / total)


def bid_ask_spread_bps(best_bid: float, best_ask: float) -> float | None:
    """Bid-ask spread in basis points."""
    if best_bid <= 0 or best_ask <= 0:
        return None

    mid = (best_bid + best_ask) / 2
    if mid == 0:
        return None

    return float((best_ask - best_bid) / mid * 10_000)


def price_change_pct(
    prices: NDArray[np.float64],
    lookback: int,
) -> float | None:
    """Price change percentage over lookback period."""
    if len(prices) < lookback + 1:
        return None

    old_price = float(prices[-(lookback + 1)])
    new_price = float(prices[-1])

    if old_price == 0:
        return None

    return (new_price - old_price) / old_price
