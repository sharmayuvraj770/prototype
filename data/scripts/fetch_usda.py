"""Fetch + parse USDA AMS Specialty Crops National Truck Rate Report (FVWTRK).

Weekly PDF, published Wednesdays (prices for the Tuesday before). Spot reefer
rates in USD per load (48-53 ft trailer, broker fees included, fuel included),
from produce origin districts to 10 destination cities.

Sources tried per week, in order:
  1. Barchart S3 mirror of the official PDF (reachable outside the US)
  2. www.ams.usda.gov/mnreports/fvwtrk.pdf (latest only; may be geo-blocked)

Usage:  python -I data/scripts/fetch_usda.py [--weeks 26]
Needs:  pdfplumber
Writes: data/raw/usda/YYYYMMDD-fvwtrk.pdf, data/processed/reefer_rates.csv
"""
import argparse
import csv
import datetime as dt
import re
import urllib.error
import urllib.request
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "usda"
OUT = ROOT / "processed" / "reefer_rates.csv"
MIRROR = "https://s3.amazonaws.com/barchart-news-store-prod/external_links/USDA/{d}-fvwtrk.pdf"

DESTS = ["Atlanta", "Baltimore", "Boston", "Chicago", "Dallas", "Los Angeles",
         "Miami", "New York", "Philadelphia", "Seattle"]
AVAIL = ["Slight Surplus", "Slight Shortage", "Surplus", "Adequate", "Shortage"]
ROW = re.compile(
    rf"^({'|'.join(DESTS)})\s+({'|'.join(AVAIL)})\s+(\d+)-(\d+)"
    r"(?:\s+(\d+)-(\d+))?(?:\s+\(([+-]?\d*)\))?\s*$"
)
PRICE_DATE = re.compile(r"PRICES FOR \w+ (\w+ \d+, \d{4})")
NOISE = ("SPECIALTY CROPS NATIONAL", "Agricultural Marketing", "Specialty Crops Market",
         "Email us", "Page ", "USDA, AMS", "Washington, DC", "Phone (", "https://",
         "In areas where", "rate by the number", "PERCENTAGE OF CHANGE", "RANGE MOSTLY")


def download(day: dt.date) -> Path | None:
    path = RAW / f"{day:%Y%m%d}-fvwtrk.pdf"
    if path.exists():
        return path
    try:
        with urllib.request.urlopen(MIRROR.format(d=f"{day:%Y%m%d}"), timeout=60) as r:
            data = r.read()
    except urllib.error.HTTPError:
        return None
    if not data.startswith(b"%PDF"):
        return None
    path.write_bytes(data)
    return path


def parse(path: Path) -> list[dict]:
    with pdfplumber.open(path) as pdf:
        lines = [ln.strip() for pg in pdf.pages for ln in (pg.extract_text() or "").splitlines()]
    rows, origin_buf, origin, commodity, season_flag = [], [], None, None, ""
    in_prices, in_commodity, price_date = False, False, None
    for ln in lines:
        if not in_prices:
            m = PRICE_DATE.search(ln)
            if m:
                in_prices = True
                price_date = dt.datetime.strptime(m.group(1), "%B %d, %Y").date()
            continue
        if ln.startswith("TRUCK SHIPMENTS"):
            break
        if ln.startswith("RANGE MOSTLY"):
            in_commodity = False
            continue
        if any(ln.startswith(n) for n in NOISE) or not ln:
            continue
        m = ROW.match(ln)
        if m:
            dest, avail, lo, hi, mlo, mhi, pct = m.groups()
            rows.append({
                "price_date": price_date.isoformat(), "origin_district": origin,
                "commodities": commodity, "destination": dest, "availability": avail,
                "rate_low": int(lo), "rate_high": int(hi),
                "mostly_low": int(mlo) if mlo else "", "mostly_high": int(mhi) if mhi else "",
                "pct_change_wow": pct if pct not in (None, "") else "",
                "season_flag": season_flag,
                "source_file": path.name,
            })
            continue
        if ln.startswith("--"):
            if origin_buf:
                origin, origin_buf = " ".join(origin_buf), []
                season_flag = ""
                for flag in ("FIRST REPORT", "LAST REPORT"):
                    if origin.endswith(" " + flag):
                        origin, season_flag = origin[: -len(flag) - 1], flag.lower()
            commodity, in_commodity = ln[2:].strip(), True
        elif in_commodity:
            commodity += " " + ln
        elif ln.isupper():
            origin_buf.append(ln)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=26)
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    wed = today - dt.timedelta(days=(today.weekday() - 2) % 7)
    all_rows = []
    for i in range(args.weeks):
        day = wed - dt.timedelta(weeks=i)
        # Holiday weeks sometimes publish a day late.
        path = download(day) or download(day + dt.timedelta(days=1))
        if not path:
            print(f"{day}: not found")
            continue
        rows = parse(path)
        print(f"{path.name}: {len(rows)} rate rows")
        all_rows += rows
    all_rows.sort(key=lambda r: (r["price_date"], r["origin_district"] or "", r["destination"]))
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote {len(all_rows)} rows -> {OUT}")


if __name__ == "__main__":
    main()
