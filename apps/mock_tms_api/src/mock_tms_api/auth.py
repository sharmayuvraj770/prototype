from fastapi import HTTPException, Request
from northstar_shared.tokens import TokenError, verify_token
from psycopg.rows import dict_row
from pydantic import BaseModel

ROLES = {"ops_rep", "carrier_sales", "pricing", "ops_manager", "broker_admin"}


class Principal(BaseModel):
    user_id: str
    tenant: str
    role: str
    client_id: str | None = None


def _unauthenticated() -> HTTPException:
    return HTTPException(
        401, {"error": "UNAUTHENTICATED"}, headers={"WWW-Authenticate": "Bearer"}
    )


async def current_principal(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthenticated()
    settings = request.app.state.settings
    try:
        claims = verify_token(
            token.strip(),
            request.app.state.public_key,
            aud=settings.api_audience,
            iss=settings.jwt_issuer,
        )
    except TokenError:
        raise _unauthenticated() from None
    if claims.role not in ROLES:
        raise _unauthenticated()
    # The token is a claim, tms.users is the truth: the user must exist with exactly this
    # tenant and role. Own short-lived connection so the route's transaction stays top-level.
    async with request.app.state.pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "select tenant_id, role from tms.users where id = %(sub)s",
            {"sub": claims.sub},
        )
        row = await cur.fetchone()
    if row is None or (row["tenant_id"], row["role"]) != (claims.tenant, claims.role):
        raise _unauthenticated()
    return Principal(
        user_id=claims.sub,
        tenant=claims.tenant,
        role=claims.role,
        client_id=claims.client_id,
    )
