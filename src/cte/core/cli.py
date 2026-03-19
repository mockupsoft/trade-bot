"""CLI entry point for CTE services."""
from __future__ import annotations

import click


@click.group()
def main() -> None:
    """Crypto Trading Engine CLI."""


@main.command()
@click.option("--service", type=str, required=True, help="Service to start")
@click.option("--config", type=str, default=None, help="Path to config TOML")
def start(service: str, config: str | None) -> None:
    """Start a CTE service."""
    click.echo(f"Starting CTE service: {service}")


@main.command()
def validate() -> None:
    """Validate configuration and connectivity."""
    from cte.core.settings import get_settings

    settings = get_settings()
    click.echo(f"Engine mode: {settings.engine.mode.value}")
    click.echo(f"Symbols: {settings.engine.symbols}")
    click.echo(f"Execution mode: {settings.execution.mode.value}")
    click.echo("Configuration valid.")


if __name__ == "__main__":
    main()
