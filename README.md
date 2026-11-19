# class-scrape

Scrapes Northeastern's public course registration system ([Banner "Browse Classes"](https://nubanner.neu.edu/StudentRegistrationSsb/ssb/registration), no login needed) into SQLite, and analyzes when and where Boston classes meet. It also powers **Open Rooms Finder** (`web/`), a small site that lists classrooms with no class scheduled.

> **Disclaimer:** This is an unofficial personal project. It is not affiliated with, endorsed by, or maintained by Northeastern University. All data comes from the publicly accessible class schedule and may be incomplete or out of date. See [Disclaimer](#disclaimer).

## Setup

```sh
python -m venv .venv
.venv\Scripts\activate          # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## Usage

```sh
python scrape.py --list-terms             # available terms and their codes
python scrape.py --term "Fall 2026"       # or --term 202710; default campus is BOS
python heatmap.py --term 202710           # figures/heatmap_202710.png (+ .csv)
python campus_map.py --term 202710        # figures/campus_map_202710.html
python buildings.py                       # rebuild buildings.csv (only for new buildings)
```

Each `scrape.py` run is saved as a new snapshot. Re-running it during a registration period builds a history of seat counts. `--term` can be repeated, and `--latest N` scrapes the N newest terms.

## Open Rooms Finder (`web/`)

A static site with no backend. `export_rooms.py` writes every room's class and final-exam times to `web/data/rooms.json`. The page then works out, in the browser and in Boston time, which rooms have no class at a given moment.

Run it locally:

```sh
python scrape.py --latest 4
python export_rooms.py
python -m http.server 8000 -d web        # open http://localhost:8000
```

Deployment: [.github/workflows/pages.yml](.github/workflows/pages.yml) runs daily, on manual trigger, and on pushes that touch the site. Each run scrapes the newest terms, exports the room data and publishes `web/` to GitHub Pages. The generated `rooms.json` is not committed. One-time setup: **Settings → Pages → Source: GitHub Actions**.

## Data

`data/classes.db` (not committed) has these tables:

| Table | Contents |
|---|---|
| `sections` | One row per section (CRN) per run: course code, title, schedule type, part of term, enrollment, capacity, waitlist, cross-list group, and the raw API record |
| `meetings` | One row per meeting time: days, start/end time, start/end date, building, room. `meeting_type = 'FNEX'` rows are final exams |
| `scrape_runs` | When each snapshot was taken |
| `terms` | Term codes and their names |

`latest_sections` and `latest_meetings` are views that show the newest complete run for each term.

`buildings.csv` maps Banner building codes to coordinates from OpenStreetMap, with manual fixes in `buildings.py`.

`holidays.csv` lists Boston no-class days (holidays and breaks), copied by hand from the [academic calendar](https://registrar.northeastern.edu/article/academic-calendar/). Banner's weekly meeting patterns don't account for them. Update it when the registrar posts a new calendar. It currently covers Fall 2026 through Summer 2027 (2026–2027 calendar PDF, posted June 4, 2026).

## Counting rules

- Students are counted once per course code. A cross-listed class appears under each of its codes, with that code's own enrollment.
- Final exams are excluded from the class-time analyses.
- Enrollment is the registered count, not attendance.

## Disclaimer

- **Unofficial.** This project is not affiliated with, endorsed by, or maintained by Northeastern University. University and building names are used only to describe locations.
- **Public data only.** The scraper reads the publicly accessible "Browse Classes" schedule without logging in. It sends one request at a time with a pause between requests, and runs at most once a day in automation. It collects no student information.
- **"Free" means no class is scheduled, nothing more.** The schedule covers registrar-scheduled classes and final exams only. It does not include events, meetings, office hours, room reservations, cancellations or building hours. Holidays and breaks are added by hand from the academic calendar, whose dates are subject to change. Rooms may be locked or in use. Follow posted signs, building access rules and university policies.
- **No warranty.** Data may be inaccurate or out of date. Use at your own risk.
- **Privacy.** The web app has no accounts, cookies, analytics or third-party resources.
