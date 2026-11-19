"""Interactive campus map: students in class per building, for each class block.

Usage:
    python campus_map.py --term 202710
    -> figures/campus_map_202710.html  (open in a browser)

How blocks are found: for each weekday, any (start, end) time pattern holding at
least --min-share of that day's enrolled seats is a "major block" (overlapping
candidates keep the larger one). An "Evening" snapshot at 7pm is added. For each
block, a section counts if it is in class at the block's midpoint, so off-grid
classes running at that moment are included too. Students are counted once per
course code, final exams are excluded (same rules as heatmap.py).
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
EVENING_AT = 19 * 60


def to_min(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:])


def fmt(m: int) -> str:
    h, mm = divmod(m, 60)
    return f"{h % 12 or 12}:{mm:02d}{'am' if h < 12 else 'pm'}"


def load(db: sqlite3.Connection, term: str):
    sql = f"""
        SELECT m.crn, s.subject_course, s.title, s.enrollment, m.begin_time, m.end_time,
               m.building, m.building_desc, {', '.join('m.' + d for d in DAYS)}
        FROM latest_meetings m
        JOIN latest_sections s ON s.run_id = m.run_id AND s.crn = m.crn
        WHERE s.term = ? AND m.meeting_type != 'FNEX'
          AND m.begin_time IS NOT NULL AND m.end_time IS NOT NULL
        ORDER BY m.crn, m.seq
    """
    rows = db.execute(sql, (term,)).fetchall()
    if not rows:
        raise SystemExit(f"No meetings for term {term} - run scrape.py first.")
    return rows


def find_blocks(rows, min_share: float) -> list[list[tuple[int, int]]]:
    blocks = []
    for d in range(len(DAYS)):
        seats: Counter = Counter()
        for r in rows:
            if r[8 + d]:
                seats[(to_min(r[4]), to_min(r[5]))] += r[3]
        total = sum(seats.values())
        cands = sorted((k for k, v in seats.items() if v >= min_share * total),
                       key=lambda k: -seats[k])
        chosen: list[tuple[int, int]] = []
        for b, e in cands:  # biggest first; drop anything overlapping a kept block
            if all(e <= cb or b >= ce for cb, ce in chosen):
                chosen.append((b, e))
        blocks.append(sorted(chosen))
    return blocks


def snapshot(rows, day: int, t: int):
    """Students in class at minute t on `day`, grouped by building."""
    seen: set[str] = set()
    by_bldg: dict[str, dict] = defaultdict(lambda: {"students": 0, "sections": 0,
                                                     "courses": []})
    for crn, course, title, enr, bt, et, bcode, bdesc, *days in rows:
        if crn in seen or not days[day] or not (to_min(bt) <= t < to_min(et)):
            continue
        seen.add(crn)
        b = by_bldg[bcode or "NONE"]
        b["students"] += enr
        b["sections"] += 1
        b["courses"].append((enr, course, title))
    out = {}
    for code, b in by_bldg.items():
        top = sorted(b["courses"], reverse=True)[:5]
        out[code] = {"s": b["students"], "n": b["sections"],
                     "top": [[c, t, e] for e, c, t in top]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--term", required=True)
    ap.add_argument("--db", default="data/classes.db")
    ap.add_argument("--buildings", default="buildings.csv")
    ap.add_argument("--min-share", type=float, default=0.05)
    ap.add_argument("--out-dir", default="figures")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    term_desc = (db.execute("SELECT description FROM terms WHERE code = ?",
                            (args.term,)).fetchone() or [args.term])[0]
    term_desc = term_desc.replace(" (View Only)", "")
    rows = load(db, args.term)
    blocks = find_blocks(rows, args.min_share)

    names = {r[6]: r[7] for r in rows if r[6]}
    buildings = {}
    with open(args.buildings, newline="") as f:
        for b in csv.DictReader(f):
            buildings[b["code"]] = {
                "name": b["banner_name"],
                "lat": float(b["lat"]) if b["lat"] else None,
                "lon": float(b["lon"]) if b["lon"] else None,
            }
    for code, desc in names.items():  # codes missing from buildings.csv
        buildings.setdefault(code, {"name": desc, "lat": None, "lon": None})
    buildings["NONE"] = {"name": "No room assigned", "lat": None, "lon": None}
    buildings.get("BOS", {})["name"] = "Boston (no specific building)"

    days = []
    for d, label in enumerate(DAY_LABELS):
        day_blocks = [{"label": f"{fmt(b)}–{fmt(e)}", "at": fmt((b + e) // 2),
                       "data": snapshot(rows, d, (b + e) // 2)}
                      for b, e in blocks[d]]
        day_blocks.append({"label": "Evening", "at": fmt(EVENING_AT),
                           "data": snapshot(rows, d, EVENING_AT)})
        days.append({"day": label, "blocks": day_blocks})

    payload = {"term": term_desc, "buildings": buildings, "days": days}
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__TITLE__", f"Campus Class Map — {term_desc}")
    out = Path(args.out_dir) / f"campus_map_{args.term}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out}")
    for d in days:
        print(f"  {d['day']}: " + ", ".join(
            f"{b['label']} ({sum(x['s'] for x in b['data'].values()):,})"
            for b in d["blocks"]))


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root {
  --bg: #fbfaf8; --surface: #ffffff; --ink: #1f1f1d; --ink-2: #4a4945; --muted: #6b6a66;
  --line: #e4e3df; --accent: #2a78d6; --accent-soft: #cde2fb; --mark: #256abf;
  --chip: #f0efec; --chip-on: #1f1f1d; --chip-on-ink: #ffffff;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615; --surface: #1f1f1d; --ink: #f0efec; --ink-2: #cfcdc7; --muted: #9a9892;
    --line: #383835; --accent: #5598e7; --accent-soft: #184f95; --mark: #5598e7;
    --chip: #2c2c2a; --chip-on: #f0efec; --chip-on-ink: #161615;
  }
}
:root[data-theme="dark"] {
  --bg: #161615; --surface: #1f1f1d; --ink: #f0efec; --ink-2: #cfcdc7; --muted: #9a9892;
  --line: #383835; --accent: #5598e7; --accent-soft: #184f95; --mark: #5598e7;
  --chip: #2c2c2a; --chip-on: #f0efec; --chip-on-ink: #161615;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: var(--bg); color: var(--ink);
  font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  display: flex; flex-direction: column; }
header { padding: 14px 20px 10px; border-bottom: 1px solid var(--line); background: var(--surface); }
h1 { font-size: 18px; margin: 0 0 2px; }
.sub { color: var(--muted); font-size: 12.5px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center; margin-top: 10px; }
.group { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
.group-label { color: var(--muted); font-size: 12px; margin-right: 4px; }
button { font: inherit; border: 0; border-radius: 6px; padding: 5px 10px; cursor: pointer;
  background: var(--chip); color: var(--ink-2); }
button:hover { color: var(--ink); }
button[aria-pressed="true"] { background: var(--chip-on); color: var(--chip-on-ink); }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
main { flex: 1; display: flex; min-height: 0; }
#map { flex: 1; min-height: 360px; }
aside { width: 340px; border-left: 1px solid var(--line); background: var(--surface);
  display: flex; flex-direction: column; min-height: 0; }
.stats { display: flex; gap: 24px; padding: 14px 16px 10px; border-bottom: 1px solid var(--line); }
.stat .v { font-size: 24px; font-weight: 650; font-variant-numeric: tabular-nums; }
.stat .k { color: var(--muted); font-size: 12px; }
.list { overflow-y: auto; padding: 6px 0 12px; flex: 1; }
.row { display: grid; grid-template-columns: 1fr 64px; gap: 2px 8px; padding: 6px 16px;
  cursor: pointer; }
.row:hover, .row.hl { background: var(--chip); }
.row .name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--ink); }
.row .num { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink-2); }
.bar { grid-column: 1 / -1; height: 6px; background: transparent; }
.bar i { display: block; height: 100%; background: var(--mark); border-radius: 0 3px 3px 0; }
.row.off .name::after { content: " · not on map"; color: var(--muted); font-size: 12px; }
.row.off .bar i { background: var(--muted); }
.legend { background: var(--surface); color: var(--ink-2); padding: 8px 10px; border-radius: 8px;
  font-size: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.15); }
.legend svg { display: block; }
.leaflet-tooltip.tt { background: var(--surface); color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,.18); padding: 8px 10px; font: inherit; }
.tt b { display: block; margin-bottom: 2px; }
.tt .m { color: var(--muted); font-size: 12px; }
.tt ol { margin: 6px 0 0; padding-left: 18px; font-size: 12px; color: var(--ink-2); }
.leaflet-container { background: var(--bg); font: inherit; }
@media (max-width: 760px) {
  main { flex-direction: column; }
  aside { width: auto; border-left: 0; border-top: 1px solid var(--line); max-height: 45vh; }
  header { padding: 12px 16px 8px; }
}
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="sub">Students in class at the midpoint of each block, by building. Counted once per course code; final exams excluded.</div>
  <div class="controls">
    <div class="group" id="days" role="group" aria-label="Day"><span class="group-label">Day</span></div>
    <div class="group" id="blocks" role="group" aria-label="Class block"><span class="group-label">Block</span></div>
    <div class="group">
      <button id="prev" aria-label="Previous block">◀</button>
      <button id="play" aria-pressed="false">▶ Play</button>
      <button id="next" aria-label="Next block">▶</button>
    </div>
  </div>
</header>
<main>
  <div id="map"></div>
  <aside>
    <div class="stats">
      <div class="stat"><div class="v" id="total">–</div><div class="k">students in class</div></div>
      <div class="stat"><div class="v" id="nb">–</div><div class="k">buildings in use</div></div>
      <div class="stat"><div class="v" id="at">–</div><div class="k">snapshot time</div></div>
    </div>
    <div class="list" id="list" role="table" aria-label="Students by building"></div>
  </aside>
</main>
<script>
const DATA = __DATA__;
const B = DATA.buildings;
document.getElementById("title").textContent = "Where students are in class — " + DATA.term + ", Boston";

const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const map = L.map("map", { zoomSnap: 0.25, scrollWheelZoom: true }).setView([42.3385, -71.0895], 16.25);
L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark ? "Dark" : "Light"}_Gray_Base/MapServer/tile/{z}/{y}/{x}`, {
  maxZoom: 19, maxNativeZoom: 16,
  attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);

// One radius scale across every block, so sizes compare across the week.
let maxS = 1;
DATA.days.forEach(d => d.blocks.forEach(b => Object.values(b.data).forEach(x => { maxS = Math.max(maxS, x.s); })));
const MAX_R = 38;
const radius = s => s > 0 ? Math.max(3, MAX_R * Math.sqrt(s / maxS)) : 0;
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const markers = {};
for (const [code, b] of Object.entries(B)) {
  if (b.lat == null) continue;
  markers[code] = L.circleMarker([b.lat, b.lon], {
    radius: 0, color: css("--surface"), weight: 2, fillColor: css("--mark"), fillOpacity: 0.62
  }).addTo(map);
  markers[code].on("mouseover", () => highlight(code, true));
  markers[code].on("mouseout", () => highlight(code, false));
}
map.fitBounds(L.latLngBounds(Object.values(markers).map(m => m.getLatLng())), { padding: [40, 40] });

// Size legend
const legend = L.control({ position: "topright" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "legend");
  const steps = [250, 1000, Math.round(maxS / 500) * 500].filter((v, i, a) => v <= maxS && a.indexOf(v) === i);
  const h = 2 * radius(steps[steps.length - 1]) + 4;
  let x = 4, svg = "";
  steps.forEach(s => {
    const r = radius(s);
    svg += `<circle cx="${x + r}" cy="${h - r - 2}" r="${r}" fill="${css("--mark")}" fill-opacity=".62" stroke="${css("--surface")}" stroke-width="2"/>` +
           `<text x="${x + r}" y="${h + 12}" text-anchor="middle" font-size="11" fill="${css("--muted")}">${s.toLocaleString()}</text>`;
    x += 2 * r + 14;
  });
  div.innerHTML = `<div style="margin-bottom:4px">Students in class</div><svg width="${x}" height="${h + 16}">${svg}</svg>`;
  return div;
};
legend.addTo(map);

let dayI = 0, blockI = 0, timer = null, current = {};
const current_r = {};
const fmtN = n => n.toLocaleString();

function tooltipHtml(code) {
  const x = current[code], b = B[code];
  if (!x) return `<b>${b.name}</b><span class="m">No classes at this time</span>`;
  const top = x.top.map(([c, t, e]) => `<li>${c} ${t} — ${e}</li>`).join("");
  return `<b>${b.name}</b><span class="m">${fmtN(x.s)} students · ${x.n} section${x.n === 1 ? "" : "s"}</span><ol>${top}</ol>`;
}

function highlight(code, on) {
  const row = document.querySelector(`.row[data-code="${code}"]`);
  if (row) row.classList.toggle("hl", on);
  const m = markers[code];
  if (!m) return;
  if (on) { m.setStyle({ fillOpacity: 0.9 }); m.bindTooltip(tooltipHtml(code), { className: "tt", direction: "top", offset: [0, -6] }).openTooltip(); }
  else { m.setStyle({ fillOpacity: 0.62 }); m.closeTooltip(); m.unbindTooltip(); }
}

function animate(code, to) {
  const m = markers[code], from = current_r[code] || 0, t0 = performance.now();
  current_r[code] = to;
  const step = now => {
    const k = Math.min(1, (now - t0) / 350), e = 1 - Math.pow(1 - k, 3);
    m.setRadius(from + (to - from) * e);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function render() {
  const day = DATA.days[dayI], blk = day.blocks[blockI];
  current = blk.data;
  document.querySelectorAll("#days button").forEach((b, i) => b.setAttribute("aria-pressed", i === dayI));
  const bc = document.getElementById("blocks");
  bc.querySelectorAll("button").forEach(b => b.remove());
  day.blocks.forEach((b, i) => {
    const btn = document.createElement("button");
    btn.textContent = b.label; btn.setAttribute("aria-pressed", i === blockI);
    btn.onclick = () => { stop(); blockI = i; render(); };
    bc.appendChild(btn);
  });
  for (const code in markers) {
    animate(code, radius(current[code] ? current[code].s : 0));
    if (markers[code].getTooltip() && markers[code].isTooltipOpen()) markers[code].setTooltipContent(tooltipHtml(code));
  }
  const entries = Object.entries(current).sort((a, b) => b[1].s - a[1].s);
  const total = entries.reduce((a, [, x]) => a + x.s, 0);
  document.getElementById("total").textContent = fmtN(total);
  document.getElementById("nb").textContent = entries.filter(([c]) => B[c] && B[c].lat != null).length;
  document.getElementById("at").textContent = blk.at;
  const top = entries.length ? entries[0][1].s : 1;
  document.getElementById("list").innerHTML = entries.map(([c, x]) => {
    const off = !markers[c];
    return `<div class="row${off ? " off" : ""}" role="row" data-code="${c}">` +
      `<span class="name" role="cell">${(B[c] || { name: c }).name}</span>` +
      `<span class="num" role="cell">${fmtN(x.s)}</span>` +
      `<span class="bar"><i style="width:${(100 * x.s / top).toFixed(1)}%"></i></span></div>`;
  }).join("");
  document.querySelectorAll(".row").forEach(r => {
    const c = r.dataset.code;
    r.onmouseenter = () => highlight(c, true);
    r.onmouseleave = () => highlight(c, false);
    r.onclick = () => { if (markers[c]) map.panTo(markers[c].getLatLng()); };
  });
}

function step(dir) {
  const n = DATA.days[dayI].blocks.length;
  blockI += dir;
  if (blockI >= n) { dayI = (dayI + 1) % DATA.days.length; blockI = 0; }
  if (blockI < 0) { dayI = (dayI - 1 + DATA.days.length) % DATA.days.length; blockI = DATA.days[dayI].blocks.length - 1; }
  render();
}
function stop() { clearInterval(timer); timer = null; const p = document.getElementById("play"); p.textContent = "▶ Play"; p.setAttribute("aria-pressed", "false"); }

const dc = document.getElementById("days");
DATA.days.forEach((d, i) => {
  const btn = document.createElement("button");
  btn.textContent = d.day;
  btn.onclick = () => { stop(); dayI = i; blockI = Math.min(blockI, d.blocks.length - 1); render(); };
  dc.appendChild(btn);
});
document.getElementById("prev").onclick = () => { stop(); step(-1); };
document.getElementById("next").onclick = () => { stop(); step(1); };
document.getElementById("play").onclick = e => {
  if (timer) return stop();
  e.target.textContent = "❚❚ Pause"; e.target.setAttribute("aria-pressed", "true");
  timer = setInterval(() => step(1), 1400);
};
document.addEventListener("keydown", e => {
  if (e.key === "ArrowRight") { stop(); step(1); }
  if (e.key === "ArrowLeft") { stop(); step(-1); }
});
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
