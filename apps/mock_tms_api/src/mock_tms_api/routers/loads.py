import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mock_tms_api.auth import Principal
from mock_tms_api.db import Conn
from mock_tms_api.errors import conflict, not_found
from mock_tms_api.pagination import decode_cursor, encode_cursor, non_negative_int
from mock_tms_api.rbac import Perm, load_scope_sql, redact_load, require
from mock_tms_api.schemas import Load, LoadEvent, LoadStatus, Page

router = APIRouter(prefix="/v1/loads", tags=["loads"])

LATE_SQL = (
    "(l.status in ('booked','dispatched','in_transit') "
    "and l.eta > l.delivery_at + interval '30 minutes')"
)
LOAD_COLS = f"""l.id, l.ref, l.status, l.lane_id, l.orig_metro, l.dest_metro, l.equipment,
  l.commodity, l.weight_lbs, l.pickup_at, l.delivery_at, l.eta, l.carrier_dot, l.assigned_rep,
  l.customer_name, l.customer_rate, l.carrier_rate, l.updated_at,
  coalesce({LATE_SQL}, false) as late,
  l.customer_rate - l.carrier_rate as margin"""

Reader = Annotated[Principal, Depends(require(Perm.LOADS_READ))]
LoadId = Annotated[int, Path(ge=1000, le=9999)]


@router.get("", response_model=Page[Load], response_model_exclude_unset=True)
async def list_loads(
    p: Reader,
    conn: Conn,
    status: LoadStatus | None = None,
    late: bool | None = None,
    orig: Annotated[str | None, Query(max_length=16)] = None,
    dest: Annotated[str | None, Query(max_length=16)] = None,
    delivery_from: AwareDatetime | None = None,
    delivery_to: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
):
    if delivery_from and delivery_to and delivery_from > delivery_to:
        raise HTTPException(422, {"error": "INVALID_DATE_RANGE"})
    if (
        delivery_from
        and delivery_to
        and delivery_to - delivery_from > timedelta(days=14)
    ):
        raise HTTPException(422, {"error": "DATE_RANGE_TOO_LARGE", "max_days": 14})
    where, params = load_scope_sql(p)
    clauses = [where]
    if status:
        clauses.append("l.status = %(status)s")
        params["status"] = status
    if late is True:
        clauses.append(LATE_SQL)
    elif late is False:
        clauses.append(f"not coalesce({LATE_SQL}, false)")
    if orig:
        clauses.append("l.orig_metro = %(orig)s")
        params["orig"] = orig
    if dest:
        clauses.append("l.dest_metro = %(dest)s")
        params["dest"] = dest
    if delivery_from:
        clauses.append("l.delivery_at >= %(dfrom)s")
        params["dfrom"] = delivery_from
    if delivery_to:
        clauses.append("l.delivery_at <= %(dto)s")
        params["dto"] = delivery_to
    if cursor:
        clauses.append("l.id > %(after)s")
        (params["after"],) = decode_cursor(cursor, non_negative_int)
    params["lim"] = limit + 1
    cur = await conn.execute(
        f"select {LOAD_COLS} from tms.loads l where {' and '.join(clauses)} "
        "order by l.id asc limit %(lim)s",
        params,
    )
    rows = await cur.fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [redact_load(r, p) for r in rows],
        "next_cursor": encode_cursor(rows[-1]["id"]) if has_more else None,
        "as_of": datetime.now(UTC),
    }


@router.get("/{load_id}", response_model=Load, response_model_exclude_unset=True)
async def get_load(load_id: LoadId, p: Reader, conn: Conn):
    where, params = load_scope_sql(p)
    params["id"] = load_id
    cur = await conn.execute(
        f"select {LOAD_COLS} from tms.loads l where {where} and l.id = %(id)s", params
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    row = redact_load(row, p)
    row["as_of"] = datetime.now(UTC)
    return row


@router.get("/{load_id}/events", response_model=Page[LoadEvent])
async def get_load_events(
    load_id: LoadId,
    p: Reader,
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
):
    # Scope is enforced by joining the parent load with the same scope clause.
    where, params = load_scope_sql(p)
    params.update(id=load_id, lim=limit + 1)
    clauses = [where, "l.id = %(id)s"]
    if cursor:  # newest first: keyset on (at desc, id desc)
        params["c_at"], params["c_id"] = decode_cursor(
            cursor, datetime.fromisoformat, non_negative_int
        )
        clauses.append("(e.at, e.id) < (%(c_at)s, %(c_id)s)")
    cur = await conn.execute(
        "select e.id, e.load_id, e.type, e.at, e.city, e.state, e.lat, e.lon, "
        "e.source_integration, e.detail "
        f"from tms.load_events e join tms.loads l on l.id = e.load_id and l.tenant_id = e.tenant_id "
        f"where {' and '.join(clauses)} order by e.at desc, e.id desc limit %(lim)s",
        params,
    )
    rows = await cur.fetchall()
    if not rows:
        # Distinguish "no events" from "no access" without leaking: re-check scope on parent.
        cur = await conn.execute(
            f"select 1 from tms.loads l where {where} and l.id = %(id)s", params
        )
        if await cur.fetchone() is None:
            raise not_found()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": rows,
        "next_cursor": encode_cursor(rows[-1]["at"], rows[-1]["id"])
        if has_more
        else None,
        "as_of": datetime.now(UTC),
    }


class BookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrier_dot: int = Field(ge=1)
    carrier_rate: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class _KeyRaceWon(Exception):
    """Raised inside the transaction to roll back a booking that lost an idempotency race."""

    def __init__(self, response: dict):
        self.response = response


def _request_hash(load_id: int, body: BookRequest) -> str:
    canon = json.dumps(
        {
            "load_id": load_id,
            "carrier_dot": body.carrier_dot,
            "carrier_rate": str(body.carrier_rate.normalize()),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode()).hexdigest()


def _key_reused() -> HTTPException:
    return conflict("IDEMPOTENCY_KEY_REUSED")


Booker = Annotated[Principal, Depends(require(Perm.LOADS_BOOK))]
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


@router.post("/{load_id}/book", response_model=Load, response_model_exclude_unset=True)
async def book_load(
    load_id: LoadId, body: BookRequest, idem_key: IdemKey, p: Booker, conn: Conn
):
    """Commit a booking. Idempotent per (tenant, Idempotency-Key); atomic and row-locked."""
    where, params = load_scope_sql(p)
    params["id"] = load_id
    rhash = _request_hash(load_id, body)
    try:
        return await _book(conn, p, load_id, body, idem_key, rhash, where, params)
    except _KeyRaceWon as won:  # transaction already rolled back
        return JSONResponse(won.response)


async def _book(conn, p, load_id, body, idem_key, rhash, where, params):
    # JSONResponse bypasses response_model; the payload is already a serialised Load.
    async with conn.transaction():
        # Lock the load row inside the caller's scope so concurrent bookings serialise.
        cur = await conn.execute(
            f"select {LOAD_COLS} from tms.loads l where {where} and l.id = %(id)s "
            "for update of l",
            params,
        )
        row = await cur.fetchone()
        if row is None:
            raise not_found()
        cur = await conn.execute(
            "select response, load_id, request_hash from tms.api_idempotency "
            "where tenant_id = %s and key = %s",
            (p.tenant, idem_key),
        )
        prior = await cur.fetchone()
        if prior is not None:
            if prior["load_id"] != load_id or prior["request_hash"] != rhash:
                raise _key_reused()
            return JSONResponse(prior["response"])
        if row["status"] != "available":
            raise conflict("LOAD_NOT_AVAILABLE")
        cur = await conn.execute(
            "select 1 from tms.carriers c join tms.carrier_compliance cc using (dot_number) "
            "where c.dot_number = %s and cc.authority_status = 'active' "
            "and cc.insurance_expires >= current_date and c.equipment = %s",
            (body.carrier_dot, row["equipment"]),
        )
        if await cur.fetchone() is None:
            raise conflict("CARRIER_NOT_ELIGIBLE")
        if body.carrier_rate > row["customer_rate"]:
            raise conflict("RATE_ABOVE_CUSTOMER_RATE")

        now = datetime.now(UTC)
        await conn.execute(
            "update tms.loads set status = 'booked', carrier_dot = %s, carrier_rate = %s, "
            "updated_at = %s where id = %s and tenant_id = %s",
            (body.carrier_dot, body.carrier_rate, now, load_id, p.tenant),
        )
        await conn.execute(
            "insert into tms.load_events (load_id, tenant_id, type, at, source_integration, detail) "
            "values (%s, %s, 'booked', %s, 'api', %s)",
            (
                load_id,
                p.tenant,
                now,
                f"booked by {p.user_id} via {p.client_id or 'rest'}",
            ),
        )
        cur = await conn.execute(
            f"select {LOAD_COLS} from tms.loads l where {where} and l.id = %(id)s",
            params,
        )
        fresh = redact_load(await cur.fetchone(), p)
        fresh["as_of"] = now
        payload = Load(**fresh).model_dump(mode="json", exclude_unset=True)
        cur = await conn.execute(
            "insert into tms.api_idempotency (tenant_id, key, response, load_id, request_hash) "
            "values (%s, %s, %s, %s, %s) on conflict do nothing returning response",
            (p.tenant, idem_key, Jsonb(payload), load_id, rhash),
        )
        inserted = await cur.fetchone()
        if inserted is None:
            # Lost a race on the key: never commit this booking; roll back either way.
            cur = await conn.execute(
                "select response, load_id, request_hash from tms.api_idempotency "
                "where tenant_id = %s and key = %s",
                (p.tenant, idem_key),
            )
            other = await cur.fetchone()
            if other["load_id"] != load_id or other["request_hash"] != rhash:
                raise _key_reused()
            raise _KeyRaceWon(other["response"])
        return JSONResponse(inserted["response"])
