"""Scrape Northeastern's public Banner "Browse Classes" data into SQLite.

Usage:
    python scrape.py --list-terms                 # show available terms + codes
    python scrape.py --term "Fall 2026"           # scrape by (unique) description
    python scrape.py --term 202710                # or by term code
    python scrape.py --term 202710 --campus BOS --db data/classes.db

Every run is stored as a new snapshot (scrape_runs.run_id), so re-running during a
registration period builds a history of enrollment over time. The views
`latest_sections` / `latest_meetings` always point at the newest complete run per
(term, campus).

How the site works (Ellucian Banner 9, no login required):
  1. GET  classSearch/classSearch          -> session cookie
  2. POST term/search?mode=search term=..  -> binds the term to the session
  3. GET  searchResults/searchResults      -> paged JSON of sections
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://nubanner.neu.edu/StudentRegistrationSsb/ssb"
PAGE_SIZE = 100  # old terms time out server-side on large pages; 100 is safe
DELAY_S = 0.5  # pause between requests, to be polite
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS terms (
    code        TEXT PRIMARY KEY,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    term          TEXT NOT NULL,
    campus        TEXT NOT NULL,
    started_at    TEXT NOT NULL,   -- UTC ISO-8601
    finished_at   TEXT,            -- NULL if the run failed part-way
    section_count INTEGER
);

-- One row per section (CRN) per run. Cross-listed courses appear once per course
-- code; cross_list groups them and cross_list_count is their combined enrollment.
CREATE TABLE IF NOT EXISTS sections (
    run_id               INTEGER NOT NULL REFERENCES scrape_runs(run_id),
    term                 TEXT NOT NULL,
    crn                  TEXT NOT NULL,
    subject              TEXT,
    subject_description  TEXT,
    course_number        TEXT,
    subject_course       TEXT,    -- e.g. ACCT1201
    section              TEXT,    -- e.g. 01
    title                TEXT,
    schedule_type        TEXT,    -- Lecture, Lab, Seminar, ...
    instructional_method TEXT,    -- Traditional, Hybrid, ...
    part_of_term         TEXT,    -- 1 = full term, 2A/2B = half terms, 1L = 1L Law
    campus               TEXT,
    credit_hours_low     REAL,
    credit_hours_high    REAL,
    enrollment           INTEGER,
    max_enrollment       INTEGER,
    seats_available      INTEGER,
    wait_count           INTEGER,
    wait_capacity        INTEGER,
    cross_list           TEXT,
    cross_list_count     INTEGER,
    cross_list_capacity  INTEGER,
    open_section         INTEGER,
    attributes           TEXT,    -- JSON list of {code, description}
    raw_json             TEXT,    -- full API record, for fields not broken out yet
    PRIMARY KEY (run_id, crn)
);

-- One row per meeting pattern. A section can have several (e.g. lecture + a
-- different-room meeting, or a final exam row with meeting_type = 'FNEX').
CREATE TABLE IF NOT EXISTS meetings (
    run_id            INTEGER NOT NULL REFERENCES scrape_runs(run_id),
    crn               TEXT NOT NULL,
    seq               INTEGER NOT NULL,  -- order within the section
    meeting_type      TEXT,   -- CLAS, LAB, FNEX (final exam), ...
    meeting_type_desc TEXT,
    start_date        TEXT,   -- YYYY-MM-DD
    end_date          TEXT,
    begin_time        TEXT,   -- HH:MM (24h), NULL if unscheduled
    end_time          TEXT,
    monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER,
    friday INTEGER, saturday INTEGER, sunday INTEGER,
    hours_week        REAL,
    campus            TEXT,
    building          TEXT,
    building_desc     TEXT,
    room              TEXT,
    PRIMARY KEY (run_id, crn, seq)
);

CREATE INDEX IF NOT EXISTS idx_sections_term ON sections(term);
CREATE INDEX IF NOT EXISTS idx_runs_term ON scrape_runs(term, campus);

DROP VIEW IF EXISTS latest_runs;
CREATE VIEW latest_runs AS
    SELECT r.* FROM scrape_runs r
    WHERE r.finished_at IS NOT NULL
      AND r.run_id = (SELECT MAX(run_id) FROM scrape_runs r2
                      WHERE r2.term = r.term AND r2.campus = r.campus
                        AND r2.finished_at IS NOT NULL);

DROP VIEW IF EXISTS latest_sections;
CREATE VIEW latest_sections AS
    SELECT s.* FROM sections s JOIN latest_runs USING (run_id);

DROP VIEW IF EXISTS latest_meetings;
CREATE VIEW latest_meetings AS
    SELECT m.* FROM meetings m JOIN latest_runs USING (run_id);
"""


class BannerClient:
    def __init__(self, delay: float = DELAY_S):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "class-scrape (personal research)"
        self.delay = delay

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        for attempt in range(5):
            try:
                r = self.s.request(method, f"{BASE}/{path}", timeout=120, **kw)
                r.raise_for_status()
                time.sleep(self.delay)
                return r
            except requests.RequestException as e:
                wait = 2 ** attempt * 2
                print(f"  request failed ({e}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
        raise RuntimeError(f"giving up on {path}")

    def terms(self) -> list[dict]:
        r = self._req("GET", "classSearch/getTerms",
                      params={"searchTerm": "", "offset": 1, "max": 1000})
        return r.json()

    def set_term(self, term: str) -> None:
        self._req("GET", "classSearch/classSearch")
        self._req("POST", "classSearch/resetDataForm")
        self._req("POST", "term/search", params={"mode": "search"}, data={"term": term})

    def search_page(self, term: str, campus: str, offset: int) -> dict:
        params = {
            "txt_term": term, "txt_campus": campus,
            "pageOffset": offset, "pageMaxSize": PAGE_SIZE,
            "sortColumn": "courseReferenceNumber", "sortDirection": "asc",
        }
        for attempt in range(4):
            d = self._req("GET", "searchResults/searchResults", params=params).json()
            if d.get("success") and d.get("data") is not None:
                return d
            if d.get("totalCount") == 0:
                return d
            wait = 2 ** attempt * 3
            print(f"  page {offset} returned success=false; retrying in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
        raise RuntimeError(f"page at offset {offset} kept failing")

    def all_sections(self, term: str, campus: str) -> list[dict]:
        self.set_term(term)
        first = self.search_page(term, campus, 0)
        total = first.get("totalCount") or 0
        rows = list(first.get("data") or [])
        print(f"  {total} sections reported")
        offset = PAGE_SIZE
        while offset < total:
            rows += self.search_page(term, campus, offset).get("data") or []
            print(f"  fetched {min(offset + PAGE_SIZE, total)}/{total}", end="\r")
            offset += PAGE_SIZE
        print()
        unique = {r["courseReferenceNumber"] for r in rows}
        if len(unique) != total or len(rows) != total:
            raise RuntimeError(
                f"expected {total} sections, got {len(rows)} rows / {len(unique)} unique "
                "CRNs - pagination drifted; re-run")
        return rows


def resolve_term(terms: list[dict], arg: str) -> dict:
    by_code = {t["code"]: t for t in terms}
    if arg in by_code:
        return by_code[arg]
    hits = [t for t in terms if arg.lower() in t["description"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"No term matches {arg!r}. Use --list-terms.")
    listing = "\n".join(f"  {t['code']}  {t['description']}" for t in hits)
    sys.exit(f"{arg!r} is ambiguous:\n{listing}\nPass the exact code instead.")


def iso_date(mdy: str | None) -> str | None:
    return datetime.strptime(mdy, "%m/%d/%Y").date().isoformat() if mdy else None


def hhmm(t: str | None) -> str | None:
    return f"{t[:2]}:{t[2:]}" if t else None


def save(db: sqlite3.Connection, run_id: int, term: str, rows: list[dict]) -> None:
    sec_rows, mtg_rows = [], []
    for r in rows:
        crn = r["courseReferenceNumber"]
        attrs = [{"code": a["code"], "description": a["description"]}
                 for a in (r.get("sectionAttributes") or [])]
        sec_rows.append((
            run_id, term, crn, r["subject"], r["subjectDescription"], r["courseNumber"],
            r["subjectCourse"], r["sequenceNumber"], r["courseTitle"],
            r["scheduleTypeDescription"], r["instructionalMethodDescription"],
            r["partOfTerm"], r["campusDescription"],
            r.get("creditHourLow"), r.get("creditHourHigh"),
            r["enrollment"], r["maximumEnrollment"], r["seatsAvailable"],
            r["waitCount"], r["waitCapacity"],
            r.get("crossList"), r.get("crossListCount"), r.get("crossListCapacity"),
            int(bool(r.get("openSection"))), json.dumps(attrs), json.dumps(r),
        ))
        for seq, mf in enumerate(r.get("meetingsFaculty") or []):
            m = mf["meetingTime"]
            mtg_rows.append((
                run_id, crn, seq, m.get("meetingType"), m.get("meetingTypeDescription"),
                iso_date(m.get("startDate")), iso_date(m.get("endDate")),
                hhmm(m.get("beginTime")), hhmm(m.get("endTime")),
                *(int(bool(m.get(d))) for d in DAYS),
                m.get("hoursWeek"), m.get("campus"), m.get("building"),
                m.get("buildingDescription"), m.get("room"),
            ))
    db.executemany(f"INSERT INTO sections VALUES ({','.join('?' * 26)})", sec_rows)
    db.executemany(f"INSERT INTO meetings VALUES ({','.join('?' * 21)})", mtg_rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--term", action="append",
                    help="term code (202710) or description ('Fall 2026'); repeatable")
    ap.add_argument("--latest", type=int, metavar="N",
                    help="scrape the N newest terms (e.g. for scheduled jobs)")
    ap.add_argument("--campus", default="BOS", help="campus code (default BOS)")
    ap.add_argument("--db", default="data/classes.db", help="SQLite path")
    ap.add_argument("--list-terms", action="store_true")
    args = ap.parse_args()

    client = BannerClient()
    terms = client.terms()
    if args.list_terms:
        for t in terms:
            print(f"{t['code']}  {t['description']}")
        return
    if not args.term and not args.latest:
        ap.error("--term or --latest is required (or use --list-terms)")
    targets = [resolve_term(terms, t) for t in args.term or []]
    if args.latest:  # the API lists terms newest first
        targets += sorted(terms, key=lambda t: t["code"], reverse=True)[:args.latest]
    targets = list({t["code"]: t for t in targets}.values())

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.db)
    db.executescript(SCHEMA)
    db.executemany("INSERT OR REPLACE INTO terms VALUES (?, ?)",
                   [(t["code"], t["description"]) for t in terms])
    db.commit()
    for term in targets:
        scrape_term(client, db, term, args.campus, args.db)


def scrape_term(client: BannerClient, db: sqlite3.Connection, term: dict,
                campus: str, db_path: str) -> None:
    print(f"Scraping {term['description']} ({term['code']}), campus {campus}")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = db.execute(
        "INSERT INTO scrape_runs (term, campus, started_at) VALUES (?, ?, ?)",
        (term["code"], campus, started)).lastrowid
    db.commit()

    rows = client.all_sections(term["code"], campus)
    with db:
        save(db, run_id, term["code"], rows)
        db.execute("UPDATE scrape_runs SET finished_at = ?, section_count = ? "
                   "WHERE run_id = ?",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    len(rows), run_id))
    print(f"Saved run {run_id}: {len(rows)} sections -> {db_path}")


if __name__ == "__main__":
    main()
