"use strict";
// Full analytics views (overview, SKU explorer, vendors, brief, alerts). Helpers live in common.js.
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

let briefSeq = 0;
async function loadBrief(sku, vendor) {
  const seq = ++briefSeq;
  if (sku) $("brief-sku").value = sku;
  if (vendor) $("brief-vendor").value = vendor;
  sku = $("brief-sku").value; vendor = $("brief-vendor").value;
  if (!sku || !vendor) return;
  const approver = $("brief-approver").value.trim();
  let d;
  try {
    d = await api(`/api/brief?sku=${encodeURIComponent(sku)}&vendor=${encodeURIComponent(vendor)}${approver ? "&approved_by=" + encodeURIComponent(approver) : ""}`);
  } catch (e) {
    if (seq === briefSeq) clear($("brief-out")).append(el("p", { class: "error", text: e.message }));
    return;
  }
  // A newer request started while this one was loading: let it render instead.
  if (seq !== briefSeq) return;
  const out = clear($("brief-out"));
  const t = d.targets, q = t.vendor_quoted_unit, cu = t.vendor_clinical_units_per_quoted_unit;

  const head = el("div", { class: "card" },
    el("h2", { text: `${t.sku_name} — ${t.vendor}` }),
    el("p", { class: "headline", text: d.headline }),
    el("div", { class: "kv" },
      el("div", {}, el("div", { class: "l", text: "Opening ask" }), el("div", { class: "big", text: unit(t.opening_ask) }), el("div", { class: "s", text: `${unit(t.opening_ask_per_quoted_unit)} per ${q}` })),
      el("div", { class: "target" }, el("div", { class: "l", text: "Target" }), el("div", { class: "big", text: unit(t.target_price) }), el("div", { class: "s", text: `${unit(t.target_per_quoted_unit)} per ${q}` })),
      el("div", {}, el("div", { class: "l", text: `Walk-away — ${t.walk_away_status}` }), el("div", { class: "big", text: unit(t.proposed_walk_away) }), el("div", { class: "s", text: t.walk_away_basis })),
      el("div", {}, el("div", { class: "l", text: "Annual value at target" }), el("div", { class: "big", text: money(t.annual_saving_at_target) }), el("div", { class: "s", text: `on ${num(t.annual_volume)} units a year` })),
    ));
  out.append(head);

  const chartCard = el("div", { class: "card" }, el("h2", { text: "How the prices compare, per clinical unit" }),
    el("p", { class: "sub", text: "Cheapest first. Bars start at zero; the dashed line is the target, and every price on record sits to its right." }));
  const chartBox = el("div");
  chartCard.append(chartBox);
  out.append(chartCard);
  requestAnimationFrame(() => priceBars(chartBox, evidenceRows(t), { targetValue: t.target_price, packSize: cu, packName: q }));
  const SHORT = { best_ever: "Best ever", internal_best: "Best Siloam site", vendor_own_best: "Vendor's own best", competitor_best: "Best competitor" };

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
  $("asof").textContent = `data to ${st.date_to} · ${num(st.rows)} price rows · ${st.skus} SKUs · ${st.vendors} vendors · ${st.hospitals} sites`;
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
