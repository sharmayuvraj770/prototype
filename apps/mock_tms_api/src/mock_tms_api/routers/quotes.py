import secrets
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation

from mock_tms_api.auth import Principal
from mock_tms_api.db import Conn
from mock_tms_api.errors import conflict, not_found
from mock_tms_api.geo import estimate_miles
from mock_tms_api.pagination import decode_cursor, encode_cursor
from mock_tms_api.rbac import Perm, require
from mock_tms_api.schemas import Page, Quote, QuoteCreate, QuoteStatus, QuoteUpdate

router = APIRouter(prefix="/v1/quotes", tags=["quotes"])

Reader = Annotated[Principal, Depends(require(Perm.QUOTES_READ))]
Writer = Annotated[Principal, Depends(require(Perm.QUOTES_WRITE))]
QuoteId = Annotated[str, Path(pattern=r"^Q-[0-9A-F]{6}$")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]

COLS = (
    "id, tenant_id, created_by, customer_name, orig_metro, dest_metro, equipment, "
    "pickup_date, rate_per_mile, miles, total, notes, status, version, created_at, updated_at"
)


def _check_pickup(pickup: date) -> None:
    # UTC calendar date: matches Postgres current_date (the postgres image defaults to UTC).
    if pickup < datetime.now(UTC).date():
        raise HTTPException(422, {"error": "PICKUP_DATE_IN_PAST"})


def _parse_version(if_match: str | None) -> int:
    if if_match is None:
        raise HTTPException(428, {"error": "PRECONDITION_REQUIRED"})
    try:
        return int(if_match.strip().strip('"'))
    except ValueError:
        raise HTTPException(412, {"error": "VERSION_CONFLICT"}) from None


async def _exists(conn: AsyncConnection, qid: str, tenant: str) -> bool:
    cur = await conn.execute(
        "select 1 from tms.quotes where id = %s and tenant_id = %s", (qid, tenant)
    )
    return await cur.fetchone() is not None


@router.post(
    "",
    operation_id="create_quote",
    summary="Create a draft customer quote for a lane (miles and total computed server-side).",
    response_model=Quote,
    status_code=201,
)
async def create_quote(body: QuoteCreate, p: Writer, conn: Conn):
    async with conn.transaction():  # top-level: see the commit rule in db.py
        return await _create_quote(body, p, conn)


async def _create_quote(body: QuoteCreate, p: Principal, conn: AsyncConnection):
    if body.orig_metro == body.dest_metro:
        raise HTTPException(422, {"error": "SAME_ORIG_DEST"})
    _check_pickup(body.pickup_date)
    try:
        miles, _ = await estimate_miles(
            conn, body.orig_metro, body.dest_metro, body.equipment
        )
    except LookupError:
        raise HTTPException(422, {"error": "UNKNOWN_METRO"}) from None
    total = body.rate_per_mile * miles
    now = datetime.now(UTC)
    for _ in range(5):
        qid = "Q-" + secrets.token_hex(3).upper()
        try:
            async with conn.transaction():  # savepoint: an id collision retries cleanly
                cur = await conn.execute(
                    f"insert into tms.quotes ({COLS}) values (%s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, 'draft', 1, %s, %s) returning " + COLS,
                    (
                        qid,
                        p.tenant,
                        p.user_id,
                        body.customer_name,
                        body.orig_metro,
                        body.dest_metro,
                        body.equipment,
                        body.pickup_date,
                        body.rate_per_mile,
                        miles,
                        total,
                        body.notes,
                        now,
                        now,
                    ),
                )
                return await cur.fetchone()
        except UniqueViolation:
            continue  # id collision: draw another
    raise HTTPException(500, {"error": "ID_GENERATION_FAILED"})


@router.get(
    "",
    operation_id="list_quotes",
    summary="List the tenant's quotes, newest first, optionally filtered by status.",
    response_model=Page[Quote],
)
async def list_quotes(
    p: Reader,
    conn: Conn,
    status: QuoteStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
):
    clauses = ["tenant_id = %(tenant)s"]
    params: dict = {"tenant": p.tenant, "lim": limit + 1}
    if status:
        clauses.append("status = %(status)s")
        params["status"] = status
    if cursor:
        params["c_at"], params["c_id"] = decode_cursor(
            cursor, datetime.fromisoformat, str
        )
        clauses.append("(created_at, id) < (%(c_at)s, %(c_id)s)")
    cur = await conn.execute(
        f"select {COLS} from tms.quotes where {' and '.join(clauses)} "
        "order by created_at desc, id desc limit %(lim)s",
        params,
    )
    rows = await cur.fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": rows,
        "next_cursor": encode_cursor(rows[-1]["created_at"], rows[-1]["id"])
        if has_more
        else None,
        "as_of": datetime.now(UTC),
    }


@router.get(
    "/{quote_id}",
    operation_id="get_quote",
    summary="Get one quote by id (404 if missing or not in the caller's tenant).",
    response_model=Quote,
)
async def get_quote(quote_id: QuoteId, p: Reader, conn: Conn):
    cur = await conn.execute(
        f"select {COLS} from tms.quotes where id = %s and tenant_id = %s",
        (quote_id, p.tenant),
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    return row


@router.patch(
    "/{quote_id}",
    operation_id="update_quote",
    summary="Update a quote's rate, pickup date, notes or revert to draft; requires If-Match version.",
    response_model=Quote,
)
async def update_quote(
    quote_id: QuoteId,
    body: QuoteUpdate,
    p: Writer,
    conn: Conn,
    if_match: IfMatch = None,
):
    version = _parse_version(if_match)
    if body.pickup_date is not None:
        _check_pickup(body.pickup_date)
    sets = ["version = version + 1", "updated_at = %(now)s"]
    params: dict = {
        "now": datetime.now(UTC),
        "id": quote_id,
        "tenant": p.tenant,
        "ver": version,
    }
    for (
        field
    ) in body.model_fields_set:  # closed set of model fields, safe to interpolate
        params[field] = getattr(body, field)
        sets.append(f"{field} = %({field})s")
    if "rate_per_mile" in body.model_fields_set:
        sets.append("total = miles * %(rate_per_mile)s")
    async with conn.transaction():  # top-level: see the commit rule in db.py
        cur = await conn.execute(
            f"update tms.quotes set {', '.join(sets)} where id = %(id)s "
            f"and tenant_id = %(tenant)s and version = %(ver)s returning {COLS}",
            params,
        )
        row = await cur.fetchone()
        if row is None:
            if not await _exists(conn, quote_id, p.tenant):
                raise not_found()
            raise HTTPException(412, {"error": "VERSION_CONFLICT"})
        return row


@router.post(
    "/{quote_id}/withdraw",
    operation_id="withdraw_quote",
    summary="Withdraw a draft quote (reversible via update_quote status=draft).",
    response_model=Quote,
)
async def withdraw_quote(quote_id: QuoteId, p: Writer, conn: Conn):
    async with conn.transaction():  # top-level: see the commit rule in db.py
        cur = await conn.execute(
            "update tms.quotes set status = 'withdrawn', version = version + 1, "
            f"updated_at = %s where id = %s and tenant_id = %s and status = 'draft' "
            f"returning {COLS}",
            (datetime.now(UTC), quote_id, p.tenant),
        )
        row = await cur.fetchone()
        if row is not None:
            return row
        if not await _exists(conn, quote_id, p.tenant):
            raise not_found()
        raise conflict("ALREADY_WITHDRAWN")
