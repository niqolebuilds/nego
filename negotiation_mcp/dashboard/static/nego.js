"use strict";
// Principal negotiations: the principals list (MOU alerts, which step each cycle is at)
// and one cycle's workspace (step tracker, items table laid out like Template_Nego,
// the anomaly queue, files and activity). Everyone signed in can look; only admins see
// the buttons that change things, and the server enforces that on every call.

const ng = { list: null, cid: null, ov: null, tab: "items", page: { q: "", filter: "all", sort: "sort", offset: 0 }, findStatus: "open", rule: "" };
const isAdmin = () => shell.user && shell.user.role === "admin";
const WHO = { admin: "Siloam", principal: "Principal", reference: "Reference" };
const STEP_DESC = {
  prepare: "Siloam builds the item list from 12 months of POs, the formulary and the current MOU.",
  identification: "The principal confirms brand, catalogue no. and status for every item.",
  current_mou: "Current MOU prices, shown to the principal for reference only.",
  rfq: "The principal quotes Qty/PO unit, HNA and discount for every active item.",
  counter_offer: "Siloam sends a counter-offer discount per item. The engine suggests where to hold the MOU price.",
  feedback1: "The principal accepts the counter offer or proposes a Feedback I discount.",
  online_nego: "Siloam records the discounts agreed in the online meeting.",
  submission: "The principal submits company documents; Siloam checks and files the BAK data.",
  closed: "Done. The agreed prices go to the BAK and MOU.",
};
const FILTERS = [["all", "All items"], ["anomalies", "With open findings"], ["increase", "Price up vs MOU"], ["decrease", "Price down vs MOU"],
  ["no_rfq", "No RFQ price yet"], ["no_mou", "Not in current MOU"], ["discontinued", "Discontinued"]];
const SORTS = [["sort", "A–Z (template order)"], ["impact", "Biggest cost impact"], ["change", "Biggest % change"], ["spend", "Biggest PO spend"]];

function ngEnter(rest) {
  wireBoard();
  const cid = rest[0] ? parseInt(rest[0], 10) : null;
  if (cid) { ngCycle(cid, rest[1] || "items"); return; }
  ngList();
}

// ---------- principals list ----------
async function ngList() {
  ng.cid = null;
  const page = clear($("nego"));
  const tiles = el("div", { class: "kpis5" });
  const tools = el("div", { class: "ng-tools" });
  const search = el("input", { id: "ng-search", placeholder: "Principal or distributor", type: "search" });
  const attention = el("input", { type: "checkbox", id: "ng-attn" });
  const box = el("div", { class: "table-wrap card ng-table" });
  page.append(
    el("div", { class: "page-head ng-head" },
      el("div", {}, el("h1", { text: "Principal negotiations" }),
        el("p", { class: "muted", text: "One negotiation per principal with all of its items, step by step as in Template_Nego." })),
      tools),
    tiles,
    el("div", { class: "filters board-filters" },
      el("label", {}, "Search", search),
      el("label", { class: "check" }, attention, "Needs attention")),
    box);
  if (isAdmin()) {
    tools.append(act("Add principal", () => principalForm()), act("Import principals", importPrincipals, "secondary"));
  }
  box.append(el("p", { class: "muted pad", text: "Loading…" }));
  let d;
  try { d = ng.list = await api("/api/nego/principals"); } catch (e) { clear(box).append(el("p", { class: "error pad", text: e.message })); return; }
  const ps = d.principals;
  const alerts = ps.filter((p) => p.mou_alert);
  const open = ps.filter((p) => p.cycle && p.cycle.status === "open");
  clear(tiles).append(
    tile("Principals", num(ps.length)),
    tile(`MOU ends within ${d.alert_months} months`, num(alerts.length), alerts.length ? "and no negotiation open yet" : "all covered"),
    tile("Negotiations open", num(open.length)),
    tile("Open findings", num(open.reduce((s, p) => s + (p.cycle.open_anomalies || 0), 0)), "to review across open negotiations"));
  if (alerts.length) tiles.children[1].classList.add("alert");
  if (!ps.length) {
    clear(box).append(el("div", { class: "empty-state" },
      el("h3", { text: "No principals yet" }),
      el("p", { class: "muted", text: isAdmin() ? "Add principals one by one, import a list, or load the fictional sample to try the workflow." : "An admin adds principals and opens negotiations." }),
      isAdmin() ? el("div", { class: "actions" }, act("Load sample principals", loadSample), act("Import principals", importPrincipals, "secondary")) : null));
    return;
  }
  const render = () => {
    const q = search.value.trim().toLowerCase();
    const rows = ps.filter((p) => (!q || `${p.name} ${p.distributor || ""}`.toLowerCase().includes(q))
      && (!attention.checked || p.mou_alert || (p.cycle && p.cycle.status === "open" && p.cycle.open_anomalies)));
    const t = el("table", { class: "ng-principals" });
    table(t, [
      { label: "Principal", get: (p) => el("div", {}, el("b", { text: p.name }), el("div", { class: "small muted", text: p.distributor || "" })) },
      { label: "Category", get: (p) => p.category || "—" },
      { label: "MOU ends", get: (p) => mouCell(p) },
      { label: "Negotiation", get: (p) => cycleCell(p) },
      { label: "Items", num: true, get: (p) => (p.cycle ? num(p.cycle.items) : "—") },
      { label: "Open findings", num: true, get: (p) => (p.cycle && p.cycle.open_anomalies ? el("span", { class: "pill medium", text: num(p.cycle.open_anomalies) }) : "—") },
      { label: "Contact", get: (p) => el("div", { class: "small" }, p.contact_name || "", el("div", { class: "muted", text: [p.contact_email, p.contact_phone].filter(Boolean).join(" · ") })) },
    ], rows, (p) => { if (p.cycle) location.hash = `#nego/${p.cycle.id}`; else if (isAdmin()) startCycleForm(p); else principalInfo(p); });
    clear(box).append(rows.length ? t : el("p", { class: "muted pad", text: "No principals match." }));
  };
  search.addEventListener("input", render);
  attention.addEventListener("change", render);
  render();
}

function mouCell(p) {
  if (!p.mou_end) return el("span", { class: "muted", text: "Not set" });
  const d = p.mou_days_left;
  const when = d < 0 ? `ended ${num(-d)} days ago` : `in ${num(d)} days`;
  return el("div", { class: p.mou_alert ? "mou alert" : "mou" },
    p.mou_alert ? el("span", { class: "dot", "aria-hidden": "true" }) : null,
    el("span", { text: fmtDate(p.mou_end) }), el("div", { class: "small muted", text: when }),
    p.mou_alert ? el("div", { class: "small warn-text", text: "Start the negotiation" }) : null);
}

function cycleCell(p) {
  const c = p.cycle;
  if (!c) return isAdmin() ? el("button", { type: "button", class: "act small-btn", text: "Start negotiation", onclick: (e) => { e.stopPropagation(); startCycleForm(p); } }) : el("span", { class: "muted", text: "Not started" });
  const keys = ng.list.steps.map((s) => s.key);
  const at = keys.indexOf(c.current_step);
  return el("div", { class: "cyc" },
    el("span", { class: `stage ${c.status === "closed" ? "s-agreed" : "s-negotiating"}`, text: c.step_label }),
    el("div", { class: "mini-steps", "aria-label": `Step ${at + 1} of ${keys.length}` },
      keys.slice(0, -1).map((k, i) => el("i", { class: i < at ? "done" : i === at ? "now" : "" }))),
    el("div", { class: "small muted", text: `${fmtDate(c.contract_start)} – ${fmtDate(c.contract_end)}` }));
}

function principalInfo(p) {
  openPanel(el("p", { class: "eyebrow", text: "Principal" }), el("h2", { id: "drawer-title", text: p.name }),
    el("p", { class: "muted", text: "No negotiation yet. An admin starts one from this list." }));
}

function openPanel(...kids) {
  closeDrawer.back = document.activeElement;
  const body = clear($("drawer-body"));
  body.append(...kids);
  $("drawer").hidden = false;
  $("drawer-scrim").hidden = false;
  $("drawer-close").focus();
  return body;
}

function field(label, input, hint) {
  return el("label", {}, label, input, hint ? el("span", { class: "hint", text: hint }) : null);
}

function principalForm(p = null) {
  const v = p || {};
  const inp = (name, attrs = {}) => el("input", { name, value: v[name] || null, ...attrs });
  const binding = el("select", { name: "binding" }, ["Nett", "Disc"].map((b) => el("option", { value: b, text: b === "Nett" ? "Nett price" : "Discount" })));
  binding.value = v.binding || "Nett";
  const msg = el("p", { class: "error", role: "alert", hidden: "" });
  const form = el("form", { class: "progress-form" },
    field("Principal name", inp("name", { required: "" })),
    field("Distributor", inp("distributor")),
    el("div", { class: "row2" }, field("Category", inp("category", { placeholder: "Consumables, Drugs, Reagents…" })), field("Binding", binding)),
    el("div", { class: "row2" }, field("Current MOU start", inp("mou_start", { type: "date" })), field("Current MOU end", inp("mou_end", { type: "date" }), "Drives the 6-month alert")),
    field("Contact person", inp("contact_name")),
    el("div", { class: "row2" }, field("Contact email", inp("contact_email", { type: "email" })), field("WhatsApp", inp("contact_phone", { placeholder: "+62 …" }))),
    msg,
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: p ? "Save" : "Add principal" })));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(form).entries());
    try {
      const saved = p ? await send(`/api/admin/nego/principals/${p.id}`, "PATCH", body) : await send("/api/admin/nego/principals", "POST", body);
      closeDrawer();
      toast(p ? "Saved" : `${saved.name} added`);
      if (ng.cid) ngCycle(ng.cid, ng.tab); else ngList();
    } catch (ex) { msg.textContent = ex.message; msg.hidden = false; }
  });
  openPanel(el("p", { class: "eyebrow", text: p ? "Edit principal" : "New principal" }), el("h2", { id: "drawer-title", text: p ? p.name : "Add a principal" }), form);
}

function importPrincipals() {
  const file = el("input", { type: "file", accept: ".csv,.xlsx", class: "sr-only", name: "file" });
  const out = el("div", { role: "status" });
  const form = el("form", { class: "upload-form" },
    el("p", { class: "muted small", text: "A CSV or Excel list with a principal name column. Distributor, category, binding, MOU start/end, PIC, email and WhatsApp are picked up when present, whatever the exact header. Existing names are updated." }),
    dropZone(file, "Choose the principals list"),
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Import" })), out);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!file.files.length) { clear(out).append(el("p", { class: "error", text: "Choose a file first." })); return; }
    const fd = new FormData(); fd.append("file", file.files[0]);
    try {
      const r = await upload("/api/admin/nego/principals/import", fd);
      clear(out).append(el("p", { class: "good-text", text: `${r.added} added, ${r.updated} updated.` }),
        r.errors.length ? el("ul", { class: "errors" }, r.errors.map((x) => el("li", { text: x }))) : null);
      ngList();
    } catch (ex) { clear(out).append(el("p", { class: "error", text: ex.message })); }
  });
  openPanel(el("p", { class: "eyebrow", text: "Principals" }), el("h2", { id: "drawer-title", text: "Import a list" }), form);
}

async function loadSample() {
  try {
    toast("Loading the sample…");
    const r = await send("/api/admin/nego/sample", "POST", {});
    toast(`${r.principals.added} principals, ${r.opened.length} negotiations opened`);
    ngList();
  } catch (e) { toast(e.message); }
}

// Date-only arithmetic in UTC, so the browser's time zone never shifts a day.
const utc = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const isoOf = (d) => d.toISOString().slice(0, 10);
function addYears(iso, n) { const d = utc(iso); d.setUTCFullYear(d.getUTCFullYear() + n); d.setUTCDate(d.getUTCDate() - 1); return isoOf(d); }
function nextDay(iso) { const d = utc(iso); d.setUTCDate(d.getUTCDate() + 1); return isoOf(d); }

function startCycleForm(p) {
  const start0 = p.mou_end ? nextDay(p.mou_end) : new Date().toISOString().slice(0, 10);
  const start = el("input", { type: "date", name: "contract_start", value: start0, required: "" });
  const end = el("input", { type: "date", name: "contract_end", value: addYears(start0, 3), required: "" });
  start.addEventListener("change", () => { if (start.value) end.value = addYears(start.value, 3); });
  const binding = el("select", { name: "binding" }, ["Nett", "Disc"].map((b) => el("option", { value: b, text: b === "Nett" ? "Nett price (HNA after discount)" : "Discount (% off HNA)" })));
  binding.value = p.binding || "Nett";
  const fee = el("input", { name: "delivery_fee", value: "Free for all Siloam Hospitals units" });
  const msg = el("p", { class: "error", role: "alert", hidden: "" });
  const form = el("form", { class: "progress-form" },
    el("div", { class: "row2" }, field("New contract starts", start), field("New contract ends", end)),
    field("Binding", binding), field("Delivery fee", fee), msg,
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Open negotiation" }), act("Edit principal", () => principalForm(p), "ghost")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const c = await send("/api/admin/nego/cycles", "POST", { principal_id: p.id, ...Object.fromEntries(new FormData(form).entries()) });
      closeDrawer();
      toast("Negotiation opened. Next: build the item list.");
      location.hash = `#nego/${c.id}/files`;
    } catch (ex) { msg.textContent = ex.message; msg.hidden = false; }
  });
  openPanel(el("p", { class: "eyebrow", text: "Start negotiation" }), el("h2", { id: "drawer-title", text: p.name }),
    el("p", { class: "muted", text: p.mou_end ? `Current MOU ends ${fmtDate(p.mou_end)}.` : "No MOU end date on file." }), form);
}

// ---------- one cycle ----------
async function ngCycle(cid, tab) {
  const page = $("nego");
  if (ng.cid !== cid || !ng.ov) {
    clear(page).append(el("p", { class: "muted pad", text: "Loading…" }));
    ng.page = { q: "", filter: "all", sort: "sort", offset: 0 };
  }
  ng.cid = cid;
  ng.tab = ["items", "findings", "files", "activity"].includes(tab) ? tab : "items";
  try { ng.ov = await api(`/api/nego/cycles/${cid}`); } catch (e) { clear(page).append(el("p", { class: "error pad", text: e.message }), el("a", { href: "#nego", text: "← All principals" })); return; }
  renderCycle();
}

async function refreshOverview() {
  ng.ov = await api(`/api/nego/cycles/${ng.cid}`);
  renderHeaderParts();
}

function renderCycle() {
  const page = clear($("nego"));
  const c = ng.ov.cycle;
  const tabs = el("nav", { class: "subtabs", "aria-label": "Negotiation sections" },
    [["items", "Items"], ["findings", "Findings"], ["files", "Files & prepare"], ["activity", "Activity"]].map(([k, label]) =>
      el("a", { href: `#nego/${c.id}/${k}`, class: ng.tab === k ? "on" : null, "data-tab": k }, label, k === "findings" ? el("span", { class: "count", id: "ng-find-count" }) : null)));
  page.append(
    el("a", { class: "back", href: "#nego", text: "← All principals" }),
    el("div", { class: "page-head ng-head" },
      el("div", {},
        el("p", { class: "eyebrow", text: c.principal.distributor ? `Distributor: ${c.principal.distributor}` : "Principal" }),
        el("h1", { text: c.principal.name }),
        el("p", { class: "muted", text: `Contract ${fmtDate(c.contract_start)} – ${fmtDate(c.contract_end)} · Binding ${c.binding === "Disc" ? "discount" : "nett price"} · PPN ${pct(ng.ov.ppn, 0)}` })),
      el("div", { class: "ng-tools" },
        el("a", { class: "act secondary", href: `/api/nego/cycles/${c.id}/export.xlsx`, text: "Download Template_Nego" }),
        isAdmin() ? act("Edit principal", () => principalForm(c.principal), "ghost") : null)),
    el("div", { id: "ng-steps" }),
    el("div", { class: "kpis5", id: "ng-kpis" }),
    el("div", { id: "ng-stages" }),
    tabs,
    el("div", { id: "ng-body" }));
  renderHeaderParts();
  ({ items: renderItems, findings: renderFindings, files: renderFiles, activity: renderActivity })[ng.tab]($("ng-body"));
}

function renderHeaderParts() {
  const { cycle: c, steps, kpis: k, anomalies: a } = ng.ov;
  const keys = steps.map((s) => s.key);
  const at = keys.indexOf(c.current_step);
  const stepsBox = clear($("ng-steps"));
  const ol = el("ol", { class: "stepper" }, steps.filter((s) => s.key !== "closed").map((s, i) =>
    el("li", { class: i < at ? "done" : i === at ? "now" : "" },
      el("span", { class: "n", text: i < at ? "✓" : String(i) }),
      el("span", { class: "t" }, el("b", { text: s.label }), el("small", { class: `who ${s.who}`, text: WHO[s.who] })))));
  const nextKey = keys[at + 1];
  const move = isAdmin() && c.status === "open" ? el("div", { class: "step-actions" },
    el("p", { class: "small", text: STEP_DESC[c.current_step] }),
    nextKey ? act(`Move to ${steps[at + 1].label}`, () => moveStep(nextKey)) : null,
    at > 0 ? act("Back a step", () => moveStep(keys[at - 1]), "ghost") : null) : el("p", { class: "small muted step-actions", text: STEP_DESC[c.current_step] });
  stepsBox.append(el("div", { class: "card stepcard" }, ol, move));

  const latest = k.latest_impact;
  clear($("ng-kpis")).append(
    tile("Items", num(k.items), `${num(k.active)} active · ${num(k.discontinued)} discontinued`),
    tile("RFQ received", `${num(k.rfq_filled)} / ${num(k.active)}`, k.rfq_filled ? `${pct(k.rfq_filled / Math.max(k.active, 1), 0)} of active items` : "waiting for the principal"),
    tile("Open findings", num(a.open), a.open_high ? `${num(a.open_high)} high · ${num(a.kept)} kept · ${num(a.fixed)} fixed` : `${num(a.kept)} kept · ${num(a.fixed)} fixed`),
    tile("Cost impact a year", latest == null ? "—" : `${latest > 0 ? "+" : ""}${money(latest)}`, latest == null ? "once prices come in" : `latest step vs MOU · spend ${money(k.baseline_spend)}`));
  const tiles = $("ng-kpis").children;
  if (a.open_high) tiles[2].classList.add("alert");
  if (latest != null) tiles[3].classList.add(latest > 0 ? "alert" : "good");
  const fc = $("ng-find-count");
  if (fc) fc.textContent = a.open ? String(a.open) : "";

  const stages = k.stages.filter((s) => s.items);
  const box = clear($("ng-stages"));
  if (stages.length) {
    box.append(el("div", { class: "stage-strip" }, stages.map((s) =>
      el("div", { class: "st" }, el("span", { class: "small muted", text: s.label }),
        el("b", { class: s.impact > 0 ? "up" : "down", text: s.impact == null ? "—" : `${s.impact > 0 ? "+" : ""}${money(s.impact)}` }),
        el("span", { class: "small", text: `${num(s.increases)} up · ${num(s.decreases)} down of ${num(s.items)}` })))));
  }
}

async function moveStep(step) {
  const label = ng.ov.steps.find((s) => s.key === step).label;
  if (!confirm(`Move this negotiation to "${label}"?`)) return;
  try {
    await send(`/api/admin/nego/cycles/${ng.cid}/step`, "POST", { step });
    toast(`Now at ${label}`);
    await refreshOverview();
    if (ng.tab === "findings") renderFindings($("ng-body"));
  } catch (e) { toast(e.message); }
}

// ---------- items ----------
const SECTION_CLASS = { identification: "sec-id", current_mou: "sec-mou", rfq: "sec-rfq", counter_offer: "sec-co", feedback1: "sec-fb", online_nego: "sec-on" };
const ITEM_COLS = [
  ["erp_code", "ERP Code", "identification", "text"], ["item_name", "Item Name", "identification", "text"], ["brand", "Brand", "identification", "text"],
  ["item_status", "Status", "identification", "text"],
  ["mou_qty", "Qty/PO unit", "current_mou", "qty"], ["mou_hna", "HNA/PO unit", "current_mou", "money"], ["mou_disc", "Disc", "current_mou", "pct"], ["mou_unit_price", "Price/pc", "current_mou", "price"],
  ["rfq_qty", "Qty/PO unit", "rfq", "qty"], ["rfq_hna", "HNA/PO unit", "rfq", "money"], ["rfq_disc", "Disc", "rfq", "pct"], ["rfq_unit_price", "Price/pc", "rfq", "price"],
  ["co_disc", "Disc", "counter_offer", "pct"], ["co_unit_price", "Price/pc", "counter_offer", "price"],
  ["fb1_disc", "Disc", "feedback1", "pct"], ["fb1_unit_price", "Price/pc", "feedback1", "price"],
  ["on_disc", "Disc", "online_nego", "pct"], ["on_unit_price", "Price/pc", "online_nego", "price"],
];
// A principal cell is "missing" only once its step has been handed back to Siloam.
const SECTION_STEP = { identification: "identification", rfq: "rfq", feedback1: "feedback1" };
const secOf = (fld) => (ITEM_COLS.find((c) => c[0] === fld) || [])[2];
function stepPassed(step) {
  if (!step) return false;
  const keys = ng.ov.steps.map((s) => s.key);
  return keys.indexOf(ng.ov.cycle.current_step) > keys.indexOf(step);
}
const SECTION_SHORT = { identification: "Item identification", current_mou: "Current MOU", rfq: "RFQ", counter_offer: "Counter offer", feedback1: "Feedback I", online_nego: "Online nego" };

function cellValue(kind, v) {
  if (v == null || v === "") return "";
  if (kind === "pct") return typeof v === "number" ? pct(v, Math.abs(v * 100 - Math.round(v * 100)) > 1e-6 ? 1 : 0) : String(v);
  if (kind === "money") return num(v, 0);
  if (kind === "price") return num(v, v < 100 ? 2 : 0);
  if (kind === "qty") return num(v, v % 1 ? 2 : 0);
  return String(v);
}

async function renderItems(body) {
  clear(body);
  const p = ng.page;
  const q = el("input", { type: "search", id: "ng-q", value: p.q || null, placeholder: "ERP code, name, brand, REF" });
  const f = el("select", { id: "ng-filter" }, FILTERS.map(([k, l]) => el("option", { value: k, text: l })));
  f.value = p.filter;
  const s = el("select", { id: "ng-sort" }, SORTS.map(([k, l]) => el("option", { value: k, text: l })));
  s.value = p.sort;
  const legend = el("div", { class: "legend-cells" },
    el("span", {}, el("i", { class: "c-req" }), "Principal fills"), el("span", {}, el("i", { class: "c-opt" }), "Optional"),
    el("span", {}, el("i", { class: "c-ref" }), "Reference / calculated"), el("span", {}, el("i", { class: "c-adm" }), "Siloam fills"));
  const wrap = el("div", { class: "table-wrap card items-wrap" });
  const pager = el("div", { class: "pager" });
  body.append(el("div", { class: "filters board-filters" }, el("label", {}, "Search", q), el("label", {}, "Show", f), el("label", {}, "Sort", s), legend), wrap, pager);
  let timer;
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { p.q = q.value.trim(); p.offset = 0; loadItems(wrap, pager); }, 250); });
  f.addEventListener("change", () => { p.filter = f.value; p.offset = 0; loadItems(wrap, pager); });
  s.addEventListener("change", () => { p.sort = s.value; p.offset = 0; loadItems(wrap, pager); });
  loadItems(wrap, pager);
}

async function loadItems(wrap, pager) {
  const p = ng.page;
  const qs = new URLSearchParams({ q: p.q, filter: p.filter, sort: p.sort, offset: p.offset, limit: 50 });
  wrap.classList.add("loading");
  let d;
  try { d = await api(`/api/nego/cycles/${ng.cid}/items?${qs}`); } catch (e) { clear(wrap).append(el("p", { class: "error pad", text: e.message })); return; }
  wrap.classList.remove("loading");
  if (!d.total) {
    const none = ng.ov.kpis.items === 0;
    clear(wrap).append(el("div", { class: "empty-state" },
      el("h3", { text: none ? "No items yet" : "No items match" }),
      el("p", { class: "muted", text: none ? "Build the item list from the PO export, formulary and current MOU." : "Try another filter or search." }),
      none && isAdmin() ? act("Go to Files & prepare", () => { location.hash = `#nego/${ng.cid}/files`; }) : null));
    clear(pager);
    return;
  }
  const t = el("table", { class: "items" });
  const sections = [];
  ITEM_COLS.forEach(([, , sec]) => { const last = sections[sections.length - 1]; if (last && last.sec === sec) last.n++; else sections.push({ sec, n: 1 }); });
  const cols = ng.ov.columns.reduce((m, c) => { m[c.field] = c; return m; }, {});
  t.append(el("thead", {},
    el("tr", { class: "sec" }, sections.map((x) => el("th", { colspan: x.n, class: SECTION_CLASS[x.sec], text: SECTION_SHORT[x.sec] })),
      el("th", { colspan: 3, class: "sec-eng", text: "Engine" })),
    el("tr", {}, ITEM_COLS.map(([fld, label, , kind]) => el("th", { class: `${kind !== "text" ? "num" : ""} ${fld === "item_name" ? "namecol" : ""} ${fld === "erp_code" ? "codecol" : ""}`, text: label, title: (cols[fld] || {}).header || label })),
      el("th", { class: "num", text: "vs MOU" }), el("th", { class: "num", text: "Impact/yr" }), el("th", { text: "Findings" }))));
  const tb = el("tbody");
  for (const it of d.items) {
    const tr = el("tr", { class: `clickable ${it.item_status === "Discontinue" ? "disc" : ""}`, tabindex: 0, onclick: () => openItem(it.id) });
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openItem(it.id); });
    for (const [fld, , , kind] of ITEM_COLS) {
      const who = (cols[fld] || {}).who;
      const cls = [kind !== "text" ? "num" : "", who === "principal" ? "c-req" : who === "optional" ? "c-opt" : who === "admin" ? "c-adm" : "c-ref",
        fld === "item_name" ? "namecol" : "", fld === "erp_code" ? "codecol" : "",
        who === "principal" && (it[fld] == null || it[fld] === "") && it.item_status !== "Discontinue" && stepPassed(SECTION_STEP[secOf(fld)]) ? "empty" : ""].join(" ");
      tr.append(el("td", { class: cls, text: cellValue(kind, it[fld]), title: fld === "item_name" ? it.item_name : null }));
    }
    const ch = it.change_pct;
    tr.append(el("td", { class: `num ${ch > 0.0005 ? "up" : ch < -0.0005 ? "down" : ""}`, text: ch == null ? "" : `${ch > 0 ? "+" : ""}${pct(ch)}` }));
    tr.append(el("td", { class: `num ${it.impact > 0 ? "up" : it.impact < 0 ? "down" : ""}`, text: it.impact == null ? "" : `${it.impact > 0 ? "+" : ""}${money(it.impact)}` }));
    tr.append(el("td", { class: "flags" }, it.flags.slice(0, 2).map((x) => el("span", { class: `sev ${x.severity}`, text: x.label })),
      it.flags.length > 2 ? el("span", { class: "small muted", text: ` +${it.flags.length - 2}` }) : null));
    tb.append(tr);
  }
  t.append(tb);
  clear(wrap).append(t);
  const p0 = d.offset + 1, p1 = d.offset + d.items.length;
  clear(pager).append(
    el("span", { class: "small muted", text: `Showing ${num(p0)}–${num(p1)} of ${num(d.total)}` }),
    el("button", { type: "button", class: "act ghost", text: "← Previous", disabled: d.offset ? null : "", onclick: () => { ng.page.offset = Math.max(0, d.offset - 50); loadItems(wrap, pager); } }),
    el("button", { type: "button", class: "act ghost", text: "Next →", disabled: p1 < d.total ? null : "", onclick: () => { ng.page.offset = d.offset + 50; loadItems(wrap, pager); } }));
}

// ---------- one item ----------
const EDIT_GROUPS = [
  ["identification", ["brand", "catalog_no", "item_status", "remarks"]],
  ["current_mou", ["mou_qty", "mou_hna", "mou_disc"]],
  ["rfq", ["rfq_qty", "rfq_hna", "rfq_disc"]],
  ["counter_offer", ["co_disc"]],
  ["feedback1", ["fb1_disc"]],
  ["online_nego", ["on_disc"]],
];

async function openItem(id) {
  const body = openPanel(el("p", { class: "muted", text: "Loading…" }));
  let it;
  try { it = await api(`/api/nego/cycles/${ng.cid}/items/${id}`); } catch (e) { clear(body).append(el("p", { class: "error", text: e.message })); return; }
  const cols = ng.ov.columns.reduce((m, c) => { m[c.field] = c; return m; }, {});
  const prices = el("div", { class: "pricegrid" },
    [["MOU", it.mou_unit_price], ["RFQ", it.rfq_unit_price], ["Counter offer", it.co_unit_price], ["Feedback I", it.fb1_unit_price], ["Online nego", it.on_unit_price], ["Last PO", it.po_unit_price]]
      .filter(([, v]) => v != null).map(([l, v]) => el("div", { class: l === "Online nego" ? "t" : null }, el("span", { text: `${l} per piece` }), el("b", { text: unit(v) }),
        it.base_price && l !== "MOU" ? el("small", { text: `${v / it.base_price - 1 > 0 ? "+" : ""}${pct(v / it.base_price - 1)} vs ${it.base_from === "mou" ? "MOU" : "PO"}` }) : null)));
  const facts = el("div", { class: "facts" },
    [["PO unit", it.po_unit_text || "—"], ["Pieces / PO unit", it.po_unit_pcs ? num(it.po_unit_pcs) : "—"], ["PO qty, 12 months", it.po_qty_12m ? num(it.po_qty_12m) : "—"],
      ["PO value, 12 months", it.po_value_12m ? money(it.po_value_12m) : "—"], ["Pieces a year", it.annual_pcs ? num(it.annual_pcs) : "—"],
      ["Impact a year", it.impact == null ? "—" : `${it.impact > 0 ? "+" : ""}${money(it.impact)}`], ["Source", { po: "PO history", formulary: "Formulary (not bought)", mou: "MOU only", template: "Template" }[it.source] || it.source || "—"]]
      .map(([l, v]) => el("div", {}, el("span", { text: l }), el("b", { text: v }))));
  const findings = el("div", { class: "findings" });
  renderFindingList(findings, it.anomalies.filter((a) => a.status !== "cleared"), () => openItem(id), true);

  const form = el("form", { class: "progress-form item-form" });
  const inputs = {};
  for (const [sec, fields] of EDIT_GROUPS) {
    const fs = el("fieldset", { class: SECTION_CLASS[sec] }, el("legend", { text: SECTION_SHORT[sec] }));
    const grid = el("div", { class: "row3" });
    for (const f of fields) {
      const c = cols[f];
      let input;
      if (f === "item_status") {
        input = el("select", { name: f }, ["", "Active", "Discontinue"].map((v) => el("option", { value: v, text: v || "—" })));
        input.value = it[f] || "";
      } else {
        const v = it[f];
        const shown = v == null ? "" : c.kind === "pct" ? String(+(v * 100).toFixed(4)) : String(v);
        input = el("input", { name: f, value: shown, inputmode: c.kind === "text" ? null : "decimal", placeholder: c.kind === "pct" ? "%" : null });
      }
      if (!isAdmin()) input.setAttribute("disabled", "");
      inputs[f] = input;
      grid.append(field(c.kind === "pct" ? `${c.header.replace("%", "")} (%)` : c.header, input));
    }
    fs.append(grid);
    form.append(fs);
  }
  const msg = el("p", { class: "error", role: "alert", hidden: "" });
  if (isAdmin()) form.append(msg, el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Save changes" }),
    el("span", { class: "small muted", text: "Every change is logged with your name." })));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const changes = {};
    for (const [f, input] of Object.entries(inputs)) {
      const c = cols[f];
      const raw = input.value.trim();
      const before = it[f];
      let v = raw === "" ? null : raw;
      if (v != null && c.kind === "pct") v = String(raw).replace(",", ".") / 100;
      else if (v != null && c.kind !== "text") v = raw;
      const same = (v == null && before == null) || (c.kind === "pct" ? v != null && before != null && Math.abs(v - before) < 1e-9 : String(v) === String(before));
      if (!same) changes[f] = v;
    }
    if (!Object.keys(changes).length) { msg.textContent = "Nothing changed."; msg.hidden = false; return; }
    try {
      const r = await send(`/api/admin/nego/cycles/${ng.cid}/items/${id}`, "PATCH", { changes });
      toast(`Saved ${r.changed.length} change${r.changed.length === 1 ? "" : "s"}`);
      await refreshOverview();
      if (ng.tab === "items") loadItems(document.querySelector(".items-wrap"), document.querySelector(".pager"));
      openItem(id);
    } catch (ex) { msg.textContent = ex.message; msg.hidden = false; }
  });
  const hist = it.history.length ? el("ul", { class: "timeline" }, it.history.slice(0, 20).map((h) =>
    el("li", {}, el("b", { text: (cols[h.field] || {}).header || h.field }), ` ${h.old ?? "—"} → ${h.new ?? "—"}`,
      el("div", { class: "small muted", text: `${h.user_email} · ${h.via} · ${new Date(h.at).toLocaleString("en-GB")}` })))) : el("p", { class: "muted small", text: "No changes yet." });
  clear(body).append(
    el("p", { class: "eyebrow", text: it.erp_code || "No ERP code" }),
    el("h2", { id: "drawer-title", text: it.item_name || "Unnamed item" }),
    el("p", { class: "muted small", text: [it.brand, it.catalog_no, it.item_status].filter(Boolean).join(" · ") }),
    prices, facts,
    el("h3", { text: "Findings" }), findings,
    el("h3", { text: isAdmin() ? "Edit" : "Values" }), form,
    el("h3", { text: "History" }), hist);
}

// ---------- findings ----------
async function renderFindings(body) {
  clear(body);
  const status = el("select", { id: "ng-fstatus" }, [["open", "Open"], ["kept", "Kept as is"], ["fixed", "Fixed"], ["cleared", "Cleared by a rescan"], ["", "All"]].map(([k, l]) => el("option", { value: k, text: l })));
  status.value = ng.findStatus;
  const chips = el("div", { class: "chips" });
  const list = el("div", { class: "findings" });
  body.append(el("div", { class: "filters board-filters" }, el("label", {}, "Status", status),
    isAdmin() ? act("Rescan now", async () => {
      try { const r = await send(`/api/admin/nego/cycles/${ng.cid}/scan`, "POST", {}); toast(`${r.found} findings · ${r.new} new · ${r.cleared} cleared`); await refreshOverview(); renderFindings(body); } catch (e) { toast(e.message); }
    }, "secondary") : null,
    el("p", { class: "small muted grow", text: "The scan proposes; a person decides. Fix applies the suggested value. Keep as is needs a reason. Decisions stay through rescans." })),
  chips, list);
  status.addEventListener("change", () => { ng.findStatus = status.value; ng.rule = ""; renderFindings(body); });
  let d;
  try { d = await api(`/api/nego/cycles/${ng.cid}/anomalies?status=${encodeURIComponent(ng.findStatus)}`); } catch (e) { list.append(el("p", { class: "error", text: e.message })); return; }
  const draw = () => {
    clear(chips).append(el("button", { type: "button", class: `chipbtn ${ng.rule ? "" : "on"}`, text: `All (${d.anomalies.length})`, onclick: () => { ng.rule = ""; draw(); } }),
      ...Object.entries(d.by_rule).sort((a, b) => b[1] - a[1]).map(([label, n]) =>
        el("button", { type: "button", class: `chipbtn ${ng.rule === label ? "on" : ""}`, text: `${label} (${n})`, onclick: () => { ng.rule = label; draw(); } })));
    const rows = ng.rule ? d.anomalies.filter((a) => a.label === ng.rule) : d.anomalies;
    renderFindingList(list, rows, () => renderFindings(body));
  };
  draw();
}

function renderFindingList(box, rows, after, inDrawer = false) {
  clear(box);
  if (!rows.length) { box.append(el("p", { class: "muted small", text: inDrawer ? "Nothing flagged for this item." : "Nothing here." })); return; }
  const shown = rows.slice(0, 200);
  for (const a of shown) {
    const reason = el("input", { placeholder: "Why keep it? e.g. confirmed with principal", "aria-label": "Reason" });
    const keepBox = el("div", { class: "keep", hidden: "" }, reason,
      act("Keep as is", async () => { await decideFinding(a, "keep", reason.value, after); }),
      act("Cancel", () => { keepBox.hidden = true; }, "ghost"));
    const actions = isAdmin() ? el("div", { class: "actions" },
      a.status === "open" && a.suggestion != null ? act(a.suggestion_text || "Apply fix", () => decideFinding(a, "fix", "", after)) : null,
      a.status === "open" ? act(a.suggestion != null ? "Mark fixed" : "Fixed it", () => decideFinding(a, "fix", "", after), a.suggestion != null ? "ghost" : "secondary") : null,
      a.status === "open" ? act("Keep as is…", () => { keepBox.hidden = false; reason.focus(); }, "ghost") : null,
      a.status === "kept" || a.status === "fixed" ? act("Reopen", () => decideFinding(a, "reopen", "", after), "ghost") : null,
      !inDrawer && a.item_id ? act("Open item", () => openItem(a.item_id), "ghost") : null) :
      (!inDrawer && a.item_id ? el("div", { class: "actions" }, act("Open item", () => openItem(a.item_id), "ghost")) : null);
    box.append(el("article", { class: `finding ${a.severity} st-${a.status}` },
      el("div", { class: "f-top" }, el("span", { class: `sev ${a.severity}`, text: a.severity }), el("b", { text: a.label }),
        !inDrawer ? el("span", { class: "small muted", text: `${a.erp_code || ""} ${a.item_name || ""}` }) : null,
        a.status !== "open" ? el("span", { class: "small status", text: { kept: "Kept as is", fixed: "Fixed", cleared: "Cleared" }[a.status] }) : null),
      el("p", { text: a.message }),
      a.reason ? el("p", { class: "small muted", text: `Reason: ${a.reason} — ${a.decided_by}` }) : null,
      actions, keepBox));
  }
  if (rows.length > shown.length) box.append(el("p", { class: "muted small", text: `Showing the first ${shown.length} of ${rows.length}. Filter by rule to see the rest.` }));
}

async function decideFinding(a, decision, reason, after) {
  try {
    await send(`/api/admin/nego/cycles/${ng.cid}/anomalies/${a.id}`, "POST", { decision, reason });
    toast(decision === "fix" ? "Fixed" : decision === "keep" ? "Kept, reason logged" : "Reopened");
    await refreshOverview();
    after();
  } catch (e) { toast(e.message); }
}

// ---------- files ----------
function renderFiles(body) {
  clear(body);
  const c = ng.ov.cycle;
  const ex = card("Template_Nego", "The workbook the principal knows: same layout, colours, drop-downs and formulas, filled with this negotiation. Rows grow past 150 when needed.",
    el("div", { class: "actions" }, el("a", { class: "act", href: `/api/nego/cycles/${c.id}/export.xlsx`, text: "Download Template_Nego" })));
  body.append(ex);
  if (!isAdmin()) return;

  const files = { po: el("input", { type: "file", accept: ".csv,.xlsx", class: "sr-only", name: "po" }),
    formulary: el("input", { type: "file", accept: ".csv,.xlsx", class: "sr-only", name: "formulary" }),
    mou: el("input", { type: "file", accept: ".csv,.xlsx", class: "sr-only", name: "mou" }) };
  const prepOut = el("div", { role: "status" });
  const prep = el("form", { class: "upload-form wide" },
    el("div", { class: "drop3" }, dropZone(files.po, "PO export (12 months)"), dropZone(files.formulary, "Formulary"), dropZone(files.mou, "Current MOU / last Template_Nego")),
    el("p", { class: "small muted", text: "CSV or Excel, any header names the systems use (Item Number, Kode Barang, ERP Code…). Rows for other principals are skipped. Building again replaces the item list and its findings." }),
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Build item list" })), prepOut);
  prep.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    Object.entries(files).forEach(([k, f]) => { if (f.files.length) fd.append(k, f.files[0]); });
    if (![...fd.keys()].length) { clear(prepOut).append(el("p", { class: "error", text: "Choose at least one file." })); return; }
    if (ng.ov.kpis.items && !confirm("Replace the current item list and its findings?")) return;
    clear(prepOut).append(el("p", { class: "muted", text: "Building…" }));
    try {
      const r = await upload(`/api/admin/nego/cycles/${c.id}/prepare`, fd);
      clear(prepOut).append(prepSummary(r));
      await refreshOverview();
      toast(`${r.items} items · ${r.scan.found} findings`);
    } catch (ex2) { clear(prepOut).append(el("p", { class: "error", text: ex2.message })); }
  });
  const prevSummary = c.prepare_summary ? prepSummary(c.prepare_summary) : null;
  body.append(card("Prepare: build the item list", "Unique items bought from this principal in the last 12 months, plus formulary items not bought, with the current MOU prices.", prep, prevSummary ? el("details", {}, el("summary", { text: `Last build: ${new Date(c.prepared_at).toLocaleString("en-GB")}` }), prevSummary) : null));

  const tfile = el("input", { type: "file", accept: ".xlsx", class: "sr-only", name: "file" });
  const impOut = el("div", { role: "status" });
  const imp = el("form", { class: "upload-form wide" }, dropZone(tfile, "Filled Template_Nego (.xlsx)"),
    el("p", { class: "small muted", text: "Matched by ERP code, then item name. Blank cells never erase a value. You see every change before it's applied." }),
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Check file" })), impOut);
  imp.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!tfile.files.length) { clear(impOut).append(el("p", { class: "error", text: "Choose the file first." })); return; }
    const fd = new FormData(); fd.append("file", tfile.files[0]); fd.append("apply", "0");
    clear(impOut).append(el("p", { class: "muted", text: "Checking…" }));
    try { const plan = await upload(`/api/admin/nego/cycles/${c.id}/import`, fd); clear(impOut).append(importPreview(plan, tfile.files[0])); }
    catch (ex2) { clear(impOut).append(el("p", { class: "error", text: ex2.message })); }
  });
  body.append(card("Import a filled template", "The principal's reply by email, or values typed offline. Identification, RFQ and discount columns are read; calculated columns are recomputed.", imp));
}

function prepSummary(r) {
  const po = r.po || {};
  return el("div", { class: "preview ok" },
    el("div", { class: "facts" },
      [["Items", num(r.items)], ["From POs", num((r.by_source || {}).po || 0)], ["Formulary only", num((r.by_source || {}).formulary || 0)], ["MOU only", num((r.by_source || {}).mou || 0)],
        po.period_from ? ["PO period", `${fmtDate(po.period_from)} – ${fmtDate(po.period_to)}`] : null,
        po.po_lines_used != null ? ["PO lines used", `${num(po.po_lines_used)} of ${num(po.po_lines)}`] : null,
        r.scan ? ["Findings", num(r.scan.found)] : null].filter(Boolean).map(([l, v]) => el("div", {}, el("span", { text: l }), el("b", { text: v })))),
    r.several_po_units && r.several_po_units.length ? el("p", { class: "small warn-text", text: `Bought in more than one PO unit: ${r.several_po_units.slice(0, 8).join(", ")}${r.several_po_units.length > 8 ? "…" : ""}` }) : null,
    r.columns_used ? el("details", {}, el("summary", { text: "Columns used" }), el("ul", { class: "small" },
      Object.entries(r.columns_used).map(([k, m]) => el("li", { text: `${{ po: "PO export", formulary: "Formulary", mou: "MOU" }[k] || k}: ${Object.entries(m).map(([a, b]) => `${a} ← ${b}`).join(", ")}` })))) : null);
}

function importPreview(plan, file) {
  const box = el("div", { class: `preview ${plan.warning || plan.errors.length ? "bad" : "ok"}` },
    el("h3", { text: plan.changes.length ? `${num(plan.items_changed)} items would change` : "No changes found" }),
    el("div", { class: "facts" }, [["Rows in file", plan.rows_in_file], ["Matched", plan.matched], ["Not in this negotiation", plan.unmatched_count], ["Items not in the file", plan.not_in_file]]
      .map(([l, v]) => el("div", {}, el("span", { text: l }), el("b", { text: num(v) })))),
    plan.warning ? el("p", { class: "error", text: plan.warning }) : null,
    Object.keys(plan.changes_by_column).length ? el("p", { class: "small", text: `Changes by column: ${Object.entries(plan.changes_by_column).map(([k, v]) => `${k} ${v}`).join(" · ")}` }) : null,
    plan.errors.length ? el("ul", { class: "errors" }, plan.errors.slice(0, 20).map((x) => el("li", { text: `Row ${x.row}: ${x.message}` }))) : null,
    plan.unmatched.length ? el("p", { class: "small muted", text: `Not matched: ${plan.unmatched.slice(0, 6).map((u) => u.erp_code || u.item_name).join(", ")}${plan.unmatched_count > 6 ? "…" : ""}` }) : null);
  if (plan.changes.length) {
    const t = el("table");
    table(t, [{ label: "ERP", get: (c) => c.erp_code }, { label: "Item", get: (c) => c.item_name }, { label: "Column", get: (c) => c.header },
      { label: "Now", get: (c) => fmtAny(c.field, c.old) }, { label: "File", get: (c) => fmtAny(c.field, c.new) }], plan.changes.slice(0, 60));
    box.append(el("details", { open: "" }, el("summary", { text: `Changes${plan.changes.length > 60 ? " (first 60)" : ""}` }), el("div", { class: "table-wrap" }, t)));
    if (!plan.warning) box.append(el("div", { class: "actions" }, act("Apply changes", async () => {
      const fd = new FormData(); fd.append("file", file); fd.append("apply", "1");
      try {
        const r = await upload(`/api/admin/nego/cycles/${ng.cid}/import`, fd);
        toast(`${r.applied_items} items updated · ${r.scan.found} findings`);
        await refreshOverview();
        renderFiles($("ng-body"));
      } catch (e) { toast(e.message); }
    })));
  }
  return box;
}

function fmtAny(f, v) {
  if (v == null) return "—";
  if (/_disc$/.test(f)) return pct(v, 1);
  if (typeof v === "number") return num(v, v % 1 ? 2 : 0);
  return String(v);
}

// ---------- activity ----------
const EVENT_TEXT = {
  "cycle.create": "Negotiation opened", "cycle.prepare": "Item list built", "cycle.update": "Details changed", "step.set": "Step changed",
  "template.import": "Template imported", "template.export": "Template downloaded", scan: "Rescanned",
  "anomaly.fixed": "Finding fixed", "anomaly.kept": "Finding kept as is", "anomaly.open": "Finding reopened",
};
function renderActivity(body) {
  clear(body);
  const ev = ng.ov.events;
  if (!ev.length) { body.append(el("p", { class: "muted", text: "Nothing yet." })); return; }
  const stepLabel = (k) => (ng.ov.steps.find((s) => s.key === k) || {}).label || k;
  body.append(el("div", { class: "card" }, el("ul", { class: "timeline" }, ev.map((e) => {
    let extra = "";
    if (e.action === "step.set" && e.detail) extra = `${stepLabel(e.detail.from)} → ${stepLabel(e.detail.to)}${e.detail.note ? ` · ${e.detail.note}` : ""}`;
    else if (e.action === "cycle.prepare" && e.detail) extra = `${e.detail.items} items`;
    else if (e.action === "template.import" && e.detail) extra = `${e.detail.items} items, ${e.detail.changes} values`;
    else if (e.action === "anomaly.kept" && e.detail) extra = e.detail.reason || "";
    return el("li", {}, el("b", { text: EVENT_TEXT[e.action] || e.action }), extra ? ` · ${extra}` : "",
      el("div", { class: "small muted", text: `${e.user_email} · ${new Date(e.at).toLocaleString("en-GB")}` }));
  }))));
}
