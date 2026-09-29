"use strict";
// Negotiation Intelligence dashboard. Vanilla JS + inline SVG, no external requests.
// Every label from the data goes into the DOM through textContent, never innerHTML.

const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7", "--series-8"];
const state = { currency: "IDR", skus: [], vendors: [], by: "vendor", sku: null, sev: "", colorOf: {} };

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return n;
}
const NS = "http://www.w3.org/2000/svg";
function sv(tag, attrs = {}, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
}
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
// Indonesian number style: Rp 9.905, Rp 5,36 M, 10,6%. Non-IDR currencies keep the international style.
const isIDR = () => (state.currency || "IDR").toUpperCase() === "IDR";
const nf = (d) => new Intl.NumberFormat(isIDR() ? "id-ID" : "en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const num = (v, d = 0) => (v == null ? "—" : nf(d).format(v));
const SCALES = [[1e12, "T", 2], [1e9, "M", 2], [1e6, "jt", 1], [1e3, "rb", 1]];
function compact(v) {
  if (v == null) return "—";
  for (const [size, word, d] of SCALES) if (Math.abs(v) >= size) return `${num(v / size, d)} ${word}`;
  return num(v, 0);
}
const prefix = (v, body) => `${v < 0 ? "-" : ""}${isIDR() ? "Rp" : state.currency} ${body}`;
const money = (v) => (v == null ? "—" : prefix(v, compact(Math.abs(v))));
const unit = (v) => (v == null ? "—" : prefix(v, num(Math.abs(v), Math.abs(v) < 100 && v !== Math.trunc(v) ? 2 : 0)));
const pct = (v, d = 1) => (v == null ? "—" : `${num(v * 100, d)}%`);
async function api(path) {
  const r = await fetch(path);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
function colorFor(name, i) {
  // Colour follows the entity, not its rank: a name keeps its slot once assigned.
  if (!(name in state.colorOf)) state.colorOf[name] = Object.keys(state.colorOf).length;
  return cssVar(SERIES[(i ?? state.colorOf[name]) % SERIES.length]);
}

// ---------- tooltip ----------
const tip = $("tooltip");
function showTip(evt, header, rows) {
  clear(tip);
  tip.append(el("div", { class: "tt-h", text: header }));
  for (const r of rows) {
    tip.append(el("div", { class: "row" },
      r.color ? el("span", { class: "key", style: `background:${r.color}` }) : null,
      el("strong", { text: r.value }), el("span", { text: r.label })));
  }
  tip.hidden = false;
  const x = Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  const y = Math.min(evt.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.hidden = true; };

// ---------- charts ----------
function table(target, headers, rows, onClick) {
  const t = clear(target);
  const thead = el("thead", {}, el("tr", {}, headers.map((h) => el("th", { class: h.num ? "num" : null, text: h.label }))));
  const tbody = el("tbody");
  for (const r of rows) {
    const tr = el("tr", { class: onClick ? "clickable" : null, onclick: onClick ? () => onClick(r) : null });
    headers.forEach((h) => {
      const v = h.get(r);
      tr.append(el("td", { class: h.num ? "num" : null }, v instanceof Node ? v : String(v ?? "—")));
    });
    tbody.append(tr);
  }
  t.append(thead, tbody);
}

function hbars(target, rows, { fmt, note, color }) {
  // Single-series horizontal bars, value labels at the bar end, hover tooltip on each bar.
  const box = clear(target);
  const W = Math.max(box.clientWidth || 480, 320), rowH = 30, labelW = Math.min(170, W * 0.36), valW = 96;
  const H = rows.length * rowH + 8;
  const max = Math.max(...rows.map((r) => r.value), 1);
  const s = sv("svg", { width: "100%", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "bar chart" });
  const plotW = W - labelW - valW;
  rows.forEach((r, i) => {
    const y = i * rowH + 6, w = Math.max(2, (r.value / max) * plotW);
    const g = sv("g", { tabindex: 0 });
    g.append(sv("text", { x: labelW - 8, y: y + 14, "text-anchor": "end" }, r.label));
    g.append(sv("rect", { x: labelW, y: y + 2, width: w, height: 16, rx: 4, fill: r.color || color || cssVar("--series-1") }));
    g.append(sv("text", { x: labelW + w + 6, y: y + 14, class: "val" }, fmt(r.value)));
    g.append(sv("rect", { x: 0, y, width: W, height: rowH, fill: "transparent" }));
    const h = (e) => showTip(e, r.label, [{ value: fmt(r.value), label: note ? note(r) : "" }]);
    g.addEventListener("pointermove", h); g.addEventListener("pointerleave", hideTip);
    g.addEventListener("focus", (e) => { const b = g.getBoundingClientRect(); h({ clientX: b.left + labelW + w, clientY: b.top }); });
    g.addEventListener("blur", hideTip);
    s.append(g);
  });
  box.append(s);
}

function lineChart(target, { labels, series, refs }) {
  const box = clear(target);
  const names = Object.keys(series);
  const colors = Object.fromEntries(names.map((n) => [n, colorFor(n)]));
  const legend = el("div", { class: "legend" },
    names.map((n) => el("span", {}, el("i", { style: `background:${colors[n]}` }), n)),
    (refs || []).map((r) => el("span", {}, el("i", { class: "dash" }), r.label)));
  box.append(legend);

  const W = Math.max(box.clientWidth || 700, 340), H = 300, m = { l: 64, r: names.length <= 4 ? 120 : 16, t: 10, b: 28 };
  const vals = names.flatMap((n) => series[n].filter((v) => v != null)).concat((refs || []).map((r) => r.value));
  if (!vals.length) { box.append(el("p", { class: "muted", text: "No purchase orders in this window." })); return; }
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || hi * 0.05; lo -= pad; hi += pad;
  const x = (i) => m.l + (i / Math.max(labels.length - 1, 1)) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  const s = sv("svg", { width: "100%", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "price trend" });

  for (let k = 0; k <= 4; k++) {
    const v = lo + ((hi - lo) * k) / 4;
    s.append(sv("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
    s.append(sv("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, compact(v)));
  }
  labels.forEach((lab, i) => {
    if (i % 6 === 0 || i === labels.length - 1) s.append(sv("text", { x: x(i), y: H - 8, "text-anchor": "middle" }, lab));
  });
  for (const r of refs || []) {
    s.append(sv("line", { x1: m.l, x2: W - m.r, y1: y(r.value), y2: y(r.value), stroke: cssVar("--ref"), "stroke-dasharray": "5 4", "stroke-width": 1.5 }));
    s.append(sv("text", { x: m.l + 6, y: y(r.value) - 5 }, `${r.label} ${unit(r.value)}`));
  }
  const ends = [];
  for (const n of names) {
    let d = "", pen = false;
    series[n].forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
    });
    s.append(sv("path", { d, fill: "none", stroke: colors[n], "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const lastI = series[n].map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0).pop();
    if (lastI != null) ends.push({ n, i: lastI, v: series[n][lastI] });
  }
  if (names.length <= 4) {
    // Direct labels at line ends, nudged apart so they never collide.
    ends.sort((a, b) => y(a.v) - y(b.v));
    let prev = -Infinity;
    for (const e of ends) {
      const yy = Math.max(y(e.v) + 4, prev + 14); prev = yy;
      s.append(sv("text", { x: x(e.i) + 8, y: yy }, e.n.length > 16 ? e.n.slice(0, 15) + "…" : e.n));
    }
  }
  const cross = sv("line", { y1: m.t, y2: H - m.b, stroke: cssVar("--text-muted"), "stroke-width": 1, visibility: "hidden" });
  const dots = sv("g");
  s.append(cross, dots);
  const hit = sv("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" });
  hit.addEventListener("pointermove", (e) => {
    const bb = s.getBoundingClientRect(), px = ((e.clientX - bb.left) / bb.width) * W;
    const i = Math.max(0, Math.min(labels.length - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (labels.length - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    clear(dots);
    const rows = [];
    for (const n of names) {
      const v = series[n][i];
      if (v == null) continue;
      dots.append(sv("circle", { cx: x(i), cy: y(v), r: 4, fill: colors[n], stroke: cssVar("--surface-1"), "stroke-width": 2 }));
      rows.push({ raw: v, value: unit(v), label: n, color: colors[n] });
    }
    rows.sort((a, b) => b.raw - a.raw);
    for (const r of refs || []) rows.push({ value: unit(r.value), label: r.label });
    showTip(e, labels[i], rows);
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); clear(dots); hideTip(); });
  s.append(hit);
  box.append(s);
}

function ladder(target, markers) {
  // A number line: every reference price and the recommended prices, on one axis.
  const box = clear(target);
  const W = Math.max(box.clientWidth || 700, 340), H = 48 + markers.length * 22, m = { l: 16, r: 16 };
  const vals = markers.map((k) => k.value);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.06 || hi * 0.05; lo -= pad; hi += pad;
  const x = (v) => m.l + ((v - lo) / (hi - lo)) * (W - m.l - m.r);
  const s = sv("svg", { width: "100%", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "price ladder" });
  const axisY = H - 22;
  s.append(sv("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: axisY, y2: axisY }));
  s.append(sv("text", { x: m.l, y: H - 4 }, "cheaper ←"));
  s.append(sv("text", { x: W - m.r, y: H - 4, "text-anchor": "end" }, "→ dearer"));
  markers.sort((a, b) => a.value - b.value).forEach((k, i) => {
    const cx = x(k.value), yy = 14 + i * 22;
    const g = sv("g", { tabindex: 0 });
    g.append(sv("line", { x1: cx, x2: cx, y1: yy + 4, y2: axisY, stroke: k.color, "stroke-width": k.strong ? 2 : 1, "stroke-dasharray": k.strong ? null : "3 3" }));
    g.append(sv("circle", { cx, cy: axisY, r: k.strong ? 6 : 4.5, fill: k.color, stroke: cssVar("--surface-1"), "stroke-width": 2 }));
    const text = `${k.label} · ${unit(k.value)}`;
    const anchor = cx + 6 + text.length * 6.6 > W - m.r ? "end" : "start";
    g.append(sv("text", { x: cx + (anchor === "end" ? -6 : 6), y: yy + 4, "text-anchor": anchor, class: k.strong ? "val" : null }, text));
    const h = (e) => showTip(e, k.label, [{ value: unit(k.value), label: k.detail || "", color: k.color }]);
    g.addEventListener("pointermove", h); g.addEventListener("pointerleave", hideTip);
    s.append(g);
  });
  box.append(s);
}

function tile(label, value, note) {
  return el("div", { class: "tile" }, el("div", { class: "label", text: label }), el("div", { class: "value", text: value }), note ? el("div", { class: "note", text: note }) : null);
}
function alertItem(a) {
  return el("li", {},
    el("span", {}, el("span", { class: `pill ${a.severity}`, text: a.severity })),
    el("span", { class: "t", text: a.title }),
    el("span", { class: "v", text: a.annual_impact ? money(a.annual_impact) : "" }),
    el("span", { class: "d", text: a.detail }));
}

