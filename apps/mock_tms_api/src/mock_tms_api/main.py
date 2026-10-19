from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from psycopg_pool import AsyncConnectionPool

from mock_tms_api.auth import Principal, current_principal
from mock_tms_api.db import Conn
from mock_tms_api.errors import install_error_handlers
from mock_tms_api.routers import carriers, loads, market, quotes
from mock_tms_api.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = AsyncConnectionPool(
            settings.database_url, min_size=1, max_size=10, open=False
        )
        await pool.open()
        app.state.pool = pool
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="Northstar TMS Public API (mock)", lifespan=lifespan)
    app.state.settings = settings
    app.state.public_key = settings.jwt_public_key_path.read_bytes()
    install_error_handlers(app)

    @app.get("/health")
    async def health(
        conn: Conn,
    ) -> dict[str, str]:
        await conn.execute("select 1")
        return {"status": "ok"}

    @app.get("/v1/whoami")
    async def whoami(
        p: Annotated[Principal, Depends(current_principal)],
    ) -> dict[str, str]:
        return {"user_id": p.user_id, "tenant": p.tenant, "role": p.role}

    app.include_router(loads.router)
    app.include_router(carriers.router)
    app.include_router(market.router)
    app.include_router(quotes.router)

    return app
