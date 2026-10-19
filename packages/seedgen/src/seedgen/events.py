"""Load-event timelines: created -> booked -> dispatched -> picked_up -> pings -> delivered.

Pure functions over plain dicts; all randomness comes from the caller's `random.Random`.
"""
import math
import random
from datetime import datetime, timedelta

from seedgen.fixtures import EDI_SOURCE, ELD_SOURCES

PING_EVERY = timedelta(hours=3)


def minutes(m: float) -> timedelta:
    return timedelta(minutes=round(m))


def _between(rng: random.Random, start: datetime, end: datetime, lo: float, hi: float) -> datetime:
    """A whole-minute point at fraction U(lo, hi) of [start, end]."""
    span = (end - start).total_seconds() / 60
    return start + minutes(span * rng.uniform(lo, hi))


class WaypointIndex:
    """Nearest-waypoint lookup over the (small) waypoints table, held in memory."""

    def __init__(self, rows: list[tuple[str, str, float, float]]):
        self.rows = rows  # (city, state, lat, lon), stable ORDER BY geoid

    def nearest(self, lat: float, lon: float) -> tuple[str, str, float, float]:
        k = math.cos(math.radians(lat))  # equirectangular: fine for ranking nearby points
        return min(self.rows, key=lambda w: (w[2] - lat) ** 2 + ((w[3] - lon) * k) ** 2)

    def by_city(self, city: str, state: str) -> tuple[str, str, float, float]:
        return next(w for w in self.rows if w[0] == city and w[1] == state)


def build_events(
    load: dict,
    *,
    now: datetime,
    rng: random.Random,
    orig: tuple[str, str, float, float],
    dest: tuple[str, str, float, float],
    waypoints: WaypointIndex,
    pings: list[int] | None = None,
    ping_city: tuple[str, str] | None = None,
) -> list[tuple]:
    """Events for one load, never later than `now`.

    `orig`/`dest` are (city, state, lat, lon). `pings` (minutes from now) overrides the
    3-hourly ping schedule; `ping_city` pins the final ping to a named waypoint.
    """
    status, pickup = load["status"], load["pickup_at"]
    out: list[tuple] = []

    def add(kind, at, place=None, source=EDI_SOURCE, detail=None):
        city, state, lat, lon = place or (None, None, None, None)
        out.append((load["id"], load["tenant_id"], kind, at, city, state, lat, lon, source, detail))

    created = min(pickup - minutes(rng.uniform(24, 120) * 60), now - minutes(rng.uniform(1, 72) * 60))
    add("created", created)
    if status == "available":
        return out
    anchor = min(pickup, now)
    if status == "cancelled":
        if load["carrier_dot"] is not None:
            add("booked", _between(rng, created, anchor, 0.2, 0.5))
        add("cancelled", _between(rng, created, anchor, 0.6, 0.95))
        return out
    booked = _between(rng, created, anchor, 0.2, 0.5)
    add("booked", booked)
    if status == "booked":
        return out
    add("dispatched", _between(rng, booked, anchor, 0.3, 0.9))
    if status == "dispatched":
        return out

    add("picked_up", pickup, orig)
    route_end = load["eta"] or load["delivery_at"]
    track_end = route_end if status == "delivered" else now
    if pings is None:
        times, t = [], pickup + PING_EVERY
        while t < track_end:
            times.append(t)
            t += PING_EVERY
    else:
        times = [now + minutes(p) for p in pings]
    eld = ELD_SOURCES[load["carrier_dot"] % 3]
    total = (route_end - pickup).total_seconds()
    for i, t in enumerate(times):
        if ping_city is not None and i == len(times) - 1:
            place = waypoints.by_city(*ping_city)
        else:
            f = min(1.0, max(0.0, (t - pickup).total_seconds() / total))
            place = waypoints.nearest(orig[2] + (dest[2] - orig[2]) * f, orig[3] + (dest[3] - orig[3]) * f)
        add("ping", t, place, source=eld)

    if load.get("late") and status == "in_transit":
        at = max(_between(rng, pickup, now, 0.5, 0.5), now - minutes(rng.uniform(30, 180)))
        add("delay", at, detail="Running behind schedule; ETA revised")
    if status == "delivered":
        add("delivered", route_end, dest)
    out.sort(key=lambda e: e[3])  # stable: equal timestamps keep generation order
    return out
