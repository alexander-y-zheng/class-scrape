# class-scrape

Scrapes Northeastern's public course registration system ([Banner "Browse Classes"](https://nubanner.neu.edu/StudentRegistrationSsb/ssb/registration), no login needed) into SQLite, and analyzes when and where Boston classes meet.

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

Each `scrape.py` run is saved as a new snapshot. Re-running it during a registration period builds a history of seat counts.

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

## Counting rules

- Students are counted once per course code. A cross-listed class appears under each of its codes, with that code's own enrollment.
- Final exams are excluded from the class-time analyses.
- Enrollment is the registered count, not attendance.
