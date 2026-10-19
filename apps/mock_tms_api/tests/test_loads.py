import psycopg


async def test_get_own_load(client, token):
    r = await client.get("/v1/loads/4521", headers=token("a.ops"))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 4521
    assert "customer_rate" not in body
    assert "carrier_rate" not in body
    assert "margin" not in body


async def test_manager_sees_financials(client, token):
    r = await client.get("/v1/loads/4521", headers=token("a.manager"))
    assert r.status_code == 200
    body = r.json()
    assert "customer_rate" in body and "margin" in body
    assert "as_of" in body


async def test_other_tenant_load_is_indistinguishable_from_missing(
    client, token, api_db
):
    with psycopg.connect(api_db) as conn:
        assert (
            conn.execute("select 1 from tms.loads where id = 9999").fetchone() is None
        )
    r1 = await client.get("/v1/loads/5150", headers=token("a.manager"))
    r2 = await client.get("/v1/loads/9999", headers=token("a.manager"))
    assert r1.status_code == r2.status_code == 404
    assert r1.content == r2.content
    r3 = await client.get("/v1/loads/5150/events", headers=token("a.manager"))
    r4 = await client.get("/v1/loads/9999/events", headers=token("a.manager"))
    assert r3.status_code == r4.status_code == 404
    assert r3.content == r4.content


async def test_ops_rep_scope_own_only(client, token, api_db):
    ops = await client.get("/v1/loads?limit=100", headers=token("a.ops"))
    mgr = await client.get("/v1/loads?limit=100", headers=token("a.manager"))
    assert ops.status_code == mgr.status_code == 200
    ids = [i["id"] for i in ops.json()["items"]]
    assert ids
    with psycopg.connect(api_db) as conn:
        rows = conn.execute(
            "select id from tms.loads where id = any(%s) and assigned_rep <> 'a.ops'",
            (ids,),
        ).fetchall()
    assert rows == []
    assert len(mgr.json()["items"]) > len(ops.json()["items"])
    # another rep's load is invisible to ops_rep
    with psycopg.connect(api_db) as conn:
        other = conn.execute(
            "select id from tms.loads where tenant_id='broker_a' and assigned_rep <> 'a.ops' limit 1"
        ).fetchone()[0]
    assert (
        await client.get(f"/v1/loads/{other}", headers=token("a.ops"))
    ).status_code == 404


async def test_pricing_cannot_read_loads(client, token):
    r = await client.get("/v1/loads/4521", headers=token("a.pricing"))
    assert r.status_code == 403
    assert r.json() == {"error": "FORBIDDEN", "required": "loads:read"}


async def test_late_filter(client, token):
    r = await client.get("/v1/loads?late=true&limit=100", headers=token("a.manager"))
    assert r.status_code == 200
    items = r.json()["items"]
    assert 6001 in [i["id"] for i in items]
    assert all(i["late"] is True for i in items)


async def test_events_newest_first_with_source(client, token):
    r = await client.get("/v1/loads/4521/events", headers=token("a.ops"))
    assert r.status_code == 200
    ev = r.json()["items"]
    assert ev[0]["type"] == "ping"
    assert ev[0]["city"] == "Joliet"
    assert ev[0]["source_integration"].startswith("eld:")
    ats = [e["at"] for e in ev]
    assert ats == sorted(ats, reverse=True)


async def test_list_rejects_bad_params(client, token):
    h = token("a.manager")
    assert (await client.get("/v1/loads?limit=100000", headers=h)).status_code == 422
    assert (await client.get("/v1/loads?status=bogus", headers=h)).status_code == 422
    assert (await client.get("/v1/loads/abc", headers=h)).status_code == 422
    assert (await client.get("/v1/loads/999", headers=h)).status_code == 422
    assert (await client.get("/v1/loads?cursor=!!!", headers=h)).status_code == 422
    r = await client.get(
        "/v1/loads?delivery_from=2026-09-01T00:00:00Z&delivery_to=2026-10-31T00:00:00Z",
        headers=h,
    )
    assert r.status_code == 422


async def test_pagination(client, token):
    h = token("a.manager")
    p1 = (await client.get("/v1/loads?limit=20", headers=h)).json()
    assert len(p1["items"]) == 20 and p1["next_cursor"] and p1["as_of"]
    p2 = (
        await client.get(f"/v1/loads?limit=20&cursor={p1['next_cursor']}", headers=h)
    ).json()
    ids1 = {i["id"] for i in p1["items"]}
    ids2 = {i["id"] for i in p2["items"]}
    assert ids2 and not ids1 & ids2
    assert min(ids2) > max(ids1)
    assert p2["as_of"]


async def test_naive_aware_datetime_mix_is_422(client, token):
    h = token("a.manager")
    r = await client.get(
        "/v1/loads?delivery_from=2026-10-01T00:00:00&delivery_to=2026-10-05T00:00:00Z",
        headers=h,
    )
    assert r.status_code == 422
    r = await client.get("/v1/loads?delivery_from=2026-10-01T00:00:00", headers=h)
    assert r.status_code == 422


async def test_inverted_date_range_is_422(client, token):
    r = await client.get(
        "/v1/loads?delivery_from=2026-10-05T00:00:00Z&delivery_to=2026-10-01T00:00:00Z",
        headers=token("a.manager"),
    )
    assert r.status_code == 422
    assert r.json() == {"error": "INVALID_DATE_RANGE"}


async def test_default_limit_is_20(client, token, api_db):
    h = token("a.manager")
    r = await client.get("/v1/loads", headers=h)
    assert len(r.json()["items"]) == 20 and r.json()["next_cursor"]
    with psycopg.connect(api_db) as conn:
        n = conn.execute(
            "select count(*) from tms.load_events where load_id = 4521"
        ).fetchone()[0]
    ev = await client.get("/v1/loads/4521/events", headers=h)
    assert len(ev.json()["items"]) == min(n, 20)


async def test_events_keyset_paging(client, token, api_db):
    with psycopg.connect(api_db) as conn:
        row = conn.execute(
            "select e.load_id, count(*) from tms.load_events e join tms.loads l "
            "on l.id = e.load_id where l.tenant_id = 'broker_a' "
            "group by e.load_id having count(*) > 20 order by count(*) desc limit 1"
        ).fetchone()
    assert row is not None, "seed should contain a broker_a load with > 20 events"
    load_id, n = row
    h = token("a.manager")
    p1 = (await client.get(f"/v1/loads/{load_id}/events", headers=h)).json()
    assert len(p1["items"]) == 20 and p1["next_cursor"]
    seen, cursor = [e["id"] for e in p1["items"]], p1["next_cursor"]
    while cursor:
        page = (
            await client.get(f"/v1/loads/{load_id}/events?cursor={cursor}", headers=h)
        ).json()
        seen += [e["id"] for e in page["items"]]
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) == n
    bad = await client.get(f"/v1/loads/{load_id}/events?cursor=!!!", headers=h)
    assert bad.status_code == 422 and bad.json() == {"error": "INVALID_CURSOR"}
