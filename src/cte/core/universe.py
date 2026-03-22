"""Canonical trading universe (Binance USDⓈ-M linear perpetuals).

Top-10 liquid USDT pairs used for dashboard paper loop and market feed.
Override via ``CTE_ENGINE_SYMBOLS`` / ``config/defaults.toml`` ``[engine] symbols``.
"""
from __future__ import annotations

# Liquid tier-1 majors + large-cap alts (Binance USDS-M perpetuals).
DEFAULT_TRADING_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
)

# Historical v1 default pair; env may still set only these two.
_LEGACY_BTC_ETH: frozenset[str] = frozenset({"BTCUSDT", "ETHUSDT"})

def merge_market_feed_symbols(symbols: list[str]) -> list[str]:
    """Merge configured engine symbols with the default 10-pair Binance USDS-M universe.

    Operators may set ``CTE_ENGINE_SYMBOLS`` to a subset; this merge still unions in
    ``DEFAULT_TRADING_SYMBOLS`` so the market feed covers all 10 majors. Venue REST
    entries use the same merged list unless ``CTE_DASHBOARD_VENUE_PROOF_SYMBOL`` locks
    execution to one pair.
    """
    merged = set(symbols) | set(DEFAULT_TRADING_SYMBOLS)
    out: list[str] = []
    for s in DEFAULT_TRADING_SYMBOLS:
        if s in merged:
            out.append(s)
    for s in symbols:
        if s in merged and s not in out:
            out.append(s)
    for s in merged:
        if s not in out:
            out.append(s)
    return out


def expand_legacy_engine_symbols(symbols: list[str]) -> list[str]:
    """Expand BTC+ETH-only configs to the full default universe.

    Operators often keep ``CTE_ENGINE_SYMBOLS`` at the old two-pair default while
    the dashboard expects the widened list. Any other explicit universe is kept
    verbatim.
    """
    if len(symbols) == 2 and set(symbols) == _LEGACY_BTC_ETH:
        return list(DEFAULT_TRADING_SYMBOLS)
    return list(symbols)


def binance_futures_default_streams(symbols: tuple[str, ...]) -> list[str]:
    """Stream names for combined WebSocket: trade + depth + mark per symbol."""
    out: list[str] = []
    for sym in symbols:
        low = sym.lower()
        out.extend(
            [
                f"{low}@trade",
                f"{low}@depth5@100ms",
                f"{low}@markPrice@1s",
            ]
        )
    return out
