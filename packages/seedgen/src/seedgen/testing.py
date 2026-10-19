"""Test helpers that need the seed generator (shared by the API and MCP server test suites).

Lives in seedgen, not northstar_shared, because seedgen depends on northstar_shared.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg
from northstar_shared.testing import temp_database

from seedgen.generate import generate
from seedgen.migrate import MIGRATIONS_DIR, apply_migrations
from seedgen.reference import SEED_DIR, load_reference


@contextmanager
def seeded_database(pg_admin_url: str, demo_now: datetime | None = None) -> Iterator[str]:
    """Fresh temp database, migrated and seeded at `demo_now` (default: now, to the minute)."""
    demo_now = demo_now or datetime.now(UTC).replace(second=0, microsecond=0)
    with temp_database(pg_admin_url) as url:
        apply_migrations(url, MIGRATIONS_DIR)
        with psycopg.connect(url) as conn:
            load_reference(conn, SEED_DIR)
            generate(conn, demo_now=demo_now)
        yield url
