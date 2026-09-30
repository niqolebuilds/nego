"use strict";
// The assistant: a guided conversation plus a free-text bar. Buttons and typed questions
// lead to the same answers. Typed text goes to /api/chat, an offline parser that only
// routes; every number comes from the engine. Helpers (el, sv, formatting, priceBars,
// hbars, api, send) are in common.js; the shell (shell.js) owns sign-in and routing.
// Every label from the data goes into the DOM through textContent, never innerHTML.

const chat = { started: false, formSeq: 0 };
const cat = () => shell.catalog;
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
const LAST_KEY = "nego:last";
function remember(q) { try { localStorage.setItem(LAST_KEY, JSON.stringify(q)); } catch (_) { /* storage off */ } }
function recall() { try { return JSON.parse(localStorage.getItem(LAST_KEY) || "null"); } catch (_) { return null; } }

async function greet() {
  const u = shell.user;
  let home = null;
  try { home = await api("/api/home"); } catch (_) { /* the greeting still works without it */ }
  const strip = home ? el("div", { class: "homestrip" },
    el("button", { type: "button", class: "stat", onclick: () => { location.hash = "#renewals/mine"; } },
      el("b", { text: num(home.my_due_14d) }), el("span", { text: "of your renewals due in 14 days" })),
    el("button", { type: "button", class: "stat", onclick: () => { me("Renewals due in the next 30 days"); openCalendar([0, 29]); } },
      el("b", { text: num(home.due_30d_open) }), el("span", { text: "open renewals due in 30 days" })),
    el("button", { type: "button", class: "stat", onclick: () => { me("Show me the risk alerts"); alertsBubble(); } },
      el("b", { text: num(home.high_alerts) }), el("span", { text: "high risk alerts" })),
    el("button", { type: "button", class: "stat good", onclick: () => { location.hash = "#renewals"; } },
      el("b", { text: money(home.realised_saving) }), el("span", { text: "saving realised so far" }))) : null;
  const last = recall();
  bot(
    el("p", {}, `Hi ${u ? u.name.split(" ")[0] : "there"}, I'm your negotiation assistant.`),
    strip,
    el("p", {}, el("b", { text: "Do you want to ask me anything, or do you want to see Renewal Calendar & Risk Alerts?" })),
    el("div", { class: "choices" },
      el("button", { type: "button", class: "choice", onclick: () => { me("Ask me anything"); askForm(); } },
        el("b", { text: "Ask me anything" }),
        el("span", { text: "The latest negotiation price and the 3 best price options for one product and vendor." })),
      el("button", { type: "button", class: "choice", onclick: () => { me("Renewal Calendar & Risk Alerts"); openCalendar(); } },
        el("b", { text: "Renewal Calendar & Risk Alerts" }),
        el("span", { text: "Identify contracts expiring in 30–90 days with pre-built negotiation targets and volume leverage." }))),
    el("p", { class: "muted small", text: "Or type any question in the bar below." }),
    last ? el("div", { class: "chips" }, el("button", { type: "button", class: "chipbtn", onclick: () => { me(`Continue: ${last.skuName} from ${last.vendor}`); showOptions(last); } },
      `Continue where you left off: ${last.skuName} from ${last.vendor}`)) : null,
  );
}

// ---------- ask me anything ----------
function skuByText(text) {
  const t = text.trim().toLowerCase();
  return cat().skus.find((s) => s.sku_name.toLowerCase() === t || s.sku.toLowerCase() === t);
}

function askForm(prefill = {}) {
  const id = ++chat.formSeq;
  const skuList = el("datalist", { id: `dl-sku-${id}` }, cat().skus.map((s) => el("option", { value: s.sku_name, label: s.equivalence_group })));
  const vendorList = el("datalist", { id: `dl-vendor-${id}` });
  const skuIn = el("input", { id: `sku-${id}`, list: skuList.id, placeholder: "e.g. IV cannula 22G", autocomplete: "off", required: "", value: prefill.skuName || null });
  const vendorIn = el("input", { id: `vendor-${id}`, list: vendorList.id, placeholder: "Pick from the list or type a name", autocomplete: "off", required: "", value: prefill.vendor || null });

  const fillVendors = () => {
    clear(vendorList);
    const s = skuByText(skuIn.value);
    const suppliers = s ? s.vendors : [];
    const competitors = s ? s.group_vendors.filter((v) => !suppliers.includes(v)) : [];
    const rest = cat().vendors.filter((v) => !suppliers.includes(v) && !competitors.includes(v));
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
  remember(q2);
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

  const summary = planSummary(t, u, co, cur);
  const planBubble = botWide(
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
      act("Copy summary", async (e) => {
        try { await navigator.clipboard.writeText(summary); e.target.textContent = "Copied"; }
        catch (_) { toast("Copy isn't available here; select the text instead."); }
      }, "secondary"),
      act("Print plan", () => printBubble(planBubble), "secondary"),
      act("See details", () => { me("See details"); details(q); }, "secondary"),
      act("Ask about another product", () => { me("Ask about another product"); askForm(); }, "ghost")),
  );
}

function planSummary(t, u, co, cur) {
  const lines = [
    `Negotiation plan: ${t.sku_name} with ${t.vendor}`,
    `Open at ${unit(t.opening_ask_per_quoted_unit)} per ${t.vendor_quoted_unit} (${unit(t.opening_ask)} per ${u}).`,
    `Target ${unit(t.target_price)} per ${u}; fallback ${unit(t.lowest_reference.price)}; walk-away (proposal) ${unit(t.proposed_walk_away)}.`,
  ];
  if (cur) lines.push(`Their current deal: ${unit(cur.invoice_price_per_clinical_unit)} per ${u}.`);
  if (co && !co.already_at_target) {
    lines.push("If they push back, trade in this order:");
    co.packages.filter((p) => p.meets_target).forEach((p, i) => lines.push(`  ${i + 1}. ${p.name}: ${describeChanges(p.changes)}`));
  }
  if (t.leverage.length) { lines.push("Leverage:"); t.leverage.forEach((x) => lines.push(`  - ${x}`)); }
  return lines.join("\n");
}

function printBubble(bubble) {
  const msg = bubble.parentElement;
  msg.classList.add("print-target");
  document.body.classList.add("printing");
  const done = () => { msg.classList.remove("print-target"); document.body.classList.remove("printing"); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
  setTimeout(done, 1500);
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
      act("Update progress", () => openRenewal(r), "secondary"),
      act("See details", () => { me(`Details: ${r.sku_name} from ${r.vendor}`); details(q); }, "ghost")));
  return el("li", { class: "card", "data-iso": r.contract_end },
    el("details", {},
      el("summary", {},
        el("span", { class: `days${soon ? " soon" : ""}` }, el("span", { class: "dot", "aria-hidden": "true" }), `${fmtDate(r.contract_end)} · ${r.days_left} days`),
        el("span", { class: "what" }, el("b", { text: r.sku_name }), el("span", { class: "meta" }, `${r.vendor} · ${r.hospital} `, stagePill(r.progress))),
        el("span", { class: "gain" }, el("span", { class: "meta", text: "Saving at target" }), el("b", { text: r.saving_at_target > 0 ? money(r.saving_at_target) : "—" }))),
      body));
}

async function openCalendar(win) {
  const body = el("div");
  const bubble = botWide(el("h3", { text: "Renewal Calendar & Risk Alerts" }), body);
  await renderCalendar(body, win || cat().renewal_window || [30, 90]);
  reveal(bubble.parentElement);
}

async function renderCalendar(body, win) {
  let d;
  try { d = await thinking(api(`/api/renewals?min_days=${win[0]}&max_days=${win[1]}`)); }
  catch (e) { clear(body).append(el("p", { class: "error", text: e.message })); return; }
  clear(body);
  body.append(el("p", { text: `Contracts ending in the next ${win[0]}–${win[1]} days, each with a target and volume leverage ready. Data as of ${fmtDate(d.as_of)}.` }));
  const wins = WINDOWS.some(([w]) => w[0] === win[0] && w[1] === win[1]) ? WINDOWS : [[win, `${win[0]}–${win[1]} days`], ...WINDOWS];
  body.append(el("div", { class: "windows", role: "group", "aria-label": "Time window" }, wins.map(([w, label]) =>
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

// ---------- free text ----------
const EXAMPLES = [
  "Best price for ceftriaxone from Medisindo",
  "Negotiate IV cannula with Prima",
  "Renewals in the next 60 days",
  "Where can we save?",
  "What do we pay for gloves?",
  "How much do we spend with Sehat Medika?",
];

function chips(items, onPick) {
  return el("div", { class: "chips" }, items.map((it) => el("button", { type: "button", class: "chipbtn", onclick: () => onPick(it) },
    typeof it === "string" ? it : it.label)));
}

function exampleChips() { return chips(EXAMPLES, (t) => handleText(t)); }

function helpBubble() {
  bot(el("p", { text: "Ask me in your own words. I understand products, vendors, hospitals and time windows, for example:" }),
    exampleChips(),
    el("p", { class: "muted small", text: "I work out prices with the engine, from Siloam's own price data. I don't guess numbers." }));
}

async function handleText(text) {
  text = (text || "").trim();
  if (!text) return;
  me(text);
  let p;
  try { p = await thinking(send("/api/chat", "POST", { text })); } catch (e) { return oops(e); }
  const q = p.sku && p.vendor ? { sku: p.sku, vendor: p.vendor, skuName: p.sku_name } : null;
  switch (p.intent) {
    case "options": case "negotiate": case "details":
      if (q) return ({ options: showOptions, negotiate, details })[p.intent](q);
      return clarify(p);
    case "renewals": return openCalendar(p.window || undefined);
    case "my_renewals": bot(el("p", { text: "Opening your renewals on the board." })); location.hash = "#renewals/mine"; return;
    case "alerts": return alertsBubble(p.vendor);
    case "savings": return savingsBubble();
    case "spend": return spendBubble();
    case "vendor": return p.vendor ? vendorBubble(p.vendor) : clarify(p);
    case "lookup": return lookupBubble(p);
    case "help": return helpBubble();
    case "greeting": return bot(el("p", { text: `Hi ${shell.user.name.split(" ")[0]}! What would you like to know?` }), exampleChips());
    default:
      return bot(
        el("p", { text: "I didn't catch that." }),
        p.suggestions && p.suggestions.length ? el("p", { class: "small", text: "Did you mean one of these?" }) : null,
        p.suggestions && p.suggestions.length ? chips(p.suggestions, (s) => handleText(`best price for ${s}`)) : null,
        el("p", { class: "small muted", text: "Try one of these:" }), exampleChips(),
        p.llm_fallback && p.llm_fallback.enabled ? el("p", { class: "muted small", text: p.llm_fallback.note }) : null);
  }
}

function clarify(p) {
  const verb = { negotiate: "negotiate", details: "see the evidence for" }[p.intent] || "get price options for";
  const go = { options: showOptions, negotiate, details }[p.intent] || showOptions;
  if (p.sku && !p.vendor) {
    const product = cat().skus.find((s) => s.sku === p.sku);
    const family = (p.candidates && p.candidates.length ? p.candidates : [product]).filter(Boolean);
    const options = [];
    family.forEach((s) => s.vendors.forEach((v) => options.push({ label: `${v} · ${s.sku_name}`, q: { sku: s.sku, vendor: v, skuName: s.sku_name } })));
    return bot(el("p", { text: `Which vendor should I ${verb}${p.group ? ` ${p.group}` : ""}?` }),
      chips(options.slice(0, 8), (o) => { me(o.label); go(o.q); }),
      el("div", { class: "actions" }, act("Another vendor", () => askForm({ skuName: product ? product.sku_name : "" }), "ghost")));
  }
  if (p.vendor && !p.sku) {
    const products = cat().skus.filter((s) => s.vendors.includes(p.vendor));
    return bot(el("p", { text: `Which product from ${p.vendor}?` }),
      chips(products.slice(0, 10).map((s) => ({ label: s.sku_name, q: { sku: s.sku, vendor: p.vendor, skuName: s.sku_name } })), (o) => { me(o.label); go(o.q); }),
      el("div", { class: "actions" }, act("Another product", () => askForm({ vendor: p.vendor }), "ghost")));
  }
  return askForm();
}

async function alertsBubble(vendor) {
  let rows;
  try { rows = await thinking(api("/api/alerts")); } catch (e) { return oops(e); }
  if (vendor) rows = rows.filter((a) => (a.vendor || "").includes(vendor));
  const top = rows.filter((a) => a.severity !== "low").slice(0, 8);
  botWide(el("h3", { text: vendor ? `Risk alerts — ${vendor}` : "Risk alerts" }),
    top.length ? el("ul", { class: "risklist" }, top.map((a) => el("li", {},
      el("span", { class: `sev ${a.severity}`, text: a.severity }), " ", el("b", { text: a.title }),
      a.annual_impact ? el("span", { class: "amt", text: ` · ${money(a.annual_impact)} a year` }) : null,
      el("div", { class: "muted", text: a.detail }))))
      : el("p", { class: "empty", text: "No open risks." }),
    el("div", { class: "actions" }, act("Where to save", () => { me("Where can we save?"); savingsBubble(); }, "secondary"),
      act("Renewal calendar", () => { me("Renewal calendar"); openCalendar(); }, "ghost")));
}

async function savingsBubble() {
  let d;
  try { d = await thinking(api("/api/savings")); } catch (e) { return oops(e); }
  const box = el("div", { class: "chartbox" });
  const b = botWide(el("h3", { text: "Where to save first" }),
    el("p", { class: "muted small", text: "Yearly spend above the best available price (the best hospital or the best equivalent product). Tap a bar for price options." }),
    box);
  requestAnimationFrame(() => {
    hbars(box, d.rows.map((r) => ({ label: r.sku_name, value: r.total_opportunity, r })), {
      fmt: money, color: cssVar("--ask"),
      note: (x) => `${pct(x.r.opportunity_pct_of_spend)} of ${money(x.r.spend_12m)} spend · best: ${x.r.group_best_vendor || x.r.internal_best_site}`,
    });
    box.querySelectorAll("g").forEach((g, i) => g.addEventListener("click", () => {
      const r = d.rows[i];
      const vendor = r.vendors[0];
      me(`Price options for ${r.sku_name} from ${vendor}`);
      showOptions({ sku: r.sku, vendor, skuName: r.sku_name });
    }));
    reveal(b.parentElement);
  });
}

async function spendBubble() {
  let d;
  try { d = await thinking(api("/api/vendors")); } catch (e) { return oops(e); }
  const box = el("div", { class: "chartbox" });
  const b = botWide(el("h3", { text: "Spend by vendor, last 12 months" }), box,
    el("p", { class: "muted small", text: "Tap a vendor for their profile." }));
  requestAnimationFrame(() => {
    hbars(box, d.spend.map((v) => ({ label: v.vendor, value: v.spend_12m, v })), {
      fmt: money, color: cssVar("--ask"),
      note: (x) => `${pct(x.v.share_of_wallet)} of spend · ${x.v.growth == null ? "new" : `${x.v.growth >= 0 ? "+" : ""}${pct(x.v.growth)} vs last year`}`,
    });
    box.querySelectorAll("g").forEach((g, i) => g.addEventListener("click", () => { me(d.spend[i].vendor); vendorBubble(d.spend[i].vendor); }));
    reveal(b.parentElement);
  });
}

async function vendorBubble(name) {
  let v, docs = [];
  try { v = await thinking(api(`/api/vendor/${encodeURIComponent(name)}`)); } catch (e) { return oops(e); }
  try { docs = await api(`/api/documents?vendor=${encodeURIComponent(v.vendor)}`); } catch (_) { /* optional */ }
  const s = v.spend;
  const tbl = el("table");
  botWide(el("h3", { text: v.vendor }),
    s ? el("div", { class: "kpis" },
      el("div", {}, el("div", { class: "l", text: "Spend, 12 months" }), el("div", { class: "v", text: money(s.spend_12m) })),
      el("div", {}, el("div", { class: "l", text: "Share of spend" }), el("div", { class: "v", text: pct(s.share_of_wallet) })),
      el("div", {}, el("div", { class: "l", text: "vs last year" }), el("div", { class: "v", text: s.growth == null ? "new" : `${s.growth >= 0 ? "+" : ""}${pct(s.growth)}` })))
      : el("p", { class: "muted", text: "No purchases in the last 12 months." }),
    el("h4", { class: "chart-title", text: "Products" }),
    el("div", { class: "table-wrap" }, tbl),
    v.alerts.length ? el("h4", { class: "chart-title", text: "Risk alerts" }) : null,
    v.alerts.length ? el("ul", { class: "risklist" }, v.alerts.slice(0, 4).map((a) => el("li", {}, el("span", { class: `sev ${a.severity}`, text: a.severity }), " ", el("b", { text: a.title })))) : null,
    docs.length ? el("h4", { class: "chart-title", text: "Documents" }) : null,
    docs.length ? docList(docs) : null);
  table(tbl, [
    { label: "Product", get: (r) => r.sku_name },
    { label: "Latest per unit", num: true, get: (r) => unit(r.latest_price_per_clinical_unit) },
    { label: "Best per unit", num: true, get: (r) => unit(r.best_price_per_clinical_unit) },
    { label: "Hospitals", num: true, get: (r) => String(r.hospitals.length) },
  ], v.products, (r) => { me(`Price options for ${r.sku_name} from ${v.vendor}`); showOptions({ sku: r.sku, vendor: v.vendor, skuName: r.sku_name }); });
}

async function lookupBubble(p) {
  const params = new URLSearchParams();
  const product = p.sku ? cat().skus.find((s) => s.sku === p.sku) : null;
  if (product) params.set("text", product.equivalence_group ? product.sku_name.split("(")[0].trim() : product.sku_name);
  if (p.vendor) params.set("vendor", p.vendor);
  if (p.hospital) params.set("hospital", p.hospital);
  let d;
  try { d = await thinking(api(`/api/lookup?${params}`)); } catch (e) { return oops(e); }
  const tbl = el("table");
  botWide(el("h3", { text: "What Siloam pays" }),
    el("p", { class: "muted small", text: "Latest and best price per unit, by product and vendor. Tap a row for price options." }),
    el("div", { class: "table-wrap" }, tbl));
  table(tbl, [
    { label: "Product", get: (r) => r.sku_name },
    { label: "Vendor", get: (r) => r.vendor },
    { label: "Latest", num: true, get: (r) => `${unit(r.latest_price_per_clinical_unit)}` },
    { label: "Best", num: true, get: (r) => unit(r.best_price_per_clinical_unit) },
    { label: "Pack", get: (r) => r.quoted_unit },
  ], d.rows, (r) => { me(`Price options for ${r.sku_name} from ${r.vendor}`); showOptions({ sku: r.sku, vendor: r.vendor, skuName: r.sku_name }); });
}

function docList(docs) {
  return el("ul", { class: "doclist" }, docs.map((d) => el("li", {},
    el("a", { href: `/api/documents/${encodeURIComponent(d.id)}/download`, text: d.filename }),
    el("span", { class: "muted small", text: ` · ${[d.vendor, d.sku, d.note].filter(Boolean).join(" · ")}` }))));
}

// ---------- page hooks (called by shell.js) ----------
function chatEnter() {
  if (!chat.started) {
    chat.started = true;
    $("composer").addEventListener("submit", (e) => {
      e.preventDefault();
      const input = $("composer-input");
      const text = input.value;
      input.value = "";
      handleText(text);
    });
    document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => {
      const go = b.dataset.go;
      if (go === "ask") { me("Ask me anything"); askForm(); }
      else if (go === "calendar") { me("Renewal Calendar & Risk Alerts"); openCalendar(); }
      else if (go === "risks") { me("Show me the risk alerts"); alertsBubble(); }
      else if (go === "savings") { me("Where can we save?"); savingsBubble(); }
      else { clear(thread); greet(); }
    }));
    greet();
  }
  setTimeout(() => $("composer-input").focus({ preventScroll: true }), 50);
}

function resetChat() {
  clear(thread);
  chat.started = false;
  const fresh = $("composer").cloneNode(true);
  $("composer").replaceWith(fresh);
  document.querySelectorAll("[data-go]").forEach((b) => b.replaceWith(b.cloneNode(true)));
}
