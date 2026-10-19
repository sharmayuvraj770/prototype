from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from northstar_shared.testing import temp_database
from seedgen.generate import generate
from seedgen.migrate import MIGRATIONS_DIR, apply_migrations
from seedgen.reference import SEED_DIR, load_reference

DEMO_NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


def _seed(url: str) -> None:
    apply_migrations(url, MIGRATIONS_DIR)
    with psycopg.connect(url) as conn:
        load_reference(conn, SEED_DIR)
        generate(conn, demo_now=DEMO_NOW)


@pytest.fixture(scope="module")
def seeded(pg_admin_url):
    with temp_database(pg_admin_url) as url:
        _seed(url)
        with psycopg.connect(url) as conn:
            yield conn


def _one(conn, q, *args):
    return conn.execute(q, args).fetchone()


def test_counts(seeded):
    assert _one(seeded, "select count(*) from tms.loads")[0] == 500
    per = dict(seeded.execute("select tenant_id, count(*) from tms.loads group by 1").fetchall())
    assert per == {"broker_a": 200, "broker_b": 170, "broker_c": 130}
    assert _one(seeded, "select count(*) from tms.users")[0] == 15
    assert _one(seeded, "select count(*) from tms.tenants")[0] == 3


def test_pinned_4521(seeded):
    row = _one(seeded, "select tenant_id, status, orig_metro, dest_metro, equipment, assigned_rep "
                       "from tms.loads where id = 4521")
    assert row == ("broker_a", "in_transit", "CHI", "DAL", "reefer", "a.ops")
    ev = _one(seeded, "select type, city, state, at, source_integration from tms.load_events "
                      "where load_id = 4521 order by at desc, id desc limit 1")
    assert ev[:3] == ("ping", "Joliet", "IL")
    assert ev[3] == DEMO_NOW - timedelta(minutes=20)
    assert ev[4].startswith("eld:")


def test_pinned_7788(seeded):
    status, carrier, rate = _one(seeded, "select status, carrier_dot, customer_rate from tms.loads where id = 7788")
    assert status == "available"
    assert carrier is None
    assert rate == 3600


def test_pinned_6001_late(seeded):
    eta, delivery_at = _one(seeded, "select eta, delivery_at from tms.loads where id = 6001")
    assert eta - delivery_at == timedelta(hours=3)
    assert delivery_at.astimezone(UTC).date() == DEMO_NOW.date()


def test_pinned_6002_stale(seeded):
    at = _one(seeded, "select max(at) from tms.load_events where load_id = 6002 and type = 'ping'")[0]
    assert at == DEMO_NOW - timedelta(hours=6)


def test_pinned_5150(seeded):
    assert _one(seeded, "select tenant_id from tms.loads where id = 5150")[0] == "broker_b"


def test_injection_only_on_fictional(seeded):
    rows = seeded.execute(
        "select c.is_fictional from tms.tenant_carriers tc join tms.carriers c using (dot_number) "
        "where tc.notes ilike '%ignore all previous%'"
    ).fetchall()
    assert rows and all(r[0] for r in rows)


def test_no_future_events(seeded):
    assert _one(seeded, "select max(at) from tms.load_events")[0] <= DEMO_NOW


def _loads_md5(url):
    with psycopg.connect(url) as conn:
        return _one(conn, "select md5(string_agg(l::text, '|' order by id)) from tms.loads l")[0]


def test_deterministic(fresh_db, other_fresh_db):
    _seed(fresh_db)
    _seed(other_fresh_db)
    assert _loads_md5(fresh_db) == _loads_md5(other_fresh_db)


def test_chi_dal_reefer_capacity(seeded):
    n = _one(seeded, "select count(*) from tms.capacity_posts where equipment = 'reefer' "
                     "and orig_metro = 'CHI' and dest_metro = 'DAL'")[0]
    assert n >= 5
