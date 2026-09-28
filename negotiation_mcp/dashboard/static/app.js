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
function compact(v) {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(2) + " tn";
  if (a >= 1e9) return (v / 1e9).toFixed(2) + " bn";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + " m";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + " k";
  return v.toFixed(0);
}
const money = (v) => (v == null ? "—" : `${state.currency} ${compact(v)}`);
const unit = (v) => (v == null ? "—" : `${state.currency} ${v.toLocaleString("en-US", { maximumFractionDigits: v < 100 ? 2 : 0 })}`);
const pct = (v, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
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
      rows.push({ value: unit(v), label: n, color: colors[n] });
    }
    rows.sort((a, b) => parseFloat(b.value.replace(/[^0-9.]/g, "")) - parseFloat(a.value.replace(/[^0-9.]/g, "")));
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

// ---------- pages ----------
async function loadOverview() {
  const d = await api("/api/overview");
  const k = d.kpis;
  clear($("kpis")).append(
    tile("Spend, last 12 months", money(k.spend_12m), `as of ${d.as_of}`),
    tile("Savings identified", money(k.savings_identified), `${pct(k.savings_pct_of_spend)} of spend`),
    tile("Internal price variance", money(k.internal_price_variance), "sites paying above the best site"),
    tile("Alerts", `${k.alerts_high} high`, `${k.alerts_total} in total`),
  );
  table($("opps"), [
    { label: "SKU", get: (r) => r.sku_name + (r.single_source ? " (single-source)" : "") },
    { label: "Spend 12m", num: true, get: (r) => money(r.spend_12m) },
    { label: "Paying / best", num: true, get: (r) => `${unit(r.weighted_price_12m)} / ${compact(r.best_available_price)}` },
    { label: "Opportunity", num: true, get: (r) => money(r.total_opportunity) },
    { label: "% of spend", num: true, get: (r) => pct(r.opportunity_pct_of_spend) },
  ], d.opportunities, (r) => { location.hash = `#sku/${encodeURIComponent(r.sku)}`; });
  hbars($("vendor-bars"), d.vendors.map((v) => ({ label: v.vendor, value: v.spend_12m, v })), {
    fmt: money, note: (r) => `${pct(r.v.share_of_wallet)} of wallet · ${r.v.growth == null ? "new" : (r.v.growth >= 0 ? "+" : "") + pct(r.v.growth)} vs prior year`,
  });
  const ul = clear($("overview-alerts"));
  d.alerts.forEach((a) => ul.append(alertItem(a)));
}

async function loadSku() {
  if (!state.sku) state.sku = state.skus[0]?.sku;
  $("sku-select").value = state.sku;
  const d = await api(`/api/sku/${encodeURIComponent(state.sku)}?by=${state.by}`);
  const b = d.benchmark, t = d.trend;
  clear($("sku-tiles")).append(
    tile("Best price ever (today's money)", unit(b.best_ever.price), `${b.best_ever.vendor} · ${b.best_ever.hospital} · ${b.best_ever.date}`),
    tile("Best Siloam site today", unit(b.internal_best?.price), b.internal_best?.hospital),
    tile("Best equivalent product", unit(b.group_best?.price), b.group_best ? `${b.group_best.vendor} · ${b.group_best.sku}` : ""),
    tile("Paid on average, 12m", unit(b.weighted_avg_paid_12m), `${money(b.annual_spend_12m)} spend`),
    tile("Internal price variance", money(b.internal_price_variance_12m), "paid above the best site"),
  );
  $("trend-title").textContent = `${b.sku_name} — price per ${b.clinical_unit}, monthly, ${state.by === "vendor" ? "by vendor" : "by site"}`;
  const refs = [];
  if (b.internal_best) refs.push({ label: "Best site (12m)", value: b.internal_best.price });
  if (b.group_best && Math.abs(b.group_best.price - (b.internal_best?.price ?? 0)) > 1e-6) refs.push({ label: "Best equivalent (12m)", value: b.group_best.price });
  lineChart($("trend"), { labels: t.months, series: t.series, refs });
  const names = Object.keys(t.series);
  table($("trend-table"), [{ label: "Month", get: (r) => r.m }, ...names.map((n) => ({ label: n, num: true, get: (r) => unit(r[n]) }))],
    t.months.map((m, i) => Object.fromEntries([["m", m], ...names.map((n) => [n, t.series[n][i]])])).reverse());
  const maxPrem = Math.max(...b.by_hospital.map((h) => h.premium_vs_internal_best), 0);
  hbars($("site-bars"), b.by_hospital.map((h, i) => ({ label: h.hospital, value: h.weighted_price, h, color: i === 0 ? cssVar("--series-3") : (h.premium_vs_internal_best === maxPrem && maxPrem > 0.05 ? cssVar("--series-2") : cssVar("--series-1")) })), {
    fmt: unit, note: (r) => `${pct(r.h.premium_vs_internal_best)} over best site · excess ${money(r.h.excess_spend_vs_internal_best)} · ${r.h.vendors.join(", ")}`,
  });
  table($("sku-vendors"), [
    { label: "Vendor", get: (r) => r.vendor },
    { label: "Best (24m)", num: true, get: (r) => unit(r.best_price) },
    { label: "Weighted 12m", num: true, get: (r) => unit(r.weighted_price_12m) },
    { label: "Latest", num: true, get: (r) => `${unit(r.latest_price)} (${r.latest_source})` },
  ], b.by_vendor, (r) => openBrief(b.sku, r.vendor));
  table($("sku-group"), [
    { label: `Equivalent products (${b.equivalence_group})`, get: (r) => r.sku_name },
    { label: "Vendors", get: (r) => r.vendors.join(", ") },
    { label: "Best 12m", num: true, get: (r) => unit(r.best_price_12m) },
  ], b.by_group_sku, (r) => { location.hash = `#sku/${encodeURIComponent(r.sku)}`; });
}

async function loadVendors() {
  const d = await api("/api/vendors");
  const sel = $("vendor-select");
  if (sel.options.length === 1) d.spend.forEach((v) => sel.append(el("option", { value: v.vendor, text: v.vendor })));
  const rows = d.prices.filter((r) => !sel.value || r.vendor === sel.value)
    .sort((a, b) => (b.premium_vs_group_best ?? -1) - (a.premium_vs_group_best ?? -1));
  table($("vendor-table"), [
    { label: "Vendor", get: (r) => r.vendor },
    { label: "SKU", get: (r) => r.sku_name },
    { label: "Weighted 12m", num: true, get: (r) => unit(r.weighted_price_12m) },
    { label: "Group best", num: true, get: (r) => unit(r.group_best_price) },
    { label: "Best from", get: (r) => r.group_best_vendor },
    { label: "Premium", num: true, get: (r) => r.premium_vs_group_best == null ? "—" :
      el("span", { class: `pill ${r.premium_vs_group_best > 0.05 ? "bad" : "good"}`, text: pct(r.premium_vs_group_best) }) },
    { label: "Spend 12m", num: true, get: (r) => money(r.spend_12m) },
  ], rows, (r) => openBrief(r.sku, r.vendor));
}

function openBrief(sku, vendor) {
  location.hash = `#brief/${encodeURIComponent(sku)}/${encodeURIComponent(vendor)}`;
}

async function loadBrief(sku, vendor) {
  const out = clear($("brief-out"));
  if (sku) $("brief-sku").value = sku;
  if (vendor) $("brief-vendor").value = vendor;
  sku = $("brief-sku").value; vendor = $("brief-vendor").value;
  if (!sku || !vendor) return;
  const approver = $("brief-approver").value.trim();
  let d;
  try {
    d = await api(`/api/brief?sku=${encodeURIComponent(sku)}&vendor=${encodeURIComponent(vendor)}${approver ? "&approved_by=" + encodeURIComponent(approver) : ""}`);
  } catch (e) { out.append(el("p", { class: "error", text: e.message })); return; }
  const t = d.targets, q = t.vendor_quoted_unit, cu = t.vendor_clinical_units_per_quoted_unit;

  const head = el("div", { class: "card" },
    el("h2", { text: `${t.sku_name} — ${t.vendor}` }),
    el("p", { class: "headline", text: d.headline }),
    el("div", { class: "kv" },
      el("div", {}, el("div", { class: "l", text: "Opening ask" }), el("div", { class: "big", text: unit(t.opening_ask) }), el("div", { class: "s", text: `${unit(t.opening_ask_per_quoted_unit)} per ${q}` })),
      el("div", { class: "target" }, el("div", { class: "l", text: "Target" }), el("div", { class: "big", text: unit(t.target_price) }), el("div", { class: "s", text: `${unit(t.target_per_quoted_unit)} per ${q}` })),
      el("div", {}, el("div", { class: "l", text: `Walk-away — ${t.walk_away_status}` }), el("div", { class: "big", text: unit(t.proposed_walk_away) }), el("div", { class: "s", text: t.walk_away_basis })),
      el("div", {}, el("div", { class: "l", text: "Annual value at target" }), el("div", { class: "big", text: money(t.annual_saving_at_target) }), el("div", { class: "s", text: `on ${Math.round(t.annual_volume).toLocaleString()} units a year` })),
    ));
  out.append(head);

  const ladderCard = el("div", { class: "card" }, el("h2", { text: "Price ladder, per clinical unit" }),
    el("p", { class: "sub", text: "Every reference price and the recommended prices on one axis. The target sits below every rung." }));
  const ladderBox = el("div");
  ladderCard.append(ladderBox);
  out.append(ladderCard);
  const SHORT = { best_ever: "Best ever", internal_best: "Best Siloam site", vendor_own_best: "Vendor's own best", competitor_best: "Best competitor" };
  const markers = t.references.map((r) => ({ label: SHORT[r.kind] || r.label, value: r.price, color: cssVar("--ref"), detail: [r.vendor, r.hospital, r.date, r.source].filter(Boolean).join(" · ") }));
  markers.push({ label: "Target", value: t.target_price, color: cssVar("--series-1"), strong: true, detail: `${pct(t.beat_margin, 0)} below the lowest reference` });
  markers.push({ label: "Opening ask", value: t.opening_ask, color: cssVar("--series-3"), strong: true, detail: `${pct(t.anchor_margin, 0)} below target` });
  markers.push({ label: "Walk-away (proposed)", value: t.proposed_walk_away, color: cssVar("--series-2"), strong: true, detail: t.walk_away_basis });
  requestAnimationFrame(() => ladder(ladderBox, markers));

  const grid = el("div", { class: "grid2" });
  const ev = el("div", { class: "card" }, el("h2", { text: "Evidence" }));
  const evTable = el("table");
  ev.append(el("div", { class: "table-wrap" }, evTable));
  table(evTable, [
    { label: "Reference", get: (r) => el("span", { title: r.label, text: SHORT[r.kind] || r.label }) },
    { label: "Per unit", num: true, get: (r) => unit(r.price) },
    { label: `Per ${q}`, num: true, get: (r) => unit(r.price * cu) },
    { label: "Where / when", get: (r) => [r.vendor, r.hospital, r.date, r.source].filter(Boolean).join(" · ") },
  ], t.references);
  if (d.beat_check) {
    const bc = d.beat_check;
    ev.append(el("h3", { text: "Today's price against the references" }),
      el("p", {}, el("span", { class: `pill ${bc.status === "BEATS_ALL" ? "good" : "bad"}`, text: bc.status }), " ", bc.headline));
  }
  const lev = el("div", { class: "card" }, el("h2", { text: "Leverage" }),
    el("ul", { class: "plain" }, t.leverage.map((x) => el("li", { text: x }))),
    el("h3", { text: "Verdict" }),
    d.verdict ? el("p", {}, el("strong", { text: d.verdict.headline })) : null,
    el("p", { class: "muted", text: d.verdict_note || "Enter the vendor's offer in Claude (negotiation_brief with an offer) for the counter-offer and verdict." }),
    el("h3", { text: "Caveats" }),
    el("ul", { class: "plain muted" }, t.caveats.map((x) => el("li", { text: x }))));
  grid.append(ev, lev);
  out.append(grid);
}

async function loadAlerts() {
  const rows = await api("/api/alerts");
  const ul = clear($("alerts-list"));
  const shown = rows.filter((a) => !state.sev || a.severity === state.sev);
  shown.forEach((a) => ul.append(alertItem(a)));
  if (!shown.length) ul.append(el("li", { class: "muted", text: "No alerts at this severity." }));
}

// ---------- routing ----------
async function route() {
  const [page, a, b] = (location.hash.slice(1) || "overview").split("/").map(decodeURIComponent);
  document.querySelectorAll(".page").forEach((p) => (p.hidden = p.id !== `page-${page}`));
  document.querySelectorAll(".tabs a").forEach((t) => t.classList.toggle("on", t.dataset.tab === page));
  hideTip();
  try {
    if (page === "overview") await loadOverview();
    else if (page === "sku") { if (a) state.sku = a; await loadSku(); }
    else if (page === "vendors") await loadVendors();
    else if (page === "brief") await loadBrief(a, b);
    else if (page === "alerts") await loadAlerts();
  } catch (e) {
    const p = document.querySelector(`#page-${page}`);
    if (p) p.prepend(el("p", { class: "error", text: e.message }));
  }
}

async function init() {
  const st = await api("/api/status");
  state.currency = st.currency || "IDR";
  $("asof").textContent = `data to ${st.date_to} · ${st.rows.toLocaleString()} price rows · ${st.skus} SKUs · ${st.vendors} vendors · ${st.hospitals} sites`;
  if (st.banner) { $("banner").textContent = st.banner; $("banner").hidden = false; }
  state.skus = await api("/api/skus");
  state.vendors = [...new Set(state.skus.flatMap((s) => s.vendors))].sort();
  for (const sel of [$("sku-select"), $("brief-sku")]) {
    let group = null, og = null;
    for (const s of state.skus) {
      if (s.equivalence_group !== group) { group = s.equivalence_group; og = el("optgroup", { label: group }); sel.append(og); }
      og.append(el("option", { value: s.sku, text: s.sku_name }));
    }
  }
  state.vendors.forEach((v) => $("brief-vendor").append(el("option", { value: v, text: v })));
  const syncVendor = () => {
    const s = state.skus.find((x) => x.sku === $("brief-sku").value);
    if (s && !s.vendors.includes($("brief-vendor").value)) $("brief-vendor").value = s.vendors[0];
  };
  $("brief-sku").addEventListener("change", syncVendor); syncVendor();
  $("sku-select").addEventListener("change", (e) => { location.hash = `#sku/${encodeURIComponent(e.target.value)}`; });
  document.querySelectorAll("[data-by]").forEach((btn) => btn.addEventListener("click", () => {
    state.by = btn.dataset.by;
    document.querySelectorAll("[data-by]").forEach((x) => x.classList.toggle("on", x === btn));
    loadSku();
  }));
  document.querySelectorAll("[data-sev]").forEach((btn) => btn.addEventListener("click", () => {
    state.sev = btn.dataset.sev;
    document.querySelectorAll("[data-sev]").forEach((x) => x.classList.toggle("on", x === btn));
    loadAlerts();
  }));
  $("vendor-select").addEventListener("change", loadVendors);
  $("sku-to-brief").addEventListener("click", () => {
    const s = state.skus.find((x) => x.sku === state.sku);
    openBrief(state.sku, s?.vendors[0] || "");
  });
  $("brief-form").addEventListener("submit", (e) => { e.preventDefault(); openBrief($("brief-sku").value, $("brief-vendor").value); route(); });
  window.addEventListener("hashchange", route);
  let rs; window.addEventListener("resize", () => { clearTimeout(rs); rs = setTimeout(route, 200); });
  await route();
}
init();
