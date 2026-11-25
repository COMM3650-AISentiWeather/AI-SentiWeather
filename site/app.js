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
const palette = ["#19685b", "#a24869", "#416da7"];
const attitudeColors = ["#247464", "#a6ada0", "#af5364"];
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
const icon = (id, label = "") => {
  const im = el("img");
  im.src = "./icons/" + (icons[id] || id) + ".svg";
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

function drawGauge(value) {
  const c = $("gauge");
  if (!c) return;
  const g = c.getContext("2d");
  const x = 280, y = 223, r = 176;
  g.clearRect(0, 0, c.width, c.height);
  g.lineWidth = 23;
  const col = ["#af5364", "#c78c80", "#cdc9ae", "#99bb9c", "#307968"];
  col.forEach((v, i) => {
    g.beginPath();
    g.strokeStyle = v;
    g.arc(x, y, r, Math.PI * (1 + i / 5) + 0.016, Math.PI * (1 + (i + 1) / 5) - 0.016);
    g.stroke();
  });
  g.fillStyle = "#50635c";
  g.textAlign = "center";
  g.font = "21px Arial";
  g.fillText("−1", x - r, y + 31);
  g.fillText("+1", x + r, y + 31);
  if (value == null) return;
  const a = Math.PI * (1 + (value + 1) / 2);
  g.beginPath();
  g.moveTo(x, y);
  g.lineTo(x + Math.cos(a) * (r + 8), y + Math.sin(a) * (r + 8));
  g.strokeStyle = "#183930";
  g.lineWidth = 7;
  g.stroke();
  g.beginPath();
  g.arc(x, y, 11, 0, Math.PI * 2);
  g.fill();
}

function week() {
  const end = Date.parse(data.cutoff);
  const start = end - 7 * 86400000;
  const rows = data.rows.filter(r => {
    if (!r.companies.length || r.score == null) return false;
    if (r.time) {
      const t = Date.parse(r.time);
      return start <= t && t < end;
    }
    const d = Date.parse(r.date + "T00:00:00+08:00");
    return d >= start && d + 86400000 <= end;
  });
  const score = avg(rows.map(r => r.score));
  drawGauge(score);
  $("weekValue").textContent = score == null ? "—" : score.toFixed(2);
  $("weekLabel").textContent = score == null ? "No scored items" : score >= 0.05 ? "Positive" : score <= -0.05 ? "Negative" : "Neutral";
  $("weekDates").textContent = hkdate(new Date(start).toISOString()) + " to " + hkdate(data.cutoff) + " · 10:00 HKT";
  const used = new Set(rows.map(r => r.platform));
  $("weekCoverage").textContent = rows.length + " scored items · " + used.size + " platforms represented. " + statusText();
}

function initHome() {
  week();
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
  const layout = {
    ...data.layout,
    height: 300,
    xaxis: { automargin: true },
    yaxis: { automargin: true },
    ...overrides
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
  return Plotly.newPlot(div, traces, layout, { responsive: true, displayModeBar: false, displaylogo: false });
}

function wordCloud(parent, items) {
  const counts = new Map();
  for (const r of items) for (const [w, n] of r.terms) counts.set(w, (counts.get(w) || 0) + n);
  const words = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 48);
  if (!words.length) {
    parent.append(el("p", "No words available."));
    return;
  }
  const c = el("canvas", null, "words");
  c.width = 920;
  c.height = 340;
  const g = c.getContext("2d");
  const placed = [];
  g.textAlign = "center";
  g.textBaseline = "middle";
  for (const [i, [w, n]] of words.entries()) {
    const size = 16 + 42 * Math.sqrt(n / words[0][1]);
    g.font = "500 " + size + "px Georgia";
    const width = g.measureText(w).width + 9;
    const h = size + 6;
    for (let j = 0; j < 1300; j++) {
      const a = j * 0.45;
      const r = Math.sqrt(j) * 5.8;
      const x = 460 + r * 2.25 * Math.cos(a);
      const y = 170 + r * 0.77 * Math.sin(a);
      const box = [x - width / 2, y - h / 2, x + width / 2, y + h / 2];
      if (box[0] < 3 || box[1] < 2 || box[2] > 917 || box[3] > 338 || placed.some(p => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) continue;
      placed.push(box);
      g.fillStyle = palette[i % 3];
      g.fillText(w, x, y);
      break;
    }
  }
  c.setAttribute("role", "img");
  c.setAttribute("aria-label", "Frequent words: " + words.slice(0, 12).map(x => x.join(" ")).join(", "));
  parent.append(c);
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
  parent.querySelectorAll(".plot").forEach(d => Plotly.purge(d));
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
        marker: { color: palette },
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
        marker: { color: attitudeColors[j] }
      })), { barmode: "stack", yaxis: { title: { text: "Scored items" } } }));
      const grid = el("div", null, "piegrid");
      panel.append(grid);
      for (const g of groups) {
        const card = el("div");
        grid.append(card);
        card.append(el("h3", g.name));
        const ns = attitudes.map(a => count(g, a));
        if (ns.some(Boolean)) {
          tasks.push(makeChart(card, [{
            ...data.traces.share,
            labels: attitudes,
            values: ns,
            sort: false,
            hole: 0.45,
            marker: { colors: attitudeColors },
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
  lastDraw = Promise.all(tasks).catch(() => {
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
 * boot
 * ------------------------------------------------------------------ */

async function init() {
  const res = await fetch("./dashboard.json", { cache: "no-cache" });
  if (!res.ok) throw new Error("Snapshot unavailable");
  data = await res.json();
  if (PAGE === "home") initHome();
  if (PAGE === "start") initStart();
  if (PAGE === "report") initReport();
}

init().catch(err => {
  const box = $("loadError");
  if (box) {
    box.hidden = false;
    box.textContent = "Could not load the data snapshot (" + (err && err.message ? err.message : "unknown error") + "). Refresh after the next successful build.";
  }
});
