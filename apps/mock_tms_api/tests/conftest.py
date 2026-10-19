import asyncio
import sys
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from mock_tms_api.main import create_app
from mock_tms_api.settings import Settings
from northstar_shared.tokens import mint_token
from northstar_shared.users import tenant_role_for
from seedgen.testing import seeded_database as _seeded_database

if sys.platform == "win32":  # psycopg async needs a selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DEMO_NOW = datetime.now(UTC).replace(second=0, microsecond=0)
ISS = "http://mock-idp:9000"
AUD = "northstar-public-api"


@contextmanager
def seeded_database(pg_admin_url):
    """Fresh temp database, migrated and seeded at DEMO_NOW. Reused by mutating test modules."""
    with _seeded_database(pg_admin_url, DEMO_NOW) as url:
        yield url


@asynccontextmanager
async def api_client(db_url, public_key_pem, key_dir, wrap=None):
    key_path = key_dir / "jwt_public.pem"
    key_path.write_bytes(public_key_pem)
    app = create_app(
        Settings(
            database_url=db_url,
            jwt_public_key_path=key_path,
            jwt_issuer=ISS,
            api_audience=AUD,
        )
    )
    asgi = wrap(app) if wrap else app  # optional ASGI middleware for probes
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi), base_url="http://test"
        ) as c,
    ):
        yield c


@pytest.fixture(scope="session")
def api_db(pg_admin_url):
    with seeded_database(pg_admin_url) as url:
        yield url


@pytest_asyncio.fixture
async def client(api_db, rsa_keys, tmp_path):
    async with api_client(api_db, rsa_keys[1], tmp_path) as c:
        yield c


@pytest.fixture
def token(rsa_keys):
    def _token(user_id: str, *, aud: str = AUD, ttl_s: int = 900) -> dict[str, str]:
        tenant, role = tenant_role_for(user_id)
        t = mint_token(
            rsa_keys[0],
            sub=user_id,
            tenant=tenant,
            role=role,
            aud=aud,
            iss=ISS,
            ttl_s=ttl_s,
        )
        return {"Authorization": f"Bearer {t}"}

    return _token
