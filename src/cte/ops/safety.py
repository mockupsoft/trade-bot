"""Environment safety guards.

Hard blocks that prevent accidental production trading.
These checks run at startup and FAIL LOUD if violated.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)

BINANCE_PRODUCTION_URLS = {
    "https://fapi.binance.com",
    "wss://fstream.binance.com",
    "https://api.binance.com",
}

BYBIT_PRODUCTION_URLS = {
    "https://api.bybit.com",
    "wss://stream.bybit.com",
}

BINANCE_TESTNET_URLS = {
    "https://testnet.binancefuture.com",
    "wss://stream.binancefuture.com",
}

BYBIT_DEMO_URLS = {
    "https://api-demo.bybit.com",
    "https://api-testnet.bybit.com",
    "wss://stream-testnet.bybit.com",
}


class SystemMode(StrEnum):
    SEED = "seed"       # UI preview with fake data
    PAPER = "paper"     # live market data + simulated fills
    DEMO = "demo"       # live market data + testnet/demo exchange orders
    LIVE = "live"       # real money (disabled in v1)


@dataclass(frozen=True)
class SafetyCheckResult:
    passed: bool
    check: str
    detail: str


def validate_environment(
    mode: str,
    binance_rest_url: str = "",
    binance_ws_url: str = "",
    bybit_rest_url: str = "",
    binance_api_key: str = "",
    binance_api_secret: str = "",
) -> list[SafetyCheckResult]:
    """Run all safety checks. Returns list of results."""
    results: list[SafetyCheckResult] = []

    # Live mode is BLOCKED in v1
    if mode == "live":
        results.append(SafetyCheckResult(
            passed=False,
            check="live_mode_blocked",
            detail="Live trading is NOT implemented. Use 'paper' or 'demo' mode.",
        ))
        return results

    # Seed mode: no checks needed
    if mode == "seed":
        results.append(SafetyCheckResult(
            passed=True, check="seed_mode", detail="Seed mode: UI preview only",
        ))
        return results

    # Demo mode: must NOT point to production
    if mode == "demo":
        if binance_rest_url in BINANCE_PRODUCTION_URLS:
            results.append(SafetyCheckResult(
                passed=False,
                check="binance_production_guard",
                detail=f"BLOCKED: Demo mode but Binance REST URL is production: {binance_rest_url}",
            ))
        else:
            results.append(SafetyCheckResult(
                passed=True, check="binance_production_guard",
                detail=f"Binance REST URL is testnet: {binance_rest_url}",
            ))

        if bybit_rest_url in BYBIT_PRODUCTION_URLS:
            results.append(SafetyCheckResult(
                passed=False,
                check="bybit_production_guard",
                detail=f"BLOCKED: Demo mode but Bybit REST URL is production: {bybit_rest_url}",
            ))

        # Demo mode: API keys required
        if not binance_api_key or not binance_api_secret:
            results.append(SafetyCheckResult(
                passed=False,
                check="api_keys_required",
                detail="Demo mode requires BINANCE_TESTNET_API_KEY and API_SECRET",
            ))
        else:
            results.append(SafetyCheckResult(
                passed=True, check="api_keys_present",
                detail="Binance testnet API keys configured",
            ))

    # Paper mode: production URLs are OK (read-only market data)
    if mode == "paper":
        results.append(SafetyCheckResult(
            passed=True, check="paper_mode",
            detail="Paper mode: live data + simulated fills (no exchange orders)",
        ))

    return results


def enforce_safety(mode: str, **kwargs: str) -> None:
    """Run safety checks and ABORT if any fail. Call at startup."""
    results = validate_environment(mode, **kwargs)
    failed = [r for r in results if not r.passed]

    if failed:
        for r in failed:
            print(f"\n  SAFETY BLOCK: [{r.check}] {r.detail}", file=sys.stderr)
        print(
            "\n  System startup ABORTED due to safety check failure.\n"
            "  Fix configuration and restart.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)


def print_startup_banner(mode: str) -> None:
    """Print clear startup banner showing the active mode."""
    banners = {
        "seed": (
            "====================================\n"
            "  CTE - SEED MODE (UI Preview)\n"
            "  Fake data only. No market feeds.\n"
            "===================================="
        ),
        "paper": (
            "========================================\n"
            "  CTE - PAPER TRADING MODE\n"
            "  Live market data + simulated fills.\n"
            "  NO real orders. NO real capital.\n"
            "========================================"
        ),
        "demo": (
            "=============================================\n"
            "  CTE - DEMO / TESTNET MODE\n"
            "  Live market data + TESTNET orders.\n"
            "  DEMO WALLET ONLY. NO REAL CAPITAL.\n"
            "  Exchange: Binance Futures Testnet\n"
            "============================================="
        ),
        "live": (
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            "  CTE - LIVE MODE (DISABLED IN V1)\n"
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        ),
    }
    banner = banners.get(mode, banners["seed"])
    print(f"\n{banner}\n")
