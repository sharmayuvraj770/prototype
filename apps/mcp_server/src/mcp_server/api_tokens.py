"""M2 shortcut; replaced by RFC 8693 token exchange in M3.

The MCP server calls the Public API only with a token for the same user (aud
`northstar-public-api`), minted here with the dev private key. No tenant-wide
credential exists.
"""
import threading
import time

from northstar_shared.tokens import mint_token

from mcp_server.identity import Identity

_REFRESH_MARGIN_S = 60


class ApiTokenProvider:
    def __init__(self, private_pem: bytes, *, issuer: str, audience: str, ttl_s: int = 300):
        if ttl_s <= _REFRESH_MARGIN_S:
            raise ValueError(f"ttl_s must exceed the {_REFRESH_MARGIN_S}s refresh margin")
        self._private_pem = private_pem
        self._issuer = issuer
        self._audience = audience
        self._ttl_s = ttl_s
        self._cache: dict[tuple[str, str, str], tuple[str, float]] = {}
        self._lock = threading.Lock()

    def token_for(self, identity: Identity) -> str:
        key = (identity.user_id, identity.tenant, identity.role)
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and now < hit[1] - _REFRESH_MARGIN_S:
                return hit[0]
            token = mint_token(
                self._private_pem, sub=identity.user_id, tenant=identity.tenant,
                role=identity.role, aud=self._audience, iss=self._issuer, ttl_s=self._ttl_s,
            )
            self._cache[key] = (token, now + self._ttl_s)
            return token
