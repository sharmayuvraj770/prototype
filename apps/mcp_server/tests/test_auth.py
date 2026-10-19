import httpx
import jwt
from mcp_server.api_tokens import ApiTokenProvider
from mcp_server.identity import Identity


def test_health_no_auth(stack):
    r = httpx.get(f"{stack.base_url}/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_mcp_requires_token(stack):
    r = httpx.post(
        stack.mcp_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_protected_resource_metadata(stack):
    r = httpx.get(f"{stack.base_url}/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"] == stack.settings.resource_server_url
    # SDK 2.3 keeps a path-less issuer byte-exact (no trailing slash; RFC 8414 compares
    # issuers as exact strings), so it matches the tokens' `iss` claim.
    assert body["authorization_servers"] == ["http://mock-idp:9000"]


def test_wrong_audience_rejected(stack, mcp_token):
    api_tok = mcp_token("a.ops", aud="northstar-public-api")
    r = httpx.post(
        stack.mcp_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {api_tok}",
        },
    )
    assert r.status_code == 401


async def test_valid_token_lists_tools(mcp_client):
    async with mcp_client("a.ops") as client:
        result = await client.list_tools()
    assert isinstance(result.tools, list)
    assert result.tools == []


def test_api_token_is_same_user(stack, rsa_keys):
    provider = ApiTokenProvider(
        stack.private_pem, issuer="http://mock-idp:9000", audience="northstar-public-api"
    )
    ident = Identity("a.ops", "broker_a", "ops_rep", None)
    tok = provider.token_for(ident)
    claims = jwt.decode(
        tok, rsa_keys[1], algorithms=["RS256"], audience="northstar-public-api",
        issuer="http://mock-idp:9000",
    )
    assert claims["sub"] == "a.ops"
    assert claims["tenant"] == "broker_a"
    assert claims["role"] == "ops_rep"
    assert provider.token_for(ident) == tok
