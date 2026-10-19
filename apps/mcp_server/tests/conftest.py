"""MCP server test harness: real mock API + real MCP app, each on uvicorn in a thread.

Session-scoped `stack` seeds a fresh database, starts both servers on free ports and
tears them down cleanly (uvicorn `should_exit`) at the end of the session.
"""
import asyncio
import socket
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx2  # the HTTP client the MCP SDK's transport is built on
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_server.server import create_app
from mcp_server.settings import Settings
from mock_tms_api.main import create_app as create_api_app
from mock_tms_api.settings import Settings as ApiSettings
from northstar_shared.tokens import mint_token
from northstar_shared.users import tenant_role_for
from seedgen.testing import seeded_database

if sys.platform == "win32":  # psycopg async needs a selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ISS = "http://mock-idp:9000"
MCP_AUD = "northstar-mcp"
TEST_REDIS_URL = "redis://localhost:56379/15"


@dataclass(frozen=True)
class StackInfo:
    base_url: str  # MCP server root, e.g. http://127.0.0.1:51234
    mcp_url: str  # base_url + "/mcp"
    api_url: str
    db_url: str
    private_pem: bytes
    log_dir: Path
    settings: Settings


class _ThreadedServer:
    """uvicorn on a pre-bound socket, in a daemon thread with a selector event loop."""

    def __init__(self, app, name: str):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, name=f"uvicorn-{name}", daemon=True)
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            asyncio.run(
                self.server.serve(sockets=[self.sock]), loop_factory=asyncio.SelectorEventLoop
            )
        except BaseException as e:  # noqa: BLE001 - surfaced by start()
            self.error = e

    def start(self, timeout: float = 30.0) -> None:
        self.thread.start()
        waited = 0.0
        while not self.server.started:
            if self.error or not self.thread.is_alive():
                raise RuntimeError(f"{self.thread.name} failed to start") from self.error
            if waited > timeout:
                raise TimeoutError(f"{self.thread.name} did not start in {timeout}s")
            self.thread.join(0.05)
            waited += 0.05

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)
        self.sock.close()


@pytest.fixture(scope="session")
def stack(pg_admin_url, rsa_keys, tmp_path_factory) -> Iterator[StackInfo]:
    priv, pub = rsa_keys
    key_dir = tmp_path_factory.mktemp("keys")
    (key_dir / "jwt_private.pem").write_bytes(priv)
    (key_dir / "jwt_public.pem").write_bytes(pub)
    log_dir = tmp_path_factory.mktemp("logs")

    with seeded_database(pg_admin_url) as db_url:
        api = _ThreadedServer(
            create_api_app(
                ApiSettings(
                    database_url=db_url,
                    jwt_public_key_path=key_dir / "jwt_public.pem",
                    jwt_issuer=ISS,
                    api_audience="northstar-public-api",
                )
            ),
            "api",
        )
        api.start()
        try:
            settings = Settings(
                api_base_url=api.url,
                redis_url=TEST_REDIS_URL,
                database_url=db_url,
                jwt_public_key_path=key_dir / "jwt_public.pem",
                jwt_private_key_path=key_dir / "jwt_private.pem",
                audit_key_path=key_dir / "audit.key",
                log_dir=log_dir,
            )
            mcp = _ThreadedServer(create_app(settings), "mcp")
            mcp.start()
            try:
                yield StackInfo(
                    base_url=mcp.url,
                    mcp_url=f"{mcp.url}/mcp",
                    api_url=api.url,
                    db_url=db_url,
                    private_pem=priv,
                    log_dir=log_dir,
                    settings=settings,
                )
            finally:
                mcp.stop()
        finally:
            api.stop()


@pytest.fixture(scope="session")
def mcp_token(stack) -> Callable[..., str]:
    """`mcp_token("a.ops")` -> an MCP-audience bearer token for a seeded user."""

    def _mint(user_id: str, *, aud: str = MCP_AUD, ttl_s: int = 900,
              client_id: str | None = "pytest") -> str:
        tenant, role = tenant_role_for(user_id)
        return mint_token(
            stack.private_pem, sub=user_id, tenant=tenant, role=role, aud=aud, iss=ISS,
            ttl_s=ttl_s, client_id=client_id,
        )

    return _mint


@pytest.fixture(scope="session")
def mcp_client(stack, mcp_token) -> Callable[..., AbstractAsyncContextManager[Client]]:
    """`async with mcp_client("a.ops") as c:` -> an SDK `Client` authed as that user.

    SDK 2.3 `Client` has no headers/auth argument: the bearer header goes on an
    `httpx2.AsyncClient` passed to `streamable_http_client(url, http_client=...)`,
    and that transport is handed to `Client`.
    """

    @asynccontextmanager
    async def _client(user_id: str, **token_kw) -> AsyncIterator[Client]:
        headers = {"Authorization": f"Bearer {mcp_token(user_id, **token_kw)}"}
        async with httpx2.AsyncClient(
            headers=headers, timeout=httpx2.Timeout(30.0, read=60.0)
        ) as http:
            transport = streamable_http_client(stack.mcp_url, http_client=http)
            async with Client(transport) as client:
                yield client

    return _client
