"""Load curated reference CSVs into the tms schema via COPY."""
import csv
import os
from pathlib import Path

import psycopg
from psycopg import sql

SEED_DIR = Path(
    os.environ.get("SEED_DIR")
    or Path(__file__).resolve().parents[4] / "data" / "seed"
)

# table -> list of (table column, csv column); load order respects FKs.
_SPECS: dict[str, list[tuple[str, str]]] = {
    "metros": [(c, c) for c in ("city", "state", "lat", "lon", "role")] + [("code", "metro")],
    "lanes": [(c, c) for c in ("lane_id", "orig", "dest", "equipment", "miles", "annual_loads_2024")],
    "rate_index": [(c, c) for c in ("week", "market", "reefer_rpm", "dry_van_rpm", "availability", "n_obs")],
    "rate_observations": [
        (c, c)
        for c in ("week", "origin_city", "destination", "rate_per_load", "miles", "rate_per_mile", "source")
    ],
    "waypoints": [(c, c) for c in ("geoid", "city", "state", "lat", "lon")],
    "carriers": [
        (c, c)
        for c in (
            "dot_number mc_number legal_name dba_name city state lat lon home_metro equipment "
            "power_units drivers safety_rating safety_rating_date recordable_crash_rate "
            "authority_since hazmat"
        ).split()
    ],
}
_BOOL_COLS = {"hazmat"}


def _value(col: str, raw: str):
    if raw == "":
        return None
    if col in _BOOL_COLS:
        return raw.strip().lower() in ("true", "t", "1")
    return raw


def load_reference(conn: psycopg.Connection, seed_dir: Path) -> dict[str, int]:
    """Clear (no cascade) and reload the six reference tables. Returns row counts."""
    counts: dict[str, int] = {}
    with conn.transaction():
        # TRUNCATE is refused on FK-referenced tables even when empty, so DELETE in
        # reverse dependency order (fails loudly if synthetic rows still reference us).
        for table in reversed(list(_SPECS)):
            conn.execute(sql.SQL("delete from {}").format(sql.Identifier("tms", table)))
        for table, mapping in _SPECS.items():
            stmt = sql.SQL("copy {} ({}) from stdin").format(
                sql.Identifier("tms", table),
                sql.SQL(", ").join(sql.Identifier(t) for t, _ in mapping),
            )
            n = 0
            with open(Path(seed_dir) / f"{table}.csv", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                with conn.cursor() as cur, cur.copy(stmt) as cp:
                    for row in reader:
                        cp.write_row([_value(t, row[c]) for t, c in mapping])
                        n += 1
            counts[table] = n
    return counts
