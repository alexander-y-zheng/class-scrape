"""Build buildings.csv: Banner building code -> latitude/longitude.

Banner only gives building codes and names, so coordinates come from
OpenStreetMap (OSM). Names are matched exactly, or via ALIASES where Banner and
OSM disagree; MANUAL covers buildings OSM doesn't have. buildings.csv is
committed, so this only needs re-running when a new term uses a new building.

Usage:
    python buildings.py                       # query OSM's Overpass API
    python buildings.py --osm-file osm.json   # reuse a saved Overpass response
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time

import requests

OVERPASS = "https://overpass-api.de/api/interpreter"
BBOX = (42.330, -71.100, 42.348, -71.078)  # main Boston campus + surroundings
QUERY = """[out:json][timeout:60];
(way["building"]["name"]({b});relation["building"]["name"]({b}););
out center tags;""".format(b=",".join(map(str, BBOX)))

# Banner code -> OSM building name, where the names differ.
ALIASES = {
    "BK": "Behrakis Health Sciences Center",
    "CN": "Cullinane Hall",
    "DK": "Northeastern University School of Law-Dockser Hall -Nulawlab",
    "EV": "East Village Residence Hall",
    "EXP": "Northeastern EXP",
    "ISEC": "Interdisciplinary Science & Engineering Complex",
    "MU": "Mugar Life Sciences Building",
    "RG": "Ruggles",
    "WVG": "West Villlage G",  # sic - typo in OSM
    "271": "271 Huntington",
}

# Buildings OSM lacks: code -> (lat, lon), or None if still unknown.
# HS = "Hastings Suite" in Banner, also known as Hastings Hall.
MANUAL: dict[str, tuple[float, float] | None] = {
    "HS": None,
}

# Codes that are not physical buildings.
NOT_BUILDINGS = {"BOS", "VTL", "XCR"}


def fetch_osm() -> dict:
    for attempt in range(5):
        try:
            r = requests.post(OVERPASS, data={"data": QUERY}, timeout=120,
                              headers={"User-Agent": "class-scrape building lookup"})
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            wait = 10 * 2 ** attempt
            print(f"Overpass failed ({e}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    sys.exit("Overpass unavailable - try later or pass --osm-file")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/classes.db")
    ap.add_argument("--osm-file", help="saved Overpass JSON response")
    ap.add_argument("--out", default="buildings.csv")
    args = ap.parse_args()

    osm = json.load(open(args.osm_file)) if args.osm_file else fetch_osm()
    by_name: dict[str, dict] = {}
    for e in osm["elements"]:
        if "center" in e:
            by_name.setdefault(e["tags"]["name"].lower(), e)

    db = sqlite3.connect(args.db)
    codes = db.execute("""SELECT building, MAX(building_desc) FROM meetings
                          WHERE building IS NOT NULL GROUP BY building""").fetchall()

    out, missing = [], []
    for code, desc in sorted(codes):
        if code in NOT_BUILDINGS:
            continue
        row = {"code": code, "banner_name": desc, "osm_name": "", "osm_id": "",
               "lat": "", "lon": ""}
        if code in MANUAL:
            if MANUAL[code]:
                row["lat"], row["lon"] = MANUAL[code]
                row["osm_name"] = "(manual)"
            else:
                missing.append(f"{code} {desc} (in MANUAL with no coordinates)")
        else:
            e = by_name.get(ALIASES.get(code, desc).lower())
            if e:
                row.update(osm_name=e["tags"]["name"], osm_id=f"{e['type']}/{e['id']}",
                           lat=round(e["center"]["lat"], 6),
                           lon=round(e["center"]["lon"], 6))
            else:
                missing.append(f"{code} {desc} (no OSM match - add to ALIASES/MANUAL)")
        out.append(row)

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"Wrote {len(out)} buildings to {args.out}")
    for m in missing:
        print("  no coordinates:", m)


if __name__ == "__main__":
    main()
