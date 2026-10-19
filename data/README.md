# Data — sources and pipeline

Real public data that grounds the synthetic Northstar TMS seed (loads, events, quotes).
`raw/` = downloads, `processed/` = full parsed tables (both gitignored, rebuildable).
**`seed/` = curated MVP world (272 KB, committed)** — the only thing the app reads.

## Seed (what the MVP uses)

| File | Rows | Feeds |
|---|---|---|
| `seed/metros.csv` | 26 | the 10 USDA destination cities, 13 freight hubs, 3 produce gateways (SFO, FAT, LRD) |
| `seed/lanes.csv` | 181 | top 100 dry + 80 reefer metro lanes by 2024 FAF volume, Chicago↔Dallas always kept; used by `quote_lane`, `find_carrier_capacity`, load generator |
| `seed/rate_index.csv` | 286 | weekly reefer/dry $/mile per market + national; used by `quote_lane` |
| `seed/rate_observations.csv` | 1,964 | USDA rows converted to $/mile, the evidence behind the index, cited as source |
| `seed/waypoints.csv` | 501 | real towns along lane corridors, used for tracking pings (+ pinned demo towns, e.g. Joliet IL) |
| `seed/carriers.csv` | 200 | slimmed FMCSA carriers with coordinates and home metro; used by `get_carrier_profile` and capacity |

Deliberate morphs, listed in `scripts/curate_seed.py`: dry van = reefer × 0.85; index × 0.8 to remove the in-season produce premium; miles = great-circle × 1.2 where FAF has no distance. Result: Chicago→Dallas reefer is about $3.23/mi × 955 mi, roughly $3.1k all-in.

## Full sources (intermediate, gitignored)

| Output | Source | How fetched | Rows |
|---|---|---|---|
| `processed/carriers.csv` | FMCSA Company Census File, Socrata `az4n-8mr2` on data.transportation.gov (refreshed daily) | SODA API, filtered server-side | 200 |
| `processed/lane_flows.csv` | BTS/FHWA Freight Analysis Framework FAF5.7.1, 2018–2024 annual (faf.ornl.gov) | 192 MB zip, streamed | 26,419 |
| `processed/faf_zones.csv` | FAF5 metadata workbook | xlsx | 132 |
| `processed/reefer_rates.csv` | USDA AMS Specialty Crops National Truck Rate Report (FVWTRK), weekly PDF | Barchart S3 mirror of the official PDF, parsed | ~2,000 (26 weeks) |
| `processed/cities.csv` | US Census 2025 Gazetteer, places | 1.2 MB zip | 19,469 |

### FMCSA carriers
- Filter: active, interstate, authorized for hire, MC docket, 5–500 power units, located in one of 22 lane states.
- `equipment` = `reefer` if cargo includes cold food, produce or meat. Otherwise `dryvan` (general freight only). FMCSA has no trailer-type field, so this is a proxy.
- The sample is stratified by safety rating (S = Satisfactory, C = Conditional, U = Unsatisfactory, blank = unrated) so compliance answers vary. Seed 4521 makes it reproducible.
- **PII is dropped at the source.** We do not fetch phone, email, street address or officer names.

### FAF5 lanes
- Kept: domestic truck flows (`dms_mode=1`, `trade_type=1`) between different zones, 250 miles or more.
- `equipment` comes from commodity: SCTG 01/03/05/07 (live animals, produce, meat, prepared foods) = reefer. Everything else = dry.
- `avg_miles_2024` = ton-miles ÷ tons. `est_truckloads_2024` assumes 20 tons per load.
- Example: Chicago IL → Dallas TX reefer is about 954 mi and about 15.5k loads in 2024.

### USDA reefer rates
- Spot rates in USD **per load**, 48–53 ft reefer, broker fees and fuel included. Published Wednesdays for the prior Tuesday.
- Origins are **produce districts** (Salinas, South Texas crossings, Yakima, …). Destinations are 10 cities, including Chicago and Dallas.
  So there is no Chicago→Dallas row. `quote_lane` must derive $/mile from these rows using FAF distances, and must label the result indicative.
- `www.ams.usda.gov` / `mymarketnews.ams.usda.gov` time out from outside the US. The S3 mirror serves the identical PDF.

## Rebuild

```bash
pip install -r data/requirements.txt
python -I data/scripts/fetch_fmcsa.py            # ~1 min, Socrata API
python -I data/scripts/fetch_usda.py --weeks 26  # ~1 min
# download FAF zip + metadata and Gazetteer zip (URLs in each script's docstring), then:
python -I data/scripts/build_faf_lanes.py        # ~3 min, streams 695 MB CSV
python -I data/scripts/build_cities.py
python -I data/scripts/curate_seed.py            # -> data/seed/
```

## Licensing
FMCSA, FAF (BTS/FHWA), USDA AMS and Census data are US federal government works: public domain, no restrictions.
