"""Database migration script.

Applies all schema migrations to the CTE database.
Usage: python scripts/migrate.py [--seed]
"""
from __future__ import annotations

import asyncio

import asyncpg
import click


async def run_migrations(dsn: str, seed: bool = False) -> None:
    """Apply all migrations and optionally seed data."""
    from cte.db.schema import ALL_MIGRATIONS, SEED_SYMBOLS, SEED_VENUES

    conn = await asyncpg.connect(dsn)

    try:
        for name, sql in ALL_MIGRATIONS:
            try:
                await conn.execute(sql)
                click.echo(f"  [OK] {name}")
            except Exception as e:
                click.echo(f"  [FAIL] {name}: {e}")
                raise

        if seed:
            await conn.execute(SEED_SYMBOLS)
            await conn.execute(SEED_VENUES)
            click.echo("  [OK] Seed data applied")

        click.echo("\nAll migrations applied successfully.")
    finally:
        await conn.close()


@click.command()
@click.option("--dsn", default="postgresql://cte@localhost:5432/cte", help="Database DSN")
@click.option("--seed", is_flag=True, help="Apply seed data (symbols, venues)")
def main(dsn: str, seed: bool) -> None:
    """Apply CTE database migrations."""
    click.echo(f"Connecting to: {dsn.split('@')[-1]}")
    click.echo("Applying migrations...")
    asyncio.run(run_migrations(dsn, seed))


if __name__ == "__main__":
    main()
