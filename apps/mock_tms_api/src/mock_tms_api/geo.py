from math import asin, cos, radians, sin, sqrt

from psycopg import AsyncConnection

EARTH_RADIUS_MI = 3958.8
ROAD_FACTOR = 1.2


def great_circle_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    dphi, dlmb = p2 - p1, radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MI * asin(sqrt(a))


async def estimate_miles(
    conn: AsyncConnection, orig: str, dest: str, equipment: str | None = None
) -> tuple[int, bool]:
    """Return (miles, derived).

    Lane miles when a lane exists for the pair (preferring `equipment`), else
    great-circle x 1.2 between metro coordinates (derived=True).
    Raises LookupError if a metro code is unknown and no lane exists.
    """
    cur = await conn.execute(
        "select miles from tms.lanes where orig = %(o)s and dest = %(d)s "
        "order by (equipment = %(eq)s) desc, lane_id limit 1",
        {"o": orig, "d": dest, "eq": equipment},
    )
    row = await cur.fetchone()
    if row is not None:
        return row["miles"], False
    cur = await conn.execute(
        "select code, lat, lon from tms.metros where code in (%(o)s, %(d)s)",
        {"o": orig, "d": dest},
    )
    pts = {r["code"]: r for r in await cur.fetchall()}
    if orig not in pts or dest not in pts:
        raise LookupError("unknown metro")
    a, b = pts[orig], pts[dest]
    return round(
        great_circle_miles(a["lat"], a["lon"], b["lat"], b["lon"]) * ROAD_FACTOR
    ), True
