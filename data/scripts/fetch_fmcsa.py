"""Fetch a carrier sample from the FMCSA Company Census File (Socrata az4n-8mr2).

Pulls candidate pools via the SODA API (no 2 GB bulk download), then draws a
seeded, stratified sample of ~200 for-hire interstate carriers: reefer-capable
(cold food / produce / meat cargo) and dry van (general freight only).

PII is dropped at the source: no phone, fax, email, street address or officer names.

Usage:  python -I data/scripts/fetch_fmcsa.py [--app-token TOKEN]
Writes: data/raw/fmcsa/pool_*.json, data/processed/carriers.csv
"""
import argparse
import csv
import json
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "fmcsa"
OUT = ROOT / "processed" / "carriers.csv"
API = "https://data.transportation.gov/resource/az4n-8mr2.json"
SEED = 4521

FIELDS = [
    "dot_number", "legal_name", "dba_name", "phy_city", "phy_state", "phy_zip",
    "power_units", "truck_units", "total_drivers", "total_cdl", "carrier_operation",
    "classdef", "docket1prefix", "docket1", "docket1_status_code", "add_date",
    "mcs150_date", "mcs150_mileage", "mcs150_mileage_year", "safety_rating",
    "safety_rating_date", "recordable_crash_rate", "hm_ind",
    "crgo_genfreight", "crgo_produce", "crgo_coldfood", "crgo_meat", "crgo_beverages",
    "owntract", "owntrail", "trmtract", "trmtrail",
]

# States on the demo lanes (FAF top truck flows + USDA destinations/origins).
LANE_STATES = ["CA", "TX", "IL", "GA", "FL", "WA", "AZ", "PA", "NJ", "NY", "MD",
               "OH", "IN", "MI", "MN", "WI", "MO", "TN", "NC", "MA", "OK", "CO"]

BASE_WHERE = (
    "status_code='A' AND carrier_operation='A' AND docket1prefix='MC' "
    "AND classdef like '%AUTHORIZED FOR HIRE%' "
    "AND power_units::number between 5 and 500 "
    "AND phy_state in ({states})"
).format(states=",".join(f"'{s}'" for s in LANE_STATES))

POOLS = {
    "reefer": BASE_WHERE + " AND (crgo_coldfood='X' OR crgo_produce='X' OR crgo_meat='X')",
    "dryvan": BASE_WHERE + " AND crgo_genfreight='X' AND crgo_coldfood IS NULL "
                           "AND crgo_produce IS NULL AND crgo_meat IS NULL",
}
# Sample sizes, with quotas per FMCSA safety rating so compliance answers are interesting.
QUOTAS = {
    "reefer": {"C": 12, "U": 3, "S": 35, None: 70},
    "dryvan": {"C": 6, "U": 2, "S": 20, None: 52},
}
PAGE = 50000


def fetch(where: str, token: str | None) -> list[dict]:
    rows, offset = [], 0
    while True:
        qs = urllib.parse.urlencode({
            "$select": ",".join(FIELDS), "$where": where,
            "$order": "dot_number", "$limit": PAGE, "$offset": offset,
        })
        req = urllib.request.Request(f"{API}?{qs}")
        if token:
            req.add_header("X-App-Token", token)
        with urllib.request.urlopen(req, timeout=300) as r:
            page = json.load(r)
        rows += page
        if len(page) < PAGE:
            return rows
        offset += PAGE
        time.sleep(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-token", help="optional Socrata app token (higher rate limit)")
    ap.add_argument("--refresh", action="store_true", help="re-download pools")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    picked: list[dict] = []

    for kind, where in POOLS.items():
        cache = RAW / f"pool_{kind}.json"
        if args.refresh or not cache.exists():
            print(f"fetching {kind} pool ...")
            cache.write_text(json.dumps(fetch(where, args.app_token)), encoding="utf-8")
        pool = json.loads(cache.read_text(encoding="utf-8"))
        print(f"{kind}: {len(pool)} candidates")
        for rating, n in QUOTAS[kind].items():
            bucket = [r for r in pool if r.get("safety_rating") == rating]
            take = rng.sample(bucket, min(n, len(bucket)))
            for r in take:
                r["equipment"] = kind
            picked += take

    picked.sort(key=lambda r: int(r["dot_number"]))
    cols = FIELDS + ["equipment"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(picked)
    print(f"wrote {len(picked)} carriers -> {OUT}")


if __name__ == "__main__":
    main()
