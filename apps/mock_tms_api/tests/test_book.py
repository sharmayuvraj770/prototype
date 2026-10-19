import asyncio
from decimal import Decimal

import psycopg
import pytest
import pytest_asyncio

from conftest import api_client, seeded_database

KEY = "idem-key-0001"


@pytest.fixture
def book_db(pg_admin_url):
    with seeded_database(pg_admin_url) as url:
        yield url


@pytest_asyncio.fixture
async def bclient(book_db, rsa_keys, tmp_path):
    async with api_client(book_db, rsa_keys[1], tmp_path) as c:
        yield c


def _carrier(db, where):
    with psycopg.connect(db) as conn:
        return conn.execute(
            "select c.dot_number from tms.carriers c join tms.carrier_compliance cc "
            f"using (dot_number) where {where} order by 1 limit 1"
        ).fetchone()[0]


def _eligible(db):
    return _carrier(
        db,
        "not c.is_fictional and cc.authority_status='active' "
        "and cc.insurance_expires >= current_date and c.equipment='reefer'",
    )


def _booked_events(db, load_id=7788):
    with psycopg.connect(db) as conn:
        return conn.execute(
            "select count(*) from tms.load_events where load_id=%s and type='booked'",
            (load_id,),
        ).fetchone()[0]


def _post(c, token, user, dot, rate=3000, key=KEY, load=7788):
    headers = {**token(user)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return c.post(
        f"/v1/loads/{load}/book",
        headers=headers,
        json={"carrier_dot": dot, "carrier_rate": rate},
    )


async def test_book_success_then_replay(bclient, token, book_db):
    dot = _eligible(book_db)
    before = _booked_events(book_db)
    r1 = await _post(bclient, token, "a.sales", dot)
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["status"] == "booked" and body["carrier_dot"] == dot
    assert Decimal(body["carrier_rate"]) == Decimal(3000)
    r2 = await _post(bclient, token, "a.sales", dot)
    assert r2.status_code == 200
    assert r2.content == r1.content
    assert _booked_events(book_db) == before + 1


async def test_book_requires_key(bclient, token, book_db):
    r = await _post(bclient, token, "a.sales", _eligible(book_db), key=None)
    assert r.status_code == 422
    r = await _post(bclient, token, "a.sales", _eligible(book_db), key="short")
    assert r.status_code == 422


async def test_ops_rep_cannot_book(bclient, token, book_db):
    r = await _post(bclient, token, "a.ops", _eligible(book_db))
    assert r.status_code == 403
    assert r.json()["required"] == "loads:book"


async def test_admin_cannot_book(bclient, token, book_db):
    r = await _post(bclient, token, "a.admin", _eligible(book_db))
    assert r.status_code == 403


async def test_book_revoked_carrier(bclient, token, book_db):
    dot = _carrier(book_db, "cc.authority_status='revoked'")
    r = await _post(bclient, token, "a.sales", dot)
    assert r.status_code == 409
    assert r.json() == {"error": "CARRIER_NOT_ELIGIBLE"}


async def test_book_expired_insurance(bclient, token, book_db):
    dot = _carrier(
        book_db, "cc.authority_status='active' and cc.insurance_expires < current_date"
    )
    r = await _post(bclient, token, "a.sales", dot)
    assert r.status_code == 409
    assert r.json() == {"error": "CARRIER_NOT_ELIGIBLE"}


async def test_book_rate_above_customer_rate(bclient, token, book_db):
    r = await _post(bclient, token, "a.sales", _eligible(book_db), rate=3600.01)
    assert r.status_code == 409
    assert r.json() == {"error": "RATE_ABOVE_CUSTOMER_RATE"}


async def test_book_extra_field_rejected(bclient, token, book_db):
    r = await bclient.post(
        "/v1/loads/7788/book",
        headers={**token("a.sales"), "Idempotency-Key": KEY},
        json={"carrier_dot": _eligible(book_db), "carrier_rate": 1, "x": 1},
    )
    assert r.status_code == 422


async def test_book_already_booked(bclient, token, book_db):
    dot = _eligible(book_db)
    assert (await _post(bclient, token, "a.sales", dot)).status_code == 200
    r = await _post(bclient, token, "a.sales", dot, key="idem-key-0002")
    assert r.status_code == 409
    assert r.json() == {"error": "LOAD_NOT_AVAILABLE"}


async def test_book_concurrent_different_keys_single_booking(bclient, token, book_db):
    dot = _eligible(book_db)
    rs = await asyncio.gather(
        _post(bclient, token, "a.sales", dot, key="idem-key-aaaa"),
        _post(bclient, token, "a.sales", dot, key="idem-key-bbbb"),
    )
    assert sorted(r.status_code for r in rs) == [200, 409]
    assert _booked_events(book_db) == 1


async def test_book_other_tenant(bclient, token, book_db):
    r = await _post(bclient, token, "b.sales", _eligible(book_db))
    assert r.status_code == 404


def _other_available_load(db):
    with psycopg.connect(db) as conn:
        return conn.execute(
            "select id from tms.loads where tenant_id='broker_a' and status='available' "
            "and equipment='reefer' and id <> 7788 order by id limit 1"
        ).fetchone()[0]


async def test_key_reused_for_different_load_409(bclient, token, book_db):
    dot = _eligible(book_db)
    other = _other_available_load(book_db)
    assert (await _post(bclient, token, "a.sales", dot)).status_code == 200
    r = await _post(bclient, token, "a.sales", dot, load=other)
    assert r.status_code == 409
    assert r.json() == {"error": "IDEMPOTENCY_KEY_REUSED"}
    assert _booked_events(book_db, other) == 0


async def test_key_reused_same_load_different_rate_409(bclient, token, book_db):
    dot = _eligible(book_db)
    assert (await _post(bclient, token, "a.sales", dot, rate=3000)).status_code == 200
    r = await _post(bclient, token, "a.sales", dot, rate=2999)
    assert r.status_code == 409
    assert r.json() == {"error": "IDEMPOTENCY_KEY_REUSED"}


async def test_concurrent_same_key_different_loads(bclient, token, book_db):
    dot = _eligible(book_db)
    other = _other_available_load(book_db)
    rs = await asyncio.gather(
        _post(bclient, token, "a.sales", dot, rate=1000),
        _post(bclient, token, "a.sales", dot, rate=1000, load=other),
    )
    assert sorted(r.status_code for r in rs) == [200, 409]
    assert [r.json() for r in rs if r.status_code == 409] == [
        {"error": "IDEMPOTENCY_KEY_REUSED"}
    ]
    assert _booked_events(book_db) + _booked_events(book_db, other) == 1
    with psycopg.connect(book_db) as conn:
        assert (
            conn.execute(
                "select count(*) from tms.loads where id in (7788, %s) and status='booked'",
                (other,),
            ).fetchone()[0]
            == 1
        )


async def test_book_equipment_mismatch(bclient, token, book_db):
    with psycopg.connect(book_db) as conn:
        assert (
            conn.execute("select equipment from tms.loads where id = 7788").fetchone()[
                0
            ]
            == "reefer"
        )
    dot = _carrier(
        book_db,
        "cc.authority_status='active' and cc.insurance_expires >= current_date "
        "and c.equipment='dry_van'",
    )
    r = await _post(bclient, token, "a.sales", dot)
    assert r.status_code == 409
    assert r.json() == {"error": "CARRIER_NOT_ELIGIBLE"}
    assert _booked_events(book_db) == 0
