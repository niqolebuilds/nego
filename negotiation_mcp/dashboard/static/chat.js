"use strict";
// Two-page assistant. Page 1 explains the app; page 2 is a guided conversation that
// shows one thing at a time. Helpers (el, sv, formatting, priceBars, hbars, api) are in common.js.
// Every label from the data goes into the DOM through textContent, never innerHTML.

const chat = { catalog: null, started: false, formSeq: 0 };
const thread = $("thread");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const dateFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const fmtDate = (iso) => dateFmt.format(new Date(iso + "T00:00:00"));

// ---------- conversation primitives ----------
function reveal(node) { node.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" }); }
function bot(...kids) {
  const bubble = el("div", { class: "bubble" }, ...kids);
  const msg = el("div", { class: "msg bot" }, el("span", { class: "avatar", "aria-hidden": "true" }), bubble);
  thread.append(msg);
  reveal(msg);
  return bubble;
}
function botWide(...kids) { const b = bot(...kids); b.parentElement.classList.add("wide"); return b; }
function me(text) {
  const msg = el("div", { class: "msg me" }, el("div", { class: "bubble", text }));
  thread.append(msg);
  return msg;
}
async function thinking(promise) {
  const msg = el("div", { class: "msg bot" }, el("span", { class: "avatar", "aria-hidden": "true" }),
    el("div", { class: "bubble", "aria-label": "Working" }, el("span", { class: "typing" }, el("i"), el("i"), el("i"))));
  thread.append(msg);
  reveal(msg);
  try { return await promise; } finally { msg.remove(); }
}
function act(label, onclick, kind = "") { return el("button", { type: "button", class: `act ${kind}`.trim(), onclick, text: label }); }
function oops(e, retry) {
  bot(el("p", { text: e.message || String(e) }),
    el("div", { class: "actions" }, retry ? act("Try again", retry) : null, act("Ask about another product", askForm, "secondary")));
}
const unitName = (d) => d?.benchmark?.clinical_unit || "unit";

// ---------- step 1: the question ----------
// The quick-action dock stays hidden until the first choice, so the opening screen asks one question only.
function showDock() { $("dock").hidden = false; }
function greet() {
  $("dock").hidden = true;
  bot(
    el("p", {}, "Hi, I'm your negotiation assistant."),
    el("p", {}, el("b", { text: "Do you want to ask me anything, or do you want to see Renewal Calendar & Risk Alerts?" })),
    el("div", { class: "choices" },
      el("button", { type: "button", class: "choice", onclick: () => { me("Ask me anything"); showDock(); askForm(); } },
        el("b", { text: "Ask me anything" }),
        el("span", { text: "The latest negotiation price and the 3 best price options for one product and vendor." })),
      el("button", { type: "button", class: "choice", onclick: () => { me("Renewal Calendar & Risk Alerts"); showDock(); openCalendar(); } },
        el("b", { text: "Renewal Calendar & Risk Alerts" }),
        el("span", { text: "Identify contracts expiring in 30–90 days with pre-built negotiation targets and volume leverage." }))),
  );
}

// ---------- ask me anything ----------
function skuByText(text) {
  const t = text.trim().toLowerCase();
  return chat.catalog.skus.find((s) => s.sku_name.toLowerCase() === t || s.sku.toLowerCase() === t);
}

function askForm(prefill = {}) {
  const id = ++chat.formSeq;
  const skuList = el("datalist", { id: `dl-sku-${id}` }, chat.catalog.skus.map((s) => el("option", { value: s.sku_name, label: s.equivalence_group })));
  const vendorList = el("datalist", { id: `dl-vendor-${id}` });
  const skuIn = el("input", { id: `sku-${id}`, list: skuList.id, placeholder: "e.g. IV cannula 22G", autocomplete: "off", required: "", value: prefill.skuName || null });
  const vendorIn = el("input", { id: `vendor-${id}`, list: vendorList.id, placeholder: "Pick from the list or type a name", autocomplete: "off", required: "", value: prefill.vendor || null });

  const fillVendors = () => {
    clear(vendorList);
    const s = skuByText(skuIn.value);
    const suppliers = s ? s.vendors : [];
    const competitors = s ? s.group_vendors.filter((v) => !suppliers.includes(v)) : [];
    const rest = chat.catalog.vendors.filter((v) => !suppliers.includes(v) && !competitors.includes(v));
    suppliers.forEach((v) => vendorList.append(el("option", { value: v, label: "supplies this product" })));
    competitors.forEach((v) => vendorList.append(el("option", { value: v, label: "sells an equivalent product" })));
    rest.forEach((v) => vendorList.append(el("option", { value: v })));
  };
  skuIn.addEventListener("input", fillVendors);
  fillVendors();

  const form = el("form", { class: "ask", novalidate: "" },
    el("label", { for: skuIn.id }, "Target clinical SKU", el("span", { class: "hint", text: "The product you're buying" }), skuIn),
    el("label", { for: vendorIn.id }, "Vendor", el("span", { class: "hint", text: "Who you're negotiating with" }), vendorIn),
    el("div", { class: "row" }, el("button", { type: "submit", class: "act", text: "Show 3 best price options" })),
    skuList, vendorList);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!skuIn.value.trim()) { skuIn.focus(); return; }
    if (!vendorIn.value.trim()) { vendorIn.focus(); return; }
    const s = skuByText(skuIn.value);
    const q = { sku: s ? s.sku : skuIn.value.trim(), vendor: vendorIn.value.trim(), skuName: s ? s.sku_name : skuIn.value.trim() };
    me(`Latest negotiation price for ${q.skuName} from ${q.vendor}. Give me the 3 best price options.`);
    showOptions(q);
  });

  bot(el("p", { text: "Tell me what you need. I'll find the latest negotiation price and the 3 best price options to negotiate with." }), form);
  if (!prefill.skuName) setTimeout(() => skuIn.focus({ preventScroll: true }), 50);
}

const qs = (q, extra = {}) => new URLSearchParams({ sku: q.sku, vendor: q.vendor, ...extra }).toString();

async function showOptions(q) {
  let d;
  try { d = await thinking(api(`/api/options?${qs(q)}`)); } catch (e) { return oops(e, () => showOptions(q)); }
  const t = d.targets, u = unitName(d), q2 = { sku: t.sku, vendor: t.vendor, skuName: t.sku_name };
  const today = t.incumbent_current_price;
  const b = botWide(
    el("h3", { text: `3 best price options — ${t.sku_name} from ${t.vendor}` }),
    el("p", { class: "today", text: today
      ? `Siloam pays ${t.vendor} ${unit(today)} per ${u} today (${unit(today * t.vendor_clinical_units_per_quoted_unit)} per ${t.vendor_quoted_unit}).`
      : `${t.vendor} hasn't supplied this product to Siloam yet, so these prices come from what Siloam and other vendors have paid.` }),
    el("div", { class: "options" }, d.options.map((o) => el("div", { class: `opt ${o.key}` },
      el("span", { class: "tag", text: o.label }),
      el("span", { class: "price", text: `${unit(o.price_per_clinical_unit)}` }),
      el("span", { class: "pack", text: `per ${u} · ${unit(o.price_per_quoted_unit)} per ${o.quoted_unit}` }),
      o.annual_saving > 0 ? el("span", { class: "save", text: `Saves ${money(o.annual_saving)}/yr · −${pct(o.saving_vs_today_pct)}` }) : null,
      el("span", { class: "why", text: o.why })))),
    el("p", { class: "walk" }, el("span", { class: "dot", "aria-hidden": "true" }),
      `Walk-away: ${unit(t.proposed_walk_away)} per ${u}. This is a proposal and needs sign-off before you use it.`),
    t.single_source ? el("p", { class: "walk", text: "Single-source product: there's no real alternative, so treat the walk-away as the point to escalate, not to leave." }) : null,
    el("div", { class: "actions" },
      act("Negotiate", () => { me(`Negotiate with ${t.vendor}`); negotiate(q2); }),
      act("See details", () => { me("See details"); details(q2); }, "secondary"),
      act("Ask about another product", () => { me("Ask about another product"); askForm(); }, "ghost")),
  );
  return b;
}

// ---------- negotiate ----------
function describeChanges(ch) {
  const parts = [];
  for (const [k, v] of Object.entries(ch)) {
    if (k === "rebate_tiers") parts.push("drop the rebate");
    else if (k === "payment_terms_days") parts.push(`payment terms of ${v} days`);
    else if (k === "free_goods_ratio") parts.push(`${pct(v)} bonus stock`);
    else if (k === "on_invoice_discount") parts.push(`${pct(v)} discount`);
  }
  return parts.join(" + ") || "no change";
}

function openingLine(t, u) {
  const r = t.lowest_reference;
  const stretchPack = unit(t.opening_ask_per_quoted_unit);
  const evidence = {
    competitor_best: `We have an equivalent product available at ${unit(r.price)} per ${u}.`,
    internal_best: `One of our hospitals already buys it at ${unit(r.price)} per ${u}.`,
    best_ever: `Siloam has bought it at ${unit(r.price)} per ${u} before.`,
    vendor_own_best: `You've supplied it to us at ${unit(r.price)} per ${u} before.`,
  }[r.kind] || `The best price on record is ${unit(r.price)} per ${u}.`;
  return `"${evidence} For the whole group, around ${num(t.annual_volume)} ${u}s a year, we're looking for ${stretchPack} per ${t.vendor_quoted_unit}."`;
}

async function negotiate(q, approvedBy) {
  let d;
  try { d = await thinking(api(`/api/negotiate?${qs(q, approvedBy ? { approved_by: approvedBy } : {})}`)); }
  catch (e) { return oops(e, () => negotiate(q, approvedBy)); }
  const t = d.targets, u = unitName(d), co = d.counter_offer, cur = d.current_offer;
  if (approvedBy) return verdictBubble(d, u);

  const trade = el("li", {}, el("span", { class: "step", text: "Step 2 · If they push back" }),
    el("h4", { text: "Trade in this order. Never give anything away for free." }));
  if (cur) {
    trade.append(el("p", { text: `Their current deal: ${unit(cur.list_price_per_quoted_unit)} per ${cur.quoted_unit} list, ${pct(cur.on_invoice_discount)} discount, ${cur.payment_terms_days} days to pay. That's ${unit(cur.invoice_price_per_clinical_unit)} per ${u}.` }));
  }
  if (co && co.already_at_target) {
    trade.append(el("p", { text: "Their current price already meets the target. Lock it in for the whole group and don't trade anything away." }));
  } else if (co) {
    trade.append(el("ol", { class: "trades" }, co.packages.filter((p) => p.meets_target).map((p) => el("li", {},
      el("b", { text: p.name }), ": ", describeChanges(p.changes), " → ",
      el("span", { class: "res", text: `${unit(p.net_invoice_price_per_clinical_unit)} per ${u} on the invoice` }),
      el("br"), el("span", { class: "muted", text: p.rationale })))));
  } else {
    trade.append(el("p", { text: `There's no current deal with ${t.vendor} on record for this product, so negotiate straight from the three options: open at Stretch, settle at Target, hold at Fallback.` }));
  }

  const approver = el("input", { placeholder: "Approved by (name)", "aria-label": "Walk-away approved by", autocomplete: "off" });
  const verdictBtn = act("Get verdict", () => {
    if (!approver.value.trim()) { approver.focus(); return; }
    me(`Walk-away approved by ${approver.value.trim()}. Should we accept, push or walk?`);
    negotiate(q, approver.value.trim());
  }, "secondary");

  botWide(
    el("h3", { text: `Negotiation plan — ${t.sku_name} with ${t.vendor}` }),
    el("ol", { class: "plan" },
      el("li", {}, el("span", { class: "step", text: "Step 1 · Open" }),
        el("h4", { text: `Ask for ${unit(t.opening_ask_per_quoted_unit)} per ${t.vendor_quoted_unit}` }),
        el("p", { text: `That's ${unit(t.opening_ask)} per ${u}. Your target is ${unit(t.target_price)}; opening lower leaves room to concede.` }),
        el("p", { class: "say", text: openingLine(t, u) })),
      trade,
      el("li", {}, el("span", { class: "step", text: "Step 3 · Use your leverage" }),
        t.leverage.length ? el("ul", { class: "trades" }, t.leverage.map((x) => el("li", { text: x })))
          : el("p", { text: "No extra leverage found in the data. Lean on the evidence behind the target." })),
      el("li", {}, el("span", { class: "step", text: "Step 4 · Know your walk-away" }),
        el("h4", {}, el("span", { class: "dot", "aria-hidden": "true" }), `${unit(t.proposed_walk_away)} per ${u} — a proposal`),
        el("p", { text: "Above this, stop and escalate. It needs sign-off by someone who isn't negotiating before the engine will say accept, push or walk." }),
        el("div", { class: "inline" }, approver, verdictBtn))),
    el("div", { class: "actions" },
      act("See details", () => { me("See details"); details(q); }, "secondary"),
      act("Ask about another product", () => { me("Ask about another product"); askForm(); }, "ghost")),
  );
}

function verdictBubble(d, u) {
  const v = d.verdict;
  if (!v) {
    return bot(el("p", { text: d.verdict_note || "No verdict: there's no current deal with this vendor on record to judge." }),
      el("div", { class: "actions" }, act("Ask about another product", askForm, "secondary")));
  }
  const words = { ACCEPT: "Accept", PUSH_ABOVE_TARGET: "Push", PUSH_ALTERNATIVE_CHEAPER: "Push", WALK: "Walk away / escalate" };
  return bot(
    el("h3", { text: `${words[v.verdict] || v.verdict}: ${v.headline.split("—").slice(1).join("—").trim() || v.headline}` }),
    el("p", { text: `Their current deal costs ${unit(v.current_enuc)} per ${u} all-in, against a target of ${unit(v.target_enuc)} and a walk-away of ${unit(v.reservation_enuc)}.` }),
    v.gap_to_target_per_unit > 0 ? el("p", { text: `Closing the gap is worth ${money(v.annual_value_of_closing_gap)} a year.` }) : null,
    el("ul", { class: "trades" }, v.rationale.map((r) => el("li", { class: "muted", text: r }))),
    el("p", { class: "muted", text: d.verdict_note }),
    el("div", { class: "actions" }, act("Ask about another product", () => { me("Ask about another product"); askForm(); }, "secondary")),
  );
}

// ---------- see details ----------
function evidenceSummary(t, u) {
  const refs = t.references, today = t.incumbent_current_price, low = t.lowest_reference;
  const SHORT = { best_ever: "the best price ever", internal_best: "the best Siloam hospital", vendor_own_best: `${t.vendor}'s own best`, competitor_best: "the best competitor" };
  const beats = `Your target, ${unit(t.target_price)}, is lower than all ${refs.length} prices on record.`;
  if (!today) return `${t.vendor} has no current price for this product. ${beats}`;
  const cheaper = refs.filter((r) => r.price < today).length;
  return `You pay ${unit(today)} per ${u} today. ${cheaper} of ${refs.length} prices on record are cheaper; the lowest is ${unit(low.price)} from ${SHORT[low.kind] || "the best reference"}. ${beats}`;
}

async function details(q) {
  let d;
  try { d = await thinking(api(`/api/options?${qs(q)}`)); } catch (e) { return oops(e, () => details(q)); }
  const t = d.targets, b = d.benchmark, u = unitName(d), cu = t.vendor_clinical_units_per_quoted_unit;
  const chartBox = el("div", { class: "chartbox" });
  const evTable = el("table");
  const siteBox = el("div", { class: "chartbox" });
  const bubble = botWide(
    el("h3", { text: `The evidence — ${t.sku_name}` }),
    el("p", { text: evidenceSummary(t, u) }),
    el("h4", { class: "chart-title", text: `How the prices compare (per ${u}, cheapest first)` }),
    el("p", { class: "muted small", text: "Bars start at zero. The dashed line is your target: everything to its right costs more." }),
    chartBox,
    el("details", { class: "tableview" }, el("summary", { text: "Show as a table" }), el("div", { class: "table-wrap" }, evTable)),
    b.by_hospital.length > 1 ? el("h4", { class: "chart-title", text: "What each Siloam hospital pays (last 12 months)" }) : null,
    b.by_hospital.length > 1 ? siteBox : null,
    el("div", { class: "actions" },
      act("Negotiate", () => { me(`Negotiate with ${t.vendor}`); negotiate(q); }),
      act("Ask about another product", () => { me("Ask about another product"); askForm(); }, "ghost")),
  );
  const rows = evidenceRows(t);
  table(evTable, [
    { label: "Price", get: (r) => el("span", { title: r.full || r.label, text: r.label }) },
    { label: `Per ${u}`, num: true, get: (r) => unit(r.value) },
    { label: `Per ${t.vendor_quoted_unit}`, num: true, get: (r) => unit(r.value * cu) },
    { label: "vs target", num: true, get: (r) => r.isTarget ? "—" : `${r.value > t.target_price ? "+" : ""}${pct(r.value / t.target_price - 1)}` },
    { label: "Source", get: (r) => r.sub || "" },
  ], [...rows].sort((a, b2) => a.value - b2.value));
  requestAnimationFrame(() => {
    priceBars(chartBox, rows, { targetValue: t.target_price, packSize: cu, packName: t.vendor_quoted_unit, unitName: u });
    if (b.by_hospital.length > 1) {
      hbars(siteBox, b.by_hospital.map((h, i) => ({ label: h.hospital, value: h.weighted_price, h,
        color: i === 0 ? cssVar("--ask") : cssVar("--series-3") })), {
        fmt: unit, note: (r) => r.h.premium_vs_internal_best > 0 ? `${pct(r.h.premium_vs_internal_best)} above the best hospital · ${r.h.vendors.join(", ")}` : `Best hospital · ${r.h.vendors.join(", ")}`,
      });
    }
    reveal(bubble.parentElement);
  });
}

// ---------- renewal calendar ----------
const WINDOWS = [[[30, 90], "30–90 days"], [[0, 29], "Next 30 days"], [[91, 180], "90–180 days"]];

function monthGrid(year, month, days, asOf, onPick) {
  const first = new Date(year, month, 1);
  const count = new Date(year, month + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7; // Monday first
  const grid = el("div", { class: "grid7" }, ["M", "T", "W", "T", "F", "S", "S"].map((d) => el("span", { class: "dow", text: d })));
  for (let i = 0; i < lead; i++) grid.append(el("span", { class: "d out", text: "." }));
  for (let d = 1; d <= count; d++) {
    const iso = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const n = days[iso];
    if (n) {
      grid.append(el("button", { type: "button", class: "d", "data-iso": iso, "aria-label": `${fmtDate(iso)}: ${n} contract${n > 1 ? "s" : ""} ending`, onclick: () => onPick(iso) },
        String(d), el("span", { class: "c", text: String(n) })));
    } else {
      grid.append(el("span", { class: `d${iso === asOf ? " today" : ""}`, text: String(d) }));
    }
  }
  return el("div", { class: "month" }, el("h4", { text: first.toLocaleDateString("en-GB", { month: "long", year: "numeric" }) }), grid);
}

function renewalCard(r) {
  // One line per renewal; tap to open the targets, leverage and actions.
  const q = { sku: r.sku, vendor: r.vendor, skuName: r.sku_name };
  const soon = r.days_left <= 45;
  const body = el("div", { class: "more" },
    el("div", { class: "figs" },
      el("div", {}, "Contract value", el("b", { text: money(r.contract_value) })),
      el("div", {}, "Paying now", el("b", { text: unit(r.contract_price_per_clinical_unit) })),
      el("div", {}, "Opening ask", el("b", { text: unit(r.opening_ask) })),
      el("div", {}, "Target", el("b", { text: unit(r.target_price) }))),
    r.volume_multiple && r.volume_multiple > 1.05
      ? el("p", { class: "lev", text: `Volume leverage: the group buys ${num(r.volume_multiple, 1)}× this hospital's volume (${num(r.group_annual_units)} vs ${num(r.site_annual_units)} a year). Negotiate for the group, not the site.` })
      : el("p", { class: "lev", text: "This hospital is most of the group's volume for this product. Lean on the competitor and best-price evidence." }),
    r.single_source ? el("p", { class: "lev", text: "Single-source: no real alternative. Escalate rather than walk." }) : null,
    r.risks.length ? el("div", { class: "chips" }, r.risks.slice(0, 2).map((a) => el("span", { class: `chip ${a.severity}`, title: a.detail, text: a.title }))) : null,
    el("div", { class: "actions" },
      act("Negotiate", () => { me(`Negotiate the ${r.sku_name} renewal with ${r.vendor}`); negotiate(q); }),
      act("See details", () => { me(`Details: ${r.sku_name} from ${r.vendor}`); details(q); }, "secondary")));
  return el("li", { class: "card", "data-iso": r.contract_end },
    el("details", {},
      el("summary", {},
        el("span", { class: `days${soon ? " soon" : ""}` }, el("span", { class: "dot", "aria-hidden": "true" }), `${fmtDate(r.contract_end)} · ${r.days_left} days`),
        el("span", { class: "what" }, el("b", { text: r.sku_name }), el("span", { class: "meta", text: `${r.vendor} · ${r.hospital}` })),
        el("span", { class: "gain" }, el("span", { class: "meta", text: "Saving at target" }), el("b", { text: r.saving_at_target > 0 ? money(r.saving_at_target) : "—" }))),
      body));
}

async function openCalendar() {
  const body = el("div");
  const bubble = botWide(el("h3", { text: "Renewal Calendar & Risk Alerts" }), body);
  await renderCalendar(body, [30, 90]);
  reveal(bubble.parentElement);
}

async function renderCalendar(body, win) {
  let d;
  try { d = await thinking(api(`/api/renewals?min_days=${win[0]}&max_days=${win[1]}`)); }
  catch (e) { clear(body).append(el("p", { class: "error", text: e.message })); return; }
  clear(body);
  body.append(el("p", { text: `Contracts ending in the next ${win[0]}–${win[1]} days, each with a target and volume leverage ready. Data as of ${fmtDate(d.as_of)}.` }));
  body.append(el("div", { class: "windows", role: "group", "aria-label": "Time window" }, WINDOWS.map(([w, label]) =>
    el("button", { type: "button", class: w[0] === win[0] && w[1] === win[1] ? "on" : null, "aria-pressed": String(w[0] === win[0]), onclick: () => renderCalendar(body, w), text: label }))));
  if (win[0] === 30 && d.urgent_under_window) {
    body.append(el("p", { class: "walk" }, el("span", { class: "dot", "aria-hidden": "true" }),
      `${d.urgent_under_window} contract${d.urgent_under_window > 1 ? "s end" : " ends"} in under 30 days. `,
      el("button", { type: "button", class: "act ghost", onclick: () => renderCalendar(body, [0, 29]), text: "Show them" })));
  }
  body.append(el("div", { class: "kpis" },
    el("div", {}, el("div", { class: "l", text: "Contracts ending" }), el("div", { class: "v", text: num(d.renewals.length) })),
    el("div", {}, el("div", { class: "l", text: "Contract value" }), el("div", { class: "v", text: money(d.total_contract_value) })),
    el("div", {}, el("div", { class: "l", text: "Saving if every target is hit" }), el("div", { class: "v", text: money(d.total_saving_at_target) }))));

  if (!d.renewals.length) {
    body.append(el("p", { class: "empty", text: "No contracts end in this window." }));
  } else {
    const counts = {};
    d.renewals.forEach((r) => { counts[r.contract_end] = (counts[r.contract_end] || 0) + 1; });
    const asOf = new Date(d.as_of + "T00:00:00");
    const start = new Date(asOf); start.setDate(start.getDate() + win[0]);
    const end = new Date(asOf); end.setDate(end.getDate() + win[1]);
    const agenda = el("ul", { class: "agenda" }, d.renewals.map(renewalCard));
    let picked = null;
    const pick = (iso) => {
      picked = picked === iso ? null : iso;
      agenda.querySelectorAll(".card").forEach((c) => { c.hidden = picked && c.dataset.iso !== picked; });
      months.querySelectorAll("button.d").forEach((b) => b.classList.toggle("sel", b.dataset.iso === picked));
    };
    const months = el("div", { class: "months" });
    for (let y = start.getFullYear(), m = start.getMonth(); y < end.getFullYear() || (y === end.getFullYear() && m <= end.getMonth()); m === 11 ? (y++, m = 0) : m++) {
      months.append(monthGrid(y, m, counts, d.as_of, pick));
    }
    body.append(months, el("p", { class: "muted", text: "Tap a highlighted date to filter, and tap a row to open its targets." }), agenda);
  }

  body.append(el("h3", { text: "Risk alerts", style: "margin-top:16px" }));
  if (d.risk_alerts.length) {
    const list = el("ul", { class: "risklist" }, d.risk_alerts.map((a, i) => el("li", { hidden: i >= 4 ? "" : null },
      el("b", { text: a.title }), a.annual_impact ? el("span", { class: "amt", text: ` · ${money(a.annual_impact)} a year` }) : null,
      el("div", { class: "muted", text: a.detail }))));
    body.append(list);
    if (d.risk_alerts.length > 4) {
      const more = act(`Show all ${d.risk_alerts.length}`, () => { list.querySelectorAll("li").forEach((li) => { li.hidden = false; }); more.remove(); }, "ghost");
      body.append(more);
    }
  } else {
    body.append(el("p", { class: "empty", text: "No high-severity risks right now." }));
  }
}

// ---------- pages ----------
function showPage() {
  const onChat = location.hash === "#chat";
  $("welcome").hidden = onChat;
  $("chat").hidden = !onChat;
  if (onChat && !chat.started) { chat.started = true; greet(); }
  window.scrollTo(0, 0);
}

async function init() {
  try {
    chat.catalog = await api("/api/catalog");
  } catch (e) {
    $("welcome").prepend(el("p", { class: "error", text: `Can't load the price book: ${e.message}` }));
    return;
  }
  state.currency = chat.catalog.currency || "IDR";
  $("asof").textContent = `Prices to ${fmtDate(chat.catalog.as_of)}`;
  if (chat.catalog.banner) { $("banner").textContent = chat.catalog.banner; $("banner").hidden = false; }
  $("start").addEventListener("click", () => { location.hash = "#chat"; });
  document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => {
    const go = b.dataset.go;
    if (go === "ask") { me("Ask me anything"); askForm(); }
    else if (go === "calendar") { me("Renewal Calendar & Risk Alerts"); openCalendar(); }
    else { clear(thread); greet(); }
  }));
  window.addEventListener("hashchange", showPage);
  showPage();
}
init();
