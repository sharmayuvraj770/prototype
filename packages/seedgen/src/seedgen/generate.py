"""Deterministic synthetic data on top of the reference tables.

Everything is derived from `random.Random(seed)` and `demo_now`; reference rows are read
in a stable ORDER BY, so two runs on fresh databases produce identical rows.
"""
import random
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg
from psycopg import sql

from seedgen.clear import clear_synthetic
from seedgen.events import WaypointIndex, build_events, minutes
from seedgen.fixtures import (
    COMMODITIES,
    COMPLIANCE_SOURCE,
    CUSTOMERS,
    DEMO_LOADS,
    ELD_SOURCES,
    FICTIONAL_CARRIERS,
    INJECTION_NOTES,
    LOADBOARD_SOURCE,
    LOADS_PER_TENANT,
    NEUTRAL_NOTES,
    PINNED_IDS,
    ROLE_SUFFIX,
    STATUS_MIX,
    TENANTS,
    USER_NAMES,
    user_email,
    user_id,
)

REAL_CARRIERS_PER_TENANT = 60
FICTIONAL_TENANTS = ("broker_a", "broker_b")
CAPACITY_POSTS = 150
CHI_DAL_REEFER_POSTS = 5
LOAD_COLS = (
    "id", "tenant_id", "ref", "status", "lane_id", "orig_metro", "dest_metro", "equipment",
    "commodity", "weight_lbs", "pickup_at", "delivery_at", "eta", "carrier_dot", "assigned_rep",
    "customer_name", "customer_rate", "carrier_rate", "updated_at",
)
EVENT_COLS = (
    "load_id", "tenant_id", "type", "at", "city", "state", "lat", "lon", "source_integration", "detail",
)


def _money(x: float) -> Decimal:
    return Decimal(f"{x:.2f}")


def _copy(conn: psycopg.Connection, table: str, cols: tuple[str, ...], rows: list) -> int:
    stmt = sql.SQL("copy {} ({}) from stdin").format(
        sql.Identifier("tms", table), sql.SQL(", ").join(map(sql.Identifier, cols))
    )
    with conn.cursor() as cur, cur.copy(stmt) as cp:
        for row in rows:
            cp.write_row(row)
    return len(rows)


def _read_reference(conn: psycopg.Connection) -> dict:
    def q(query: str) -> list[tuple]:
        return conn.execute(query).fetchall()

    return {
        "metros": {r[0]: r[1:] for r in q("select code, city, state, lat, lon from tms.metros order by code")},
        "lanes": [
            dict(zip(("lane_id", "orig", "dest", "equipment", "miles", "weight"), r))
            for r in q("select lane_id, orig, dest, equipment, miles, annual_loads_2024 "
                       "from tms.lanes order by lane_id")
        ],
        "rpm": {
            r[0]: {"reefer": float(r[1]), "dry_van": float(r[2])}
            for r in q("select distinct on (market) market, reefer_rpm, dry_van_rpm "
                       "from tms.rate_index order by market, week desc")
        },
        "waypoints": WaypointIndex(q("select city, state, lat, lon from tms.waypoints order by geoid")),
        "carriers": [
            dict(zip(("dot_number", "equipment", "home_metro", "safety_rating"), r))
            for r in q("select dot_number, equipment, home_metro, safety_rating from tms.carriers "
                       "where not is_fictional order by dot_number")
        ],
    }


def _status_pool(n: int, rng: random.Random) -> list[str]:
    """Exact status mix (largest-remainder rounding), shuffled."""
    exact = [(s, n * p) for s, p in STATUS_MIX]
    counts = {s: int(x) for s, x in exact}
    by_remainder = sorted(exact, key=lambda sx: -(sx[1] - int(sx[1])))  # stable on ties
    for s, _ in by_remainder[: n - sum(counts.values())]:
        counts[s] += 1
    pool = [s for s, _ in STATUS_MIX for _ in range(counts[s])]
    rng.shuffle(pool)
    return pool


def _random_times(status: str, miles: int, now: datetime, rng: random.Random) -> dict:
    transit = minutes(miles / 50 * 60 + 120)
    late = status in ("in_transit", "dispatched") and rng.random() < 0.15
    if status == "delivered":
        pickup = now - transit - minutes(rng.uniform(3 * 60, 30 * 24 * 60))
    elif status == "in_transit":
        pickup = now - minutes(transit.total_seconds() / 60 * rng.uniform(0.1, 0.9))
    elif status == "dispatched":
        pickup = now + minutes(rng.uniform(2 * 60, 48 * 60))
    elif status in ("booked", "available"):
        pickup = now + minutes(rng.uniform(24 * 60, (7 if status == "booked" else 10) * 24 * 60))
    else:  # cancelled
        pickup = now + minutes(rng.uniform(-10 * 24 * 60, 7 * 24 * 60))
    delivery = pickup + transit
    if status == "delivered":
        eta = delivery + minutes(rng.uniform(-45, 45))  # actual arrival
    elif status in ("in_transit", "dispatched"):
        eta = delivery + minutes(rng.uniform(45, 300) if late else rng.uniform(-60, 25))
    else:
        eta = None
    return {"pickup_at": pickup, "delivery_at": delivery, "eta": eta, "late": late}


def _pick_carrier(pool: list[dict], lane: dict, rng: random.Random, eld_only: bool = False) -> int:
    cands = [c for c in pool if c["equipment"] == lane["equipment"]]
    if eld_only:
        cands = [c for c in cands if ELD_SOURCES[c["dot_number"] % 3].startswith("eld:")]
    home = [c for c in cands if c["home_metro"] == lane["orig"]]
    if home and (eld_only or rng.random() < 0.8):
        cands = home
    return rng.choice(cands)["dot_number"]


def _build_load(spec: dict, lane: dict, ref: dict, pool: list[dict], now: datetime,
                rng: random.Random) -> dict:
    """`spec` holds id, tenant_id, status and optional pinned overrides."""
    status = spec["status"]
    if "pickup_min" in spec:
        eta = None if spec["eta_min"] is None else now + minutes(spec["eta_min"])
        delivery = now + minutes(spec["delivery_min"])
        times = {"pickup_at": now + minutes(spec["pickup_min"]), "delivery_at": delivery, "eta": eta,
                 "late": eta is not None and eta > delivery + timedelta(minutes=30)}
    else:
        times = _random_times(status, lane["miles"], now, rng)
    has_carrier = status != "available" and (status != "cancelled" or rng.random() < 0.5)
    carrier = _pick_carrier(pool, lane, rng, spec.get("eld_tracked", False)) if has_carrier else None
    rpm_table = ref["rpm"].get(ref["metros"][lane["dest"]][0], ref["rpm"]["NATIONAL"])
    customer = lane["miles"] * rpm_table[lane["equipment"]] * rng.uniform(1.05, 1.25)
    customer = _money(spec.get("customer_rate", customer))
    carrier_rate = _money(float(customer) * rng.uniform(0.80, 0.90)) if carrier else None
    t = spec["tenant_id"]
    rep = spec.get("assigned_rep") or user_id(t, "ops" if rng.random() < 0.5 else "manager")
    return {
        "id": spec["id"], "tenant_id": t, "ref": f"PO-{rng.randint(100000, 999999)}", "status": status,
        "lane_id": lane["lane_id"], "orig_metro": lane["orig"], "dest_metro": lane["dest"],
        "equipment": lane["equipment"], "commodity": rng.choice(COMMODITIES[lane["equipment"]]),
        "weight_lbs": rng.randint(18_000, 44_000), **times, "carrier_dot": carrier,
        "assigned_rep": rep, "customer_name": rng.choice(CUSTOMERS), "customer_rate": customer,
        "carrier_rate": carrier_rate,
    }


def _loads_and_events(ref: dict, pools: dict[str, list[dict]], now: datetime, rng: random.Random):
    lanes = ref["lanes"]
    lane_by_id = {ln["lane_id"]: ln for ln in lanes}
    specs = [dict(d) for d in DEMO_LOADS]
    pinned_per_tenant = Counter(d["tenant_id"] for d in DEMO_LOADS)
    tenants = [t for t in TENANTS for _ in range(LOADS_PER_TENANT[t] - pinned_per_tenant[t])]
    statuses = _status_pool(len(tenants), rng)
    ids = rng.sample([i for i in range(1000, 10000) if i not in PINNED_IDS], len(tenants))
    random_lanes = rng.choices(lanes, weights=[ln["weight"] for ln in lanes], k=len(tenants))
    specs += [{"id": i, "tenant_id": t, "status": s, "lane": ln}
              for i, t, s, ln in zip(ids, tenants, statuses, random_lanes)]

    def place(code: str) -> tuple:  # (city, state, lat, lon)
        return ref["metros"][code]

    loads, events = [], []
    for spec in specs:
        lane = spec.get("lane") or lane_by_id[spec["lane_id"]]
        load = _build_load(spec, lane, ref, pools[spec["tenant_id"]], now, rng)
        evs = build_events(load, now=now, rng=rng, orig=place(lane["orig"]), dest=place(lane["dest"]),
                           waypoints=ref["waypoints"], pings=spec.get("pings"),
                           ping_city=spec.get("ping_city"))
        load["updated_at"] = max(e[3] for e in evs)
        loads.append(load)
        events.append(evs)
    order = sorted(range(len(loads)), key=lambda k: loads[k]["id"])
    return [loads[k] for k in order], [e for k in order for e in events[k]]


def _compliance(carriers: list[dict], now: datetime, rng: random.Random) -> list[tuple]:
    rows, conditional_seen = [], 0
    for c in carriers:  # ordered by dot_number
        status, days = "active", rng.randint(30, 400)
        if c["safety_rating"] == "unsatisfactory":
            status = "revoked"
        elif c["safety_rating"] == "conditional":
            if conditional_seen % 2 == 0:
                days = -rng.randint(1, 90)
            conditional_seen += 1
        observed = now - minutes(rng.uniform(1, 30) * 24 * 60)
        rows.append((c["dot_number"], status, now.date() + timedelta(days=days), COMPLIANCE_SOURCE, observed))
    return rows


def _tenant_carriers(pools, loads, now, rng) -> list[tuple]:
    hauled = Counter((ld["tenant_id"], ld["carrier_dot"]) for ld in loads if ld["carrier_dot"])
    rows = []
    for t in TENANTS:
        for c in pools[t]:
            note = rng.choice(NEUTRAL_NOTES) if rng.random() < 0.5 else None
            rows.append((t, c["dot_number"], _money(rng.uniform(78, 99)),
                         hauled[(t, c["dot_number"])] + rng.randint(0, 40), note,
                         now - minutes(rng.uniform(1, 90) * 24 * 60) if note else None))
        if t in FICTIONAL_TENANTS:
            for fc, note in zip(FICTIONAL_CARRIERS, INJECTION_NOTES):
                rows.append((t, fc["dot_number"], _money(rng.uniform(78, 99)), rng.randint(0, 5), note,
                             now - minutes(rng.uniform(1, 20) * 24 * 60)))
    return rows


def _capacity_posts(carriers, ref, now, rng) -> list[tuple]:
    metros = list(ref["metros"])  # sorted by code
    chi_reefer = [c for c in carriers if c["equipment"] == "reefer" and c["home_metro"] == "CHI"]
    open_dest = set(rng.sample(range(CHI_DAL_REEFER_POSTS, CAPACITY_POSTS), round(CAPACITY_POSTS * 0.3)))
    rows = []
    for i in range(CAPACITY_POSTS):
        if i < CHI_DAL_REEFER_POSTS:
            c, orig, dest = rng.choice(chi_reefer), "CHI", "DAL"
        else:
            c = rng.choice(carriers)
            orig = c["home_metro"] if c["home_metro"] and rng.random() < 0.7 else rng.choice(metros)
            dests = [ln["dest"] for ln in ref["lanes"] if ln["orig"] == orig and ln["equipment"] == c["equipment"]]
            dest = None if i in open_dest else rng.choice(dests or [m for m in metros if m != orig])
        rows.append((c["dot_number"], c["equipment"], orig, dest, now.date() + timedelta(days=rng.randint(0, 7)),
                     LOADBOARD_SOURCE, now - minutes(rng.uniform(30, 72 * 60))))
    return rows


def generate(conn: psycopg.Connection, *, demo_now: datetime, seed: int = 4521) -> dict[str, int]:
    """Replace all synthetic rows; return table -> rows inserted."""
    rng = random.Random(seed)
    now = demo_now
    counts: dict[str, int] = {}
    with conn.transaction():
        clear_synthetic(conn)
        ref = _read_reference(conn)
        real = ref["carriers"]

        counts["tenants"] = _copy(conn, "tenants", ("id", "name"), list(TENANTS.items()))
        names = iter(USER_NAMES)
        users = [(user_id(t, s), t, user_email(t, s), next(names), role)
                 for t in TENANTS for s, role in ROLE_SUFFIX.items()]
        counts["users"] = _copy(conn, "users", ("id", "tenant_id", "email", "name", "role"), users)

        fict_cols = ("dot_number", "mc_number", "legal_name", "city", "state", "home_metro",
                     "equipment", "safety_rating", "is_fictional")
        counts["carriers"] = _copy(conn, "carriers", fict_cols, [
            (c["dot_number"], str(c["dot_number"] - 9_000_000), c["legal_name"], *ref["metros"][c["home_metro"]][:2],
             c["home_metro"], c["equipment"], c["safety_rating"], True) for c in FICTIONAL_CARRIERS
        ])
        all_carriers = real + [{k: c[k] for k in ("dot_number", "equipment", "home_metro", "safety_rating")}
                               for c in FICTIONAL_CARRIERS]
        counts["carrier_compliance"] = _copy(
            conn, "carrier_compliance",
            ("dot_number", "authority_status", "insurance_expires", "source_integration", "observed_at"),
            _compliance(all_carriers, now, rng),
        )

        pools = {t: sorted(rng.sample(real, REAL_CARRIERS_PER_TENANT), key=lambda c: c["dot_number"])
                 for t in TENANTS}
        loads, events = _loads_and_events(ref, pools, now, rng)
        counts["tenant_carriers"] = _copy(
            conn, "tenant_carriers",
            ("tenant_id", "dot_number", "on_time_pct", "loads_hauled", "notes", "notes_updated_at"),
            _tenant_carriers(pools, loads, now, rng),
        )
        counts["capacity_posts"] = _copy(
            conn, "capacity_posts",
            ("dot_number", "equipment", "orig_metro", "dest_metro", "available_date",
             "source_integration", "posted_at"),
            _capacity_posts(all_carriers, ref, now, rng),
        )
        counts["loads"] = _copy(conn, "loads", LOAD_COLS, [tuple(ld[c] for c in LOAD_COLS) for ld in loads])
        counts["load_events"] = _copy(conn, "load_events", EVENT_COLS, events)
    return counts
