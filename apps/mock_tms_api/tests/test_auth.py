import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from mock_tms_api.errors import conflict, forbidden, install_error_handlers
from northstar_shared.tokens import mint_token


def _assert_401(r):
    assert r.status_code == 401
    assert r.json() == {"error": "UNAUTHENTICATED"}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_health_no_auth(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_missing_token_401(client):
    r = await client.get("/v1/whoami")
    assert r.status_code == 401
    assert r.json() == {"error": "UNAUTHENTICATED"}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_garbage_token_401(client):
    r = await client.get("/v1/whoami", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401
    assert r.json() == {"error": "UNAUTHENTICATED"}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_wrong_audience_rejected(client, token):
    r = await client.get("/v1/whoami", headers=token("a.ops", aud="northstar-mcp"))
    assert r.status_code == 401
    assert r.json() == {"error": "UNAUTHENTICATED"}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_expired_401(client, token):
    r = await client.get("/v1/whoami", headers=token("a.ops", ttl_s=-60))
    assert r.status_code == 401
    assert r.json() == {"error": "UNAUTHENTICATED"}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_valid_token_reaches_route(client, token):
    r = await client.get("/v1/whoami", headers=token("a.ops"))
    assert r.status_code == 200
    assert r.json() == {"user_id": "a.ops", "tenant": "broker_a", "role": "ops_rep"}


async def test_unknown_role_401(client, rsa_keys):
    t = mint_token(
        rsa_keys[0],
        sub="a.ops",
        tenant="broker_a",
        role="superuser",
        aud="northstar-public-api",
        iss="http://mock-idp:9000",
    )
    _assert_401(
        await client.get("/v1/whoami", headers={"Authorization": f"Bearer {t}"})
    )


async def test_wrong_issuer_401(client, rsa_keys):
    t = mint_token(
        rsa_keys[0],
        sub="a.ops",
        tenant="broker_a",
        role="ops_rep",
        aud="northstar-public-api",
        iss="http://evil",
    )
    _assert_401(
        await client.get("/v1/whoami", headers={"Authorization": f"Bearer {t}"})
    )


async def test_wrong_signing_key_401(client):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    t = mint_token(
        pem,
        sub="a.ops",
        tenant="broker_a",
        role="ops_rep",
        aud="northstar-public-api",
        iss="http://mock-idp:9000",
    )
    _assert_401(
        await client.get("/v1/whoami", headers={"Authorization": f"Bearer {t}"})
    )


async def test_error_helpers_bodies():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/f")
    async def f():
        raise forbidden("loads:read")

    @app.get("/c")
    async def c():
        raise conflict("ALREADY_BOOKED")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as cl:
        r = await cl.get("/f")
        assert (r.status_code, r.json()) == (
            403,
            {"error": "FORBIDDEN", "required": "loads:read"},
        )
        r = await cl.get("/c")
        assert (r.status_code, r.json()) == (409, {"error": "ALREADY_BOOKED"})
        r = await cl.get("/nope")
        assert (r.status_code, r.json()) == (404, {"error": "NOT_FOUND"})
        r = await cl.post("/c")
        assert (r.status_code, r.json()) == (405, {"error": "METHOD_NOT_ALLOWED"})


def _bearer(rsa_keys, *, sub, tenant, role):
    t = mint_token(
        rsa_keys[0],
        sub=sub,
        tenant=tenant,
        role=role,
        aud="northstar-public-api",
        iss="http://mock-idp:9000",
    )
    return {"Authorization": f"Bearer {t}"}


async def test_unknown_user_401(client, rsa_keys):
    h = _bearer(rsa_keys, sub="a.ghost", tenant="broker_a", role="ops_rep")
    _assert_401(await client.get("/v1/whoami", headers=h))


async def test_token_tenant_must_match_user_401(client, rsa_keys):
    h = _bearer(rsa_keys, sub="b.pricing", tenant="broker_a", role="pricing")
    _assert_401(await client.get("/v1/whoami", headers=h))


async def test_token_role_must_match_user_401(client, rsa_keys):
    h = _bearer(rsa_keys, sub="a.ops", tenant="broker_a", role="ops_manager")
    _assert_401(await client.get("/v1/whoami", headers=h))
    _assert_401(await client.get("/v1/loads/4521", headers=h))


async def test_routing_errors_use_codes(client, token):
    r = await client.get("/v1/nope", headers=token("a.ops"))
    assert (r.status_code, r.json()) == (404, {"error": "NOT_FOUND"})
    r = await client.delete("/v1/whoami", headers=token("a.ops"))
    assert (r.status_code, r.json()) == (405, {"error": "METHOD_NOT_ALLOWED"})


async def test_validation_error_shape(client, token):
    r = await client.get("/v1/loads?limit=100000&status=bogus", headers=token("a.ops"))
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "VALIDATION_FAILED"
    assert set(body) == {"error", "fields"}
    locs = [tuple(f["loc"]) for f in body["fields"]]
    assert ("query", "limit") in locs and ("query", "status") in locs
    assert all(set(f) == {"loc", "msg"} and f["msg"] for f in body["fields"])
