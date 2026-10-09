"use strict";
// Admin > Master data. One place to see every set of master data, review what is new, and
// label items (generic name, group, tags) so the same medicine from different brands can be
// compared. The server decides who may do this (/api/admin only); this page is the interface.

const master = { filter: "new", q: "", items: [], total: 0, counts: {}, picked: new Set(), loading: false };

const masterReadOnly = () => !shell.user || shell.user.role !== "admin";

async function adminMaster(body) {
  const sets = el("div", { class: "mset" });
  const tools = el("div", { class: "mtools" });
  const bulk = el("div", { class: "mbulk", hidden: "" });
  const list = el("div", { class: "table-wrap" });
  const more = el("div", { class: "actions" });
  const importBox = el("div");
  body.append(
    card("Master data", masterReadOnly() ? "Everything the engine and the negotiations are built on. You can read it; an administrator changes it." : "Everything the engine and the negotiations are built on. New data shows up here for review.", sets),
    card("Items", masterReadOnly() ? "One row per ERP code, with the generic name and group that the Brands comparison uses." : "One row per ERP code. Give each item a generic name so different brands of the same medicine can be compared. Items from new negotiations arrive as “New”.",
      tools, bulk, list, more),
    importBox);
  master.picked = new Set();
  master.filter = "new";
  master.q = "";

  const search = el("input", { type: "search", placeholder: "Search code, name, brand, generic, group, tag", "aria-label": "Search items", class: "msearch" });
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { master.q = search.value.trim(); masterLoadItems(true); }, 250); });
  const file = el("input", { type: "file", accept: ".csv,.xlsx", class: "sr-only", id: "mfile" });
  file.addEventListener("change", () => { if (file.files.length) checkImport(importBox, file); });
  put(tools, el("div", { class: "chips", id: "mchips" }), search,
    el("a", { class: "act secondary", href: "/api/admin/master/items.xlsx", text: "Export Excel" }),
    masterReadOnly() ? null : el("label", { class: "act secondary", for: "mfile", text: "Import Excel or CSV" }), masterReadOnly() ? null : file);

  master.draw = () => drawItems(list, more, bulk);
  master.refreshSets = () => drawSets(sets);
  await drawSets(sets);
  await masterLoadItems(true);
}

async function drawSets(box) {
  let d;
  try { d = await api("/api/admin/master/overview"); } catch (e) { put(clear(box), el("p", { class: "error", text: e.message })); return; }
  master.counts = d.counts;
  put(clear(box), d.sets.map((s) => el("div", { class: `mcard ${s.attention ? "attn" : ""}` },
    el("span", { class: "lbl", text: s.label }), el("b", { text: num(s.count) }),
    s.note ? el("span", { class: "small muted", text: s.note }) : null,
    s.attention ? el("span", { class: "badge", text: `${num(s.attention)} ${s.attention_label}` }) : null,
    s.updated ? el("span", { class: "small muted", text: `Updated ${new Date(s.updated).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}` }) : null)));
  drawChips();
}

function drawChips() {
  const box = $("mchips");
  if (!box) return;
  const c = master.counts;
  put(clear(box), [["new", "Needs review", c.new], ["unlabelled", "No generic name", c.unlabelled], ["all", "All items", c.all]].map(([k, label, n]) =>
    el("button", { type: "button", class: `chipbtn ${master.filter === k ? "on" : ""}`, "aria-pressed": String(master.filter === k), onclick: () => { master.filter = k; masterLoadItems(true); drawChips(); } },
      label, " ", el("b", { text: num(n ?? 0) }))));
}

async function masterLoadItems(reset) {
  if (reset) master.items = [];
  const qs = new URLSearchParams({ filter: master.filter, q: master.q, offset: master.items.length, limit: 50 });
  let d;
  try { d = await api(`/api/admin/master/items?${qs}`); } catch (e) { toast(e.message); return; }
  master.items = master.items.concat(d.items);
  master.total = d.total;
  master.counts = d.counts;
  drawChips();
  master.draw();
}

function drawItems(list, more, bulk) {
  clear(list); clear(more);
  if (!master.items.length) {
    put(list, el("p", { class: "muted pad", text: master.filter === "new" ? "Nothing waiting for review." : "No items match." }));
    drawBulk(bulk);
    return;
  }
  const ro = masterReadOnly();
  const all = el("input", { type: "checkbox", "aria-label": "Select all shown" });
  all.checked = master.items.every((i) => master.picked.has(i.erp_code));
  all.addEventListener("change", () => { master.items.forEach((i) => (all.checked ? master.picked.add(i.erp_code) : master.picked.delete(i.erp_code))); master.draw(); });
  const tbl = el("table", { class: "mtable" }, el("thead", {}, el("tr", {}, el("th", {}, ro ? null : all),
    ["ERP code", "Item", "Brand", "Generic name", "Group", "Tags", ""].map((h) => el("th", { text: h })))));
  const tb = el("tbody");
  for (const it of master.items) tb.append(itemRow(it));
  tbl.append(tb);
  put(list, tbl);
  if (master.items.length < master.total) put(more, act(`Show more (${num(master.total - master.items.length)} left)`, () => masterLoadItems(false), "secondary"));
  drawBulk(bulk);
}

function itemRow(it) {
  const pick = el("input", { type: "checkbox", "aria-label": `Select ${it.erp_code}` });
  pick.checked = master.picked.has(it.erp_code);
  const ro = masterReadOnly();
  pick.addEventListener("change", () => { pick.checked ? master.picked.add(it.erp_code) : master.picked.delete(it.erp_code); drawBulk(document.querySelector(".mbulk")); });
  const status = el("span", { class: `badge ${it.status === "new" ? "" : "ok"}`, text: it.status === "new" ? "New" : "Reviewed" });
  const saved = el("span", { class: "small muted msaved" });
  const input = (field, label) => {
    if (ro) return el("span", { text: it[field] || "—" });
    const i = el("input", { value: it[field] || null, "aria-label": `${label} for ${it.erp_code}`, maxlength: field === "tags" ? 300 : 120 });
    i.addEventListener("change", async () => {
      saved.textContent = "Saving…";
      try {
        const r = await send(`/api/admin/master/items/${encodeURIComponent(it.erp_code)}`, "PATCH", { changes: { [field]: i.value.trim() } });
        Object.assign(it, r.item);
        i.value = it[field] || "";
        status.className = `badge ${it.status === "new" ? "" : "ok"}`; status.textContent = it.status === "new" ? "New" : "Reviewed";
        saved.textContent = "Saved ✓";
        master.refreshSets();
      } catch (e) { saved.textContent = e.message; }
    });
    return i;
  };
  return el("tr", {}, el("td", {}, ro ? null : pick), el("td", { class: "mono", text: it.erp_code }),
    el("td", {}, el("b", { text: it.item_name || "—" }), it.catalog_no ? el("div", { class: "small muted", text: `REF ${it.catalog_no}` }) : null),
    el("td", { text: it.brand || "—" }),
    el("td", {}, input("generic_name", "Generic name")), el("td", {}, input("group_key", "Group")), el("td", {}, input("tags", "Tags")),
    el("td", {}, status, ro ? null : saved));
}

function drawBulk(box) {
  if (!box || masterReadOnly()) return;
  clear(box);
  const n = master.picked.size;
  box.hidden = !n;
  if (!n) return;
  const group = el("input", { placeholder: "Group, e.g. Paracetamol 500 mg", "aria-label": "Group for selected" });
  const generic = el("input", { placeholder: "Generic name", "aria-label": "Generic name for selected" });
  const tag = el("input", { placeholder: "Add tag", "aria-label": "Tag to add to selected" });
  const run = (changes, done) => async () => {
    try {
      const r = await send("/api/admin/master/items/bulk", "POST", { erp_codes: [...master.picked], ...changes });
      toast(`${num(r.updated)} items updated`);
      done && done();
      master.picked = new Set();
      await master.refreshSets();
      await masterLoadItems(true);
    } catch (e) { toast(e.message); }
  };
  put(box, el("b", { text: `${num(n)} selected` }),
    el("span", { class: "mrow" }, generic, act("Set generic name", () => generic.value.trim() ? run({ generic_name: generic.value.trim() })() : generic.focus(), "secondary")),
    el("span", { class: "mrow" }, group, act("Set group", () => group.value.trim() ? run({ group_key: group.value.trim() })() : group.focus(), "secondary")),
    el("span", { class: "mrow" }, tag, act("Add tag", () => tag.value.trim() ? run({ tags_add: tag.value.trim() })() : tag.focus(), "secondary")),
    act("Mark reviewed", run({ status: "reviewed" })), act("Clear selection", () => { master.picked = new Set(); master.draw(); }, "ghost"));
}

async function checkImport(box, file) {
  const fd = new FormData();
  fd.append("file", file.files[0]);
  fd.append("apply", "0");
  put(clear(box), card("Import", null, el("p", { class: "muted", text: "Checking…" })));
  let r;
  try { r = await upload("/api/admin/master/items/import", fd); } catch (e) { put(clear(box), card("Import", null, el("p", { class: "error", text: e.message }))); return; }
  const map = Object.entries(r.mapping).map(([f, h]) => `${h} → ${f.replace("po_unit_text", "unit").replace("_", " ")}`).join(" · ");
  put(clear(box), card("Import: check before applying", `${file.files[0].name}. Nothing changes until you apply. Blank cells never erase a label.`,
    el("p", { class: "small", text: `Columns read: ${map}` }),
    el("div", { class: "facts" }, [["Rows read", r.rows_read], ["New items", r.new], ["Changed", r.changed], ["Unchanged", r.unchanged], ["Skipped (no code or duplicate)", r.skipped]]
      .map(([l, v]) => el("div", {}, el("span", { text: l }), el("b", { text: num(v) })))),
    r.sample.length ? el("details", {}, el("summary", { text: "Examples" }), el("ul", { class: "small" }, r.sample.map((s) => el("li", {
      text: s.kind === "new" ? `New: ${s.erp_code} ${s.item_name || ""}` : `${s.erp_code}: ${Object.entries(s.fields).map(([k, v]) => `${k.replace("_", " ")} ${v.old || "—"} → ${v.new}`).join("; ")}` })))) : null,
    el("div", { class: "actions" },
      r.new + r.changed ? act(`Apply (${num(r.new + r.changed)} items)`, async () => {
        const f2 = new FormData(); f2.append("file", file.files[0]); f2.append("apply", "1");
        try { const done = await upload("/api/admin/master/items/import", f2); toast(`Imported: ${num(done.new)} new, ${num(done.changed)} changed`); clear(box); file.value = ""; master.refreshSets(); masterLoadItems(true); }
        catch (e) { toast(e.message); }
      }) : el("span", { class: "muted small", text: "Nothing to apply." }),
      act("Cancel", () => { clear(box); file.value = ""; }, "ghost"))));
}
