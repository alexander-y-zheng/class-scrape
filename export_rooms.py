"""Export room schedules for the free-rooms web app (web/data/rooms.json).

Usage:
    python export_rooms.py                      # all terms in the DB, meetings not yet over
    python export_rooms.py --db data/classes.db --out web/data/rooms.json

Each room gets the list of times it is booked by a scheduled class or final exam:
    [days_mask, start_min, end_min, start_date, end_date, courses, kind]
days_mask uses bit 0 = Monday ... bit 6 = Sunday; minutes are from midnight
(Boston time). Cross-listed sections sharing a room and time are merged into one
booking. Meetings without a real room (online, "BOS DEPT", unassigned) are skipped.

No-class days (holidays, breaks) come from holidays.csv, which is maintained by hand
from the registrar's academic calendar, since Banner's meeting patterns ignore them:
https://registrar.northeastern.edu/article/academic-calendar/
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NOT_ROOMS = {"BOS", "VTL", "XCR"}


def to_min(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/classes.db")
    ap.add_argument("--buildings", default="buildings.csv")
    ap.add_argument("--holidays", default="holidays.csv")
    ap.add_argument("--out", default="web/data/rooms.json")
    ap.add_argument("--since", type=date.fromisoformat,
                    help="drop meetings that ended before this date (default: yesterday)")
    args = ap.parse_args()
    since = (args.since or date.today() - timedelta(days=1)).isoformat()

    db = sqlite3.connect(args.db)
    rows = db.execute(f"""
        SELECT m.building, m.building_desc, m.room, m.meeting_type, m.begin_time,
               m.end_time, m.start_date, m.end_date, s.subject_course,
               COALESCE(s.cross_list_capacity, s.max_enrollment),
               {', '.join('m.' + d for d in DAYS)}
        FROM latest_meetings m
        JOIN latest_sections s ON s.run_id = m.run_id AND s.crn = m.crn
        WHERE m.building IS NOT NULL AND m.room IS NOT NULL
          AND m.begin_time IS NOT NULL AND m.end_time IS NOT NULL
          AND m.end_date >= ?
    """, (since,)).fetchall()

    coords = {}
    with open(args.buildings, newline="") as f:
        for b in csv.DictReader(f):
            coords[b["code"]] = (float(b["lat"]), float(b["lon"])) if b["lat"] else None

    bookings: dict[tuple, dict[tuple, set]] = defaultdict(lambda: defaultdict(set))
    capacity: dict[tuple, int] = defaultdict(int)
    names: dict[str, str] = {}
    for (bcode, bdesc, room, mtype, bt, et, sd, ed, course, cap, *days) in rows:
        if bcode in NOT_ROOMS:
            continue
        names[bcode] = bdesc
        mask = sum(1 << i for i, d in enumerate(days) if d)
        if not mask:
            continue
        kind = "exam" if mtype == "FNEX" else "class"
        key = (bcode, room)
        bookings[key][(mask, to_min(bt), to_min(et), sd, ed, kind)].add(course)
        if kind == "class" and cap:
            capacity[key] = max(capacity[key], cap)

    rooms = []
    for (bcode, room), slots in sorted(bookings.items()):
        rooms.append({
            "b": bcode, "r": room, "cap": capacity.get((bcode, room)) or None,
            "m": sorted([mask, s, e, sd, ed, "/".join(sorted(c)), kind]
                        for (mask, s, e, sd, ed, kind), c in slots.items()),
        })

    with open(args.holidays, newline="") as f:
        closures = [[h["start_date"], h["end_date"], h["name"], h["note"]]
                    for h in csv.DictReader(f) if h["end_date"] >= since]

    terms = db.execute("""SELECT t.code, t.description, r.finished_at
                          FROM latest_runs r JOIN terms t ON t.code = r.term
                          WHERE EXISTS (SELECT 1 FROM meetings m
                                        WHERE m.run_id = r.run_id AND m.end_date >= ?)
                          ORDER BY t.code""", (since,)).fetchall()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "terms": [{"code": c, "name": d.replace(" (View Only)", ""), "scraped_at": s}
                  for c, d, s in terms],
        "buildings": {c: {"name": n, "ll": coords.get(c)} for c, n in sorted(names.items())},
        "closures": closures,
        "rooms": rooms,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    n_book = sum(len(r["m"]) for r in rooms)
    print(f"Wrote {out}: {len(rooms)} rooms, {n_book} bookings, "
          f"{out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
