from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from mock_tms_api.auth import Principal
from mock_tms_api.db import Conn
from mock_tms_api.errors import not_found
from mock_tms_api.rbac import Perm, require
from mock_tms_api.schemas import Carrier, Compliance, Scorecard

router = APIRouter(prefix="/v1/carriers", tags=["carriers"])

Reader = Annotated[Principal, Depends(require(Perm.CARRIERS_READ))]
Dot = Annotated[int, Path(ge=1, le=99999999)]


@router.get("/{dot}", response_model=Carrier)
async def get_carrier(dot: Dot, p: Reader, conn: Conn):
    # Carrier census data is public; not tenant-scoped.
    cur = await conn.execute(
        "select * from tms.carriers where dot_number = %(dot)s", {"dot": dot}
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    row["as_of"] = datetime.now(UTC)
    return row


@router.get("/{dot}/compliance", response_model=Compliance)
async def get_compliance(dot: Dot, p: Reader, conn: Conn):
    cur = await conn.execute(
        "select authority_status, insurance_expires, "
        "insurance_expires >= current_date as insurance_valid, source_integration, observed_at "
        "from tms.carrier_compliance where dot_number = %(dot)s",
        {"dot": dot},
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    row["as_of"] = datetime.now(UTC)
    return row


@router.get("/{dot}/scorecard", response_model=Scorecard)
async def get_scorecard(dot: Dot, p: Reader, conn: Conn):
    # Tenant-scoped in SQL; notes are returned raw (MCP server sanitises them).
    cur = await conn.execute(
        "select on_time_pct, loads_hauled, notes, notes_updated_at from tms.tenant_carriers "
        "where tenant_id = %(tenant)s and dot_number = %(dot)s",
        {"tenant": p.tenant, "dot": dot},
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    row["as_of"] = datetime.now(UTC)
    return row
