import psycopg


async def test_profile_public(client, token, api_db):
    with psycopg.connect(api_db) as conn:
        dot, name = conn.execute(
            "select dot_number, legal_name from tms.carriers where not is_fictional order by dot_number limit 1"
        ).fetchone()
    r = await client.get(f"/v1/carriers/{dot}", headers=token("a.ops"))
    assert r.status_code == 200
    body = r.json()
    assert body["legal_name"] == name
    assert "as_of" in body


async def test_compliance_revoked_for_unsatisfactory(client, token, api_db):
    with psycopg.connect(api_db) as conn:
        row = conn.execute(
            "select c.dot_number from tms.carriers c join tms.carrier_compliance cc using (dot_number) "
            "where c.safety_rating = 'unsatisfactory' limit 1"
        ).fetchone()
    assert row is not None
    r = await client.get(f"/v1/carriers/{row[0]}/compliance", headers=token("a.sales"))
    assert r.status_code == 200
    body = r.json()
    assert body["authority_status"] == "revoked"
    assert isinstance(body["insurance_valid"], bool)
    assert "as_of" in body


async def test_scorecard_notes_are_tenant_scoped(client, token, api_db):
    with psycopg.connect(api_db, autocommit=True) as conn:
        dot, old = conn.execute(
            "select a.dot_number, b.notes from tms.tenant_carriers a join tms.tenant_carriers b "
            "on a.dot_number = b.dot_number and b.tenant_id = 'broker_b' "
            "where a.tenant_id = 'broker_a' order by a.dot_number limit 1"
        ).fetchone()
        conn.execute(
            "update tms.tenant_carriers set notes = 'B-ONLY-SECRET' "
            "where tenant_id = 'broker_b' and dot_number = %s",
            (dot,),
        )
        try:
            r = await client.get(
                f"/v1/carriers/{dot}/scorecard", headers=token("a.manager")
            )
            assert r.status_code == 200
            assert "B-ONLY-SECRET" not in (r.json()["notes"] or "")
            rb = await client.get(
                f"/v1/carriers/{dot}/scorecard", headers=token("b.manager")
            )
            assert rb.json()["notes"] == "B-ONLY-SECRET"
        finally:
            conn.execute(
                "update tms.tenant_carriers set notes = %s "
                "where tenant_id = 'broker_b' and dot_number = %s",
                (old, dot),
            )


async def test_scorecard_404_without_relationship(client, token, api_db):
    with psycopg.connect(api_db) as conn:
        dot = conn.execute(
            "select dot_number from tms.carriers c where not exists (select 1 from tms.tenant_carriers t "
            "where t.dot_number = c.dot_number and t.tenant_id = 'broker_a') limit 1"
        ).fetchone()[0]
    r = await client.get(f"/v1/carriers/{dot}/scorecard", headers=token("a.manager"))
    assert r.status_code == 404
    assert r.json() == {"error": "NOT_FOUND"}


async def test_injection_note_returned_raw_by_api(client, token):
    r = await client.get("/v1/carriers/9900001/scorecard", headers=token("a.sales"))
    assert r.status_code == 200
    assert "Ignore all previous instructions" in r.json()["notes"]
