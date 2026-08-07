"""CLI for explicit inventory and workflow database lifecycle operations."""

from pathlib import Path
from typing import Annotated

import typer

from invoice_agents.db.core import (
    DatabaseKind,
    infer_kind,
    migrate_database,
    seed_inventory,
    verify_database,
)

app = typer.Typer(no_args_is_help=True, help="Explicit SQLite setup and verification.")


@app.command("migrate")
def migrate_command(
    db: Annotated[Path, typer.Option("--db", help="Database file to create or migrate")],
    kind: Annotated[DatabaseKind | None, typer.Option()] = None,
) -> None:
    """Apply versioned migrations without seeding inferred data."""

    selected = kind or infer_kind(db)
    applied = migrate_database(db, selected)
    typer.echo(f"database={db.resolve()} kind={selected.value} applied={applied}")


@app.command("seed")
def seed_command(
    db: Annotated[Path, typer.Option("--db", help="Migrated inventory database")],
) -> None:
    """Seed the four inventory facts supplied by the challenge README."""

    count = seed_inventory(db)
    typer.echo(f"database={db.resolve()} seeded_rows={count}")


@app.command("verify")
def verify_command(
    db: Annotated[Path, typer.Option("--db", help="Database file to verify")],
    kind: Annotated[DatabaseKind | None, typer.Option()] = None,
) -> None:
    """Fail loudly if signature, schema, integrity, or seed identity is wrong."""

    result = verify_database(db, kind)
    typer.echo(str(result))
