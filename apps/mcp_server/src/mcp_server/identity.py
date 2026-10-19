"""Who is calling: verified from the bearer token only, never from tool arguments."""
from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from northstar_shared.tokens import TokenError, verify_token
from northstar_shared.users import ROLE_SUFFIX

ROLES = frozenset(ROLE_SUFFIX.values())


@dataclass(frozen=True)
class Identity:
    user_id: str
    tenant: str
    role: str
    client_id: str | None


class JwtTokenVerifier(TokenVerifier):
    """RS256 JWT check (signature, exp, iss, aud = this MCP server) for the SDK bearer gate."""

    def __init__(self, public_pem: bytes, *, audience: str, issuer: str):
        self._public_pem = public_pem
        self._audience = audience
        self._issuer = issuer

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = verify_token(token, self._public_pem, aud=self._audience, iss=self._issuer)
        except TokenError:
            return None
        if claims.role not in ROLES:
            return None
        return AccessToken(
            token=token,
            client_id=claims.client_id or "dev",
            scopes=claims.scope.split(),
            expires_at=claims.exp,
            subject=claims.sub,
            claims={"tenant": claims.tenant, "role": claims.role, "iss": claims.iss,
                    "client_id": claims.client_id},
        )


def current_identity() -> Identity | None:
    """The verified caller of the current request, or None outside an authed request."""
    tok = get_access_token()
    if tok is None or tok.subject is None or not tok.claims:
        return None
    tenant, role = tok.claims.get("tenant"), tok.claims.get("role")
    if not tenant or role not in ROLES:
        return None
    return Identity(user_id=tok.subject, tenant=tenant, role=role,
                    client_id=tok.claims.get("client_id"))
