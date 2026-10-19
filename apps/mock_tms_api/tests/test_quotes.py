import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from conftest import api_client

PRICER = "a.pricing"
BODY = {
    "customer_name": "Acme Foods",
    "orig_metro": "CHI",
    "dest_metro": "DAL",
    "equipment": "reefer",
    "pickup_date": (datetime.now(UTC).date() + timedelta(days=14)).isoformat(),
    "rate_per_mile": "3.20",
}


async def _create(client, token, user=PRICER, **over):
    return await client.post("/v1/quotes", json={**BODY, **over}, headers=token(user))


async def test_create_and_get(client, token):
    r = await _create(client, token)
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["id"].startswith("Q-") and len(q["id"]) == 8
    assert q["miles"] == 955
    assert Decimal(str(q["total"])) == Decimal("3056.00")
    assert q["status"] == "draft" and q["version"] == 1
    assert q["created_by"] == PRICER and q["tenant_id"] == "broker_a"
    g = await client.get(f"/v1/quotes/{q['id']}", headers=token(PRICER))
    assert g.status_code == 200 and g.json()["id"] == q["id"]


async def test_update_requires_if_match(client, token):
    q = (await _create(client, token)).json()
    r = await client.patch(
        f"/v1/quotes/{q['id']}", json={"notes": "x"}, headers=token(PRICER)
    )
    assert r.status_code == 428
    assert r.json() == {"error": "PRECONDITION_REQUIRED"}


async def test_update_recomputes_total_and_bumps_version(client, token):
    q = (await _create(client, token)).json()
    r = await client.patch(
        f"/v1/quotes/{q['id']}",
        json={"rate_per_mile": "4.00", "notes": "fuel incl."},
        headers={**token(PRICER), "If-Match": "1"},
    )
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["version"] == 2 and Decimal(str(u["total"])) == Decimal("3820.00")
    assert u["notes"] == "fuel incl."


async def test_update_stale_version(client, token):
    q = (await _create(client, token)).json()
    h = {**token(PRICER), "If-Match": "1"}
    assert (
        await client.patch(f"/v1/quotes/{q['id']}", json={"notes": "a"}, headers=h)
    ).status_code == 200
    r = await client.patch(f"/v1/quotes/{q['id']}", json={"notes": "b"}, headers=h)
    assert r.status_code == 412
    assert r.json() == {"error": "VERSION_CONFLICT"}


async def test_if_match_non_integer(client, token):
    q = (await _create(client, token)).json()
    r = await client.patch(
        f"/v1/quotes/{q['id']}",
        json={"notes": "a"},
        headers={**token(PRICER), "If-Match": '"abc"'},
    )
    assert r.status_code == 412
    assert r.json() == {"error": "VERSION_CONFLICT"}


async def test_patch_unknown_quote_404_and_empty_body_422(client, token):
    h = {**token(PRICER), "If-Match": "1"}
    r = await client.patch("/v1/quotes/Q-FFFFFF", json={"notes": "a"}, headers=h)
    assert r.status_code == 404
    q = (await _create(client, token)).json()
    r = await client.patch(f"/v1/quotes/{q['id']}", json={}, headers=h)
    assert r.status_code == 422
    r = await client.patch(
        f"/v1/quotes/{q['id']}", json={"status": "withdrawn"}, headers=h
    )
    assert r.status_code == 422


async def test_withdraw_then_revert(client, token):
    q = (await _create(client, token)).json()
    w = await client.post(f"/v1/quotes/{q['id']}/withdraw", headers=token(PRICER))
    assert w.status_code == 200
    assert w.json()["status"] == "withdrawn" and w.json()["version"] == 2
    again = await client.post(f"/v1/quotes/{q['id']}/withdraw", headers=token(PRICER))
    assert again.status_code == 409
    assert again.json() == {"error": "ALREADY_WITHDRAWN"}
    r = await client.patch(
        f"/v1/quotes/{q['id']}",
        json={"status": "draft"},
        headers={**token(PRICER), "If-Match": "2"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "draft" and r.json()["version"] == 3


async def test_other_tenant_quote_404(client, token):
    q = (await _create(client, token)).json()
    g = await client.get(f"/v1/quotes/{q['id']}", headers=token("b.pricing"))
    assert g.status_code == 404
    p = await client.patch(
        f"/v1/quotes/{q['id']}",
        json={"notes": "x"},
        headers={**token("b.pricing"), "If-Match": "1"},
    )
    assert p.status_code == 404
    w = await client.post(f"/v1/quotes/{q['id']}/withdraw", headers=token("b.pricing"))
    assert w.status_code == 404
    lst = await client.get("/v1/quotes?limit=100", headers=token("b.pricing"))
    assert q["id"] not in [i["id"] for i in lst.json()["items"]]


async def test_ops_rep_cannot_write(client, token):
    r = await _create(client, token, user="a.ops")
    assert r.status_code == 403
    assert r.json()["required"] == "quotes:write"
    assert (await client.get("/v1/quotes", headers=token("a.ops"))).status_code == 403


async def test_admin_read_only(client, token):
    assert (await client.get("/v1/quotes", headers=token("a.admin"))).status_code == 200
    r = await _create(client, token, user="a.admin")
    assert r.status_code == 403


async def test_rejects_extra_and_bounds(client, token):
    assert (await _create(client, token, bogus=1)).status_code == 422
    assert (await _create(client, token, rate_per_mile="50")).status_code == 422
    assert (await _create(client, token, rate_per_mile="0")).status_code == 422
    assert (await _create(client, token, notes="x" * 1001)).status_code == 422
    assert (await _create(client, token, notes="x" * 1000)).status_code == 201
    assert (await _create(client, token, equipment="flatbed")).status_code == 422
    assert (await _create(client, token, orig_metro="ZZZ")).status_code == 422
    q = (await _create(client, token)).json()
    h = {**token(PRICER), "If-Match": "1"}
    assert (
        await client.patch(
            f"/v1/quotes/{q['id']}", json={"rate_per_mile": "50"}, headers=h
        )
    ).status_code == 422
    assert (
        await client.patch(f"/v1/quotes/{q['id']}", json={"miles": 5}, headers=h)
    ).status_code == 422


async def test_bad_quote_id_422(client, token):
    r = await client.get("/v1/quotes/not-a-quote", headers=token(PRICER))
    assert r.status_code == 422


async def test_list_filter_and_keyset_paging(client, token):
    ids = [
        (await _create(client, token, user="c.pricing")).json()["id"] for _ in range(5)
    ]
    await client.post(f"/v1/quotes/{ids[0]}/withdraw", headers=token("c.pricing"))
    seen, cursor = [], None
    while True:
        url = "/v1/quotes?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=token("c.pricing"))).json()
        assert len(page["items"]) <= 2
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5
    assert seen[:5] == list(reversed(ids))[:5] or set(seen) == set(ids)
    w = (
        await client.get("/v1/quotes?status=withdrawn", headers=token("c.pricing"))
    ).json()
    assert [i["id"] for i in w["items"]] == [ids[0]]
    assert (
        await client.get("/v1/quotes?status=bogus", headers=token("c.pricing"))
    ).status_code == 422
    assert (
        await client.get("/v1/quotes?limit=101", headers=token("c.pricing"))
    ).status_code == 422
    assert (
        await client.get("/v1/quotes?cursor=!!!", headers=token("c.pricing"))
    ).status_code == 422


async def test_openapi_operation_ids(client):
    spec = (await client.get("/openapi.json")).json()
    found, methods = {}, set()
    for path, item in spec["paths"].items():
        if path.startswith("/v1/quotes"):
            for method, op in item.items():
                found[op["operationId"]] = (method, path)
                methods.add(method)
                assert op.get("summary")
    assert set(found) == {
        "create_quote",
        "list_quotes",
        "get_quote",
        "update_quote",
        "withdraw_quote",
    }
    assert "delete" not in methods
    assert found["update_quote"] == ("patch", "/v1/quotes/{quote_id}")


def _commit_probe(db_url, seen):
    """ASGI wrapper: when the final response body is sent, read the quote from a SEPARATE
    connection. Catches writes that are only committed after the response goes out."""

    def wrap(app):
        async def probed(scope, receive, send):
            if scope["type"] != "http":
                return await app(scope, receive, send)
            chunks: list[bytes] = []

            async def _send(msg):
                if msg["type"] == "http.response.body":
                    chunks.append(msg.get("body", b""))
                    if not msg.get("more_body"):
                        body = json.loads(b"".join(chunks) or b"null")
                        if isinstance(body, dict) and "version" in body:
                            async with await psycopg.AsyncConnection.connect(
                                db_url
                            ) as c:
                                cur = await c.execute(
                                    "select version, status from tms.quotes where id = %s",
                                    (body["id"],),
                                )
                                seen.append((body["version"], await cur.fetchone()))
                await send(msg)

            await app(scope, receive, _send)

        return probed

    return wrap


async def test_writes_committed_before_response(api_db, rsa_keys, tmp_path, token):
    seen: list = []
    async with api_client(
        api_db, rsa_keys[1], tmp_path, wrap=_commit_probe(api_db, seen)
    ) as c:
        q = (await _create(c, token)).json()
        await c.patch(
            f"/v1/quotes/{q['id']}",
            json={"notes": "n"},
            headers={**token(PRICER), "If-Match": "1"},
        )
        await c.post(f"/v1/quotes/{q['id']}/withdraw", headers=token(PRICER))
    assert seen == [(1, (1, "draft")), (2, (2, "draft")), (3, (3, "withdrawn"))]


def test_every_route_closes_its_connection_before_the_response(rsa_keys, tmp_path):
    """Structural guard for the commit rule in db.py: no route may take get_conn with the
    default request scope (that commits after the response is sent)."""
    from fastapi.routing import APIRoute
    from mock_tms_api.db import get_conn
    from mock_tms_api.main import create_app
    from mock_tms_api.settings import Settings

    key = tmp_path / "k.pem"
    key.write_bytes(rsa_keys[1])
    app = create_app(
        Settings(database_url="postgresql://unused/x", jwt_public_key_path=key)
    )

    def walk(dep):
        for d in dep.dependencies:
            yield d
            yield from walk(d)

    from mock_tms_api.routers import carriers, loads, market, quotes

    # FastAPI 0.149 mounts included routers lazily, so walk each router's own routes too.
    routes = [
        *app.routes,
        *(r for m in (carriers, loads, market, quotes) for r in m.router.routes),
    ]
    checked = 0
    for route in routes:
        if isinstance(route, APIRoute):
            for d in walk(route.dependant):
                if d.call is get_conn:
                    assert d.scope == "function", route.path
                    checked += 1
    assert checked >= 10


async def test_same_orig_dest_422(client, token):
    r = await _create(client, token, dest_metro="CHI")
    assert (r.status_code, r.json()) == (422, {"error": "SAME_ORIG_DEST"})


async def test_pickup_in_past_422_on_create_and_patch(client, token):
    today = datetime.now(UTC).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    r = await _create(client, token, pickup_date=yesterday)
    assert (r.status_code, r.json()) == (422, {"error": "PICKUP_DATE_IN_PAST"})
    assert (
        await _create(client, token, pickup_date=today.isoformat())
    ).status_code == 201
    q = (await _create(client, token)).json()
    h = {**token(PRICER), "If-Match": "1"}
    r = await client.patch(
        f"/v1/quotes/{q['id']}", json={"pickup_date": yesterday}, headers=h
    )
    assert (r.status_code, r.json()) == (422, {"error": "PICKUP_DATE_IN_PAST"})
    g = await client.get(f"/v1/quotes/{q['id']}", headers=token(PRICER))
    assert g.json()["version"] == 1
