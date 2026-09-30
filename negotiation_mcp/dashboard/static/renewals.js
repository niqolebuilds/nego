"use strict";
// Renewals: a board with one column per stage, and a drawer to update one renewal
// (stage, owner, next step, offers, notes) with its timeline and documents.
// Every signed-in user can update progress; each change is recorded with who and when.

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

// ---------- board ----------
function wireBoard() {
  if (board.wired) return;
  board.wired = true;
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
  ["f-hospital", "f-vendor", "f-search"].forEach((id) => $(id).addEventListener("input", renderBoard));
  $("f-window").addEventListener("change", loadBoard);
  $("f-mine").addEventListener("change", loadBoard);
}

function fillFilters() {
  const c = shell.catalog;
  if (!c || $("f-hospital").options.length > 1) return;
  c.hospitals.forEach((h) => $("f-hospital").append(el("option", { value: h, text: h })));
  c.vendors.forEach((v) => $("f-vendor").append(el("option", { value: v, text: v })));
}

async function boardEnter(rest) {
  wireBoard();
  fillFilters();
  $("f-mine").checked = rest && rest[0] === "mine";
  await loadBoard();
}

async function loadBoard() {
  const [lo, hi] = $("f-window").value.split("-");
  const mine = $("f-mine").checked ? "&mine=1" : "";
  $("board").classList.add("loading");
  try { board.data = await api(`/api/pipeline?min_days=${lo}&max_days=${hi}${mine}`); }
  catch (e) { clear($("board")).append(el("p", { class: "error", text: e.message })); return; }
  finally { $("board").classList.remove("loading"); }
  renderKpis();
  renderBoard();
}

function renderKpis() {
  const k = board.data.kpis;
  clear($("board-kpis")).append(
    tile("Open renewals", num(k.open), `${num(board.data.renewals.length)} in this window`),
    tile("Pipeline value", money(k.pipeline_value), "contract value still open"),
    tile("Saving at target", money(k.saving_at_target), "if every open target is hit"),
    tile("Saving realised", money(k.realised_saving), `${num(k.agreed)} agreed`),
  );
  $("board-kpis").lastChild.classList.add("good");
}

function renderBoard() {
  if (!board.data) return;
  const h = $("f-hospital").value, v = $("f-vendor").value, text = $("f-search").value.trim().toLowerCase();
  const rows = board.data.renewals.filter((r) => (!h || r.hospital === h) && (!v || r.vendor === v)
    && (!text || `${r.sku_name} ${r.vendor} ${r.hospital}`.toLowerCase().includes(text)));
  const cols = clear($("board"));
  STAGES.forEach(([key, label]) => {
    const inCol = rows.filter((r) => ((r.progress && r.progress.stage) || "not_started") === key);
    const list = el("ul", { class: "cards", "data-stage": key });
    inCol.forEach((r) => list.append(boardCard(r)));
    if (!inCol.length) list.append(el("li", { class: "empty-col", text: "Drop a card here" }));
    const col = el("section", { class: `col s-${key}`, "aria-label": label },
      el("header", {}, el("b", { text: label }), el("span", { class: "count", text: String(inCol.length) })), list);
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("over"); });
    col.addEventListener("dragleave", () => col.classList.remove("over"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault();
      col.classList.remove("over");
      const k = e.dataTransfer.getData("text/plain");
      await moveCard(k, key);
    });
    cols.append(col);
  });
}

async function moveCard(key, stage) {
  const r = board.data.renewals.find((x) => x.key === key);
  if (!r || (r.progress && r.progress.stage) === stage) return;
  try {
    await saveProgress(key, { stage });
    toast(`${r.sku_name} → ${STAGE_LABEL[stage]}`);
    if (stage === "agreed" && !(r.progress && r.progress.agreed_price)) openRenewal(r);
    await loadBoard();
  } catch (ex) { toast(ex.message); }
}

function boardCard(r) {
  const p = r.progress || {};
  const move = el("select", { class: "move", "aria-label": `Stage for ${r.sku_name}` },
    STAGES.map(([k, label]) => el("option", { value: k, text: label })));
  move.value = p.stage || "not_started";
  move.addEventListener("change", () => moveCard(r.key, move.value));
  move.addEventListener("click", (e) => e.stopPropagation());
  const card = el("li", { class: "bcard", draggable: "true", tabindex: 0, "data-key": r.key },
    el("div", { class: "b-top" },
      el("span", { class: `days${r.days_left <= 45 ? " soon" : ""}` }, el("span", { class: "dot", "aria-hidden": "true" }),
        `${shortDate.format(new Date(r.contract_end + "T00:00:00"))} · ${r.days_left}d`),
      p.owner ? el("span", { class: "owner", title: p.owner, text: initials(p.owner.split("@")[0].replace(/[._-]/g, " ")) }) : null),
    el("b", { class: "b-name", text: r.sku_name }),
    el("span", { class: "meta", text: `${r.vendor} · ${r.hospital}` }),
    el("div", { class: "b-figs" },
      el("span", {}, "Target ", el("b", { text: unit(r.target_price) })),
      r.saving_at_target > 0 ? el("span", { class: "save-sm", text: `saves ${money(r.saving_at_target)}` }) : null),
    r.offer_check ? offerChip(r.offer_check) : null,
    r.realised_saving ? el("span", { class: "zone good", text: `Realised ${money(r.realised_saving)}` }) : null,
    p.next_step ? el("span", { class: "next", text: `Next: ${p.next_step}${p.due_date ? ` · ${shortDate.format(new Date(p.due_date + "T00:00:00"))}` : ""}` }) : null,
    move);
  card.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", r.key); card.classList.add("dragging"); });
  card.addEventListener("dragend", () => card.classList.remove("dragging"));
  card.addEventListener("click", () => openRenewal(r));
  card.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === card) openRenewal(r); });
  return card;
}
