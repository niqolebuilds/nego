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
    c.submitted_steps && c.submitted_steps[c.current_step] ? el("span", { class: "sent-pill", text: "Principal sent ✓" }) : null,
    p.overdue_days ? el("span", { class: "late-pill", text: `Overdue ${p.overdue_days} day${p.overdue_days === 1 ? "" : "s"}` }) :
      c.step_due && !(c.submitted_steps || {})[c.current_step] ? el("span", { class: "small muted", text: `Due ${fmtDate(c.step_due)}` }) : null,
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
  ng.tab = ["items", "findings", "negotiate", "files", "activity"].includes(tab) ? tab : "items";
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
    [["items", "Items"], ["findings", "Findings"], ["negotiate", "Negotiate"], ["files", "Files & prepare"], ["activity", "Activity"]].map(([k, label]) =>
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
        isAdmin() && c.status === "open" ? act("Principal link", () => linkPanel(c), "secondary") : null,
        isAdmin() ? act("Edit principal", () => principalForm(c.principal), "ghost") : null)),
    el("div", { id: "ng-steps" }),
    el("div", { class: "kpis5", id: "ng-kpis" }),
    el("div", { id: "ng-stages" }),
    tabs,
    el("div", { id: "ng-body" }));
  renderHeaderParts();
  ({ items: renderItems, findings: renderFindings, negotiate: renderNegotiate, files: renderFiles, activity: renderActivity })[ng.tab]($("ng-body"));
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
  const sent = (c.submitted_steps || {})[c.current_step];
  const turn = steps[at] && steps[at].who === "principal";
  const banner = sent ? el("p", { class: "sent-banner", text: `The principal sent ${steps[at].label} on ${new Date(sent.at).toLocaleString("en-GB")}. Review the findings, then move to the next step.` })
    : turn ? el("p", { class: "turn-banner", text: `It's the principal's turn${c.step_due ? `, due ${fmtDate(c.step_due)}` : ""}. Their link and reminders go out automatically (see Activity → Messages); Principal link shares it by hand.` }) : null;
  stepsBox.append(el("div", { class: "card stepcard" }, ol, banner, move));

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

const PRINCIPAL_STEPS = new Set(["identification", "rfq", "feedback1"]);

async function moveStep(step) {
  const label = ng.ov.steps.find((s) => s.key === step).label;
  if (!PRINCIPAL_STEPS.has(step)) {
    if (!confirm(`Move this negotiation to "${label}"?`)) return;
    return doMove(step, label, false);
  }
  // A principal step: offer to send their link automatically (email + WhatsApp via Power Automate).
  let auto = { enabled: false, missing: [] };
  try { auto = await api("/api/admin/nego/notify"); } catch (_) { /* shown as off */ }
  const p = ng.ov.cycle.principal;
  const to = [p.contact_email, p.contact_phone].filter(Boolean).join(" · ");
  const sendBox = el("input", { type: "checkbox", id: "send-link" });
  sendBox.checked = auto.enabled && !!to;
  if (!auto.enabled || !to) sendBox.disabled = true;
  const why = !auto.enabled ? `Automatic sending is off: set ${auto.missing.join(" and ")} on the server (see deploy/POWER_AUTOMATE.md). You can still share the link by hand.`
    : !to ? "The principal has no email or WhatsApp number. Add one with Edit principal, or share the link by hand." : `To: ${to}`;
  const days = (await api("/api/settings").catch(() => ({ settings: [] }))).settings.find((x) => x.key === "step_days");
  const d0 = new Date(); d0.setDate(d0.getDate() + (days ? days.value : 7));
  const dueInput = el("input", { type: "date", id: "step-due", value: d0.toISOString().slice(0, 10), min: new Date().toISOString().slice(0, 10) });
  const body = openPanel(el("p", { class: "eyebrow", text: "Move step" }), el("h2", { id: "drawer-title", text: `Move to ${label}` }),
    el("p", { class: "muted", text: "It's the principal's turn at this step. They fill it online or in Excel from their link." }),
    el("form", { class: "progress-form", onsubmit: (e) => e.preventDefault() }, field("Deadline for the principal", dueInput,
      "Reminders go out 3 days and 1 day before, then daily if late (Engine settings).")),
    el("label", { class: "check big-check" }, sendBox, el("span", {}, el("b", { text: "Send the link to the principal automatically" }), el("span", { class: "small muted block", text: "Email and WhatsApp, through Power Automate. A new link replaces any older one." }))),
    el("p", { class: `small ${auto.enabled && to ? "" : "warn-text"}`, text: why }),
    el("div", { class: "actions" }, act(`Move to ${label}`, async () => { closeDrawer(); await doMove(step, label, sendBox.checked, dueInput.value); }), act("Cancel", closeDrawer, "ghost")));
  return body;
}

async function doMove(step, label, sendLink, due) {
  try {
    const r = await send(`/api/admin/nego/cycles/${ng.cid}/step`, "POST", { step, send_link: sendLink, due: due || null });
    const m = r.message;
    toast(m ? (m.status === "queued" ? `Now at ${label}. Link sent to ${m.recipient}` : `Now at ${label}. Link not sent: ${m.reason === "no_contact" ? "no contact details" : "automatic sending is off"}`) : `Now at ${label}`);
    await refreshOverview();
    if (ng.tab === "findings") renderFindings($("ng-body"));
    if (ng.tab === "activity") renderActivity($("ng-body"));
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
      el("th", { colspan: 4, class: "sec-eng", text: "Engine" })),
    el("tr", {}, ITEM_COLS.map(([fld, label, , kind]) => el("th", { class: `${kind !== "text" ? "num" : ""} ${fld === "item_name" ? "namecol" : ""} ${fld === "erp_code" ? "codecol" : ""}`, text: label, title: (cols[fld] || {}).header || label })),
      el("th", { class: "num", text: "Market/pc", title: "Lowest confirmed market benchmark per piece incl. PPN" }),
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
    tr.append(el("td", { class: "num", text: it.bench_pp == null ? "" : num(it.bench_pp, it.bench_pp < 100 ? 2 : 0), title: it.bench_source || null }));
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
  const reason = el("div", {},
    it.price_reason ? el("p", { class: "reason-note" }, el("b", { text: "Principal's reason: " }), it.price_reason) : null,
    it.co_note ? el("p", { class: "reason-note co" }, el("b", { text: "Counter offer basis: " }), it.co_note) : null,
    it.benchmarks && it.benchmarks.length ? el("div", { class: "bm-list" }, el("b", { text: "Market benchmarks" }),
      it.benchmarks.map((m) => benchRow(m, () => openItem(id)))) : null);

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
    prices, facts, reason,
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
  "anomaly.fixed": "Finding fixed", "message.queued": "Link queued for the principal", "message.sent": "Message delivered to Power Automate",
  "message.failed": "Message failed", "message.skipped": "Message not sent", "link.create": "Principal link created", "link.revoke": "Principal link revoked",
  "link.open": "Principal opened the link", "principal.submit": "Principal sent the step", "principal.excel": "Principal uploaded Excel", "principal.download": "Principal downloaded Excel", "anomaly.kept": "Finding kept as is", "anomaly.open": "Finding reopened",
};
async function renderMessages(box) {
  let d;
  try { d = await api(`/api/admin/nego/cycles/${ng.cid}/messages`); } catch (e) { box.append(el("p", { class: "error", text: e.message })); return; }
  const a = d.automation;
  clear(box).append(el("div", { class: "card msgs" },
    el("div", { class: "msg-head" }, el("h3", { text: "Messages to the principal" }),
      el("span", { class: `pill ${a.enabled ? "good" : ""}`, text: a.enabled ? `Automatic sending on · ${a.webhook_host}` : "Automatic sending off" }),
      isAdmin() && ng.ov.cycle.status === "open" && PRINCIPAL_STEPS.has(ng.ov.cycle.current_step) ? act("Send link now", async () => {
        try { const r = await send(`/api/admin/nego/cycles/${ng.cid}/links/send`, "POST", {}); toast(r.status === "queued" ? `Link sent to ${r.recipient}` : "Not sent: see the message list"); await refreshOverview(); renderActivity($("ng-body")); } catch (e) { toast(e.message); }
      }, "secondary") : null),
    d.messages.length ? el("table", { class: "msg-table" }, el("thead", {}, el("tr", {}, ["When", "Message", "To", "Status", ""].map((h) => el("th", { text: h })))),
      el("tbody", {}, d.messages.map((m) => el("tr", {},
        el("td", { text: new Date(m.created_at).toLocaleString("en-GB") }),
        el("td", { text: { "principal.step_opened": "Link for the step", "principal.reminder": "Deadline reminder", "siloam.step_submitted": "Notice to Siloam: principal sent", "siloam.mou_alert": "MOU alert" }[m.event] || m.event }),
        el("td", { text: m.recipient || "—" }),
        el("td", {}, el("span", { class: `mstat s-${m.status}`, text: { queued: "Queued", sending: "Sending", sent: "Sent", failed: `Failed (${m.attempts} tries)`, skipped: "Not sent" }[m.status] || m.status }),
          m.last_error ? el("div", { class: "small muted", text: m.last_error }) : null),
        el("td", {}, isAdmin() && m.status === "failed" ? act("Retry", async () => {
          try { await send(`/api/admin/nego/cycles/${ng.cid}/messages/${m.id}/retry`, "POST", {}); toast("Queued again"); setTimeout(() => renderActivity($("ng-body")), 800); } catch (e) { toast(e.message); }
        }, "ghost") : null))))) : el("p", { class: "muted small", text: "No messages yet. They're sent when the negotiation moves to a principal step." })));
}

function renderActivity(body) {
  clear(body);
  const msgBox = el("div");
  body.append(msgBox);
  renderMessages(msgBox);
  const ev = ng.ov.events;
  if (!ev.length) { body.append(el("p", { class: "muted", text: "Nothing yet." })); return; }
  body.append(el("h3", { class: "act-title", text: "Activity" }));
  const stepLabel = (k) => (ng.ov.steps.find((s) => s.key === k) || {}).label || k;
  body.append(el("div", { class: "card" }, el("ul", { class: "timeline" }, ev.map((e) => {
    let extra = "";
    if (e.action === "step.set" && e.detail) extra = `${stepLabel(e.detail.from)} → ${stepLabel(e.detail.to)}${e.detail.note ? ` · ${e.detail.note}` : ""}`;
    else if (e.action === "cycle.prepare" && e.detail) extra = `${e.detail.items} items`;
    else if (e.action === "template.import" && e.detail) extra = `${e.detail.items} items, ${e.detail.changes} values`;
    else if (e.action === "anomaly.kept" && e.detail) extra = e.detail.reason || "";
    else if (e.action.startsWith("message.") && e.detail) extra = [e.detail.to, e.detail.error].filter(Boolean).join(" · ");
    return el("li", {}, el("b", { text: EVENT_TEXT[e.action] || e.action }), extra ? ` · ${extra}` : "",
      el("div", { class: "small muted", text: `${e.user_email} · ${new Date(e.at).toLocaleString("en-GB")}` }));
  }))));
}

// ---------- principal link ----------
async function linkPanel(c) {
  const body = openPanel(el("p", { class: "eyebrow", text: "Principal link" }), el("h2", { id: "drawer-title", text: c.principal.name }));
  const days = el("input", { type: "number", min: 1, max: 90, value: 14, id: "link-days" });
  const out = el("div", { role: "status" });
  const list = el("div");
  const draw = (links) => {
    clear(list).append(links.length ? el("ul", { class: "timeline" }, links.map((l) => el("li", {},
      el("b", { text: l.revoked_at ? "Revoked" : l.expires_at < new Date().toISOString() ? "Expired" : "Active" }),
      ` · created ${new Date(l.created_at).toLocaleString("en-GB")} by ${l.created_by}`,
      el("div", { class: "small muted", text: `Valid until ${new Date(l.expires_at).toLocaleString("en-GB")}${l.last_used_at ? ` · last opened ${new Date(l.last_used_at).toLocaleString("en-GB")}` : " · not opened yet"}` }),
      !l.revoked_at ? act("Revoke", async () => { try { draw((await send(`/api/admin/nego/cycles/${c.id}/links/${l.id}/revoke`, "POST", {})).links); } catch (e) { toast(e.message); } }, "ghost") : null))) :
      el("p", { class: "muted small", text: "No links yet." }));
  };
  const create = act("Create link", async () => {
    try {
      const l = await send(`/api/admin/nego/cycles/${c.id}/links`, "POST", { days: +days.value || 14 });
      const url = location.origin + l.path;
      const field = el("input", { value: url, readonly: "", id: "link-url" });
      clear(out).append(el("div", { class: "preview ok" },
        el("p", { class: "small", text: "Copy this link now and send it to the principal by WhatsApp or email. It isn't shown again; create a new one if it's lost." }),
        field, el("div", { class: "actions" }, act("Copy", async () => { field.select(); try { await navigator.clipboard.writeText(url); toast("Copied"); } catch (_) { document.execCommand("copy"); toast("Copied"); } }),
          el("a", { class: "act secondary", href: `https://wa.me/?text=${encodeURIComponent(`Siloam Hospitals: silakan isi ${c.principal.name} di tautan berikut: ${url}`)}`, target: "_blank", rel: "noopener", text: "Share on WhatsApp" }))));
      draw((await api(`/api/admin/nego/cycles/${c.id}/links`)).links);
    } catch (e) { clear(out).append(el("p", { class: "error", text: e.message })); }
  });
  body.append(
    el("p", { class: "muted", text: "The principal opens this link to fill their current step online or in Excel. They only see their own items and their own columns: no PO volumes, no Siloam findings, no other principals." }),
    el("p", { class: "small", text: "Links are sent automatically when you move the negotiation to a principal step (if Power Automate is set up). To send by hand, create one here." }),
    el("form", { class: "progress-form", onsubmit: (e) => e.preventDefault() }, field("Valid for (days)", days), el("div", { class: "actions" }, create)),
    out, el("h3", { text: "Links" }), list);
  try { draw((await api(`/api/admin/nego/cycles/${c.id}/links`)).links); } catch (e) { list.append(el("p", { class: "error", text: e.message })); }
}

// ---------- negotiate: counter offer, benchmarks, online nego, package ----------
// Append children, skipping null/false (Element.append would print them as text).
const put = (n, ...kids) => { n.append(...kids.flat(2).filter((k) => k != null && k !== false)); return n; };

function benchRow(m, after) {
  return el("div", { class: `bm st-${m.status}` },
    el("div", {}, el("b", { text: `${m.source}: ${unit(m.price_pp)}/pc` }), el("span", { class: "small muted", text: ` · ${m.bm_name}${m.bm_unit ? ` (${m.bm_unit})` : ""}` }),
      el("div", { class: "small muted", text: `Match ${pct(m.confidence, 0)} by ${m.method === "ref" ? "catalogue no." : m.method === "remembered" ? "earlier confirmation" : "name"} · ${m.status === "confirmed" ? "confirmed" : m.status === "rejected" ? "rejected" : "to review"}` })),
    isAdmin() ? el("div", { class: "actions" },
      m.status !== "confirmed" ? act("Confirm", () => decideBench(m.id, "confirm", after), "ghost") : null,
      m.status !== "rejected" ? act("Not the same", () => decideBench(m.id, "reject", after), "ghost") : null) : null);
}

async function decideBench(mid, decision, after) {
  try { await send(`/api/admin/nego/cycles/${ng.cid}/benchmarks/${mid}`, "POST", { decision }); await refreshOverview(); after(); }
  catch (e) { toast(e.message); }
}

async function renderNegotiate(body) {
  clear(body);
  const c = ng.ov.cycle;
  const coBox = el("div"), bmBox = el("div"), onBox = el("div"), pkBox = el("div");
  put(body, 
    card("4 · Counter offer", "The engine proposes a counter-offer discount per item: down to the lowest of the MOU price, Siloam's last PO price and a confirmed market benchmark. It never asks for less than the principal quoted, and at most the set number of extra points (Engine settings).", coBox),
    card("Market benchmarks", "INAPROC e-Katalog, SIMO Inhealth or any price list your team can legitimately use. The app matches rows to items by catalogue no. and name; strong matches are confirmed automatically, the rest wait for you. Principals never see benchmarks.", bmBox),
    card("6 · Online Nego", "After the meeting, record what was agreed. Start from the principal's Feedback I discount (else the counter offer) and edit items in the Items tab.", onBox),
    card("Submission package", "The agreed prices as Excel Confirmation and BAK draft (by binding), active items only, A–Z, no duplicate ERP codes. Company documents the principal sends at the last step are encrypted and only admins can open them.", pkBox));
  drawCO(coBox); drawBench(bmBox); drawOn(onBox, c); drawPackage(pkBox);
}

async function drawCO(box) {
  put(clear(box), el("p", { class: "muted small", text: "Working out suggestions…" }));
  let plan;
  try { plan = await api(`/api/nego/cycles/${ng.cid}/co-plan`); } catch (e) { put(clear(box), el("p", { class: "error", text: e.message })); return; }
  const k = ng.ov.kpis.stages;
  const rfq = k.find((s) => s.key === "rfq"), co = k.find((s) => s.key === "co");
  const facts = el("div", { class: "facts" },
    [["Items with an RFQ", num(rfq.items)], ["RFQ impact a year", rfq.impact == null ? "—" : `${rfq.impact > 0 ? "+" : ""}${money(rfq.impact)}`],
      ["Counter offers set", num(co.items)], ["Counter offer impact", co.impact == null ? "—" : `${co.impact > 0 ? "+" : ""}${money(co.impact)}`],
      ["New suggestions", num(plan.count)], ["Impact if applied", plan.co_impact == null ? "—" : `${plan.co_impact > 0 ? "+" : ""}${money(plan.co_impact)}`]]
      .map(([l, v]) => el("div", {}, el("span", { text: l }), el("b", { text: v }))));
  const sample = plan.suggestions.slice(0, 5).map((s) => el("li", { class: "small", text: `${pct(s.co_disc, 1)} — ${s.note}` }));
  put(clear(box), facts, sample.length ? el("details", {}, el("summary", { text: "Examples" }), el("ul", {}, sample)) : null,
    isAdmin() ? el("div", { class: "actions" },
      plan.count ? act(`Apply ${num(plan.count)} suggestions`, async () => {
        try { const r = await send(`/api/admin/nego/cycles/${ng.cid}/co`, "POST", { overwrite: false }); toast(`${r.applied} counter offers set`); await refreshOverview(); drawCO(box); } catch (e) { toast(e.message); }
      }) : el("span", { class: "small muted", text: "Every item with an RFQ already has a counter offer." }),
      co.items ? act("Recalculate all", async () => {
        if (!confirm("Replace every counter-offer discount with a fresh suggestion?")) return;
        try { const r = await send(`/api/admin/nego/cycles/${ng.cid}/co`, "POST", { overwrite: true }); toast(`${r.applied} counter offers recalculated`); await refreshOverview(); drawCO(box); } catch (e) { toast(e.message); }
      }, "ghost") : null) : null);
}

async function drawBench(box) {
  const file = el("input", { type: "file", accept: ".csv,.xlsx", class: "sr-only", name: "file" });
  const source = el("select", { name: "source" }, ["INAPROC e-Katalog", "SIMO Inhealth", "Other price list"].map((v) => el("option", { value: v, text: v })));
  const incl = el("input", { type: "checkbox", name: "incl_ppn", checked: "" });
  const out = el("div", { role: "status" });
  const review = el("div");
  clear(box);
  if (isAdmin()) {
    const form = el("form", { class: "upload-form wide" },
      el("div", { class: "row3" }, el("label", {}, "Source", source), el("label", { class: "check" }, incl, "Prices include PPN"), el("span")),
      dropZone(file, "Benchmark file (.csv or .xlsx)"),
      el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Import and match" }),
        act("Match again", async () => { try { const r = await send(`/api/admin/nego/cycles/${ng.cid}/benchmarks/match`, "POST", {}); toast(`${r.items_matched} items matched · ${r.to_review} to review`); await refreshOverview(); drawBench(box); } catch (e) { toast(e.message); } }, "ghost")),
      out);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!file.files.length) { put(clear(out), el("p", { class: "error", text: "Choose a file first." })); return; }
      const fd = new FormData(); fd.append("file", file.files[0]); fd.append("source", source.value); fd.append("incl_ppn", incl.checked ? "1" : "0");
      put(clear(out), el("p", { class: "muted", text: "Importing…" }));
      try {
        const r = await upload("/api/admin/nego/benchmarks", fd);
        const m = await send(`/api/admin/nego/cycles/${ng.cid}/benchmarks/match`, "POST", {});
        toast(`${r.rows} rows imported · ${m.items_matched} items matched · ${m.to_review} to review`);
        await refreshOverview(); drawBench(box);
      } catch (ex) { put(clear(out), el("p", { class: "error", text: ex.message })); }
    });
    put(box, form);
  }
  put(box, review);
  let d;
  try { d = await api(`/api/nego/cycles/${ng.cid}/benchmarks?status=suggested`); } catch (e) { put(review, el("p", { class: "error", text: e.message })); return; }
  let conf = [];
  try { conf = (await api(`/api/nego/cycles/${ng.cid}/benchmarks?status=confirmed`)).matches; } catch (_) { /* shown as 0 */ }
  put(review, el("p", { class: "small", text: `${num(new Set(conf.map((m) => m.item_id)).size)} items have a confirmed benchmark · ${num(d.matches.length)} matches to review` }));
  if (d.matches.length) {
    const t = el("table", { class: "bm-table" });
    table(t, [
      { label: "Item", get: (m) => el("div", {}, el("b", { text: m.item_name }), el("div", { class: "small muted", text: [m.erp_code, m.item_brand].filter(Boolean).join(" · ") })) },
      { label: "Benchmark", get: (m) => el("div", {}, m.bm_name, el("div", { class: "small muted", text: [m.source, m.bm_brand, m.bm_unit].filter(Boolean).join(" · ") })) },
      { label: "Price/pc", num: true, get: (m) => unit(m.price_pp) },
      { label: "Match", num: true, get: (m) => pct(m.confidence, 0) },
      { label: "", get: (m) => isAdmin() ? el("div", { class: "actions" }, act("Same", () => decideBench(m.id, "confirm", () => drawBench(box)), "ghost"), act("Different", () => decideBench(m.id, "reject", () => drawBench(box)), "ghost")) : "" },
    ], d.matches.slice(0, 40));
    put(review, el("details", {}, el("summary", { text: `Review ${num(d.matches.length)} possible matches` }),
      el("div", { class: "table-wrap" }, t), d.matches.length > 40 ? el("p", { class: "small muted", text: `Showing 40 of ${d.matches.length}. Confirm or reject these to see more.` }) : null));
  }
}

function drawOn(box, c) {
  const esc = ng.ov.escalation;
  const at = el("input", { type: "datetime-local", value: c.meeting_at || null });
  const notes = el("textarea", { rows: 3, placeholder: "Agreements, follow-ups, who attended" });
  notes.value = c.meeting_notes || "";
  put(clear(box), 
    el("div", { class: `esc ${esc.needed ? "need" : esc.items_agreed ? "ok" : ""}` },
      el("b", { text: !esc.items_agreed ? "No agreed prices yet" : esc.needed ? "Escalation needed before the BAK" : "Within limits: no escalation needed" }),
      esc.items_agreed ? el("span", { class: "small", text: ` · ${num(esc.items_agreed)} items agreed · impact a year ${esc.impact > 0 ? "+" : ""}${money(esc.impact)}` }) : null,
      esc.reasons.length ? el("ul", { class: "small" }, esc.reasons.map((r) => el("li", { text: r }))) : null),
    isAdmin() ? el("form", { class: "progress-form", onsubmit: async (e) => {
      e.preventDefault();
      try { await send(`/api/admin/nego/cycles/${ng.cid}`, "PATCH", { meeting_at: at.value, meeting_notes: notes.value }); toast("Meeting saved"); await refreshOverview(); } catch (ex) { toast(ex.message); }
    } }, el("div", { class: "row2" }, field("Meeting date and time", at), el("span")), field("Meeting notes", notes),
      el("div", { class: "actions" }, el("button", { type: "submit", class: "act secondary", text: "Save meeting" }),
        act("Fill agreed discounts (Feedback I, else counter offer)", async () => {
          try { const r = await send(`/api/admin/nego/cycles/${ng.cid}/on-fill`, "POST", { overwrite: false }); toast(`${r.filled} items filled`); await refreshOverview(); renderNegotiate($("ng-body")); } catch (e) { toast(e.message); }
        }))) : el("p", { class: "small", text: c.meeting_notes || "No meeting recorded yet." }));
}

async function drawPackage(box) {
  let chk;
  try { chk = await api(`/api/nego/cycles/${ng.cid}/package`); } catch (e) { put(clear(box), el("p", { class: "error", text: e.message })); return; }
  put(clear(box), el("div", { class: `preview ${chk.ready ? "ok" : "bad"}` },
    el("h3", { text: chk.ready ? `Ready: ${num(chk.agreed)} agreed items` : `${num(chk.agreed)} agreed items — not ready yet` }),
    chk.problems.length ? el("ul", { class: "errors" }, chk.problems.map((p) => el("li", { text: p }))) : null,
    isAdmin() && chk.agreed ? el("div", { class: "actions" }, el("a", { class: "act", href: `/api/admin/nego/cycles/${ng.cid}/package.xlsx`, text: chk.ready ? "Download Confirmation & BAK draft" : "Download as draft" })) : null));
  if (!isAdmin()) return;
  let docs;
  try { docs = await api(`/api/admin/nego/cycles/${ng.cid}/documents`); } catch (_) { return; }
  put(box, el("h3", { text: "Company documents from the principal" }),
    docs.documents.length ? el("ul", { class: "doclist" }, docs.documents.map((d) => el("li", {},
      el("a", { href: `/api/admin/nego/cycles/${ng.cid}/documents/${d.id}`, text: `${d.label}: ${d.filename}` }),
      el("span", { class: "small muted", text: ` · ${num(d.size / 1024, 0)} KB · ${new Date(d.uploaded_at).toLocaleString("en-GB")}` })))) :
      el("p", { class: "small muted", text: "None yet. The principal uploads them at the last step (Submission). Each download is logged." }));
}
