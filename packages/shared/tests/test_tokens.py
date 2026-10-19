import time

import jwt
import pytest

from northstar_shared.tokens import TokenError, mint_token, verify_token

AUD = "northstar-public-api"
ISS = "http://idp"


def _mint(priv, **kw):
    args = dict(sub="a.ops", tenant="broker_a", role="ops_rep", aud=AUD, iss=ISS)
    args.update(kw)
    return mint_token(priv, **args)


def test_roundtrip(rsa_keys):
    priv, pub = rsa_keys
    c = verify_token(_mint(priv), pub, aud=AUD, iss=ISS)
    assert (c.sub, c.tenant, c.role) == ("a.ops", "broker_a", "ops_rep")


def test_wrong_audience(rsa_keys):
    priv, pub = rsa_keys
    with pytest.raises(TokenError):
        verify_token(_mint(priv, aud="northstar-mcp"), pub, aud=AUD, iss=ISS)


def test_expired(rsa_keys):
    priv, pub = rsa_keys
    with pytest.raises(TokenError):
        verify_token(_mint(priv, ttl_s=-1), pub, aud=AUD, iss=ISS)


def test_wrong_issuer(rsa_keys):
    priv, pub = rsa_keys
    with pytest.raises(TokenError):
        verify_token(_mint(priv, iss="http://evil"), pub, aud=AUD, iss=ISS)


def _payload():
    now = int(time.time())
    return dict(sub="a.ops", tenant="broker_a", role="ops_rep", aud=AUD, iss=ISS,
                iat=now, exp=now + 600)


def test_hs256_and_none_rejected(rsa_keys):
    _, pub = rsa_keys
    # PyJWT refuses PEM-as-HMAC-secret, so use an arbitrary secret; verifier must reject HS256 anyway
    hs256 = jwt.encode(_payload(), "x" * 32, algorithm="HS256")
    none = jwt.encode(_payload(), None, algorithm="none")
    for t in (hs256, none):
        with pytest.raises(TokenError):
            verify_token(t, pub, aud=AUD, iss=ISS)


def test_missing_tenant_claim(rsa_keys):
    priv, pub = rsa_keys
    p = _payload()
    del p["tenant"]
    t = jwt.encode(p, priv, algorithm="RS256")
    with pytest.raises(TokenError):
        verify_token(t, pub, aud=AUD, iss=ISS)
