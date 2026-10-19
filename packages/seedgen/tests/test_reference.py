import csv

import psycopg

from seedgen.migrate import MIGRATIONS_DIR, apply_migrations
from seedgen.reference import SEED_DIR, load_reference

TABLES = ["metros", "lanes", "rate_index", "rate_observations", "waypoints", "carriers"]


def _count(name):
    with open(SEED_DIR / f"{name}.csv", newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def test_counts_match_csv(fresh_db):
    apply_migrations(fresh_db, MIGRATIONS_DIR)
    with psycopg.connect(fresh_db) as conn:
        counts = load_reference(conn, SEED_DIR)
        assert counts == {t: _count(t) for t in TABLES}
        for t in TABLES:
            assert conn.execute(f"select count(*) from tms.{t}").fetchone()[0] == counts[t]
        assert conn.execute("select miles from tms.lanes where lane_id='CHI-DAL-R'").fetchone()[0] == 955
        assert conn.execute("select count(*) from tms.carriers where hazmat").fetchone()[0] >= 0
