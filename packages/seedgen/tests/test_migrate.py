from pathlib import Path

import psycopg

from seedgen.migrate import MIGRATIONS_DIR, apply_migrations


def test_applies_all(fresh_db):
    assert apply_migrations(fresh_db, MIGRATIONS_DIR) == [
        "001_tms.sql",
        "002_idempotency_binding.sql",
        "003_quotes.sql",
    ]
    with psycopg.connect(fresh_db) as conn:
        assert conn.execute("select to_regclass('tms.loads')").fetchone()[0] is not None


def test_second_run_is_noop(fresh_db):
    apply_migrations(fresh_db, MIGRATIONS_DIR)
    assert apply_migrations(fresh_db, MIGRATIONS_DIR) == []
