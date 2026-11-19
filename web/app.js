"use strict";

// Booking tuple from export_rooms.py: [daysMask, startMin, endMin, startDate, endDate, courses, kind]
const TZ = "America/New_York";
const DAY_START = 7 * 60, DAY_END = 22 * 60; // timeline range
const $ = id => document.getElementById(id);

const state = {
  data: null, mode: "now", date: "", minute: 0,
  minFree: 30, building: "", search: "", open: new Set(),
};

// ---------- time helpers (always Boston time, whatever the device's zone) ----------
function bostonNow() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date()).map(p => [p.type, p.value]));
  return { date: `${parts.year}-${parts.month}-${parts.day}`, minute: +parts.hour * 60 + +parts.minute };
}
const weekdayIdx = d => { const [y, m, dd] = d.split("-").map(Number); return (new Date(Date.UTC(y, m - 1, dd)).getUTCDay() + 6) % 7; };
const fmtTime = m => { const h = Math.floor(m / 60) % 24, mm = m % 60; return `${h % 12 || 12}:${String(mm).padStart(2, "0")}${h < 12 ? "am" : "pm"}`; };
const fmtDur = m => m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ""}` : `${m}m`;
const fmtDate = d => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const toHHMM = m => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- schedule logic ----------
function bookingsOn(room, date) {
  const bit = 1 << weekdayIdx(date);
  return room.m.filter(b => (b[0] & bit) && b[3] <= date && date <= b[4]).sort((a, b) => a[1] - b[1]);
}

// Merge overlapping/back-to-back bookings into busy intervals.
function busyIntervals(bookings) {
  const out = [];
  for (const b of bookings) {
    const last = out[out.length - 1];
    if (last && b[1] <= last[1]) last[1] = Math.max(last[1], b[2]);
    else out.push([b[1], b[2]]);
  }
  return out;
}

function roomState(room, date, t) {
  const bookings = bookingsOn(room, date);
  const busy = busyIntervals(bookings);
  const cur = busy.find(([s, e]) => s <= t && t < e);
  if (cur) {
    const now = bookings.find(b => b[1] <= t && t < b[2]);
    return { free: false, until: cur[1], what: now ? now[5] : "", bookings };
  }
  const next = busy.find(([s]) => s > t);
  return { free: true, until: next ? next[0] : null, mins: (next ? next[0] : 24 * 60) - t, bookings };
}

// ---------- rendering ----------
function roomLabel(room) {
  const b = state.data.buildings[room.b];
  return `${b ? b.name : room.b} ${room.r}`;
}

function matchesSearch(room, q) {
  if (!q) return true;
  const hay = `${roomLabel(room)} ${room.b} ${room.r} ${room.b}${room.r}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).every(w => hay.includes(w));
}

function timeline(st, t) {
  const span = DAY_END - DAY_START, pct = m => ((Math.min(Math.max(m, DAY_START), DAY_END) - DAY_START) / span) * 100;
  const bars = busyIntervals(st.bookings).map(([s, e]) =>
    `<i style="left:${pct(s)}%;width:${pct(e) - pct(s)}%"></i>`).join("");
  const now = t >= DAY_START && t <= DAY_END ? `<i class="now" style="left:${pct(t)}%"></i>` : "";
  const ticks = [7, 10, 13, 16, 19, 22].map(h => `<span>${h % 12 || 12}${h < 12 ? "am" : "pm"}</span>`).join("");
  const list = st.bookings.length
    ? st.bookings.map(b => `<li><span class="t">${fmtTime(b[1])}–${fmtTime(b[2])}</span><span>${esc(b[5])}${b[6] === "exam" ? " (final exam)" : ""}</span></li>`).join("")
    : `<li>No classes scheduled this day.</li>`;
  return `<div class="detail"><div class="timeline" aria-hidden="true">${bars}${now}</div>
    <div class="axis" aria-hidden="true">${ticks}</div>
    <ul class="sched" aria-label="Scheduled classes">${list}</ul></div>`;
}

function render() {
  const { data } = state;
  if (!data) return;
  if (state.mode === "now") Object.assign(state, bostonNow());
  const { date, minute: t } = state;

  const anyClasses = data.rooms.some(r => bookingsOn(r, date).length);
  const note = $("break-note");
  if (!anyClasses) {
    note.hidden = false;
    note.textContent = "No classes are scheduled on this date. It may be a weekend, a break, or outside the terms in this data. Buildings may be closed.";
  } else if (t < DAY_START || t >= DAY_END) {
    note.hidden = false;
    note.textContent = "This is outside normal class hours. Buildings may be closed or restricted.";
  } else note.hidden = true;

  const rows = [];
  let freeCount = 0;
  for (const room of data.rooms) {
    if (state.building && room.b !== state.building) continue;
    const st = roomState(room, date, t);
    const ok = st.free && st.mins >= state.minFree;
    if (ok) freeCount++;
    if (ok || (state.search && matchesSearch(room, state.search))) {
      if (matchesSearch(room, state.search)) rows.push({ room, st });
    }
  }
  const total = data.rooms.filter(r => !state.building || r.b === state.building).length;
  $("status").innerHTML = `<strong>${fmtDate(date)}, ${fmtTime(t)}</strong> · ` +
    `${freeCount} of ${total} rooms have no class for ${state.minFree ? `at least ${fmtDur(state.minFree)}` : "now"}`;

  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.room.b)) groups.set(r.room.b, []);
    groups.get(r.room.b).push(r);
  }
  const order = [...groups.keys()].sort((a, b) => data.buildings[a].name.localeCompare(data.buildings[b].name));
  const html = order.map(code => {
    const list = groups.get(code).sort((a, b) =>
      (b.st.free - a.st.free) || ((b.st.mins || 0) - (a.st.mins || 0)) || a.room.r.localeCompare(b.room.r, undefined, { numeric: true }));
    const nFree = list.filter(x => x.st.free).length;
    const items = list.map(({ room, st }) => {
      const id = `${room.b}|${room.r}`, open = state.open.has(id);
      const pill = st.free
        ? `<span class="pill free">${st.until == null ? "free rest of day" : `free until ${fmtTime(st.until)}`}</span>`
        : `<span class="pill busy">in use until ${fmtTime(st.until)}</span>`;
      const sub = st.free
        ? (st.until == null ? "No more classes today" : `${fmtDur(st.mins)} until the next class`)
        : `Class now: ${esc(st.what)}`;
      return `<div class="room"><button data-id="${esc(id)}" aria-expanded="${open}">
          <span class="name">${esc(room.r)}${room.cap ? `<span class="cap">up to ${room.cap}</span>` : ""}</span>
          <span class="state">${pill}</span><span class="sub">${sub}</span></button>
          ${open ? timeline(st, t) : ""}</div>`;
    }).join("");
    return `<section class="bldg"><h3>${esc(data.buildings[code].name)} <span class="count">${nFree} free</span></h3>${items}</section>`;
  }).join("");
  $("results").innerHTML = html || `<p class="empty">No matching rooms. Try a shorter “free for at least” or another building.</p>`;
}

// ---------- wiring ----------
function setMode(mode) {
  state.mode = mode;
  $("mode-now").setAttribute("aria-pressed", mode === "now");
  $("mode-pick").setAttribute("aria-pressed", mode === "pick");
  $("pick").hidden = mode !== "pick";
  if (mode === "pick") { $("date").value = state.date; $("time").value = toHHMM(state.minute); }
  render();
}

$("mode-now").onclick = () => setMode("now");
$("mode-pick").onclick = () => setMode("pick");
$("date").onchange = e => { if (e.target.value) { state.date = e.target.value; render(); } };
$("time").onchange = e => { if (e.target.value) { const [h, m] = e.target.value.split(":"); state.minute = +h * 60 + +m; render(); } };
$("min-free").onchange = e => { state.minFree = +e.target.value; render(); };
$("building").onchange = e => { state.building = e.target.value; render(); };
$("search").oninput = e => { state.search = e.target.value.trim(); render(); };
$("results").onclick = e => {
  const btn = e.target.closest("button[data-id]");
  if (!btn) return;
  const id = btn.dataset.id;
  state.open.has(id) ? state.open.delete(id) : state.open.add(id);
  render();
};
setInterval(() => { if (state.mode === "now") render(); }, 60 * 1000);

fetch("data/rooms.json", { cache: "no-cache" })
  .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then(data => {
    state.data = data;
    const sel = $("building");
    const used = new Set(data.rooms.map(r => r.b));
    Object.entries(data.buildings).filter(([c]) => used.has(c))
      .sort((a, b) => a[1].name.localeCompare(b[1].name))
      .forEach(([c, b]) => sel.add(new Option(b.name, c)));
    const updated = new Date(data.generated_at).toLocaleString("en-US", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" });
    $("data-info").textContent = `Schedule data: ${data.terms.map(t => t.name).join(", ")}. Last updated ${updated} (Boston time).`;
    Object.assign(state, bostonNow());
    render();
  })
  .catch(err => { $("status").textContent = `Could not load the schedule data (${err.message}).`; });
