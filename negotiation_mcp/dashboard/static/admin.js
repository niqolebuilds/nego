"use strict";
// Admin console. The server rejects every /api/admin call from non-admins; this page
// is only the interface. Sections: price data (upload, preview, apply, versions),
// documents, engine settings, users, activity.

const adminState = { sub: "data", staged: null };

function adminEnter(sub) {
  const nego = shell.user && shell.user.role === "negotiator";  // read-only master data, nothing else
  if (nego) sub = "master";
  $("admin-subtabs").hidden = nego;
  adminState.sub = ["data", "master", "documents", "settings", "users", "activity"].includes(sub) ? sub : "data";
  document.querySelectorAll("#admin-subtabs a").forEach((a) => a.classList.toggle("on", a.dataset.sub === adminState.sub));
  const body = clear($("admin-body"));
  return ({ data: adminData, master: adminMaster, documents: adminDocs, settings: adminSettings, users: adminUsers, activity: adminActivity })[adminState.sub](body);
}

function card(title, sub, ...kids) {
  return el("section", { class: "card admin-card" }, el("h2", { text: title }), sub ? el("p", { class: "sub", text: sub }) : null, ...kids);
}

function dropZone(input, label) {
  const zone = el("label", { class: "dropzone" }, input, el("b", { text: label }), el("span", { class: "muted small", text: "Drag a file here or click to choose" }));
  const name = el("span", { class: "filename" });
  zone.append(name);
  input.addEventListener("change", () => { name.textContent = input.files.length ? input.files[0].name : ""; });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault(); zone.classList.remove("over");
    if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; input.dispatchEvent(new Event("change")); }
  });
  return zone;
}

// ---------- price data ----------
async function adminData(body) {
  const statusBox = el("div");
  const previewBox = el("div");
  const versionsBox = el("div");
  const historyBox = el("div");
  const kind = el("select", { id: "up-kind" },
    ["price_history", "sku_master", "price_index", "rebate_programs"].map((k) => el("option", { value: k })));
  const file = el("input", { type: "file", accept: ".csv,.xlsx", id: "up-file", class: "sr-only" });
  const msg = el("p", { class: "error", role: "alert", hidden: "" });
  const form = el("form", { class: "upload-form" },
    el("label", { for: "up-kind" }, "What are you uploading?", kind),
    dropZone(file, "Choose a CSV or Excel (.xlsx) file"),
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Check file" }),
      el("span", { class: "muted small", text: "Nothing changes until you apply it." })),
    msg);
  const templates = el("p", { class: "small" }, "Templates: ");
  body.append(
    card("Current data", "What the engine is using right now.", statusBox),
    card("Upload price lists and records", "Purchase orders, contracts, quotes, the SKU master, the price index or rebate programs. The file is checked row by row first; you see a preview and choose to add or replace.",
      form, templates, previewBox),
    card("Versions", "Every apply creates a version. Activate an older one to roll back.", versionsBox),
    card("Recent uploads", null, historyBox));

  let kinds = {};
  try { const u = await api("/api/admin/uploads"); kinds = u.kinds; renderUploads(historyBox, u.uploads); } catch (e) { historyBox.append(el("p", { class: "error", text: e.message })); }
  [...kind.options].forEach((o) => { o.textContent = kinds[o.value] || o.value; });
  Object.entries(kinds).forEach(([k, label], i) => {
    templates.append(i ? ", " : "", el("a", { href: `/api/admin/templates/${k}`, text: label.split(" (")[0] }));
  });
  renderVersions(statusBox, versionsBox);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.hidden = true;
    if (!file.files.length) { msg.textContent = "Choose a file first."; msg.hidden = false; return; }
    const fd = new FormData();
    fd.append("file", file.files[0]);
    fd.append("kind", kind.value);
    clear(previewBox).append(el("p", { class: "muted", text: "Checking…" }));
    try {
      adminState.staged = await upload("/api/admin/uploads", fd);
      renderPreview(previewBox, adminState.staged, () => adminEnter("data"));
    } catch (ex) { clear(previewBox); msg.textContent = ex.message; msg.hidden = false; }
  });
}

function renderPreview(box, up, done) {
  clear(box);
  const ok = up.status === "staged";
  const facts = [
    ["Rows", num(up.rows)],
    up.date_from ? ["Dates", `${fmtDate(up.date_from)} – ${fmtDate(up.date_to)}`] : null,
    up.vendors != null ? ["Vendors", num(up.vendors)] : null,
    up.skus != null ? ["Products", num(up.skus)] : null,
    up.hospitals != null ? ["Hospitals", num(up.hospitals)] : null,
  ].filter(Boolean);
  const sample = el("table");
  box.append(el("div", { class: `preview ${ok ? "ok" : "bad"}` },
    el("h3", { text: ok ? `Ready to apply: ${up.filename}` : `Fix these before applying: ${up.filename}` }),
    el("div", { class: "facts" }, facts.map(([l, v]) => el("div", {}, el("span", { text: l }), el("b", { text: v })))),
    up.new_vendors && up.new_vendors.length ? el("p", { class: "small", text: `New vendors: ${up.new_vendors.join(", ")}` }) : null,
    up.new_skus && up.new_skus.length ? el("p", { class: "small", text: `New products: ${up.new_skus.join(", ")}` }) : null,
    up.unknown_columns && up.unknown_columns.length ? el("p", { class: "small muted", text: `Ignored columns: ${up.unknown_columns.join(", ")}` }) : null,
    up.errors.length ? el("ul", { class: "errors" }, up.errors.map((x) => el("li", { text: x }))) : null,
    el("details", {}, el("summary", { text: "First rows" }), el("div", { class: "table-wrap" }, sample))));
  const cols = Object.keys(up.sample[0] || {}).slice(0, 9);
  table(sample, cols.map((c) => ({ label: c, get: (r) => r[c] })), up.sample);
  const mode = el("div", { class: "modes", role: "radiogroup", "aria-label": "How to apply" },
    el("label", {}, el("input", { type: "radio", name: "mode", value: "add", checked: "" }), el("b", { text: "Add" }), " to the current data (duplicates are skipped)"),
    el("label", {}, el("input", { type: "radio", name: "mode", value: "replace" }), el("b", { text: "Replace" }), " this file in the current data"));
  const out = el("p", { class: "small", role: "status" });
  if (ok) box.lastChild.append(mode);
  box.lastChild.append(el("div", { class: "actions" },
    ok ? act("Apply", async () => {
      const m = box.querySelector("input[name=mode]:checked").value;
      if (m === "replace" && !confirm("Replace this file in the current data? You can roll back from Versions.")) return;
      out.textContent = "Applying…";
      try {
        const res = await send(`/api/admin/uploads/${up.id}/apply`, "POST", { mode: m });
        toast(`Applied: ${num(res.rows)} rows now in use`);
        await loadCatalog();
        done();
      } catch (ex) { out.textContent = ex.message; }
    }) : null,
    act("Discard", async () => { try { await send(`/api/admin/uploads/${up.id}/discard`, "POST", {}); } catch (_) { /* already gone */ } done(); }, "ghost"),
    out));
}

async function renderVersions(statusBox, versionsBox) {
  let v;
  try { v = await api("/api/admin/versions"); } catch (e) { versionsBox.append(el("p", { class: "error", text: e.message })); return; }
  const s = v.status || {};
  clear(statusBox).append(...[el("div", { class: "facts" },
    ...[["Source", v.active === "sample" ? "Bundled sample" : `Version ${v.active}`],
      ["Rows", num(s.rows)], ["Products", num(s.skus)], ["Vendors", num(s.vendors)], ["Hospitals", num(s.hospitals)],
      ["Dates", s.date_from ? `${fmtDate(s.date_from)} – ${fmtDate(s.date_to)}` : "—"]].map(([l, x]) => el("div", {}, el("span", { text: l }), el("b", { text: x })))),
    s.is_sample ? el("p", { class: "small warn-text", text: "This is still sample data. Replace the price list with your real export to switch it off." }) : null,
    v.env_override ? el("p", { class: "small warn-text", text: "NEGOTIATION_DATA_DIR is set on the server, so it overrides the versions below." }) : null].filter(Boolean));
  const tbl = el("table");
  const rows = [{ id: "sample", created_at: null, user_email: "", note: "Bundled synthetic sample", rows: null, active: v.active === "sample" }, ...v.versions];
  clear(versionsBox).append(el("div", { class: "table-wrap" }, tbl));
  table(tbl, [
    { label: "Version", get: (r) => r.id },
    { label: "When", get: (r) => (r.created_at ? stamp.format(new Date(r.created_at)) : "—") },
    { label: "By", get: (r) => r.user_email || "—" },
    { label: "What", get: (r) => r.note || "" },
    { label: "Rows", num: true, get: (r) => (r.rows == null ? "—" : num(r.rows)) },
    { label: "", get: (r) => r.active ? el("span", { class: "zone good", text: "In use" })
      : el("button", { type: "button", class: "act ghost", text: "Activate", onclick: async (e) => {
        e.stopPropagation();
        if (!confirm(`Switch the engine to ${r.id === "sample" ? "the sample data" : `version ${r.id}`}?`)) return;
        try { await send(`/api/admin/versions/${encodeURIComponent(r.id)}/activate`, "POST", {}); await loadCatalog(); toast("Version activated"); adminEnter("data"); }
        catch (ex) { toast(ex.message); }
      } }) },
  ], rows);
}

function renderUploads(box, uploads) {
  if (!uploads.length) { box.append(el("p", { class: "muted small", text: "No uploads yet." })); return; }
  const tbl = el("table");
  box.append(el("div", { class: "table-wrap" }, tbl));
  table(tbl, [
    { label: "When", get: (r) => stamp.format(new Date(r.at)) },
    { label: "File", get: (r) => r.filename },
    { label: "Type", get: (r) => r.kind.replace("_", " ") },
    { label: "Rows", num: true, get: (r) => num(r.rows) },
    { label: "Status", get: (r) => el("span", { class: `zone ${r.status === "applied" ? "good" : r.status === "invalid" ? "bad" : "warn"}`, text: r.status }) },
    { label: "By", get: (r) => r.user_email },
  ], uploads);
}

// ---------- documents ----------
async function adminDocs(body) {
  const c = shell.catalog;
  const file = el("input", { type: "file", id: "doc-file", class: "sr-only", accept: ".pdf,.docx,.xlsx,.csv,.png,.jpg,.jpeg" });
  const vendor = el("select", { id: "doc-vendor" }, el("option", { value: "", text: "No vendor" }), c.vendors.map((v) => el("option", { value: v, text: v })));
  const sku = el("select", { id: "doc-sku" }, el("option", { value: "", text: "No product" }), c.skus.map((s) => el("option", { value: s.sku, text: s.sku_name })));
  const note = el("input", { id: "doc-note", placeholder: "e.g. Signed 2025 contract", maxlength: 300 });
  const msg = el("p", { class: "small", role: "status" });
  const listBox = el("div");
  const search = el("input", { placeholder: "Search documents", "aria-label": "Search documents" });
  const form = el("form", { class: "upload-form" },
    dropZone(file, "Choose a document (PDF, Word, Excel, CSV or image, up to 20 MB)"),
    el("div", { class: "row3" },
      el("label", { for: "doc-vendor" }, "Vendor", vendor), el("label", { for: "doc-sku" }, "Product", sku),
      el("label", { for: "doc-note" }, "Note", note)),
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Upload" }), msg));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!file.files.length) { msg.textContent = "Choose a file first."; return; }
    const fd = new FormData();
    fd.append("file", file.files[0]); fd.append("vendor", vendor.value); fd.append("sku", sku.value); fd.append("note", note.value);
    msg.textContent = "Uploading…";
    try { await upload("/api/admin/documents", fd); toast("Document uploaded"); adminEnter("documents"); }
    catch (ex) { msg.textContent = ex.message; }
  });
  body.append(
    card("Upload a document", "Contracts, quotes, price letters. Link it to a vendor or product so negotiators find it in the renewal and vendor views. Everyone signed in can open documents.", form),
    card("Documents", null, search, listBox));
  const load = async () => {
    let docs = [];
    try { docs = await api(`/api/documents${search.value ? `?text=${encodeURIComponent(search.value)}` : ""}`); } catch (e) { clear(listBox).append(el("p", { class: "error", text: e.message })); return; }
    const tbl = el("table");
    clear(listBox).append(docs.length ? el("div", { class: "table-wrap" }, tbl) : el("p", { class: "muted small", text: "No documents yet." }));
    if (!docs.length) return;
    table(tbl, [
      { label: "File", get: (d) => el("a", { href: `/api/documents/${encodeURIComponent(d.id)}/download`, text: d.filename }) },
      { label: "Vendor", get: (d) => d.vendor || "—" },
      { label: "Product", get: (d) => d.sku || "—" },
      { label: "Note", get: (d) => d.note || "" },
      { label: "Size", num: true, get: (d) => `${num(d.size / 1024)} KB` },
      { label: "Added", get: (d) => `${stamp.format(new Date(d.at))} · ${d.user_email.split("@")[0]}` },
      { label: "", get: (d) => el("button", { type: "button", class: "act ghost", text: "Delete", onclick: async () => {
        if (!confirm(`Delete ${d.filename}?`)) return;
        try { await send(`/api/admin/documents/${encodeURIComponent(d.id)}`, "DELETE"); toast("Deleted"); load(); } catch (ex) { toast(ex.message); }
      } }) },
    ], docs);
  };
  let t;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  load();
}

// ---------- settings ----------
async function adminSettings(body) {
  let d;
  try { d = await api("/api/settings"); } catch (e) { body.append(el("p", { class: "error", text: e.message })); return; }
  const groups = {};
  d.settings.forEach((s) => { (groups[s.group] = groups[s.group] || []).push(s); });
  const inputs = {};
  const form = el("form", { class: "settings-form" });
  const show = (s, v) => (s.kind === "fraction" && v != null ? String(+(v * 100).toFixed(4)) : v == null ? "" : String(v));
  Object.entries(groups).forEach(([g, items]) => {
    const fs = el("fieldset", {}, el("legend", { text: g }));
    items.forEach((s) => {
      let input;
      if (s.kind === "bool") {
        input = el("input", { type: "checkbox", id: `set-${s.key}` });
        input.checked = !!s.value;
      } else {
        input = el("input", { id: `set-${s.key}`, inputmode: "decimal", value: show(s, s.value) || null,
          placeholder: s.optional ? "not set" : show(s, s.default) });
      }
      inputs[s.key] = { s, input };
      const reset = el("button", { type: "button", class: "act ghost", text: "Default", title: `Reset to ${show(s, s.default) || "not set"}`,
        onclick: () => { if (s.kind === "bool") input.checked = !!s.default; else input.value = show(s, s.default); } });
      fs.append(el("div", { class: "setting" },
        el("label", { for: input.id }, el("b", { text: s.label }), s.help ? el("span", { class: "muted small", text: s.help }) : null),
        el("div", { class: "inline" }, input, s.kind === "fraction" ? el("span", { class: "unit", text: "%" }) : null, reset)));
    });
    form.append(fs);
  });
  const msg = el("p", { class: "small", role: "status" });
  form.append(el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Save settings" }), msg));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const changes = {};
    for (const [k, { s, input }] of Object.entries(inputs)) {
      let v;
      if (s.kind === "bool") v = input.checked;
      else if (input.value.trim() === "") v = s.optional ? null : s.default;
      else {
        v = parseFloat(input.value.replace(",", "."));
        if (!Number.isFinite(v)) { msg.textContent = `"${s.label}" must be a number`; input.focus(); return; }
        if (s.kind === "fraction") v = v / 100;
      }
      if (v !== s.value) changes[k] = v;
    }
    if (!Object.keys(changes).length) { msg.textContent = "Nothing changed."; return; }
    msg.textContent = "Saving…";
    try {
      const res = await send("/api/admin/settings", "PUT", { changes });
      toast(`Saved ${res.changed.length} setting${res.changed.length === 1 ? "" : "s"}`);
      await loadCatalog();
      adminEnter("settings");
    } catch (ex) { msg.textContent = ex.message; }
  });
  body.append(card("Engine settings", "These drive every target, plan and alert, in the app and for Claude via the MCP server. Changes apply immediately and are logged.", form),
    card("About the assistant", null,
      el("p", { class: "small", text: "The chat understands typed questions with a built-in parser that runs on this server; nothing is sent outside. The engine computes every number." }),
      el("p", { class: "small muted", text: "Sending unclear questions to Claude is planned. It needs an Anthropic API key on the server, and the question text and engine results would be sent to Anthropic. The switch above records your choice; it has no effect until that's built." })));
}

// ---------- users ----------
async function adminUsers(body) {
  const email = el("input", { id: "u-email", type: "email", placeholder: "name@siloamhospitals.com", required: "" });
  const name = el("input", { id: "u-name", placeholder: "Full name", required: "" });
  const role = el("select", { id: "u-role" }, el("option", { value: "viewer", text: "Viewer: sees everything, updates renewals" }), el("option", { value: "negotiator", text: "Negotiator: runs negotiations (prices, counter offers, anomalies, principal links)" }),
    el("option", { value: "admin", text: "Admin: also uploads data, tunes the engine, manages users" }));
  const msg = el("p", { class: "small", role: "status" });
  const form = el("form", { class: "row3" },
    el("label", { for: "u-email" }, "Email", email), el("label", { for: "u-name" }, "Name", name), el("label", { for: "u-role" }, "Role", role),
    el("div", { class: "actions" }, el("button", { type: "submit", class: "act", text: "Invite" }), msg));
  const listBox = el("div");
  const linkBox = el("div", { id: "setup-link-box" });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const u = await send("/api/admin/users", "POST", { email: email.value, name: name.value, role: role.value });
      await adminEnter("users");
      if (u.setup_link) showSetupLink(u.email, u.setup_link);
    } catch (ex) { msg.textContent = ex.message; }
  });
  body.append(card("Invite someone", "Only invited people can sign in. Inviting gives you a one-time link: send it to them (email or WhatsApp) so they set their own password. You never see their password.", form),
    linkBox, card("Users", null, listBox));
  let users;
  try { users = (await api("/api/admin/users")).users; } catch (e) { listBox.append(el("p", { class: "error", text: e.message })); return; }
  const tbl = el("table");
  listBox.append(el("div", { class: "table-wrap" }, tbl));
  const patch = async (u, change, label) => {
    try { await send(`/api/admin/users/${u.id}`, "PATCH", change); toast(label); adminEnter("users"); } catch (ex) { toast(ex.message); adminEnter("users"); }
  };
  table(tbl, [
    { label: "Name", get: (u) => u.name },
    { label: "Email", get: (u) => u.email },
    { label: "Role", get: (u) => {
      const s = el("select", { "aria-label": `Role for ${u.name}` }, el("option", { value: "viewer", text: "Viewer" }), el("option", { value: "negotiator", text: "Negotiator" }), el("option", { value: "admin", text: "Admin" }));
      s.value = u.role;
      s.addEventListener("change", () => patch(u, { role: s.value }, `${u.name} is now ${s.value}`));
      return s;
    } },
    { label: "Status", get: (u) => el("span", { class: `zone ${u.active ? "good" : "bad"}`, text: u.active ? "Active" : "Disabled" }) },
    { label: "Password", get: (u) => el("span", { class: `zone ${u.has_password ? "good" : "warn"}`, text: u.has_password ? "Set" : "Not set yet" }) },
    { label: "Last sign-in", get: (u) => (u.last_login ? stamp.format(new Date(u.last_login)) : "never") },
    { label: "", get: (u) => el("div", { class: "actions" },
      u.active ? el("button", { type: "button", class: "act ghost", text: u.has_password ? "Reset link" : "Setup link",
        onclick: async () => {
          try { const r = await send(`/api/admin/users/${u.id}/setup-link`, "POST", {}); showSetupLink(r.email, r.setup_link); }
          catch (ex) { toast(ex.message); }
        } }) : null,
      el("button", { type: "button", class: "act ghost", text: u.active ? "Disable" : "Enable",
        onclick: () => patch(u, { active: !u.active }, `${u.name} ${u.active ? "disabled" : "enabled"}`) })) },
  ], users);
}

function showSetupLink(email, link) {
  const box = $("setup-link-box");
  if (!box) return;
  const input = el("input", { value: link, readonly: "", "aria-label": "Setup link", class: "linkbox" });
  const copy = el("button", { type: "button", class: "act", text: "Copy link",
    onclick: async () => { try { await navigator.clipboard.writeText(link); toast("Link copied"); } catch (_) { input.select(); } } });
  clear(box).append(card(`Password link for ${email}`,
    "Send this to them. It works once and expires in 7 days. Opening it lets them choose their password and signs them in. Making a new link cancels the old one.",
    el("div", { class: "row-link" }, input, copy)));
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------- activity ----------
function describeEvent(e) {
  const d = e.detail || {};
  switch (e.action) {
    case "signin": return "signed in";
    case "signin.failed": return "failed to sign in";
    case "user.password_set": return `set a password for ${d.email}`;
    case "user.setup_link": return `made a password link for ${d.email}`;
    case "user.invite": return `invited ${d.email} as ${d.role}`;
    case "user.update": return `updated ${d.email}${d.role ? ` (role: ${d.role})` : ""}${d.active === 0 ? " (disabled)" : d.active === 1 ? " (enabled)" : ""}`;
    case "upload.stage": return `checked ${d.file} (${num(d.rows)} rows, ${d.status})`;
    case "upload.apply": return `applied an upload (${d.mode}); ${num(d.rows_now)} rows now in use`;
    case "upload.discard": return "discarded an upload";
    case "version.activate": return `switched the data to ${d.version === "sample" ? "the sample" : `version ${d.version}`}`;
    case "document.upload": return `uploaded ${d.file}${d.vendor ? ` for ${d.vendor}` : ""}`;
    case "document.delete": return `deleted ${d.file}`;
    case "settings.update": return `changed settings: ${Object.entries(d).map(([k, v]) => `${k} ${v.from ?? "unset"} → ${v.to ?? "unset"}`).join(", ")}`;
    default: return e.action;
  }
}

async function adminActivity(body) {
  let d;
  try { d = await api("/api/admin/activity"); } catch (e) { body.append(el("p", { class: "error", text: e.message })); return; }
  body.append(card("Activity", "Who did what, newest first.",
    d.events.length ? el("ol", { class: "timeline" }, d.events.map((e) => el("li", {},
      el("b", { text: e.user_email.split("@")[0] }), ` ${describeEvent(e)}`,
      el("span", { class: "muted small", text: ` · ${stamp.format(new Date(e.at))}` }))))
      : el("p", { class: "muted", text: "Nothing yet." })));
}
