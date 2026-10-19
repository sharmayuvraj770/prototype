"""Build a US city coordinate table from the Census Gazetteer places file.

Used to place pickup/delivery cities and in-transit tracking pings on real
coordinates (e.g. "truck last seen near Joliet, IL").

Download first (1.2 MB):
  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip
  -> data/raw/census/

Usage:  python -I data/scripts/build_cities.py
Writes: data/processed/cities.csv
"""
import csv
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP = ROOT / "raw" / "census" / "2025_Gaz_place_national.zip"
OUT = ROOT / "processed" / "cities.csv"
SUFFIXES = (" city", " town", " village", " borough", " municipality", " CDP")


def main() -> None:
    with zipfile.ZipFile(ZIP) as zf, zf.open(zf.namelist()[0]) as raw:
        rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"), delimiter="|"))
    out = []
    for r in rows:
        r = {k.strip(): v.strip() for k, v in r.items()}
        if r["FUNCSTAT"] not in ("A", "F"):  # active incorporated + consolidated (e.g. Nashville)
            continue
        name = r["NAME"]
        for s in SUFFIXES:
            if name.endswith(s):
                name = name[: -len(s)]
                break
        out.append({
            "geoid": r["GEOID"], "city": name, "state": r["USPS"],
            "lat": float(r["INTPTLAT"]), "lon": float(r["INTPTLONG"]),
            "land_sqmi": round(float(r["ALAND_SQMI"]), 2),
        })
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"wrote {len(out)} cities -> {OUT}")


if __name__ == "__main__":
    main()
