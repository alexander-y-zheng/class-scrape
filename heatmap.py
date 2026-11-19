"""Heatmap of how many students are sitting in class, by weekday and time of day.

Usage:
    python heatmap.py --term 202710
    python heatmap.py --term 202710 --week-of 2026-09-28   # only sections meeting that week

Counting rules:
  * Students are counted once per course code (a cross-listed class shows up under
    each of its codes with that code's own enrollment, so nothing is double counted).
  * Only scheduled class meetings count (final exams are excluded).
  * A section is counted in a time bin if any of its meetings covers that bin, so a
    section with two overlapping meeting rows is not counted twice.
  * By default every section in the term counts, including half-term (2A/2B)
    sections - i.e. "a week where everything is running". Use --week-of to count
    only meetings whose date range overlaps a specific Monday-Sunday week.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BIN_MIN = 5

# Sequential single-hue ramp (light -> dark blue), lightest = near zero.
RAMP = ["#ffffff", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5",
        "#256abf", "#184f95", "#0d366b"]
INK, INK_MUTED, GRID = "#1f1f1d", "#6b6a66", "#e4e3df"


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def build_grid(db: sqlite3.Connection, term: str, week_of: date | None):
    sql = f"""
        SELECT m.crn, s.enrollment, m.begin_time, m.end_time, m.start_date, m.end_date,
               {', '.join('m.' + d for d in DAYS)}
        FROM latest_meetings m
        JOIN latest_sections s ON s.run_id = m.run_id AND s.crn = m.crn
        WHERE s.term = ? AND m.meeting_type != 'FNEX'
          AND m.begin_time IS NOT NULL AND m.end_time IS NOT NULL
    """
    rows = db.execute(sql, (term,)).fetchall()
    if not rows:
        raise SystemExit(f"No meetings for term {term} - run scrape.py first.")

    if week_of:
        wk_start = week_of - timedelta(days=week_of.weekday())
        wk_end = wk_start + timedelta(days=6)
        rows = [r for r in rows
                if date.fromisoformat(r[4]) <= wk_end and date.fromisoformat(r[5]) >= wk_start]

    start = min(to_min(r[2]) for r in rows) // 60 * 60
    end = -(-max(to_min(r[3]) for r in rows) // 60) * 60
    n_bins = (end - start) // BIN_MIN

    occupied: dict[str, set[tuple[int, int]]] = {}
    enrollment: dict[str, int] = {}
    for crn, enr, bt, et, _sd, _ed, *days in rows:
        enrollment[crn] = enr
        b0 = (to_min(bt) - start) // BIN_MIN
        b1 = -(-(to_min(et) - start) // BIN_MIN)  # ceil: partial bins count
        cells = occupied.setdefault(crn, set())
        for d, meets in enumerate(days):
            if meets:
                cells.update((d, b) for b in range(b0, b1))

    grid = np.zeros((n_bins, 7))
    for crn, cells in occupied.items():
        for d, b in cells:
            grid[b, d] += enrollment[crn]
    return grid, start


def plot(grid: np.ndarray, start: int, title: str, subtitle: str, out: Path) -> None:
    n_bins = grid.shape[0]
    cmap = LinearSegmentedColormap.from_list("seq_blue", RAMP)
    fig, ax = plt.subplots(figsize=(8, 10), dpi=150)
    fig.patch.set_facecolor("white")
    im = ax.imshow(grid, aspect="auto", cmap=cmap, interpolation="nearest",
                   extent=(-0.5, 6.5, n_bins, 0), vmin=0, zorder=2)
    ax.set_facecolor("none")

    for x in np.arange(0.5, 6.5, 1):  # surface gap between day columns
        ax.axvline(x, color="white", lw=2.5, zorder=3)
    hours = range(start // 60, start // 60 + n_bins * BIN_MIN // 60 + 1)
    ax.set_yticks([(h * 60 - start) / BIN_MIN for h in hours])
    ax.set_yticklabels([f"{h % 12 or 12}{'am' if h < 12 else 'pm'}" for h in hours])
    ax.set_xticks(range(7))
    ax.set_xticklabels(DAY_LABELS)
    ax.xaxis.tick_top()
    ax.tick_params(colors=INK_MUTED, length=0, labelsize=10)
    for y in ax.get_yticks():
        ax.axhline(y, color=GRID, lw=0.6, zorder=0, alpha=0.9)
    for s in ax.spines.values():
        s.set_visible(False)

    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.03)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=INK_MUTED, length=0, labelsize=9)
    cb.set_label("Students in class", color=INK_MUTED)

    fig.suptitle(title, x=0.06, ha="left", color=INK, fontsize=15, fontweight="bold")
    fig.text(0.06, 0.935, subtitle, ha="left", color=INK_MUTED, fontsize=9.5)
    fig.subplots_adjust(top=0.88, left=0.1, right=0.95, bottom=0.03)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white")
    print(f"Wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--term", required=True, help="term code, e.g. 202710")
    ap.add_argument("--db", default="data/classes.db")
    ap.add_argument("--week-of", type=date.fromisoformat,
                    help="YYYY-MM-DD; only count sections meeting that week")
    ap.add_argument("--out-dir", default="figures")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    term_desc = (db.execute("SELECT description FROM terms WHERE code = ?",
                            (args.term,)).fetchone() or [args.term])[0]
    term_desc = term_desc.replace(" (View Only)", "")
    grid, start = build_grid(db, args.term, args.week_of)

    suffix = f"_week_{args.week_of}" if args.week_of else ""
    stem = Path(args.out_dir) / f"heatmap_{args.term}{suffix}"
    stem.parent.mkdir(parents=True, exist_ok=True)
    with open(stem.with_suffix(".csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time"] + DAY_LABELS)
        for b, row in enumerate(grid):
            t = start + b * BIN_MIN
            w.writerow([f"{t // 60:02d}:{t % 60:02d}"] + [int(v) for v in row])

    scope = f"sections meeting the week of {args.week_of}" if args.week_of \
        else "all sections in the term, incl. half-term"
    plot(grid, start,
         f"Students in class — {term_desc}, Boston",
         f"Enrollment summed per course code, {BIN_MIN}-min bins; {scope}; finals excluded",
         stem.with_suffix(".png"))

    flat = [(grid[b, d], d, b) for d in range(7) for b in range(grid.shape[0])]
    print("Busiest moments:")
    for v, d, b in sorted(flat, reverse=True)[:5]:
        t = start + b * BIN_MIN
        print(f"  {DAY_LABELS[d]} {t // 60:02d}:{t % 60:02d}  {int(v):,} students")


if __name__ == "__main__":
    main()
