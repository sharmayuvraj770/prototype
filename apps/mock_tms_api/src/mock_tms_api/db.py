from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from psycopg import AsyncConnection
from psycopg.rows import dict_row


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.pool.connection() as conn:
        conn.row_factory = dict_row
        yield conn


# RULE: every route takes its connection through `Conn`. scope="function" makes the pool
# context (which commits on clean exit) close BEFORE the response is sent; the FastAPI
# default ("request") would commit after the client already has the response.
# Write handlers additionally wrap ALL their statements in one top-level
# `async with conn.transaction():` opened before any other query on the connection, so the
# commit point is explicit and never depends on dependency teardown order.
Conn = Annotated[AsyncConnection, Depends(get_conn, scope="function")]
