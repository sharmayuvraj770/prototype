from datetime import UTC, datetime, timedelta

import psycopg


async def test_capacity_chi_dal_reefer(client, token):
    r = await client.get(
        "/v1/capacity?equipment=reefer&orig=CHI&dest=DAL", headers=token("a.sales")
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) >= 5
    assert all(i["source_integration"] == "loadboard:dat-mock" for i in body["items"])
    assert all(i["legal_name"] and i["safety_rating"] for i in body["items"])
    assert all(i["dest_metro"] in ("DAL", None) for i in body["items"])
    assert "as_of" in body


async def test_capacity_range_capped(client, token):
    today = datetime.now(UTC).date()
    r = await client.get(
        f"/v1/capacity?available_from={today}&available_to={today + timedelta(days=20)}",
        headers=token("a.sales"),
    )
    assert r.status_code == 422


async def test_capacity_inverted_range(client, token):
    today = datetime.now(UTC).date()
    r = await client.get(
        f"/v1/capacity?available_from={today + timedelta(days=3)}&available_to={today}",
        headers=token("a.sales"),
    )
    assert r.status_code == 422
    assert r.json() == {"error": "INVALID_DATE_RANGE"}


async def test_ops_rep_no_capacity(client, token):
    r = await client.get("/v1/capacity", headers=token("a.ops"))
    assert r.status_code == 403


async def test_lane_chi_dal(client, token):
    r = await client.get(
        "/v1/lanes/CHI/DAL?equipment=reefer", headers=token("a.pricing")
    )
    assert r.status_code == 200
    body = r.json()
    assert body["miles"] == 955
    assert body["derived"] is False
    assert body["lane"]["lane_id"] == "CHI-DAL-R"
    assert len(body["rate_history"]) == 8
    assert body["market"] == "Dallas"
    weeks = [h["week"] for h in body["rate_history"]]
    assert weeks == sorted(weeks, reverse=True)
    assert {"week", "rpm", "availability", "n_obs"} <= set(body["rate_history"][0])


async def test_lane_derived_pair(client, token, api_db):
    with psycopg.connect(api_db) as conn:
        pair = conn.execute(
            "select a.code, b.code from tms.metros a, tms.metros b where a.code <> b.code "
            "and not exists (select 1 from tms.lanes l where l.orig = a.code and l.dest = b.code) "
            "order by 1, 2 limit 1"
        ).fetchone()
    assert pair
    r = await client.get(
        f"/v1/lanes/{pair[0]}/{pair[1]}?equipment=dry_van", headers=token("a.pricing")
    )
    assert r.status_code == 200
    body = r.json()
    assert body["derived"] is True
    assert body["lane"] is None
    assert body["miles"] > 0


async def test_lane_tenant_history(client, token):
    r = await client.get(
        "/v1/lanes/CHI/DAL?equipment=reefer", headers=token("a.pricing")
    )
    th = r.json()["tenant_history"]
    assert "delivered_count_90d" in th
    assert "avg_carrier_rate_90d" in th
    r2 = await client.get("/v1/lanes/CHI/DAL?equipment=reefer", headers=token("a.ops"))
    assert r2.status_code == 403
    assert r2.json()["required"] == "rates:read"


async def test_lane_unknown_metro_and_bad_code(client, token):
    h = token("a.pricing")
    assert (
        await client.get("/v1/lanes/ZZZ/DAL?equipment=reefer", headers=h)
    ).status_code == 404
    assert (
        await client.get("/v1/lanes/chi/DAL?equipment=reefer", headers=h)
    ).status_code == 422
    assert (await client.get("/v1/lanes/CHI/DAL", headers=h)).status_code == 422


async def test_rates_index(client, token):
    h = token("a.pricing")
    r = await client.get("/v1/rates/index?weeks=4", headers=h)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 4
    assert all(i["market"] == "NATIONAL" for i in items)
    assert [i["week"] for i in items] == sorted(
        (i["week"] for i in items), reverse=True
    )
    r = await client.get("/v1/rates/index?market=Dallas&weeks=26", headers=h)
    assert len(r.json()["items"]) == 26
    assert (await client.get("/v1/rates/index?weeks=27", headers=h)).status_code == 422
    assert (
        await client.get("/v1/rates/index", headers=token("a.ops"))
    ).status_code == 403


async def test_capacity_keyset_paging(client, token):
    h = token("a.sales")
    base = "/v1/capacity?equipment=reefer&dest=DAL&limit=5"
    p1 = (await client.get(base, headers=h)).json()
    assert len(p1["items"]) == 5 and p1["next_cursor"]
    seen = [i["id"] for i in p1["items"]]
    cursor = p1["next_cursor"]
    while cursor:
        page = (await client.get(f"{base}&cursor={cursor}", headers=h)).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) > 5
    full = (
        await client.get("/v1/capacity?equipment=reefer&dest=DAL&limit=100", headers=h)
    ).json()
    assert seen == [i["id"] for i in full["items"]]
    bad = await client.get(f"{base}&cursor=!!!", headers=h)
    assert bad.status_code == 422 and bad.json() == {"error": "INVALID_CURSOR"}
