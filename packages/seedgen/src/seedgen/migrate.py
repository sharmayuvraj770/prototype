"""Minimal SQL migration runner (raw psycopg 3)."""
import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(
    os.environ.get("MIGRATIONS_DIR")
    or Path(__file__).resolve().parents[4] / "db" / "migrations"
)


def apply_migrations(conninfo: str, migrations_dir: Path) -> list[str]:
    """Apply unapplied *.sql files in lexical order, one transaction each.

    Returns the filenames applied in this call.
    """
    applied: list[str] = []
    with psycopg.connect(conninfo) as conn:
        conn.execute(
            "create table if not exists public.schema_migrations ("
            "name text primary key, applied_at timestamptz default now())"
        )
        conn.commit()
        done = {r[0] for r in conn.execute("select name from public.schema_migrations")}
        conn.commit()
        for path in sorted(Path(migrations_dir).glob("*.sql"), key=lambda p: p.name):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "insert into public.schema_migrations (name) values (%s)", (path.name,)
                )
            applied.append(path.name)
    return applied
