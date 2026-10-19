"""Curate the MVP seed world from data/processed/ into data/seed/.

Keeps only what the MVP tools touch, and reshapes it freely (this is demo data):
  metros.csv           26 metro hubs = USDA destinations + top freight hubs + produce gateways
  lanes.csv            truck lanes between those metros only (FAF 2024), reefer + dry
  rate_observations.csv USDA spot rates converted to $/mile (origin district -> destination)
  rate_index.csv       weekly reefer $/mile by destination + national; dry van derived
  waypoints.csv        real cities along lane corridors, for in-transit tracking pings
  carriers.csv         FMCSA sample, slimmed, geocoded, pinned to nearest home metro

Morphs (documented, deliberate):
  - road miles = great-circle x 1.2 where FAF distance is unavailable
  - dry van $/mile = reefer $/mile x 0.85 (USDA reports reefer only)
  - index $/mile x 0.8 to strip the in-season rural produce premium
  - lanes cut to the top 100 dry + 80 reefer by volume, Chicago<->Dallas pinned

Usage:  python -I data/scripts/curate_seed.py
"""
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "processed"
SEED = ROOT / "seed"

# code: (hub city, state, [FAF zone ids], why)
METROS = {
    "CHI": ("Chicago", "IL", ["171", "181"], "usda_dest"),
    "DAL": ("Dallas", "TX", ["484"], "usda_dest"),
    "ATL": ("Atlanta", "GA", ["131"], "usda_dest"),
    "LAX": ("Los Angeles", "CA", ["061"], "usda_dest"),
    "SEA": ("Seattle", "WA", ["531"], "usda_dest"),
    "MIA": ("Miami", "FL", ["122"], "usda_dest"),
    "NYC": ("New York", "NY", ["092", "341", "363", "423"], "usda_dest"),
    "PHL": ("Philadelphia", "PA", ["101", "342", "421"], "usda_dest"),
    "BAL": ("Baltimore", "MD", ["241"], "usda_dest"),
    "BOS": ("Boston", "MA", ["251", "331", "441"], "usda_dest"),
    "HOU": ("Houston", "TX", ["486"], "hub"),
    "PHX": ("Phoenix", "AZ", ["041"], "hub"),
    "DEN": ("Denver", "CO", ["081"], "hub"),
    "MCI": ("Kansas City", "MO", ["201", "291"], "hub"),
    "STL": ("St. Louis", "MO", ["172", "292"], "hub"),
    "MEM": ("Memphis", "TN", ["471"], "hub"),
    "BNA": ("Nashville", "TN", ["472"], "hub"),
    "IND": ("Indianapolis", "IN", ["182"], "hub"),
    "CMH": ("Columbus", "OH", ["393"], "hub"),
    "DTW": ("Detroit", "MI", ["261"], "hub"),
    "MSP": ("Minneapolis", "MN", ["271"], "hub"),
    "MCO": ("Orlando", "FL", ["123"], "hub"),
    "CLT": ("Charlotte", "NC", ["371"], "hub"),
    "SFO": ("San Francisco", "CA", ["064"], "produce_gateway"),
    "FAT": ("Fresno", "CA", ["065"], "produce_gateway"),
    "LRD": ("Laredo", "TX", ["487"], "produce_gateway"),
}

# USDA origin district -> representative shipping city
USDA_ORIGINS = {
    "IMPERIAL, COACHELLA VALLEYS CA, CENTRAL AND WESTERN AZ, MEXICO CROSSINGS THROUGH CALEXICO AND SAN LUIS": ("El Centro", "CA"),
    "KERN DISTRICT CALIFORNIA": ("Bakersfield", "CA"),
    "OXNARD DISTRICT CALIFORNIA": ("Oxnard", "CA"),
    "SALINAS-WATSONVILLE CALIFORNIA": ("Salinas", "CA"),
    "SANTA MARIA CALIFORNIA": ("Santa Maria", "CA"),
    "SOUTH AND CENTRAL DISTRICT CALIFORNIA": ("Fresno", "CA"),
    "DELAWARE, MARYLAND AND EASTERN SHORE VIRGINIA": ("Salisbury", "MD"),
    "VIDALIA DISTRICT GEORGIA": ("Vidalia", "GA"),
    "GEORGIA": ("Tifton", "GA"),
    "NORTH CAROLINA": ("Raleigh", "NC"),
    "MEXICO CROSSINGS THROUGH SOUTH TEXAS": ("McAllen", "TX"),
    "MEXICO CROSSINGS THROUGH NOGALES ARIZONA": ("Nogales", "AZ"),
    "YAKIMA VALLEY AND WENATCHEE DISTRICT WASHINGTON": ("Yakima", "WA"),
    "CENTRAL AND SOUTH FLORIDA": ("Lakeland", "FL"),
    "SAN LUIS VALLEY COLORADO": ("Monte Vista", "CO"),
    "NEW YORK": ("Rochester", "NY"),
}

CIRCUITY = 1.2
DRY_FACTOR = 0.85
PRODUCE_PREMIUM = 0.8     # USDA rates are rural produce origins in season; scale to general-market level
TOP_LANES = {"dry": 100, "reefer": 80}
PINNED_LANES = {"CHI-DAL-R", "CHI-DAL-V", "DAL-CHI-R", "DAL-CHI-V"}
MIN_LANE_LOADS = 300      # per year, after merging zone parts
MIN_RATE_MILES = 300      # short hauls give nonsense $/mile
WAYPOINT_MIN_SQMI = 8     # skip hamlets
WAYPOINT_MAX_OFF_MI = 25
PINNED_WAYPOINTS = [("Joliet", "IL")]  # load 4521's last ping (roadmap demo fixtures)


def read(name):
    with (PROC / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write(name, rows):
    SEED.mkdir(exist_ok=True)
    with (SEED / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"{name}: {len(rows)} rows")


def miles(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def main():
    cities = read("cities.csv")
    by_name = {}
    for c in cities:  # keep the largest place when names collide
        k = (c["city"].lower(), c["state"])
        if k not in by_name or float(c["land_sqmi"]) > float(by_name[k]["land_sqmi"]):
            by_name[k] = c
    for c in cities:  # aliases for consolidated cities, e.g. "Nashville-Davidson metropolitan government (balance)"
        short = c["city"].split(" (")[0].split("-")[0]
        for s in (" metropolitan government", " consolidated government", " unified government", " city"):
            short = short.removesuffix(s)
        by_name.setdefault((short.lower(), c["state"]), c)

    def geo(city, state):
        c = by_name.get((city.lower(), state))
        return (float(c["lat"]), float(c["lon"])) if c else None

    # metros
    metro_geo, zone_to_metro, metro_rows = {}, {}, []
    for code, (city, st, zones, why) in METROS.items():
        metro_geo[code] = geo(city, st)
        assert metro_geo[code], f"no coords for {city}, {st}"
        for z in zones:
            zone_to_metro[z] = code
        metro_rows.append({"metro": code, "city": city, "state": st, "lat": metro_geo[code][0],
                           "lon": metro_geo[code][1], "faf_zones": "|".join(zones), "role": why})
    write("metros.csv", metro_rows)

    # lanes: merge FAF zone parts into metros
    agg = defaultdict(lambda: {"ktons": 0.0, "value": 0.0, "tmiles": 0.0})
    for r in read("lane_flows.csv"):
        o, d = zone_to_metro.get(r["orig_zone"]), zone_to_metro.get(r["dest_zone"])
        if not o or not d or o == d:
            continue
        a = agg[(o, d, r["equipment"])]
        a["ktons"] += float(r["ktons_2024"])
        a["value"] += float(r["value_musd_2024"])
        a["tmiles"] += float(r["mtonmiles_2024"])
    lanes = []
    for (o, d, eq), a in agg.items():
        loads = round(a["ktons"] * 1000 / 20)
        if loads < MIN_LANE_LOADS:
            continue
        lanes.append({"lane_id": f"{o}-{d}-{'R' if eq == 'reefer' else 'V'}", "orig": o, "dest": d,
                      "equipment": "reefer" if eq == "reefer" else "dry_van",
                      "miles": round(a["tmiles"] / a["ktons"] * 1000),
                      "annual_loads_2024": loads, "annual_ktons_2024": round(a["ktons"], 1),
                      "value_per_ton_usd": round(a["value"] / a["ktons"] * 1000)})
    lanes.sort(key=lambda x: -x["annual_loads_2024"])
    keep, count = [], defaultdict(int)
    for l in lanes:
        eq = "reefer" if l["equipment"] == "reefer" else "dry"
        if count[eq] < TOP_LANES[eq] or l["lane_id"] in PINNED_LANES:
            keep.append(l)
            count[eq] += 1
    lanes = keep
    write("lanes.csv", lanes)

    # rate observations ($/mile) and weekly index
    obs = []
    for r in read("reefer_rates.csv"):
        og = USDA_ORIGINS.get(r["origin_district"])
        dg = geo(r["destination"], {"Atlanta": "GA", "Baltimore": "MD", "Boston": "MA", "Chicago": "IL",
                                    "Dallas": "TX", "Los Angeles": "CA", "Miami": "FL", "New York": "NY",
                                    "Philadelphia": "PA", "Seattle": "WA"}[r["destination"]])
        if not og or not geo(*og):
            continue
        mi = round(miles(geo(*og), dg) * CIRCUITY)
        if mi < MIN_RATE_MILES:
            continue
        lo, hi = (r["mostly_low"], r["mostly_high"]) if r["mostly_low"] else (r["rate_low"], r["rate_high"])
        per_load = (int(lo) + int(hi)) / 2
        obs.append({"week": r["price_date"], "origin_city": f"{og[0]}, {og[1]}",
                    "destination": r["destination"], "availability": r["availability"],
                    "rate_per_load": per_load, "miles": mi, "rate_per_mile": round(per_load / mi, 2),
                    "source": f"USDA FVWTRK {r['source_file']}"})
    write("rate_observations.csv", obs)

    weekly = defaultdict(list)
    for o in obs:
        weekly[(o["week"], o["destination"])].append(o)
        weekly[(o["week"], "NATIONAL")].append(o)
    index = []
    for (week, dest), rows in sorted(weekly.items()):
        rpm = statistics.median(x["rate_per_mile"] for x in rows) * PRODUCE_PREMIUM
        avail = statistics.mode(x["availability"] for x in rows)
        index.append({"week": week, "market": dest, "reefer_rpm": round(rpm, 2),
                      "dry_van_rpm": round(rpm * DRY_FACTOR, 2), "availability": avail,
                      "n_obs": len(rows)})
    write("rate_index.csv", index)

    # waypoints along lane corridors (straight line, sampled)
    big = [c for c in cities if float(c["land_sqmi"]) >= WAYPOINT_MIN_SQMI]
    big_geo = [(float(c["lat"]), float(c["lon"]), c) for c in big]
    seen, waypoints = set(), []
    for pair in {(l["orig"], l["dest"]) for l in lanes}:
        a, b = metro_geo[pair[0]], metro_geo[pair[1]]
        for i in range(1, 12):
            t = i / 12
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            cand = [(miles(p, (la, lo)), c) for la, lo, c in big_geo
                    if abs(la - p[0]) < 0.5 and abs(lo - p[1]) < 0.6]
            if not cand:
                continue
            d, c = min(cand, key=lambda x: x[0])
            if d <= WAYPOINT_MAX_OFF_MI and c["geoid"] not in seen:
                seen.add(c["geoid"])
                waypoints.append({"geoid": c["geoid"], "city": c["city"], "state": c["state"],
                                  "lat": c["lat"], "lon": c["lon"]})
    for city, st in PINNED_WAYPOINTS:  # demo fixtures reference these by name
        c = by_name[(city.lower(), st)]
        if c["geoid"] not in seen:
            seen.add(c["geoid"])
            waypoints.append({"geoid": c["geoid"], "city": c["city"], "state": c["state"],
                              "lat": c["lat"], "lon": c["lon"]})
    write("waypoints.csv", waypoints)

    # carriers (unmatched city -> largest city in its state)
    state_anchor = {}
    for c in cities:
        if c["state"] not in state_anchor or float(c["land_sqmi"]) > float(state_anchor[c["state"]]["land_sqmi"]):
            state_anchor[c["state"]] = c
    out = []
    for c in read("carriers.csv"):
        g = geo(c["phy_city"].title(), c["phy_state"])
        if not g and c["phy_state"] in state_anchor:
            a = state_anchor[c["phy_state"]]
            g = (float(a["lat"]), float(a["lon"]))
        home = min(metro_geo, key=lambda m: miles(g, metro_geo[m])) if g else None
        out.append({
            "dot_number": c["dot_number"], "mc_number": c["docket1"],
            "legal_name": c["legal_name"], "dba_name": c["dba_name"],
            "city": c["phy_city"].title(), "state": c["phy_state"],
            "lat": g[0] if g else "", "lon": g[1] if g else "", "home_metro": home or "",
            "equipment": "reefer" if c["equipment"] == "reefer" else "dry_van",
            "power_units": c["power_units"], "drivers": c["total_drivers"],
            "safety_rating": {"S": "satisfactory", "C": "conditional", "U": "unsatisfactory"}.get(c["safety_rating"], "unrated"),
            "safety_rating_date": c["safety_rating_date"][:10],
            "recordable_crash_rate": c["recordable_crash_rate"],
            "authority_since": c["add_date"][:10], "hazmat": c["hm_ind"] == "Y",
        })
    write("carriers.csv", out)


if __name__ == "__main__":
    main()
