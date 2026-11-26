"use strict";

/* ------------------------------------------------------------------ *
 * AI SentiWeather — shared data layer + per-page controllers
 * Pages: index.html (home), start.html (selection), report.html
 * ------------------------------------------------------------------ */

const $ = id => document.getElementById(id);
const PAGE = document.body.dataset.page;

const platforms = {
  nyt: "The New York Times",
  guardian: "The Guardian",
  bbc: "BBC",
  hacker_news: "Hacker News",
  reddit: "Reddit",
  bluesky: "Bluesky"
};

const companies = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google_deepmind: "Google DeepMind",
  deepseek: "DeepSeek",
  bytedance_seed: "ByteDance Seed"
};

const icons = {
  google_deepmind: "deepmind",
  bytedance_seed: "bytedance"
};

const sections = ["Coverage volume", "Average sentiment", "Sentiment distribution", "Sentiment over time", "Frequent words", "Voices in the sample"];
const attitudes = ["positive", "neutral", "negative"];
const wantsDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
const palette = () => (wantsDark()
  ? ["#f0885c", "#8fbdf0", "#b9a6e0"]
  : ["#c9552f", "#4a7fb5", "#8a7aa0"]);
const attitudeColors = () => (wantsDark()
  ? ["#3f9e89", "#8b9590", "#d4717f"]
  : ["#247464", "#a6ada0", "#af5364"]);
const list = new Intl.ListFormat("en", { style: "long", type: "conjunction" });

const el = (tag, text, cls) => {
  const x = document.createElement(tag);
  if (text != null) x.textContent = text;
  if (cls) x.className = cls;
  return x;
};
const avg = a => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
const isoDate = t => new Date(t).toISOString().slice(0, 10);
const shift = (d, n) => isoDate(Date.parse(d + "T00:00:00Z") + n * 86400000);
const hkdate = t => isoDate(Date.parse(t) + 8 * 3600000);
const names = (set, lookup) => list.format([...set].map(k => lookup[k]));
const cssVar = (name, fallback = "") =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
const MONO_ICONS = new Set(["nyt", "openai", "anthropic", "reddit", "bluesky", "bytedance_seed", "deepseek", "google_deepmind"]);
const icon = (id, label = "") => {
  const im = el("img");
  im.src = "./icons/" + (icons[id] || id) + ".svg";
  if (MONO_ICONS.has(id)) im.className = "mono";
  im.alt = "";
  im.title = label;
  return im;
};

const ps = new Set();
const cs = new Set();
let data = null;
let preset = "Past 7 days";
let selectedRows = [];
let renderVersion = 0;
let lastDraw = Promise.resolve();
/* The report page receives its exact window through the URL. */
let fixedWindow = null;

/* ------------------------------------------------------------------ *
 * coverage windows
 * ------------------------------------------------------------------ */

function range() {
  const ranges = [...ps].map(p => data.ranges[p]);
  if (!ranges.length || ranges.some(r => !r)) return null;
  const a = ranges.map(r => r[0]).sort().at(-1);
  const b = ranges.map(r => r[1]).sort()[0];
  return a <= b ? [a, b] : null;
}

function bounds(choice) {
  const r = range();
  if (!r) return null;
  if (fixedWindow) {
    const [a, b] = fixedWindow;
    return a >= r[0] && b <= r[1] && a <= b ? [a, b] : null;
  }
  let end = r[1];
  let start = end;
  if (choice === "Past 7 days") start = shift(end, -6);
  if (choice === "Past month" || choice === "Past year") {
    const d = new Date(end + "T00:00:00Z");
    const day = d.getUTCDate();
    d.setUTCDate(1);
    d.setUTCMonth(d.getUTCMonth() - (choice === "Past year" ? 12 : 1));
    const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
    d.setUTCDate(Math.min(day, last));
    start = shift(isoDate(d), 1);
  }
  if (choice === "Custom") {
    start = $("from")?.value || "";
    end = $("to")?.value || "";
  }
  return start && end && start >= r[0] && start <= end && end <= r[1] ? [start, end] : null;
}

function statusText() {
  const latest = data.updated
    ? new Date(data.updated).toLocaleString("en-GB", { timeZone: "Asia/Hong_Kong", hour12: false }) + " HKT"
    : null;
  const reported = Object.keys(platforms).filter(p => data.status[p]);
  const gaps = reported.filter(p => data.status[p] !== "ok");
  const missing = Object.keys(platforms).filter(p => !data.ranges[p]);
  const bits = [latest ? "Last run " + latest : "Built from the latest verified checkpoints"];
  if (gaps.length) bits.push("incomplete: " + list.format(gaps.map(p => platforms[p])));
  if (missing.length) bits.push("no verified window yet: " + list.format(missing.map(p => platforms[p])));
  return bits.join(" · ") + ".";
}

/* ------------------------------------------------------------------ *
 * home
 * ------------------------------------------------------------------ */

let gaugeFrame = 0;

function drawGauge(value, animate = true) {
  const c = $("gauge");
  if (!c) return;
  const g = c.getContext("2d");
  const x = 280, y = 223, r = 176;
  const ink = cssVar("--ink", "#143730");
  const label = cssVar("--gauge-text", "#50635c");
  const mood = cssVar("--mood", "#1c7a6c");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const bands = ["#af5364", "#c78c80", "#cdc9ae", "#99bb9c", "#307968"];

  /* the arc is cut where the score actually changes category:
     red below -0.05, a thin neutral notch, green above +0.05 */
  const NEUTRAL = 0.05;
  const mid = NEUTRAL + (1 - NEUTRAL) / 2;
  const stops = [-1, -mid, -NEUTRAL, NEUTRAL, mid, 1];
  const angleOf = value => Math.PI * (1 + (value + 1) / 2);

  const paint = v => {
    g.clearRect(0, 0, c.width, c.height);
    g.lineWidth = 23;
    bands.forEach((band, i) => {
      const from = angleOf(stops[i]);
      const to = angleOf(stops[i + 1]);
      const gap = to - from > 0.12 ? 0.012 : 0.004;
      if (to - from <= gap * 2) return;
      g.beginPath();
      g.strokeStyle = band;
      g.arc(x, y, r, from + gap, to - gap);
      g.stroke();
    });
    g.fillStyle = label;
    g.textAlign = "center";
    g.font = "21px Arial";
    g.fillText("−1", x - r, y + 31);
    g.fillText("+1", x + r, y + 31);
    if (v == null) return;
    const a = Math.PI * (1 + (v + 1) / 2);
    const dx = Math.cos(a);
    const dy = Math.sin(a);
    const nx = -dy;
    const ny = dx;
    const tip = r + 1;
    const shoulder = 10;
    const halfBase = 2.7;
    const pivotR = 11;

    /* a single tapered hairline, anchored inside the pivot so it grows out of it */
    g.beginPath();
    g.moveTo(x + dx * tip, y + dy * tip);
    g.lineTo(x + dx * shoulder + nx * halfBase, y + dy * shoulder + ny * halfBase);
    g.lineTo(x + dx * shoulder - nx * halfBase, y + dy * shoulder - ny * halfBase);
    g.closePath();
    g.fillStyle = ink;
    g.fill();
    g.lineJoin = "round";
    g.lineWidth = 1.6;
    g.strokeStyle = ink;
    g.stroke();

    /* pivot: hairline ring with a mood-coloured heart */
    g.beginPath();
    g.arc(x, y, pivotR, 0, Math.PI * 2);
    g.strokeStyle = ink;
    g.lineWidth = 1.5;
    g.stroke();
    g.beginPath();
    g.arc(x, y, 3.4, 0, Math.PI * 2);
    g.fillStyle = mood;
    g.fill();
  };

  cancelAnimationFrame(gaugeFrame);
  if (value == null || reduce || !animate) {
    paint(value);
    return;
  }
  const from = -1, duration = 950, started = performance.now();
  const step = now => {
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    paint(from + (value - from) * eased);
    if (progress < 1) gaugeFrame = requestAnimationFrame(step);
  };
  gaugeFrame = requestAnimationFrame(step);
}

/* ---- mood gauge with a browsable history -------------------------- */

const weather = { end: null, days: 7, score: null };
let daySeries = [];
let stripGeometry = null;

function coverageBounds() {
  const ranges = Object.values(data.ranges || {}).filter(Boolean);
  if (!ranges.length) return null;
  const starts = ranges.map(r => r[0]).sort();
  const ends = ranges.map(r => r[1]).sort();
  return [starts[0], ends[ends.length - 1]];
}

function clampEnd(date) {
  const bounds = coverageBounds();
  if (!bounds) return date;
  if (!date) return bounds[1];
  if (date < bounds[0]) return bounds[0];
  if (date > bounds[1]) return bounds[1];
  return date;
}

function dayLabel(date) {
  return new Date(date + "T00:00:00+08:00").toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Hong_Kong"
  });
}

/* one point per calendar day across the whole collected period */
function buildDaySeries() {
  const bounds = coverageBounds();
  daySeries = [];
  if (!bounds) return;
  const buckets = new Map();
  for (const r of data.rows) {
    if (!r.companies.length || r.score == null) continue;
    if (r.date < bounds[0] || r.date > bounds[1]) continue;
    if (!buckets.has(r.date)) buckets.set(r.date, []);
    buckets.get(r.date).push(r.score);
  }
  for (let d = bounds[0]; d <= bounds[1]; d = shift(d, 1)) {
    const values = buckets.get(d) || [];
    daySeries.push({
      date: d,
      n: values.length,
      mean: values.length ? values.reduce((a, v) => a + v, 0) / values.length : null
    });
  }
}

function windowRows() {
  const end = clampEnd(weather.end);
  const bounds = coverageBounds();
  let start = shift(end, -(weather.days - 1));
  if (bounds && start < bounds[0]) start = bounds[0];
  const rows = data.rows.filter(r =>
    r.companies.length && r.score != null && r.date >= start && r.date <= end
  );
  return { start, end, rows };
}

const WINDOW_COPY = {
  1: ["Emotion of the day", "That day&rsquo;s AI mood"],
  7: ["Emotion of the week", "This week&rsquo;s AI mood"],
  30: ["Emotion of the month", "This month&rsquo;s AI mood"]
};

/* a paged window can hold 1–30 days, so pick the copy by size, not by exact key */
function windowCopy(days) {
  if (days <= 1) return WINDOW_COPY[1];
  if (days <= 10) return WINDOW_COPY[7];
  return WINDOW_COPY[30];
}

function pillMatches(pill, days) {
  if (pill === 1) return days <= 1;
  if (pill === 7) return days > 1 && days <= 10;
  return days > 10;
}

function renderWeather(animate = true) {
  if (!data || !daySeries.length) return;
  const { start, end, rows } = windowRows();
  const score = avg(rows.map(r => r.score));
  const mood = score == null ? "" : score >= 0.05 ? "mood-positive" : score <= -0.05 ? "mood-negative" : "mood-neutral";
  document.body.classList.remove("mood-positive", "mood-negative", "mood-neutral");
  if (mood) document.body.classList.add(mood);
  weather.score = score;
  drawGauge(score, animate);

  $("weekValue").textContent = score == null ? "—" : score.toFixed(2);
  $("weekLabel").textContent = score == null
    ? "No scored items"
    : score >= 0.05 ? "Positive" : score <= -0.05 ? "Negative" : "Neutral";

  const copy = windowCopy(weather.days);
  if ($("weekKicker")) $("weekKicker").textContent = copy[0];
  if ($("weekTitle")) $("weekTitle").innerHTML = copy[1];

  const sameDay = start === end;
  $("weekDates").textContent = sameDay
    ? dayLabel(end) + (data.cutoff && end === hkdate(data.cutoff) ? " · " + new Date(data.cutoff).toLocaleTimeString("en-GB", { timeZone: "Asia/Hong_Kong", hour: "2-digit", minute: "2-digit", hour12: false }) + " HKT" : "")
    : dayLabel(start) + " – " + dayLabel(end) + " · " + weather.days + "-day window";
  const used = new Set(rows.map(r => r.platform));
  $("weekCoverage").textContent = rows.length + " scored items · " + (used.size || 0) +
    " platforms represented. " + statusText();

  syncWeatherControls(start, end);
  drawStrip(start, end);
}

function syncWeatherControls(start, end) {
  const bounds = coverageBounds();
  const input = $("weekDate");
  if (input) {
    if (bounds) {
      input.min = bounds[0];
      input.max = bounds[1];
    }
    input.value = weather.end;
  }
  if ($("prevWindow")) $("prevWindow").disabled = !!bounds && weather.end <= bounds[0];
  if ($("nextWindow")) $("nextWindow").disabled = !!bounds && weather.end >= bounds[1];
  document.querySelectorAll(".wpills button").forEach(b => {
    b.setAttribute("aria-pressed", String(pillMatches(+b.dataset.days, weather.days)));
  });
  if ($("weekDates")) $("weekDates").dataset.range = start + "/" + end;
}

function stepWindow(days) {
  const next = clampEnd(shift(weather.end, days));
  if (next === weather.end) return;
  weather.end = next;
  renderWeather();
  saveWeatherState();
}

function setWindowDays(days) {
  weather.days = days;
  const bounds = coverageBounds();
  if (bounds) {
    const earliest = shift(bounds[0], days - 1);
    if (weather.end < earliest) weather.end = clampEnd(earliest);
  }
  renderWeather();
  saveWeatherState();
}

function saveWeatherState() {
  if (!location.protocol.startsWith("http")) return;
  const params = new URLSearchParams(location.search);
  const bounds = coverageBounds();
  if (bounds && weather.end && weather.end < bounds[1]) params.set("date", weather.end);
  else params.delete("date");
  if (weather.days !== 7) params.set("days", String(weather.days));
  else params.delete("days");
  const query = params.toString();
  history.replaceState(null, "", location.pathname + (query ? "?" + query : ""));
}

/* the timeline strip: daily means, the active window, and click-to-jump */
const STRIP_VISIBLE_DAYS = 30;

function drawStrip(start, end) {
  const c = $("weekStrip");
  const scroller = $("stripScroll");
  if (!c || !scroller || !daySeries.length) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const height = Math.round(parseFloat(getComputedStyle(c).height)) || 68;
  const viewport = scroller.clientWidth || c.parentElement.clientWidth || 720;
  const n = daySeries.length;
  /* 30 days always fill the visible width; everything else waits off-screen */
  const width = Math.max(viewport, Math.round((n * viewport) / STRIP_VISIBLE_DAYS));
  c.style.width = width + "px";
  c.width = Math.round(width * dpr);
  c.height = Math.round(height * dpr);
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, width, height);

  const padX = 12, padTop = 14, padBottom = 20;
  const span = Math.max(1, n - 1);
  const x = i => padX + (width - 2 * padX) * (i / span);
  const extremes = daySeries.filter(d => d.mean != null).map(d => Math.abs(d.mean));
  const scale = Math.min(1, Math.max(0.35, (Math.max(0, ...extremes) || 0.2) * 1.25));
  const y = v => padTop + (height - padTop - padBottom) * (1 - (v + scale) / (2 * scale));
  const pos = cssVar("--pos", "#1c7a6c");
  const neg = cssVar("--neg", "#b2485c");
  const neu = cssVar("--neu", "#8a7438");
  const ink = cssVar("--ink", "#102c26");
  const muted = cssVar("--gauge-text", "#506b64");
  const mood = cssVar("--mood", "#1c7a6c");

  let startIdx = daySeries.findIndex(d => d.date >= start);
  if (startIdx < 0) startIdx = 0;
  let endIdx = -1;
  for (let i = 0; i < n; i++) if (daySeries[i].date <= end) endIdx = i;
  if (endIdx < 0) endIdx = n - 1;

  /* selected window: soft band */
  const bandLeft = Math.max(padX - 6, x(startIdx) - 3);
  const bandRight = Math.min(width - padX + 6, x(endIdx) + 3);
  g.fillStyle = withAlpha(mood, 0.10);
  roundRect(g, bandLeft, 4, Math.max(8, bandRight - bandLeft), height - 8, 8);
  g.fill();

  /* the -1 / 0 / +1 grid */
  g.strokeStyle = withAlpha(ink, 0.10);
  g.lineWidth = 1;
  g.setLineDash([3, 4]);
  g.beginPath();
  g.moveTo(padX, y(0));
  g.lineTo(width - padX, y(0));
  g.stroke();
  g.setLineDash([]);

  /* the daily line, broken where a day has no scored items */
  g.strokeStyle = withAlpha(ink, 0.62);
  g.lineWidth = 1.8;
  g.lineJoin = "round";
  g.beginPath();
  let drawing = false;
  daySeries.forEach((point, i) => {
    if (point.mean == null) {
      drawing = false;
      return;
    }
    if (drawing) g.lineTo(x(i), y(point.mean));
    else g.moveTo(x(i), y(point.mean));
    drawing = true;
  });
  g.stroke();

  /* a dot per day, coloured by where it lands */
  daySeries.forEach((point, i) => {
    if (point.mean == null) return;
    const colour = point.mean >= 0.05 ? pos : point.mean <= -0.05 ? neg : neu;
    g.beginPath();
    g.arc(x(i), y(point.mean), i === endIdx ? 4.4 : 2.6, 0, Math.PI * 2);
    g.fillStyle = colour;
    g.fill();
    if (i === endIdx) {
      g.beginPath();
      g.arc(x(i), y(point.mean), 7.5, 0, Math.PI * 2);
      g.strokeStyle = withAlpha(colour, 0.55);
      g.lineWidth = 1.4;
      g.stroke();
    }
  });

  stripGeometry = { width, height, padX, n, span };
  keepWindowInView(startIdx, endIdx, viewport);
  syncStripRail();
  syncStripDates();
}

/* slide the strip so the active window is on screen, without fighting the user */
function keepWindowInView(startIdx, endIdx, viewport) {
  const scroller = $("stripScroll");
  if (!scroller || !stripGeometry) return;
  const geom = stripGeometry;
  const at = i => geom.padX + (geom.width - 2 * geom.padX) * (i / geom.span);
  const left = at(startIdx);
  const right = at(endIdx);
  const maxScroll = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
  let want = scroller.scrollLeft;
  if (left < scroller.scrollLeft + 10) want = left - 24;
  else if (right > scroller.scrollLeft + viewport - 10) want = right - viewport + 24;
  want = Math.max(0, Math.min(maxScroll, want));
  if (Math.abs(want - scroller.scrollLeft) > 2) scroller.scrollLeft = want;
}

/* the two edge labels describe the slice currently on screen */
function syncStripDates() {
  const scroller = $("stripScroll");
  const from = $("stripFrom");
  const to = $("stripTo");
  if (!scroller || !from || !to || !stripGeometry || !daySeries.length) return;
  const geom = stripGeometry;
  const step = (geom.width - 2 * geom.padX) / Math.max(1, geom.span);
  const first = Math.max(0, Math.min(geom.n - 1, Math.round((scroller.scrollLeft - geom.padX) / Math.max(1, step))));
  const last = Math.max(first, Math.min(geom.n - 1, Math.round((scroller.scrollLeft + scroller.clientWidth - geom.padX) / Math.max(1, step))));
  from.textContent = shortDay(daySeries[first].date);
  to.textContent = shortDay(daySeries[last].date);
}

/* a rail that is always visible, because macOS hides native scrollbars */
function syncStripRail() {
  const scroller = $("stripScroll");
  const rail = $("stripRail");
  const thumb = $("stripThumb");
  if (!scroller || !rail || !thumb) return;
  const max = scroller.scrollWidth - scroller.clientWidth;
  if (max <= 2) {
    rail.hidden = true;
    return;
  }
  rail.hidden = false;
  const trackW = rail.clientWidth || 1;
  const width = Math.max(30, Math.round(trackW * (scroller.clientWidth / scroller.scrollWidth)));
  thumb.style.width = width + "px";
  const travel = Math.max(1, trackW - width);
  thumb.style.transform = "translateX(" + Math.round((scroller.scrollLeft / max) * travel) + "px)";
}

function initStripRail() {
  const scroller = $("stripScroll");
  const rail = $("stripRail");
  const thumb = $("stripThumb");
  if (!scroller || !rail || !thumb) return;
  scroller.addEventListener("scroll", () => { syncStripRail(); syncStripDates(); }, { passive: true });
  window.addEventListener("resize", syncStripRail);

  let drag = null;
  const scrollTo = clientX => {
    const trackW = rail.clientWidth;
    const width = thumb.offsetWidth;
    const travel = Math.max(1, trackW - width);
    const left = clientX - rail.getBoundingClientRect().left - width / 2;
    const ratio = Math.max(0, Math.min(1, left / travel));
    scroller.scrollLeft = ratio * (scroller.scrollWidth - scroller.clientWidth);
  };

  thumb.addEventListener("pointerdown", event => {
    drag = { x: event.clientX, scroll: scroller.scrollLeft };
    thumb.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });
  thumb.addEventListener("pointermove", event => {
    if (!drag) return;
    const trackW = rail.clientWidth - thumb.offsetWidth;
    const max = scroller.scrollWidth - scroller.clientWidth;
    scroller.scrollLeft = drag.scroll + ((event.clientX - drag.x) / Math.max(1, trackW)) * max;
    event.preventDefault();
  });
  const stop = () => { drag = null; };
  thumb.addEventListener("pointerup", stop);
  thumb.addEventListener("pointercancel", stop);

  rail.addEventListener("pointerdown", event => {
    if (event.target === thumb) return;
    scrollTo(event.clientX);
    event.preventDefault();
  });
}

function shortDay(date) {
  return new Date(date + "T00:00:00+08:00")
    .toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "Asia/Hong_Kong" });
}

function stripIndexAt(clientX) {
  const c = $("weekStrip");
  if (!c || !stripGeometry) return -1;
  const box = c.getBoundingClientRect();
  const geom = stripGeometry;
  const ratio = (clientX - box.left - geom.padX) / Math.max(1, geom.width - 2 * geom.padX);
  const i = Math.round(ratio * geom.span);
  return Math.min(geom.n - 1, Math.max(0, i));
}

function withAlpha(colour, alpha) {
  const hex = String(colour).trim();
  if (hex.startsWith("#") && (hex.length === 7 || hex.length === 4)) {
    const full = hex.length === 4 ? "#" + [...hex.slice(1)].map(c => c + c).join("") : hex;
    const r = parseInt(full.slice(1, 3), 16);
    const gg = parseInt(full.slice(3, 5), 16);
    const b = parseInt(full.slice(5, 7), 16);
    return "rgba(" + r + ", " + gg + ", " + b + ", " + alpha + ")";
  }
  return "rgba(80, 120, 110, " + alpha + ")";
}

function roundRect(g, x, y, w, h, r) {
  const radius = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + radius, y);
  g.arcTo(x + w, y, x + w, y + h, radius);
  g.arcTo(x + w, y + h, x, y + h, radius);
  g.arcTo(x, y + h, x, y, radius);
  g.arcTo(x, y, x + w, y, radius);
  g.closePath();
}

function initWeatherControls() {
  const bounds = coverageBounds();
  if (!bounds) return;
  const input = $("weekDate");
  if (input) {
    input.addEventListener("change", () => {
      if (!input.value) return;
      weather.end = clampEnd(input.value);
      renderWeather();
      saveWeatherState();
    });
  }
  const prev = $("prevWindow"), next = $("nextWindow");
  if (prev) prev.onclick = () => stepWindow(-weather.days);
  if (next) next.onclick = () => stepWindow(weather.days);
  document.querySelectorAll(".wpills button").forEach(b => {
    b.onclick = () => setWindowDays(+b.dataset.days);
  });
  if ($("latestWindow")) {
    $("latestWindow").onclick = () => {
      weather.end = clampEnd(null);
      renderWeather();
      saveWeatherState();
    };
  }

  const strip = $("weekStrip");
  const tip = $("stripTip");
  if (strip && tip) {
    const pointAt = clientX => {
      const index = stripIndexAt(clientX);
      if (index < 0 || !stripGeometry) return null;
      return { index, geom: stripGeometry };
    };
    strip.addEventListener("mousemove", event => {
      const hit = pointAt(event.clientX);
      if (!hit) return;
      const { index, geom } = hit;
      const point = daySeries[index];
      tip.hidden = false;
      tip.textContent = shortDay(point.date) + " · " +
        (point.mean == null ? "no scored items" : (point.mean >= 0 ? "+" : "") + point.mean.toFixed(2)) +
        " · " + point.n + " items";
      const canvasBox = strip.getBoundingClientRect();
      const hostBox = tip.parentElement.getBoundingClientRect();
      const left = canvasBox.left - hostBox.left + geom.padX + (geom.width - 2 * geom.padX) * (index / geom.span);
      tip.style.left = Math.min(Math.max(18, left), hostBox.width - 18) + "px";
    });
    strip.addEventListener("mouseleave", () => { tip.hidden = true; });
    strip.addEventListener("click", event => {
      const hit = pointAt(event.clientX);
      if (!hit) return;
      weather.end = clampEnd(daySeries[hit.index].date);
      renderWeather();
      saveWeatherState();
    });
  }
}

function initHome() {
  const params = new URLSearchParams(location.search);
  const wantedDays = +(params.get("days") || 7);
  weather.days = [1, 7, 30].includes(wantedDays) ? wantedDays : 7;
  weather.end = clampEnd(params.get("date") || null);
  buildDaySeries();
  initWeatherControls();
  initStripRail();
  renderWeather(false);
  const scheme = window.matchMedia("(prefers-color-scheme: dark)");
  const redraw = () => {
    drawGauge(weather.score, false);
    drawStrip(windowRows().start, windowRows().end);
  };
  if (scheme.addEventListener) scheme.addEventListener("change", redraw);
  else if (scheme.addListener) scheme.addListener(redraw);
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => drawStrip(windowRows().start, windowRows().end), 120);
  });
  for (const [id, key] of [["aboutDialog", "about"], ["contactDialog", "contact"]]) {
    const dlg = $(id);
    if (!dlg) continue;
    document.querySelectorAll('[data-panel="' + key + '"]').forEach(b => (b.onclick = () => dlg.showModal()));
    dlg.querySelector(".close").onclick = () => dlg.close();
    dlg.addEventListener("click", e => {
      if (e.target === dlg) dlg.close();
    });
  }
}

/* ------------------------------------------------------------------ *
 * selection
 * ------------------------------------------------------------------ */

const BLOB_SIZE = [148, 112, 126, 100, 136, 108];
const BLOB_DY = ["-10px", "14px", "-22px", "8px", "18px", "-6px"];
const BLOB_ROT = ["-3deg", "2deg", "-1.5deg", "3deg", "-2deg", "1.5deg"];

function choiceReason(key, set, other) {
  if (set.has(key)) return "";
  if (key in platforms && !data.ranges[key]) return "No verified collection window for this source yet.";
  if (other.size > 1 && set.size >= 1) return "Keep one selection on this side while comparing multiple options on the other.";
  return set.size >= 3 ? "Choose up to three options on one side." : "";
}

function buildBubbles(hostId, items, set, other) {
  const host = $(hostId);
  if (!host) return;
  host.replaceChildren();
  Object.entries(items).forEach(([key, label], i) => {
    const b = el("button", null, "blob");
    b.type = "button";
    b.style.setProperty("--size", BLOB_SIZE[i % BLOB_SIZE.length] + "px");
    b.style.setProperty("--dy", BLOB_DY[i % BLOB_DY.length]);
    b.style.setProperty("--rot", BLOB_ROT[i % BLOB_ROT.length]);
    b.append(icon(key, label), el("span", label));
    b.dataset.key = key;
    b.dataset.host = hostId;
    b.setAttribute("aria-pressed", set.has(key));
    const reason = choiceReason(key, set, other);
    if (reason) {
      b.setAttribute("aria-disabled", "true");
      b.title = reason;
    }
    b.onclick = () => {
      if (reason) {
        $("notice").textContent = reason;
        return;
      }
      if (set.has(key)) set.delete(key);
      else set.add(key);
      $("notice").textContent = "";
      updateStart(key);
    };
    host.append(b);
  });
}

function updateStart(refocusKey) {
  buildBubbles("platforms", platforms, ps, cs);
  buildBubbles("companies", companies, cs, ps);
  if (refocusKey) {
    const again = document.querySelector('.blob[data-key="' + refocusKey + '"]');
    if (again) again.focus({ preventScroll: true });
  }
  const r = range();
  $("available").textContent = r
    ? "Shared coverage · " + r.join(" to ") + " · Hong Kong dates"
    : "Choose a source with a verified collection window.";
  for (const field of [$("from"), $("to")]) {
    if (!field) continue;
    field.min = r?.[0] || "";
    field.max = r?.[1] || "";
  }
  const box = $("presets");
  if (box) {
    box.replaceChildren();
    for (const key of ["Past 7 days", "Past month", "Past year", "Custom"]) {
      const b = el("button", key);
      b.type = "button";
      const invalid = key === "Custom" ? !r : !bounds(key);
      const reason = "This period exceeds the selected sources’ shared coverage.";
      b.setAttribute("aria-pressed", preset === key);
      if (invalid) {
        b.setAttribute("aria-disabled", "true");
        b.title = reason;
      }
      b.onclick = () => {
        if (invalid) {
          $("notice").textContent = reason;
          return;
        }
        preset = key;
        if (key === "Custom" && !bounds("Custom")) {
          [$("from").value, $("to").value] = r;
        }
        updateStart();
      };
      box.append(b);
    }
  }
  if ($("custom")) $("custom").hidden = preset !== "Custom";
  const d = bounds(preset);
  const ok = ps.size && cs.size && d;
  const go = $("go");
  if (go) {
    go.setAttribute("aria-disabled", ok ? "false" : "true");
    go.dataset.disabled = ok ? "false" : "true";
    if (ok) {
      const params = new URLSearchParams({
        p: [...ps].join(","),
        c: [...cs].join(","),
        preset,
        from: d[0],
        to: d[1]
      });
      go.href = "report.html?" + params.toString();
    } else {
      go.href = "start.html";
    }
  }
  if ($("sentence")) {
    $("sentence").textContent = ok
      ? "Explore how " + names(ps, platforms) + (ps.size === 1 ? " discusses " : " discuss ") + names(cs, companies) + " from " + d[0] + " to " + d[1] + "."
      : "Choose a platform, a company and an available period.";
  }
  if (r && !d && $("notice")) $("notice").textContent = "Choose dates within " + r.join(" to ") + ".";
}

function initStart() {
  const q = new URLSearchParams(location.search);
  const picked = (q.get("p") || "").split(",").filter(k => platforms[k]);
  const pickedC = (q.get("c") || "").split(",").filter(k => companies[k]);
  const usable = Object.keys(platforms).filter(k => data.ranges[k]);
  picked.forEach(k => ps.add(k));
  pickedC.forEach(k => cs.add(k));
  if (!ps.size && usable.length) ps.add(usable.includes("hacker_news") ? "hacker_news" : usable[0]);
  if (!cs.size) cs.add("openai");
  if (q.get("preset")) preset = q.get("preset");
  if (q.get("from") && $("from")) $("from").value = q.get("from");
  if (q.get("to") && $("to")) $("to").value = q.get("to");
  $("from")?.addEventListener("change", () => updateStart());
  $("to")?.addEventListener("change", () => updateStart());
  updateStart();
}

/* ------------------------------------------------------------------ *
 * report
 * ------------------------------------------------------------------ */

function bucket(d) {
  const grain = $("grain")?.value || "Day";
  if (grain === "Month") return d.slice(0, 7) + "-01";
  if (grain === "Week") return shift(d, -((new Date(d + "T00:00:00Z").getUTCDay() + 6) % 7));
  return d;
}

function makeChart(parent, traces, overrides = {}) {
  const div = el("div", null, "plot");
  parent.append(div);
  const ink = cssVar("--ink", "#153e38");
  const muted = cssVar("--muted", "#5b716b");
  const line = cssVar("--line", "#d9d7cb");
  const axis = extra => {
    const base = extra ? { ...extra } : {};
    const title = { ...(base.title || {}) };
    title.font = { color: muted, ...(title.font || {}) };
    return {
      automargin: true,
      gridcolor: line,
      zerolinecolor: line,
      linecolor: line,
      tickfont: { color: muted, ...(base.tickfont || {}) },
      ...base,
      title
    };
  };
  const { xaxis: xOverride, yaxis: yOverride, ...rest } = overrides;
  const layout = {
    ...data.layout,
    colorway: palette(),
    height: 300,
    font: { ...(data.layout.font || {}), color: ink },
    ...rest,
    xaxis: axis(xOverride),
    yaxis: axis(yOverride)
  };
  const save = el("button", "Save chart PNG", "chart-save");
  save.type = "button";
  parent.append(save);
  save.onclick = () => Plotly.downloadImage(div, {
    format: "png",
    filename: "ai-sentiweather-chart",
    width: 1000,
    height: 480
  });
  /* A grid column can settle after Plotly has measured it (fonts, scrollbars,
     a neighbouring card). Watch the box and re-measure whenever it changes. */
  if ("ResizeObserver" in window) {
    let lastWidth = 0;
    const observer = new ResizeObserver(entries => {
      const width = Math.round(entries[0].contentRect.width);
      if (!div.data || !width || width === lastWidth) return;
      lastWidth = width;
      Plotly.Plots.resize(div);
    });
    observer.observe(div);
    div._resizeObserver = observer;
  }
  return Plotly.newPlot(div, traces, layout, { responsive: true, displayModeBar: false, displaylogo: false });
}

const wordRedraws = [];
let wordResizeBound = false;

function bindWordResize() {
  if (wordResizeBound) return;
  wordResizeBound = true;
  let timer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      for (let i = wordRedraws.length - 1; i >= 0; i--) {
        if (!wordRedraws[i].canvas.isConnected) wordRedraws.splice(i, 1);
        else wordRedraws[i].draw();
      }
    }, 160);
  });
}

function wordCloud(parent, items) {
  const counts = new Map();
  for (const r of items) for (const [w, n] of r.terms) counts.set(w, (counts.get(w) || 0) + n);
  const words = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 72);
  if (!words.length) {
    parent.append(el("p", "No words available."));
    return;
  }

  const c = el("canvas", null, "words");
  c.setAttribute("role", "img");
  c.setAttribute("aria-label", "Frequent words: " + words.slice(0, 12).map(x => x.join(" ")).join(", "));
  parent.append(c);

  const maxN = words[0][1];
  const minN = words[words.length - 1][1];
  const span = Math.max(1, maxN - minN);

  const draw = () => {
    const panel = c.parentElement;
    let width = 900;
    if (panel) {
      const box = getComputedStyle(panel);
      width = Math.round(panel.clientWidth - parseFloat(box.paddingLeft || 0) - parseFloat(box.paddingRight || 0));
    }
    if (!width || width < 80) return;
    const height = Math.round(Math.min(Math.max(width * 0.26, 200), 310));
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    c.width = Math.round(width * dpr);
    c.height = Math.round(height * dpr);
    c.style.width = width + "px";
    c.style.height = height + "px";
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, width, height);
    g.textAlign = "center";
    g.textBaseline = "middle";

    /* keep the biggest words dominant but leave the smallest readable */
    const room = Math.min(1, Math.max(0.66, width / 980));
    const scale = n => {
      const rank = (n - minN) / span;
      return Math.round((14 + 60 * Math.pow(rank, 1.5)) * room);
    };

    const placed = [];
    const cx = width / 2;
    const cy = height / 2;
    /* the spiral is stretched to the shape of the panel, not to a circle */
    const reachX = Math.max(70, width * 0.46 - 18);
    const reachY = Math.max(60, height * 0.44 - 14);
    const stepX = reachX / 34;
    const stepY = reachY / 34;

    words.forEach(([w, n], i) => {
      const size = scale(n);
      g.font = (size >= 30 ? "600 " : "500") + " " + size + "px Georgia, 'Times New Roman', serif";
      const tw = g.measureText(w).width + Math.max(6, size * 0.22);
      const th = size * 1.06;
      for (let j = 0; j < 4200; j++) {
        const a = j * 0.38;
        const r = Math.sqrt(j);
        const x = cx + r * stepX * Math.cos(a);
        const y = cy + r * stepY * Math.sin(a);
        if (Math.abs(x - cx) > reachX * 1.55) break;
        const box = [x - tw / 2, y - th / 2, x + tw / 2, y + th / 2];
        if (box[0] < 3 || box[1] < 3 || box[2] > width - 3 || box[3] > height - 3) continue;
        if (placed.some(p => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) continue;
        placed.push(box);
        g.fillStyle = palette()[i % 3];
        g.fillText(w, x, y);
        break;
      }
    });
  };

  draw();
  bindWordResize();
  wordRedraws.push({ canvas: c, draw });
  parent.append(el("small", words.slice(0, 8).map(([w, n]) => w + " (" + n + ")").join(" · ")));
}

function renderReport() {
  const seq = ++renderVersion;
  const dates = bounds(preset);
  if (!dates) return;
  const basis = cs.size > 1 ? "company" : "platform";
  const options = basis === "company" ? [...cs] : [...ps];
  const kind = $("kind")?.value || "all";
  selectedRows = data.rows.filter(r =>
    ps.has(r.platform) &&
    r.companies.some(c => cs.has(c)) &&
    r.date >= dates[0] && r.date <= dates[1] &&
    (kind === "all" || (kind === "comment" ? r.type === "comment" : r.type !== "comment"))
  );
  const groups = options.map(k => ({
    name: companies[k] || platforms[k],
    rows: selectedRows.filter(r => (basis === "company" ? r.companies.includes(k) : r.platform === k))
  }));
  $("reportTitle").textContent = names(cs, companies) + " on " + names(ps, platforms);
  $("subtitle").textContent = dates.join(" to ") + " · Hong Kong time · " + selectedRows.length + " distinct items";
  $("reportNote").textContent = "Forum counts are samples. Scores estimate text tone using an English lexicon. Media scores use headlines. " + statusText();
  const parent = $("charts");
  parent.querySelectorAll(".plot").forEach(d => {
    if (d._resizeObserver) d._resizeObserver.disconnect();
    Plotly.purge(d);
  });
  parent.replaceChildren();
  const buckets = new Set();
  for (let d = dates[0]; d <= dates[1]; d = shift(d, 1)) buckets.add(bucket(d));
  const xs = [...buckets];
  const tasks = [];
  const keyed = groups.map(group => {
    const map = new Map();
    for (const r of group.rows) {
      const k = bucket(r.date);
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(r);
    }
    return map;
  });
  const line = which => groups.map((g, i) => ({
    ...data.traces.trend,
    name: g.name,
    x: xs,
    y: xs.map(d => {
      const b = keyed[i].get(d) || [];
      return which === "count" ? b.length : avg(b.map(r => r.score).filter(v => v != null));
    }),
    connectgaps: false
  }));
  const count = (g, a) => g.rows.filter(r => r.attitude === a).length;
  for (const [i, name] of sections.entries()) {
    if ($("toggle" + i) && !$("toggle" + i).checked) continue;
    const panel = el("section", null, "panel");
    panel.append(el("h2", name));
    parent.append(panel);
    if (!selectedRows.length) {
      panel.append(el("p", "No matching items in this selection."));
      continue;
    }
    if (i === 0 || i === 3) {
      tasks.push(makeChart(panel, line(i === 0 ? "count" : "score"), {
        xaxis: { type: "date" },
        yaxis: i === 0
          ? { rangemode: "tozero", title: { text: "Items" } }
          : { range: [-1, 1], title: { text: "Mean score" } }
      }));
    }
    if (i === 1) {
      const means = groups.map(g => avg(g.rows.map(r => r.score).filter(v => v != null)));
      tasks.push(makeChart(panel, [{
        ...data.traces.average,
        y: groups.map(g => g.name),
        x: means,
        marker: { color: palette() },
        text: means.map(v => (v == null ? "" : v.toFixed(2))),
        textposition: "auto"
      }], { xaxis: { range: [-1, 1] }, yaxis: { automargin: true } }));
      panel.append(el("small", groups.map(g => g.name + " · " + g.rows.filter(r => r.score != null).length + " scored").join(" / ")));
    }
    if (i === 2) {
      tasks.push(makeChart(panel, attitudes.map((a, j) => ({
        ...data.traces.distribution,
        name: a,
        x: groups.map(g => g.name),
        y: groups.map(g => count(g, a)),
        marker: { color: attitudeColors()[j] }
      })), { barmode: "stack", yaxis: { title: { text: "Scored items" } } }));
      const grid = el("div", null, "piegrid");
      panel.append(grid);
      /* Lay every card out first: Plotly measures its container when it draws,
         so a card must already sit in its final grid column. */
      const cards = groups.map(g => {
        const card = el("div");
        grid.append(card);
        card.append(el("h3", g.name));
        return { card, g };
      });
      for (const { card, g } of cards) {
        const ns = attitudes.map(a => count(g, a));
        if (ns.some(Boolean)) {
          tasks.push(makeChart(card, [{
            ...data.traces.share,
            labels: attitudes,
            values: ns,
            sort: false,
            hole: 0.45,
            marker: { colors: attitudeColors() },
            textinfo: "percent"
          }], { height: 240, margin: { t: 10, l: 12, r: 12, b: 10 }, showlegend: false }));
        } else card.append(el("p", "No scored items."));
      }
    }
    if (i === 4) {
      for (const g of groups) {
        panel.append(el("h3", g.name));
        wordCloud(panel, g.rows);
      }
    }
    if (i === 5) {
      for (const g of groups) {
        panel.append(el("h3", g.name));
        for (const a of attitudes) {
          const ranking = r => (a === "positive" ? -r.score : a === "negative" ? r.score : Math.abs(r.score));
          const r = g.rows.filter(r => r.attitude === a).sort((l, rr) =>
            ranking(l) - ranking(rr) ||
            (rr.time || rr.date).localeCompare(l.time || l.date) ||
            l.id.localeCompare(rr.id)
          )[0];
          const div = el("div", null, "sample");
          div.append(el("span", a, "tag"));
          if (r) {
            div.append(el("p", r.excerpt), el("small", platforms[r.platform] + " · " + r.date + " · " + r.precision + " · " + r.score.toFixed(2)));
            if (r.url) {
              const link = el("a", "Open original");
              link.href = r.url;
              link.target = "_blank";
              link.rel = "noopener noreferrer";
              div.append(link);
            }
          } else div.append(el("p", "No matching example."));
          panel.append(div);
        }
      }
    }
  }
  lastDraw = Promise.all(tasks).then(() => {
    if (seq !== renderVersion) return;
    parent.querySelectorAll(".plot").forEach(div => {
      if (div.data) Plotly.Plots.resize(div);
    });
  }).catch(() => {
    if (seq === renderVersion && $("exportStatus")) $("exportStatus").textContent = "A chart could not render. Reload to retry.";
  });
}

/* ------------------------------------------------------------------ *
 * exports
 * ------------------------------------------------------------------ */

function download(blob, file) {
  const u = URL.createObjectURL(blob);
  const a = el("a");
  a.href = u;
  a.download = file;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1500);
}

function csv() {
  const cols = ["id", "platform", "type", "date", "time", "precision", "companies", "unknown", "score", "attitude", "scope", "title", "excerpt", "url", "channels", "source_type", "collected_at", "text_scope", "date_boundary_note", "query_terms", ...Object.keys(companies)];
  const escape = v => {
    let s = Array.isArray(v) ? v.join("|") : String(v ?? "");
    if (typeof v !== "number" && /^[\s]*[=+@\-\t\r]/.test(s)) s = "'" + s;
    return '"' + s.replaceAll('"', '""') + '"';
  };
  download(new Blob(["\ufeff", cols.join(",") + "\r\n" + selectedRows.map(r =>
    cols.map(c => escape(c in companies ? (r.companies.includes(c) ? 1 : r.unknown.includes(c) ? "" : 0) : r[c])).join(",")
  ).join("\r\n")], { type: "text/csv;charset=utf-8" }), "ai-sentiweather.csv");
}

async function png() {
  await lastDraw;
  const pieces = [];
  let height = 35;
  const measuring = el("canvas").getContext("2d");
  function paragraph(text, size = 18) {
    measuring.font = size + "px Arial";
    let line = "";
    for (const w of String(text).split(/\s+/)) {
      if (line && measuring.measureText(line + " " + w).width > 920) {
        pieces.push([line, height, size]);
        height += size * 1.4;
        line = "";
      }
      line += (line ? " " : "") + w;
    }
    pieces.push([line, height, size]);
    height += size * 1.6;
  }
  paragraph($("reportTitle").textContent, 29);
  paragraph($("subtitle").textContent);
  paragraph($("reportNote").textContent, 15);
  for (const panel of $("charts").children) {
    for (const node of panel.querySelectorAll("h2,h3,.plot,.words,.sample,small,p")) {
      if ((node.closest(".plot") && !node.matches(".plot")) || (node.closest(".sample") && !node.matches(".sample"))) continue;
      if (node.matches(".plot,.words")) {
        const im = new Image();
        im.src = node.matches(".plot")
          ? await Plotly.toImage(node, { format: "png", width: 920, height: 290 })
          : node.toDataURL();
        await im.decode();
        pieces.push([im, height]);
        height += (im.height * 920) / im.width + 18;
      } else paragraph(node.innerText, node.matches("h2") ? 25 : 17);
    }
    height += 12;
  }
  const c = el("canvas");
  c.width = 1000;
  c.height = Math.ceil(height + 30);
  const g = c.getContext("2d");
  g.fillStyle = "#fbfaf5";
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = "#13302a";
  g.textBaseline = "top";
  for (const [v, y, s] of pieces) {
    if (typeof v === "string") {
      g.font = s + "px Arial";
      g.fillText(v, 40, y);
    } else g.drawImage(v, 40, y, 920, (v.height * 920) / v.width);
  }
  c.toBlob(b => {
    if (b) download(b, "ai-sentiweather.png");
    else if ($("exportStatus")) $("exportStatus").textContent = "Image too large. Hide a section and try again.";
  });
}

/* ------------------------------------------------------------------ *
 * report page init
 * ------------------------------------------------------------------ */

function initReport() {
  const q = new URLSearchParams(location.search);
  (q.get("p") || "").split(",").filter(k => platforms[k]).forEach(k => ps.add(k));
  (q.get("c") || "").split(",").filter(k => companies[k]).forEach(k => cs.add(k));
  if (!ps.size || !cs.size) {
    location.replace("start.html?" + q.toString());
    return;
  }
  if (q.get("preset")) preset = q.get("preset");
  const from = q.get("from");
  const to = q.get("to");
  if (from && to) fixedWindow = [from, to];
  sections.forEach((name, i) => {
    const l = el("label");
    const b = el("input");
    b.type = "checkbox";
    b.id = "toggle" + i;
    b.checked = true;
    b.onchange = renderReport;
    l.append(b, document.createTextNode(" " + name));
    $("toggles").append(l);
  });
  $("grain").onchange = renderReport;
  $("kind").onchange = renderReport;
  $("pdf").onclick = async () => {
    await lastDraw;
    window.print();
  };
  $("png").onclick = () => png().catch(() => {
    if ($("exportStatus")) $("exportStatus").textContent = "PNG export failed. Try again with fewer visible sections.";
  });
  $("csv").onclick = csv;
  const back = $("editSelection");
  if (back) back.href = "start.html?" + new URLSearchParams({ p: [...ps].join(","), c: [...cs].join(","), preset }).toString();
  renderReport();
}

/* ------------------------------------------------------------------ *
 * motion modules
 * ------------------------------------------------------------------ */

let revealObserver = null;

function motionQuery() {
  return window.matchMedia("(prefers-reduced-motion: reduce)");
}

function measureWave() {
  for (const path of document.querySelectorAll(".wave-line, .wave-glow")) {
    try {
      path.style.setProperty("--len", Math.ceil(path.getTotalLength()) + "px");
    } catch (error) {
      /* geometry unavailable: CSS fallback length is used */
    }
  }
}

function armReveals(scope = document) {
  const targets = [...scope.querySelectorAll("[data-reveal]:not(.is-in)")];
  if (!targets.length) return;
  if (motionQuery().matches || !("IntersectionObserver" in window)) {
    targets.forEach(el => el.classList.add("is-in"));
    return;
  }
  if (!revealObserver) {
    revealObserver = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("is-in");
        revealObserver.unobserve(entry.target);
      }
    }, { threshold: 0.06, rootMargin: "0px 0px -6% 0px" });
  }
  targets.forEach(el => revealObserver.observe(el));
}

function playIntro() {
  const targets = [...document.querySelectorAll("[data-reveal]:not(.is-in)")];
  if (!targets.length) return;
  if (motionQuery().matches) {
    targets.forEach(el => el.classList.add("is-in"));
    return;
  }
  requestAnimationFrame(() => requestAnimationFrame(() => {
    targets.forEach(el => el.classList.add("is-in"));
  }));
}

function prepareMotion() {
  measureWave();
  if (PAGE === "home") playIntro();
  else armReveals();
}

prepareMotion();

/* ------------------------------------------------------------------ *
 * countdown to the next daily refresh
 * ------------------------------------------------------------------ */

/* The scheduled job runs at 00:00 UTC, which is 08:00 in Hong Kong. The data
   cutoff is 07:55 HKT so a slightly early trigger still lands on today's
   window. Hong Kong is a fixed UTC+8 offset, so no DST handling is needed. */
const REFRESH_UTC_HOUR = 0;
const REFRESH_UTC_MINUTE = 0;
const HK_OFFSET_MS = 8 * 60 * 60 * 1000;

function nextRefreshAt(now = Date.now()) {
  const hkt = new Date(now + HK_OFFSET_MS);
  const midnight = Date.UTC(hkt.getUTCFullYear(), hkt.getUTCMonth(), hkt.getUTCDate());
  let at = midnight + (REFRESH_UTC_HOUR * 60 + REFRESH_UTC_MINUTE) * 60000;
  if (at <= now) at += 24 * 3600000;
  return at;
}

function startUpdateCountdown() {
  const clock = $("nextClock");
  const at = $("nextAt");
  if (at) {
    const pad = n => String(n).padStart(2, "0");
    const hktH = (REFRESH_UTC_HOUR + 8) % 24;
    at.textContent = pad(hktH) + ":" + pad(REFRESH_UTC_MINUTE) + " HKT (" +
      pad(REFRESH_UTC_HOUR) + ":" + pad(REFRESH_UTC_MINUTE) + " UTC)";
  }
  if (!clock) return;
  const tick = () => {
    const total = Math.max(0, Math.floor((nextRefreshAt() - Date.now()) / 1000));
    const pad = n => String(n).padStart(2, "0");
    clock.textContent = pad(Math.floor(total / 3600)) + ":" +
      pad(Math.floor((total % 3600) / 60)) + ":" + pad(total % 60);
  };
  tick();
  setInterval(tick, 1000);
}

/* ------------------------------------------------------------------ *
 * boot
 * ------------------------------------------------------------------ */

async function init() {
  startUpdateCountdown();
  const res = await fetch("./dashboard.json", { cache: "no-store" });
  if (!res.ok) throw new Error("Snapshot unavailable");
  data = await res.json();
  if (PAGE === "home") initHome();
  if (PAGE === "start") initStart();
  if (PAGE === "report") initReport();
}

init().catch(err => {
  document.querySelectorAll("[data-reveal]").forEach(el => el.classList.add("is-in"));
  const box = $("loadError");
  if (box) {
    box.hidden = false;
    box.textContent = "Could not load the data snapshot (" + (err && err.message ? err.message : "unknown error") + "). Refresh after the next successful build.";
  }
});
