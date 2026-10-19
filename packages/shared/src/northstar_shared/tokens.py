import time
from uuid import uuid4

import jwt
from pydantic import BaseModel

_REQUIRED = ["exp", "iat", "sub", "tenant", "role", "aud", "iss"]


class TokenError(Exception):
    pass


class Claims(BaseModel):
    sub: str
    tenant: str
    role: str
    aud: str
    iss: str
    exp: int
    iat: int
    jti: str
    scope: str = ""
    client_id: str | None = None


def mint_token(
    private_key_pem: bytes,
    *,
    sub: str,
    tenant: str,
    role: str,
    aud: str,
    iss: str,
    ttl_s: int = 900,
    scope: str = "",
    client_id: str | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub, "tenant": tenant, "role": role, "aud": aud, "iss": iss,
        "iat": now, "exp": now + ttl_s, "jti": uuid4().hex, "scope": scope,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def verify_token(token: str, public_key_pem: bytes, *, aud: str, iss: str) -> Claims:
    try:
        data = jwt.decode(
            token, public_key_pem, algorithms=["RS256"], audience=aud, issuer=iss,
            options={"require": _REQUIRED},
        )
        return Claims(**data)
    except Exception as e:  # any failure, incl. pydantic validation, is a TokenError
        raise TokenError(str(e)) from e
