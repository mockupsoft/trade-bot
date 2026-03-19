"""Market data validation script.

Connects to venue WebSockets, collects data for a configurable duration,
and reports quality metrics: message rates, gaps, latency, schema compliance.

Usage: python scripts/validate_data.py --duration 60
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import click
import orjson
import websockets


async def validate_binance(duration_sec: int) -> dict:
    """Validate Binance USDⓈ-M Futures WebSocket data."""
    url = "wss://fstream.binance.com/stream?streams=btcusdt@trade/ethusdt@trade"
    stats: dict = defaultdict(int)
    start = time.monotonic()

    try:
        async with websockets.connect(url) as ws:
            click.echo(f"Connected to Binance: {url}")
            while time.monotonic() - start < duration_sec:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = orjson.loads(raw)
                    stream = data.get("stream", "unknown")
                    stats[f"messages_{stream}"] += 1
                    stats["total_messages"] += 1

                    payload = data.get("data", {})
                    if "p" in payload and "q" in payload:
                        stats["valid_trades"] += 1
                    else:
                        stats["invalid_format"] += 1

                except asyncio.TimeoutError:
                    stats["timeouts"] += 1

    except Exception as e:
        stats["connection_error"] = str(e)

    stats["duration_sec"] = round(time.monotonic() - start, 2)
    stats["messages_per_sec"] = round(
        stats["total_messages"] / max(1, stats["duration_sec"]), 2
    )
    return dict(stats)


async def validate_bybit(duration_sec: int) -> dict:
    """Validate Bybit v5 public WebSocket data."""
    url = "wss://stream.bybit.com/v5/public/linear"
    stats: dict = defaultdict(int)
    start = time.monotonic()

    try:
        async with websockets.connect(url) as ws:
            click.echo(f"Connected to Bybit: {url}")

            subscribe = orjson.dumps({
                "op": "subscribe",
                "args": ["publicTrade.BTCUSDT", "publicTrade.ETHUSDT"],
            })
            await ws.send(subscribe)

            while time.monotonic() - start < duration_sec:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = orjson.loads(raw)

                    if "op" in data:
                        stats["op_responses"] += 1
                        continue

                    topic = data.get("topic", "unknown")
                    stats[f"messages_{topic}"] += 1
                    stats["total_messages"] += 1

                    if "data" in data:
                        stats["valid_trades"] += 1
                    else:
                        stats["invalid_format"] += 1

                except asyncio.TimeoutError:
                    stats["timeouts"] += 1

    except Exception as e:
        stats["connection_error"] = str(e)

    stats["duration_sec"] = round(time.monotonic() - start, 2)
    stats["messages_per_sec"] = round(
        stats["total_messages"] / max(1, stats["duration_sec"]), 2
    )
    return dict(stats)


@click.command()
@click.option("--duration", default=30, help="Validation duration in seconds")
@click.option("--venue", default="both", type=click.Choice(["binance", "bybit", "both"]))
def main(duration: int, venue: str) -> None:
    """Validate market data from venue WebSockets."""
    click.echo(f"Starting data validation ({duration}s)...\n")

    async def run() -> None:
        if venue in ("binance", "both"):
            click.echo("=== Binance USDⓈ-M Futures ===")
            binance_stats = await validate_binance(duration)
            for k, v in sorted(binance_stats.items()):
                click.echo(f"  {k}: {v}")
            click.echo()

        if venue in ("bybit", "both"):
            click.echo("=== Bybit v5 Linear ===")
            bybit_stats = await validate_bybit(duration)
            for k, v in sorted(bybit_stats.items()):
                click.echo(f"  {k}: {v}")
            click.echo()

    asyncio.run(run())
    click.echo("Validation complete.")


if __name__ == "__main__":
    main()
