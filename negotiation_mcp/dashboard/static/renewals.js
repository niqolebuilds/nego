"use strict";
// Renewals: one card per principal MOU, in columns by renewal stage. The stage comes from
// that principal's negotiation (Negotiations tab), so it is never set twice. Opening a
// card shows the MOU, the negotiation step and the engine's price targets for the
// principal's SKUs. openRenewal() below is the per-SKU drawer the Assistant's calendar uses.

const STAGES = [
  ["not_started", "Not started"], ["preparing", "Preparing"], ["negotiating", "In negotiation"],
  ["offer_received", "Offer received"], ["agreed", "Agreed"], ["escalated", "Escalated"], ["lost", "Lost / re-tender"],
];
const STAGE_LABEL = Object.fromEntries(STAGES);
const ZONE = {
  below_stretch: ["good", "Better than your opening ask"], at_target: ["good", "At or below target"],
  near_target: ["warn", "Close to target"], push: ["warn", "Push"], walk: ["bad", "Above walk-away"],
};
const board = { data: null, wired: false };
const shortDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const stamp = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function stagePill(progress) {
  const k = (progress && progress.stage) || "not_started";
  return el("span", { class: `stage s-${k}`, text: STAGE_LABEL[k] || k });
}

function offerChip(check) {
  if (!check) return null;
  const [tone, label] = ZONE[check.zone] || ["warn", check.zone];
  return el("span", { class: `zone ${tone}`, title: check.move, text: `${label} · ${check.vs_target_pct > 0 ? "+" : ""}${pct(check.vs_target_pct)} vs target` });
}

async function saveProgress(key, changes) {
  const res = await send("/api/renewals/progress", "PATCH", { key, ...changes });
  if (board.data) {
    const r = board.data.renewals.find((x) => x.key === key);
    if (r) { r.progress = res.progress; if (res.offer_check) r.offer_check = res.offer_check; }
  }
  return res;
}

// ---------- drawer ----------
function closeDrawer() {
  $("drawer").hidden = true;
  $("drawer-scrim").hidden = true;
  if (closeDrawer.back) { closeDrawer.back.focus(); closeDrawer.back = null; }
}

async function openRenewal(r) {
  wireBoard();
  closeDrawer.back = document.activeElement;
  const body = clear($("drawer-body"));
  $("drawer").hidden = false;
  $("drawer-scrim").hidden = false;
  const p = r.progress || { stage: "not_started" };
  const cu = r.clinical_units_per_quoted_unit || 1;
  const pack = r.quoted_unit === "each" ? "unit" : r.quoted_unit;
  const perPack = (v) => (v == null ? "" : String(Math.round(v * cu)));

  const stage = el("select", { id: "d-stage" }, STAGES.map(([k, label]) => el("option", { value: k, text: label })));
  stage.value = p.stage || "not_started";
  const owner = el("input", { id: "d-owner", type: "email", value: p.owner || null, placeholder: "email of who's leading this" });
  const nextStep = el("input", { id: "d-next", value: p.next_step || null, placeholder: "e.g. Send counter-offer" });
  const due = el("input", { id: "d-due", type: "date", value: p.due_date || null });
  const offer = el("input", { id: "d-offer", inputmode: "numeric", value: perPack(p.latest_offer) || null, placeholder: `Rp per ${pack}` });
  const agreed = el("input", { id: "d-agreed", inputmode: "numeric", value: perPack(p.agreed_price) || null, placeholder: `Rp per ${pack}` });
  const notes = el("textarea", { id: "d-notes", rows: 3, placeholder: "What happened, what was said" });
  notes.value = p.notes || "";
  const offerHint = el("span", { class: "hint" });
  const agreedHint = el("span", { class: "hint" });
  const toUnit = (input) => { const v = parseFloat(String(input.value).replace(/[^\d.,]/g, "").replace(/\./g, "").replace(",", ".")); return Number.isFinite(v) && v > 0 ? v / cu : null; };
  const sanity = el("p", { class: "sanity", role: "status" });
  const hint = (input, out) => {
    const u = toUnit(input);
    out.textContent = u && cu > 1 ? `= ${unit(u)} per unit` : "";
    // An offer far from anything on record is usually a typing or pack-size slip.
    const far = u && (u < r.fallback_price * 0.5 || u > r.proposed_walk_away * 2);
    sanity.textContent = far ? `That's far from any price on record (${unit(r.fallback_price)}–${unit(r.proposed_walk_away)} per unit). Check it's per ${pack}.` : "";
  };
  offer.addEventListener("input", () => hint(offer, offerHint)); hint(offer, offerHint);
  agreed.addEventListener("input", () => hint(agreed, agreedHint)); hint(agreed, agreedHint);
  const check = el("div", { class: "offercheck" });
  const showCheck = (c) => {
    clear(check);
    if (!c) return;
    const [tone] = ZONE[c.zone] || ["warn"];
    check.className = `offercheck ${tone}`;
    check.append(offerChip(c), el("p", { text: c.move }));
  };
  showCheck(r.offer_check);
  const timeline = el("ol", { class: "timeline" });
  const docs = el("div");
  const status = el("p", { class: "muted small", role: "status" });

  const form = el("form", { class: "progress-form" },
    el("div", { class: "row2" },
      el("label", { for: "d-stage" }, "Stage", stage),
      el("label", { for: "d-due" }, "Next step due", due)),
    el("label", { for: "d-owner" }, "Owner",
      el("div", { class: "inline" }, owner, el("button", { type: "button", class: "act ghost", text: "Assign to me",
        onclick: () => { owner.value = shell.user.email; } }))),
    el("label", { for: "d-next" }, "Next step", nextStep),
    el("div", { class: "row2" },
      el("label", { for: "d-offer" }, `Vendor's latest offer (per ${pack})`, offer, offerHint),
      el("label", { for: "d-agreed" }, `Agreed price (per ${pack})`, agreed, agreedHint)),
    sanity,
    check,
    el("label", { for: "d-notes" }, "Notes", notes),
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Save progress" }), status));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    status.textContent = "Saving…";
    try {
      const res = await saveProgress(r.key, {
        stage: stage.value, owner: owner.value.trim() || null, next_step: nextStep.value, due_date: due.value || null,
        notes: notes.value, latest_offer: toUnit(offer), agreed_price: toUnit(agreed),
      });
      r.progress = res.progress;
      r.offer_check = res.offer_check || null;
      showCheck(res.offer_check);
      renderTimeline(timeline, res.events);
      status.textContent = "Saved";
      toast("Progress saved");
      if (!$("renewals").hidden) renderBoard();
    } catch (ex) { status.textContent = ex.message; }
  });

  const q = { sku: r.sku, vendor: r.vendor, skuName: r.sku_name };
  body.append(
    el("p", { class: "eyebrow", text: `${r.vendor} · ${r.hospital}` }),
    el("h2", { id: "drawer-title", text: r.sku_name }),
    el("p", { class: "muted" }, el("span", { class: `dot ${r.days_left <= 45 ? "" : "calm"}`, "aria-hidden": "true" }),
      `Contract ends ${fmtDate(r.contract_end)} · ${r.days_left} days left · ${money(r.contract_value)} a year`),
    el("div", { class: "pricegrid" },
      ...[["Paying now", r.contract_price_per_clinical_unit], ["Stretch", r.opening_ask], ["Target", r.target_price],
        ["Fallback", r.fallback_price], ["Walk-away*", r.proposed_walk_away]].map(([l, v]) =>
        el("div", { class: l === "Target" ? "t" : null }, el("span", { text: l }), el("b", { text: unit(v) }), cu > 1 ? el("small", { text: `${unit(v * cu)} / ${pack}` }) : null))),
    el("p", { class: "muted small", text: "*Walk-away is a proposal until signed off." }),
    el("div", { class: "actions" },
      act("Negotiation plan", () => { closeDrawer(); location.hash = "#chat"; setTimeout(() => { me(`Negotiate the ${r.sku_name} renewal with ${r.vendor}`); negotiate(q); }, 60); }),
      act("Price evidence", () => { closeDrawer(); location.hash = "#chat"; setTimeout(() => { me(`Details: ${r.sku_name} from ${r.vendor}`); details(q); }, 60); }, "secondary")),
    el("h3", { text: "Progress" }), form,
    el("h3", { text: "Documents" }), docs,
    el("h3", { text: "Timeline" }), timeline);
  $("drawer-close").focus();
  renderDocs(docs, r);
  try { renderTimeline(timeline, await api(`/api/renewals/events?key=${encodeURIComponent(r.key)}`)); } catch (_) { /* optional */ }
}

function renderTimeline(list, events) {
  clear(list);
  if (!events || !events.length) { list.append(el("li", { class: "muted", text: "No updates yet." })); return; }
  const FIELD = { stage: "stage", owner: "owner", next_step: "next step", due_date: "due date", notes: "notes",
    latest_offer: "latest offer", agreed_price: "agreed price" };
  events.forEach((e) => {
    let what;
    if (e.field === "stage") what = `moved it from ${STAGE_LABEL[e.old] || "Not started"} to ${STAGE_LABEL[e.new] || e.new}`;
    else if (e.field === "latest_offer" || e.field === "agreed_price") what = `set the ${FIELD[e.field]} to ${e.new ? unit(parseFloat(e.new)) + " per unit" : "blank"}`;
    else if (e.field === "notes") what = "updated the notes";
    else what = `set the ${FIELD[e.field] || e.field} to ${e.new || "blank"}`;
    list.append(el("li", {}, el("b", { text: e.user_email.split("@")[0] }), ` ${what}`,
      el("span", { class: "muted small", text: ` · ${stamp.format(new Date(e.at))}` })));
  });
}

async function renderDocs(box, r) {
  clear(box);
  let docs = [];
  try { docs = await api(`/api/documents?renewal=${encodeURIComponent(r.key)}`); } catch (_) { /* optional */ }
  let vendorDocs = [];
  try { vendorDocs = (await api(`/api/documents?vendor=${encodeURIComponent(r.vendor)}`)).filter((d) => !docs.some((x) => x.id === d.id)); } catch (_) { /* optional */ }
  const all = [...docs, ...vendorDocs];
  box.append(all.length ? docList(all) : el("p", { class: "muted small", text: "No documents linked yet." }));
  if (shell.user.role === "admin") {
    const file = el("input", { type: "file", accept: ".pdf,.docx,.xlsx,.csv,.png,.jpg,.jpeg", "aria-label": "Document to attach" });
    const note = el("input", { placeholder: "Note, e.g. 2025 contract", "aria-label": "Note" });
    const msg = el("span", { class: "muted small" });
    box.append(el("form", { class: "inline attach", onsubmit: async (e) => {
      e.preventDefault();
      if (!file.files.length) { file.focus(); return; }
      const fd = new FormData();
      fd.append("file", file.files[0]); fd.append("vendor", r.vendor); fd.append("sku", r.sku);
      fd.append("renewal_key", r.key); fd.append("note", note.value);
      msg.textContent = "Uploading…";
      try { await upload("/api/admin/documents", fd); toast("Document attached"); renderDocs(box, r); }
      catch (ex) { msg.textContent = ex.message; }
    } }, file, note, el("button", { type: "submit", class: "act secondary", text: "Attach" }), msg));
  }
}

// ---------- MOU board ----------
function wireBoard() {
  if (board.wired) return;
  board.wired = true;
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
  $("f-search").addEventListener("input", renderBoard);
  $("f-category").addEventListener("change", renderBoard);
  $("f-window").addEventListener("change", loadBoard);
  $("f-mine").addEventListener("change", renderBoard);
}

async function boardEnter(rest) {
  wireBoard();
  $("f-mine").checked = rest && rest[0] === "mine";
  await loadBoard();
}

async function loadBoard() {
  const days = $("f-window").value;
  $("board").classList.add("loading");
  try { board.data = await api(`/api/nego/mou-board${days ? `?max_days=${days}` : ""}`); }
  catch (e) { clear($("board")).append(el("p", { class: "error", text: e.message })); return; }
  finally { $("board").classList.remove("loading"); }
  const cats = [...new Set(board.data.mous.map((m) => m.category).filter(Boolean))].sort();
  const sel = $("f-category"), keep = sel.value;
  clear(sel).append(el("option", { value: "", text: "All categories" }), ...cats.map((c) => el("option", { value: c, text: c })));
  sel.value = cats.includes(keep) ? keep : "";
  renderKpis();
  renderBoard();
}

function renderKpis() {
  const k = board.data.kpis, by = k.by_stage;
  const inFlight = by.preparing + by.with_principal + by.negotiating;
  clear($("board-kpis")).append(
    tile("MOUs in this window", num(k.mous), `${num(by.renewed)} already renewed`),
    tile("Not started", num(by.not_started), k.alerts ? `${num(k.alerts)} within ${board.data.alert_months} months: start now` : "none urgent"),
    tile("In negotiation", num(inFlight), k.overdue ? `${num(k.overdue)} waiting on an overdue principal` : "nothing overdue"),
    tile("Saving at target", money(k.saving_at_target), "engine targets on open MOUs' SKUs"),
  );
  if (k.alerts) $("board-kpis").children[1].classList.add("alert");
  $("board-kpis").lastChild.classList.add("good");
}

function mouMatches(m) {
  const text = $("f-search").value.trim().toLowerCase(), cat = $("f-category").value;
  const mine = $("f-mine").checked && shell.user ? shell.user.email.toLowerCase() : "";
  return (!text || `${m.name} ${m.distributor || ""} ${m.contact_name || ""}`.toLowerCase().includes(text))
    && (!cat || m.category === cat)
    && (!mine || ((m.cycle && m.cycle.created_by) || "").toLowerCase() === mine);
}

function renderBoard() {
  if (!board.data) return;
  const rows = board.data.mous.filter(mouMatches);
  const cols = clear($("board"));
  board.data.stages.forEach(({ key, label }) => {
    const inCol = rows.filter((m) => m.stage === key);
    const list = el("ul", { class: "cards" });
    inCol.forEach((m) => list.append(mouCard(m)));
    if (!inCol.length) list.append(el("li", { class: "empty-col", text: "None" }));
    cols.append(el("section", { class: `col s-${key}`, "aria-label": label },
      el("header", {}, el("b", { text: label }), el("span", { class: "count", text: String(inCol.length) })), list));
  });
}

function daysText(d) {
  if (d == null) return "no end date";
  return d < 0 ? `ended ${num(-d)}d ago` : `${num(d)}d left`;
}

function mouCard(m) {
  const c = m.cycle, t = m.price_targets || {};
  const open = c && c.status === "open";
  const card = el("li", { class: `bcard${m.mou_alert ? " urgent" : ""}`, tabindex: 0 },
    el("div", { class: "b-top" },
      el("span", { class: `days${m.mou_days_left != null && m.mou_days_left <= 90 ? " soon" : ""}` },
        el("span", { class: "dot", "aria-hidden": "true" }),
        m.mou_end ? `${fmtDate(m.mou_end)} · ${daysText(m.mou_days_left)}` : "MOU end not set")),
    el("b", { class: "b-name", text: m.name }),
    el("span", { class: "meta", text: [m.category, m.distributor && m.distributor !== m.name ? `via ${m.distributor}` : ""].filter(Boolean).join(" · ") }),
    open ? el("span", { class: "next", text: `Step: ${c.step_label}${m.overdue_days ? ` · overdue ${m.overdue_days}d` : c.step_due ? ` · due ${fmtDate(c.step_due)}` : ""}` }) : null,
    m.stage === "renewed" ? el("span", { class: "zone good", text: `New MOU to ${fmtDate(m.new_mou_end)}` }) : null,
    m.mou_alert ? el("span", { class: "zone warn", text: "Start the negotiation" }) : null,
    t.skus ? el("div", { class: "b-figs" },
      el("span", {}, el("b", { text: num(t.skus) }), " SKUs priced"),
      t.saving_at_target > 0 ? el("span", { class: "save-sm", text: `saves ${money(t.saving_at_target)}` }) : null) : null);
  card.addEventListener("click", () => openMou(m));
  card.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === card) openMou(m); });
  return card;
}

function openMou(m) {
  wireBoard();
  closeDrawer.back = document.activeElement;
  const body = clear($("drawer-body"));
  $("drawer").hidden = false;
  $("drawer-scrim").hidden = false;
  const c = m.cycle, t = m.price_targets || { items: [] };
  const isAdmin = shell.user && shell.user.role === "admin";
  const open = c && c.status === "open";
  const steps = board.data.steps.map((s) => s.key);
  const at = c ? steps.indexOf(c.current_step) : -1;
  const goNego = () => { closeDrawer(); location.hash = c ? `#nego/${c.id}` : "#nego"; };
  body.append(
    el("p", { class: "eyebrow", text: `MOU · ${m.category || "principal"}` }),
    el("h2", { id: "drawer-title", text: m.name }),
    el("p", { class: "muted", text: [m.distributor && `Distributor: ${m.distributor}`, m.binding === "Disc" ? "Discount-bound" : "Nett-price bound"].filter(Boolean).join(" · ") }),
    el("div", { class: "pricegrid" },
      el("div", {}, el("span", { text: "MOU start" }), el("b", { text: m.mou_start ? fmtDate(m.mou_start) : "—" })),
      el("div", { class: m.mou_alert ? "t" : null }, el("span", { text: "MOU end" }), el("b", { text: m.mou_end ? fmtDate(m.mou_end) : "—" }), el("small", { text: daysText(m.mou_days_left) })),
      el("div", {}, el("span", { text: "Stage" }), el("b", { text: (board.data.stages.find((s) => s.key === m.stage) || {}).label || m.stage })),
      m.new_mou_end ? el("div", { class: "t" }, el("span", { text: "New MOU end" }), el("b", { text: fmtDate(m.new_mou_end) })) : null),
    el("h3", { text: "Negotiation" }),
    c ? el("div", {},
      el("p", {}, `${open ? "Open" : "Closed"} · step ${at + 1} of ${steps.length}: `, el("b", { text: c.step_label }),
        c.items ? ` · ${num(c.items)} items` : "", c.open_anomalies ? ` · ${num(c.open_anomalies)} open findings` : ""),
      el("div", { class: "mini-steps", "aria-hidden": "true" }, steps.slice(0, -1).map((k, i) => el("i", { class: i < at ? "done" : i === at ? "now" : "" }))),
      el("p", { class: "small muted", text: `New contract ${fmtDate(c.contract_start)} – ${fmtDate(c.contract_end)}` }))
      : el("p", { class: "muted", text: isAdmin ? "No negotiation yet. Start one from Negotiations: it builds the item list from POs, the formulary and this MOU." : "No negotiation yet. An admin starts it from Negotiations." }),
    el("div", { class: "actions" }, act(c ? "Open the negotiation" : "Go to Negotiations", goNego)),
    el("h3", { text: "Contact" }),
    el("p", { class: "small" }, m.contact_name || "—", el("br"), el("span", { class: "muted", text: [m.contact_email, m.contact_phone].filter(Boolean).join(" · ") || "No email or WhatsApp yet: links can't be sent automatically." })),
    el("h3", { text: "Price targets from the engine" }),
    t.items.length ? el("div", {},
      el("p", { class: "small muted", text: `${num(t.skus)} SKUs in the price data from this principal or its distributor. Open one for its plan.` }),
      el("div", { class: "table-wrap" }, (() => {
        const tbl = el("table");
        table(tbl, [
          { label: "SKU", get: (r) => el("div", {}, el("b", { text: r.sku_name }), el("div", { class: "small muted", text: r.hospital })) },
          { label: "Paying", num: true, get: (r) => unit(r.contract_price_per_clinical_unit) },
          { label: "Target", num: true, get: (r) => el("b", { text: unit(r.target_price) }) },
          { label: "Saving / yr", num: true, get: (r) => (r.saving_at_target > 0 ? money(r.saving_at_target) : "—") },
        ], t.items, (r) => { const q = { sku: r.sku, vendor: r.vendor, skuName: r.sku_name }; closeDrawer(); location.hash = "#chat"; setTimeout(() => { me(`Negotiate ${r.sku_name} with ${r.vendor}`); negotiate(q); }, 60); });
        return tbl;
      })()))
      : el("p", { class: "small muted", text: "No SKUs in the price data match this principal or distributor name yet. Load the real price history in Admin → Price data." }));
  $("drawer-close").focus();
}
