from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from mock_tms_api.auth import Principal
from mock_tms_api.db import Conn
from mock_tms_api.errors import not_found
from mock_tms_api.geo import estimate_miles
from mock_tms_api.pagination import decode_cursor, encode_cursor, non_negative_int
from mock_tms_api.rbac import Perm, has_perm, require
from mock_tms_api.schemas import CapacityPost, LaneDetail, Page, RateIndexRow

router = APIRouter(prefix="/v1", tags=["market"])

CapacityReader = Annotated[Principal, Depends(require(Perm.CAPACITY_READ))]
RatesReader = Annotated[Principal, Depends(require(Perm.RATES_READ))]
Equipment = Literal["reefer", "dry_van"]
METRO = r"^[A-Z]{3}$"
MetroPath = Annotated[str, Path(pattern=METRO)]
MetroQuery = Annotated[str | None, Query(pattern=METRO)]


@router.get("/capacity", response_model=Page[CapacityPost])
async def list_capacity(
    p: CapacityReader,
    conn: Conn,
    equipment: Equipment | None = None,
    orig: MetroQuery = None,
    dest: MetroQuery = None,
    available_from: date | None = None,
    available_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
):
    # Capacity posts are market (load board) data, intentionally not tenant-scoped.
    start = available_from or datetime.now(UTC).date()
    end = available_to or (start + timedelta(days=7))
    if start > end:
        raise HTTPException(422, {"error": "INVALID_DATE_RANGE"})
    if end - start > timedelta(days=14):
        raise HTTPException(422, {"error": "DATE_RANGE_TOO_LARGE", "max_days": 14})
    clauses = ["cp.available_date between %(start)s and %(end)s"]
    params: dict = {"start": start, "end": end, "lim": limit + 1}
    if equipment:
        clauses.append("cp.equipment = %(eq)s")
        params["eq"] = equipment
    if orig:
        clauses.append("cp.orig_metro = %(orig)s")
        params["orig"] = orig
    if dest:
        clauses.append("(cp.dest_metro = %(dest)s or cp.dest_metro is null)")
        params["dest"] = dest
    if cursor:  # keyset on (available_date, id), matching the order by
        params["c_d"], params["c_id"] = decode_cursor(
            cursor, date.fromisoformat, non_negative_int
        )
        clauses.append("(cp.available_date, cp.id) > (%(c_d)s, %(c_id)s)")
    cur = await conn.execute(
        "select cp.id, cp.dot_number, c.legal_name, c.safety_rating, cp.equipment, cp.orig_metro, "
        "cp.dest_metro, cp.available_date, cp.source_integration, cp.posted_at "
        "from tms.capacity_posts cp join tms.carriers c on c.dot_number = cp.dot_number "
        f"where {' and '.join(clauses)} "
        "order by cp.available_date, cp.id limit %(lim)s",
        params,
    )
    rows = await cur.fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": rows,
        "next_cursor": encode_cursor(rows[-1]["available_date"], rows[-1]["id"])
        if has_more
        else None,
        "as_of": datetime.now(UTC),
    }


@router.get(
    "/lanes/{orig}/{dest}", response_model=LaneDetail, response_model_exclude_unset=True
)
async def get_lane(
    orig: MetroPath, dest: MetroPath, equipment: Equipment, p: RatesReader, conn: Conn
):
    cur = await conn.execute(
        "select code, city from tms.metros where code in (%(o)s, %(d)s)",
        {"o": orig, "d": dest},
    )
    metros = {r["code"]: r["city"] for r in await cur.fetchall()}
    if orig not in metros or dest not in metros:
        raise not_found()

    cur = await conn.execute(
        "select lane_id, orig, dest, equipment, miles, annual_loads_2024 from tms.lanes "
        "where orig = %(o)s and dest = %(d)s order by (equipment = %(eq)s) desc, lane_id limit 1",
        {"o": orig, "d": dest, "eq": equipment},
    )
    lane = await cur.fetchone()
    miles, derived = await estimate_miles(conn, orig, dest, equipment)

    cur = await conn.execute(
        "select 1 from tms.rate_index where market = %(m)s limit 1", {"m": metros[dest]}
    )
    market = metros[dest] if await cur.fetchone() else "NATIONAL"
    rpm_col = "reefer_rpm" if equipment == "reefer" else "dry_van_rpm"
    cur = await conn.execute(
        f"select week, {rpm_col} as rpm, availability, n_obs from tms.rate_index "
        "where market = %(m)s order by week desc limit 8",
        {"m": market},
    )
    history = await cur.fetchall()

    cur = await conn.execute(
        "select count(*) as n, avg(carrier_rate) as avg_rate from tms.loads "
        "where tenant_id = %(tenant)s and orig_metro = %(o)s and dest_metro = %(d)s "
        "and equipment = %(eq)s and status = 'delivered' "
        "and delivery_at >= now() - interval '90 days'",
        {"tenant": p.tenant, "o": orig, "d": dest, "eq": equipment},
    )
    agg = await cur.fetchone()
    tenant_history: dict = {"delivered_count_90d": agg["n"]}
    if has_perm(p, Perm.FINANCIALS_READ):
        tenant_history["avg_carrier_rate_90d"] = agg["avg_rate"]
    return {
        "orig": orig,
        "dest": dest,
        "equipment": equipment,
        "lane": lane,
        "miles": miles,
        "derived": derived,
        "market": market,
        "rate_history": history,
        "tenant_history": tenant_history,
        "as_of": datetime.now(UTC),
    }


@router.get("/rates/index", response_model=Page[RateIndexRow])
async def rate_index(
    p: RatesReader,
    conn: Conn,
    market: Annotated[str, Query(max_length=40)] = "NATIONAL",
    weeks: Annotated[int, Query(ge=1, le=26)] = 8,
):
    cur = await conn.execute(
        "select week, market, reefer_rpm, dry_van_rpm, availability, n_obs from tms.rate_index "
        "where market = %(m)s order by week desc limit %(n)s",
        {"m": market, "n": weeks},
    )
    return {
        "items": await cur.fetchall(),
        "next_cursor": None,
        "as_of": datetime.now(UTC),
    }
