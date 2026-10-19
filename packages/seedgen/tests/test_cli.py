from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from seedgen import cli
from seedgen.cli import run_init, wait_for_db
from seedgen.keys import ensure_keys
from seedgen.migrate import MIGRATIONS_DIR
from seedgen.reference import SEED_DIR

T = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


def _init(url, tmp_path, now):
    return run_init(database_url=url, seed_dir=SEED_DIR, keys_dir=tmp_path / "keys", demo_now=now)


def test_init_is_idempotent(fresh_db, tmp_path):
    assert _init(fresh_db, tmp_path, T) == "seeded"
    assert _init(fresh_db, tmp_path, T) == "skipped"
    with psycopg.connect(fresh_db) as conn:
        assert conn.execute("select count(*) from tms.loads").fetchone()[0] == 500


def test_init_reseeds_when_stale(fresh_db, tmp_path):
    assert _init(fresh_db, tmp_path, T) == "seeded"
    later = T + timedelta(hours=13)
    assert _init(fresh_db, tmp_path, later) == "reseeded"
    with psycopg.connect(fresh_db) as conn:
        assert conn.execute("select count(*) from tms.loads").fetchone()[0] == 500
        at = conn.execute("select at from tms.load_events where load_id = 4521 "
                          "order by at desc, id desc limit 1").fetchone()[0]
    assert at == later - timedelta(minutes=20)


def test_reseed_clears_quotes(fresh_db, tmp_path):
    assert _init(fresh_db, tmp_path, T) == "seeded"
    with psycopg.connect(fresh_db) as conn:
        uid, tid = conn.execute("select id, tenant_id from tms.users limit 1").fetchone()
        conn.execute(
            "insert into tms.quotes (id, tenant_id, created_by, customer_name, orig_metro, "
            "dest_metro, equipment, pickup_date, rate_per_mile, miles, total, status, "
            "created_at, updated_at) values ('Q-ABCDEF', %s, %s, 'c', 'CHI', 'DAL', 'reefer', "
            "current_date, 3.00, 900, 2700, 'draft', now(), now())",
            (tid, uid),
        )
    assert _init(fresh_db, tmp_path, T + timedelta(hours=13)) == "reseeded"
    with psycopg.connect(fresh_db) as conn:
        assert conn.execute("select count(*) from tms.quotes").fetchone()[0] == 0


def test_keys_created_once(tmp_path):
    d = tmp_path / "k"
    assert ensure_keys(d) is True
    before = (d / "jwt_private.pem").read_bytes(), (d / "jwt_public.pem").read_bytes()
    assert ensure_keys(d) is False
    assert before == ((d / "jwt_private.pem").read_bytes(), (d / "jwt_public.pem").read_bytes())
    assert before[0].startswith(b"-----BEGIN PRIVATE KEY")


def test_force_reseeds_inside_window(fresh_db, tmp_path):
    assert _init(fresh_db, tmp_path, T) == "seeded"
    later = T + timedelta(hours=1)  # well inside the 12 h window
    assert (
        run_init(
            database_url=fresh_db,
            seed_dir=SEED_DIR,
            keys_dir=tmp_path / "keys",
            demo_now=later,
            force=True,
        )
        == "reseeded"
    )
    with psycopg.connect(fresh_db) as conn:
        assert conn.execute("select count(*) from tms.loads").fetchone()[0] == 500
        assert conn.execute("select demo_now from tms.seed_meta").fetchone()[0] == later


def test_wait_for_db_retries_then_raises():
    sleeps: list[float] = []
    with pytest.raises(psycopg.OperationalError):
        wait_for_db(
            "postgresql://x:y@127.0.0.1:1/none?connect_timeout=1",
            attempts=3,
            delay=0.5,
            sleep=sleeps.append,
        )
    assert sleeps == [0.5, 0.5]  # 3 attempts, a pause between each


def test_wait_for_db_returns_when_reachable(fresh_db):
    sleeps: list[float] = []
    wait_for_db(fresh_db, attempts=3, delay=0.5, sleep=sleeps.append)
    assert sleeps == []


def test_main_honours_force_reseed(monkeypatch):
    calls = {}
    monkeypatch.setattr(cli, "wait_for_db", lambda url: calls.setdefault("waited", url))
    monkeypatch.setattr(cli, "run_init", lambda **kw: calls.update(kw) or "reseeded")
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/db")
    monkeypatch.setenv("FORCE_RESEED", "1")
    cli.main()
    assert calls["force"] is True and calls["waited"] == "postgresql://h/db"
    monkeypatch.delenv("FORCE_RESEED")
    cli.main()
    assert calls["force"] is False
