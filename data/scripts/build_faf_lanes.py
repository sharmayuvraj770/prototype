"""Build truck lane volumes from BTS/FHWA Freight Analysis Framework FAF5.7.1 (2018-2024).

Streams the CSV straight out of the zip (695 MB uncompressed, never extracted),
keeps domestic truck flows between different FAF zones of 250+ miles (truckload
brokerage territory), and aggregates by origin zone x destination zone x
equipment group.

Equipment group is a proxy from commodity (SCTG2), not something FAF reports:
  reefer = 01 live animals/fish, 03 other ag products (produce), 05 meat/seafood,
           07 other prepared foodstuffs
  dry    = everything else
Average lane distance = ton-miles / tons (FAF units: tmiles in millions, tons in thousands).

Download first (192 MB):
  https://faf.ornl.gov/faf5/Data/Download_Files/FAF5.7.1_2018-2024.zip -> data/raw/faf/
  https://faf.ornl.gov/faf5/data/FAF5_metadata.xlsx                    -> data/raw/faf/

Usage:  python -I data/scripts/build_faf_lanes.py
Needs:  openpyxl
Writes: data/processed/faf_zones.csv, data/processed/lane_flows.csv
"""
import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "faf"
ZIP = RAW / "FAF5.7.1_2018-2024.zip"
META = RAW / "FAF5_metadata.xlsx"
OUT_ZONES = ROOT / "processed" / "faf_zones.csv"
OUT_LANES = ROOT / "processed" / "lane_flows.csv"

REEFER_SCTG = {"01", "03", "05", "07"}
YEARS = ["2022", "2023", "2024"]
MIN_DIST_BAND = 3  # 250+ miles


def load_zones() -> dict[str, dict]:
    wb = openpyxl.load_workbook(META, read_only=True)
    rows = list(wb["FAF Zone (Domestic)"].iter_rows(values_only=True))[1:]
    zones = {}
    for code, short, long_ in rows:
        if not code:
            continue
        code = str(code).zfill(3)
        zones[code] = {
            "zone_id": code, "name": short, "long_name": long_,
            "state": short.rsplit(" ", 1)[-1] if " " in short else short,
            "is_remainder": short.startswith("Rest of"),
        }
    return zones


def main() -> None:
    zones = load_zones()
    agg = defaultdict(lambda: defaultdict(float))
    with zipfile.ZipFile(ZIP) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(name) as raw:
            for r in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")):
                if (r["dms_mode"] != "1" or r["trade_type"] != "1"
                        or r["dms_orig"] == r["dms_dest"]
                        or int(r["dist_band"]) < MIN_DIST_BAND):
                    continue
                group = "reefer" if r["sctg2"] in REEFER_SCTG else "dry"
                a = agg[(r["dms_orig"], r["dms_dest"], group)]
                for y in YEARS:
                    a[f"ktons_{y}"] += float(r[f"tons_{y}"])
                    a[f"value_musd_{y}"] += float(r[f"value_{y}"])
                    a[f"mtonmiles_{y}"] += float(r[f"tmiles_{y}"])

    lanes = []
    for (o, d, g), a in agg.items():
        t = a["ktons_2024"]
        if t <= 0:
            continue
        lanes.append({
            "orig_zone": o, "orig_name": zones[o]["name"],
            "dest_zone": d, "dest_name": zones[d]["name"], "equipment": g,
            **{k: round(v, 3) for k, v in a.items()},
            "avg_miles_2024": round(a["mtonmiles_2024"] / t * 1000),
            "est_truckloads_2024": round(t * 1000 / 20),  # ~20 tons per loaded truck
        })
    lanes.sort(key=lambda x: -x["ktons_2024"])

    with OUT_ZONES.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["zone_id", "name", "long_name", "state", "is_remainder"])
        w.writeheader()
        w.writerows(zones.values())
    with OUT_LANES.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(lanes[0].keys()))
        w.writeheader()
        w.writerows(lanes)
    print(f"wrote {len(zones)} zones, {len(lanes)} lanes -> {OUT_LANES}")


if __name__ == "__main__":
    main()
