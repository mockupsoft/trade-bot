"""Pure-function feature computations for the streaming engine.

Each function takes pre-computed accumulators/state and returns a single
feature value. No I/O, no side effects, fully deterministic.

All 10 required feature families:
 1. returns / returns_z / momentum_z
 2. taker_flow_imbalance
 3. spread_bps / spread_widening
 4. orderbook_imbalance
 5. liquidation_imbalance
 6. venue_divergence_bps
 7. freshness_score
 8. execution_feasibility
 9. whale_risk_flag
10. urgent_news_flag
"""
from __future__ import annotations

from cte.features.accumulators import MomentumHistory, ReturnHistory, WindowState
from cte.features.types import (
    FRESHNESS_MAX_AGE_MS,
    MAX_ACCEPTABLE_SPREAD_BPS,
    SPREAD_WIDENING_FLOOR,
    TARGET_DEPTH_QTY_BTC,
    TARGET_DEPTH_QTY_ETH,
    VenueState,
)


# ──────────────────────────────────────────────────────────────────
# 1. Returns & Momentum Z-scores
# ──────────────────────────────────────────────────────────────────

def compute_returns(window: WindowState) -> float | None:
    """Simple return over the window: (last - first) / first.

    Returns None if no prices in window.
    """
    first = window.first_price()
    last = window.last_price()
    if first is None or last is None or first <= 0:
        return None
    return (last - first) / first


def compute_returns_z(
    current_return: float | None,
    history: ReturnHistory,
) -> float | None:
    """Z-score of the current window return vs recent history.

    High positive → unusually strong upward move
    High negative → unusually strong downward move
    None → insufficient history
    """
    if current_return is None:
        return None
    return history.z_score(current_return)


def compute_momentum_z(
    window: WindowState,
    history: MomentumHistory,
) -> float | None:
    """Z-score of net taker flow (buy_vol - sell_vol) vs recent history.

    Separates flow pressure from price movement.
    High positive → unusually strong buy pressure
    High negative → unusually strong sell pressure
    """
    net_flow = window.totals.buy_volume - window.totals.sell_volume
    return history.z_score(net_flow)


# ──────────────────────────────────────────────────────────────────
# 2. Taker Flow Imbalance
# ──────────────────────────────────────────────────────────────────

def compute_taker_flow_imbalance(window: WindowState) -> float | None:
    """(buy_vol - sell_vol) / (buy_vol + sell_vol), range [-1, +1].

    +1 = all buys (maximum aggression)
    -1 = all sells
     0 = balanced
    None = no volume
    """
    bv = window.totals.buy_volume
    sv = window.totals.sell_volume
    total = bv + sv
    if total <= 0:
        return None
    return (bv - sv) / total


# ──────────────────────────────────────────────────────────────────
# 3. Spread BPS & Spread Widening
# ──────────────────────────────────────────────────────────────────

def compute_spread_bps(window: WindowState) -> float | None:
    """Most recent bid-ask spread in basis points."""
    return window.latest_spread_bps()


def compute_spread_widening(window: WindowState) -> float | None:
    """current_spread / mean_spread_in_window.

    > 1.0 → spread is wider than recent average (deteriorating liquidity)
    < 1.0 → spread is tighter than recent average
    None  → insufficient data
    """
    current = window.latest_spread_bps()
    if current is None:
        return None

    t = window.totals
    if t.spread_count == 0:
        return None

    avg = t.spread_bps_sum / t.spread_count
    if avg < SPREAD_WIDENING_FLOOR:
        return None

    return current / avg


# ──────────────────────────────────────────────────────────────────
# 4. Orderbook Imbalance
# ──────────────────────────────────────────────────────────────────

def compute_ob_imbalance(window: WindowState) -> float | None:
    """(bid_qty - ask_qty) / (bid_qty + ask_qty) from latest snapshot.

    Range [-1, +1]. Positive = bid-heavy (buy support).
    Uses the most recent orderbook snapshot, not the window average,
    because orderbook state is point-in-time not cumulative.
    """
    snap = window.latest_ob_snapshot()
    if snap is None:
        return None

    bid_qty, ask_qty = snap
    total = bid_qty + ask_qty
    if total <= 0:
        return None

    return (bid_qty - ask_qty) / total


# ──────────────────────────────────────────────────────────────────
# 5. Liquidation Imbalance
# ──────────────────────────────────────────────────────────────────

def compute_liquidation_imbalance(window: WindowState) -> float | None:
    """(long_liq - short_liq) / (long_liq + short_liq).

    Positive → more longs being liquidated (bearish pressure)
    Negative → more shorts being liquidated (bullish pressure)
    None     → no liquidations in window (common; liquidations are sparse)
    """
    lv = window.totals.liq_long_vol
    sv = window.totals.liq_short_vol
    total = lv + sv
    if total <= 0:
        return None
    return (lv - sv) / total


# ──────────────────────────────────────────────────────────────────
# 6. Binance-vs-Bybit Divergence
# ──────────────────────────────────────────────────────────────────

def compute_venue_divergence_bps(
    binance: VenueState,
    bybit: VenueState,
) -> float | None:
    """(binance_mid - bybit_mid) / avg_mid × 10000.

    Positive → Binance trades higher than Bybit
    Negative → Binance trades lower
    None     → one or both venues have no data

    Divergence > 5 bps is notable. > 20 bps is extreme (arbitrage opportunity
    or one venue lagging). In v1 we only use this as a signal context, not
    as an arb trigger.
    """
    if binance.is_stale or bybit.is_stale:
        return None

    b_mid = binance.last_mid
    y_mid = bybit.last_mid

    if b_mid <= 0 or y_mid <= 0:
        return None

    avg_mid = (b_mid + y_mid) / 2.0
    return (b_mid - y_mid) / avg_mid * 10_000


# ──────────────────────────────────────────────────────────────────
# 7. Freshness Score
# ──────────────────────────────────────────────────────────────────

def compute_freshness(
    now_ms: int,
    last_trade_ms: int,
    last_ob_ms: int,
    binance_ms: int,
    bybit_ms: int,
) -> dict:
    """Data freshness as {source: age_ms} + composite [0, 1] score.

    The composite score is the minimum freshness across critical sources
    (trade + orderbook). If any source is completely stale, composite → 0.
    """
    def age(last: int) -> int:
        return max(0, now_ms - last) if last > 0 else now_ms

    def score(age_ms: int, max_ms: int) -> float:
        if max_ms <= 0:
            return 0.0
        return max(0.0, 1.0 - age_ms / max_ms)

    trade_age = age(last_trade_ms)
    ob_age = age(last_ob_ms)
    binance_age = age(binance_ms)
    bybit_age = age(bybit_ms)

    trade_score = score(trade_age, FRESHNESS_MAX_AGE_MS["trade"])
    ob_score = score(ob_age, FRESHNESS_MAX_AGE_MS["orderbook"])
    binance_score = score(binance_age, FRESHNESS_MAX_AGE_MS["venue"])
    bybit_score = score(bybit_age, FRESHNESS_MAX_AGE_MS["venue"])

    composite = min(trade_score, ob_score) * max(binance_score, bybit_score)

    return {
        "trade_age_ms": trade_age,
        "orderbook_age_ms": ob_age,
        "binance_age_ms": binance_age,
        "bybit_age_ms": bybit_age,
        "composite": round(composite, 4),
    }


# ──────────────────────────────────────────────────────────────────
# 8. Execution Feasibility Score
# ──────────────────────────────────────────────────────────────────

def compute_execution_feasibility(
    spread_bps: float | None,
    ob_bid_qty: float,
    ob_ask_qty: float,
    freshness_composite: float,
    symbol: str,
) -> float | None:
    """Composite score [0, 1] estimating whether execution conditions are favorable.

    Components:
    - spread_score: 1 if spread ≤ 1 bps, 0 if spread ≥ MAX_ACCEPTABLE_SPREAD_BPS
    - depth_score: min(available_depth / target_depth, 1.0)
    - freshness: composite freshness

    Final = min(spread_score, depth_score) × freshness
    Using min() instead of weighted average because any single bad component
    should tank the score (you can't trade well with wide spread OR no depth).
    """
    if spread_bps is None:
        return None

    spread_score = max(0.0, 1.0 - spread_bps / MAX_ACCEPTABLE_SPREAD_BPS)

    target_qty = TARGET_DEPTH_QTY_BTC if "BTC" in symbol else TARGET_DEPTH_QTY_ETH
    available_depth = min(ob_bid_qty, ob_ask_qty)
    depth_score = min(1.0, available_depth / target_qty) if target_qty > 0 else 0.0

    return min(spread_score, depth_score) * freshness_composite


# ──────────────────────────────────────────────────────────────────
# 9. Whale Risk Flag
# ──────────────────────────────────────────────────────────────────

def compute_whale_risk_flag(
    last_whale_event_ms: int,
    now_ms: int,
    lookback_ms: int = 3_600_000,  # 60 minutes
) -> bool:
    """True if a qualifying whale transfer was detected within lookback.

    This is context-only. It does NOT trigger trades — it gates them.
    When True, the signal engine should require higher confidence.
    """
    if last_whale_event_ms <= 0:
        return False
    return (now_ms - last_whale_event_ms) < lookback_ms


# ──────────────────────────────────────────────────────────────────
# 10. Urgent News Flag
# ──────────────────────────────────────────────────────────────────

def compute_urgent_news_flag(
    last_news_event_ms: int,
    now_ms: int,
    lookback_ms: int = 1_800_000,  # 30 minutes
) -> bool:
    """True if a high-impact news/context event was detected within lookback.

    Same gating logic as whale flag. When True, signal engine should
    pause or require elevated confidence.
    """
    if last_news_event_ms <= 0:
        return False
    return (now_ms - last_news_event_ms) < lookback_ms


# ──────────────────────────────────────────────────────────────────
# VWAP helper (from window totals)
# ──────────────────────────────────────────────────────────────────

def compute_vwap(window: WindowState) -> float | None:
    """Volume-weighted average price over the window."""
    t = window.totals
    if t.volume <= 0:
        return None
    return t.pq_sum / t.volume
