from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends

from mock_tms_api.auth import Principal, current_principal
from mock_tms_api.errors import forbidden


class Perm(StrEnum):
    LOADS_READ = "loads:read"
    LOADS_BOOK = "loads:book"
    CARRIERS_READ = "carriers:read"
    CAPACITY_READ = "capacity:read"
    RATES_READ = "rates:read"
    FINANCIALS_READ = "financials:read"
    QUOTES_READ = "quotes:read"
    QUOTES_WRITE = "quotes:write"


_ALL = frozenset(Perm)

ROLE_PERMS: dict[str, frozenset[Perm]] = {
    "ops_rep": frozenset({Perm.LOADS_READ, Perm.CARRIERS_READ}),
    "carrier_sales": _ALL,
    "pricing": frozenset(
        {
            Perm.CARRIERS_READ,
            Perm.RATES_READ,
            Perm.FINANCIALS_READ,
            Perm.QUOTES_READ,
            Perm.QUOTES_WRITE,
        }
    ),
    "ops_manager": _ALL,
    "broker_admin": _ALL - {Perm.LOADS_BOOK, Perm.QUOTES_WRITE},
}

FINANCIAL_FIELDS = ("customer_rate", "carrier_rate", "margin")


def has_perm(p: Principal, perm: Perm) -> bool:
    return perm in ROLE_PERMS.get(p.role, frozenset())


def require(perm: Perm) -> Callable[..., Principal]:
    async def dep(p: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if not has_perm(p, perm):
            raise forbidden(perm.value)
        return p

    return dep


def load_scope_sql(p: Principal) -> tuple[str, dict[str, Any]]:
    """WHERE fragment (table alias `l`) restricting loads to the principal's scope."""
    sql = "l.tenant_id = %(tenant)s"
    params: dict[str, Any] = {"tenant": p.tenant}
    if p.role == "ops_rep":
        sql += " and l.assigned_rep = %(user)s"
        params["user"] = p.user_id
    return sql, params


def redact_load(row: dict, p: Principal) -> dict:
    if has_perm(p, Perm.FINANCIALS_READ):
        return row
    return {k: v for k, v in row.items() if k not in FINANCIAL_FIELDS}
