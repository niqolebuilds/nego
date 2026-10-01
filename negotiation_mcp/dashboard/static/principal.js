"use strict";
// Principal portal: one step at a time, one SKU at a time if they like, with checks as they
// type. Every value goes through the server (portal.py), which decides what this principal
// may see and change; this page only makes entry easy. Data goes into the DOM through
// textContent, never innerHTML.

const P = { me: null, lang: "id", filter: "todo", q: "", items: [], total: 0, counts: {}, view: "list", one: 0, loading: false };
try { P.lang = localStorage.getItem("nego-p-lang") || "id"; } catch (_) { /* private mode */ }
try { P.view = localStorage.getItem("nego-p-view") || (window.innerWidth < 700 ? "one" : "list"); } catch (_) { P.view = window.innerWidth < 700 ? "one" : "list"; }

const T = {
  id: {
    portal: "Portal Principal Siloam", loading: "Memuat…",
    invalid_t: "Tautan tidak berlaku", invalid_p: "Tautan ini sudah kedaluwarsa atau dicabut. Minta tautan baru ke tim pengadaan Siloam.",
    step_of: (n) => `Langkah ${n} dari 6`,
    steps: { identification: "Konfirmasi data item", rfq: "Isi harga penawaran (RFQ)", feedback1: "Tanggapi counter offer Siloam" },
    howto: {
      identification: ["Periksa brand dan nomor katalog (REF) setiap SKU.", "Pilih Aktif jika masih dijual, atau Tidak dijual lagi.", "Semua tersimpan otomatis. Klik Kirim ke Siloam jika sudah selesai."],
      rfq: ["Harga MOU saat ini ditampilkan sebagai acuan. Jika harga tidak berubah, klik Sama dengan MOU.", "Isi HNA per kemasan (sebelum PPN), isi per kemasan, dan diskon dalam %.", "Jika harga per pcs naik, mohon isi alasannya. Semua tersimpan otomatis."],
      feedback1: ["Siloam mengirim counter offer berupa diskon per SKU.", "Klik Terima, atau isi diskon Anda sendiri dan alasannya.", "Klik Kirim ke Siloam jika sudah selesai."],
    },
    contract: "Kontrak", due: "Batas waktu", progress: (d, a) => `${d} dari ${a} SKU selesai`,
    download: "Unduh Excel", upload: "Unggah Excel", send: "Kirim ke Siloam",
    f: { todo: "Belum diisi", check: "Perlu dicek", up: "Harga naik", all: "Semua", done: "Selesai" },
    search: "Cari nama, kode, brand, REF", view_list: "Daftar", view_one: "Satu per satu",
    fill_rfq: "Isi semua yang kosong sama dengan MOU", fill_fb1: "Terima semua counter offer", next_todo: "Ke SKU berikutnya yang belum diisi",
    fill_confirm_rfq: (n) => `Isi semua SKU yang belum diisi dengan harga MOU saat ini? Anda tetap bisa mengubahnya.`,
    fill_confirm_fb1: () => "Terima counter offer Siloam untuk semua SKU yang belum ditanggapi?",
    filled: (n) => `${n} SKU terisi`, none: "Tidak ada SKU di sini.", more: "Tampilkan lebih banyak", prev: "← Sebelumnya", next: "Berikutnya →",
    state: { missing: "Belum diisi", check: "Perlu dicek", done: "Selesai" },
    mou_now: "MOU saat ini", same_mou: "Sama dengan MOU", status: "Status", active: "Aktif", disc_btn: "Tidak dijual lagi",
    qty: (pack) => `Isi per ${pack} (pcs)`, hna: (pack) => `HNA per ${pack}, sebelum PPN`, disc: "Diskon",
    per_pcs: "Harga per pcs termasuk PPN", vs_mou: "dari MOU", same: "sama dengan MOU",
    brand: "Brand / pabrikan", ref: "Nomor katalog (REF)", remarks: "Keterangan (opsional)",
    rfq_price: "Harga RFQ Anda", co: "Counter offer Siloam", accept: "Terima counter offer", own: "Atau isi diskon Anda",
    reason: "Alasan", reason_pick: "Pilih alasan…", reason_detail: "Keterangan singkat (opsional)", yes: "Ya, sudah benar", confirmed: "Sudah dikonfirmasi",
    saved: "Tersimpan ✓", saving: "Menyimpan…", unsaved: "Belum tersimpan. Coba lagi", retry: "Coba lagi",
    waiting_t: "Menunggu Siloam", waiting_p: "Tidak ada yang perlu diisi saat ini. Tim Siloam akan menghubungi Anda untuk langkah berikutnya.",
    sent_t: "Terima kasih, sudah terkirim", sent_p: (at) => `Dikirim pada ${at}. Tim Siloam sedang meninjau. Anda akan dihubungi untuk langkah berikutnya.`,
    sum_t: "Periksa sebelum mengirim", sum_items: "SKU", sum_done: "Selesai", sum_missing: "Belum diisi", sum_check: "Perlu dicek", sum_disc: "Tidak dijual lagi",
    sum_left: "Perlu dilengkapi dulu", sum_fix: "Perbaiki", sum_up: "Harga naik (dengan alasan)", sum_ok: "Semua SKU sudah lengkap.",
    sum_agree: "Saya sudah memeriksa dan data ini benar.", sum_send: "Kirim sekarang", cancel: "Batal", close: "Tutup",
    up_t: "Unggah Excel", up_p: "Gunakan file Excel yang diunduh dari halaman ini. Kolom yang bisa diisi berwarna oranye. Baris yang salah tidak disimpan dan ditampilkan di bawah.",
    up_check: "Periksa file", up_res: (a, r) => `${a} baris siap disimpan, ${r} baris perlu diperbaiki.`, up_save: (n) => `Simpan ${n} baris`,
    up_done: (n) => `${n} baris tersimpan`, row: "Baris", not_found: (n) => `${n} baris tidak dikenali (kode ERP tidak ada di daftar).`,
    sent_ok: "Terkirim. Terima kasih!", err: "Terjadi kesalahan",
  },
  en: {
    portal: "Siloam Principal Portal", loading: "Loading…",
    invalid_t: "Link not valid", invalid_p: "This link has expired or was revoked. Ask Siloam's procurement team for a new one.",
    step_of: (n) => `Step ${n} of 6`,
    steps: { identification: "Confirm item details", rfq: "Quote your prices (RFQ)", feedback1: "Respond to Siloam's counter offer" },
    howto: {
      identification: ["Check the brand and catalogue number (REF) of every SKU.", "Choose Active if you still sell it, or Discontinued.", "Everything saves automatically. Click Send to Siloam when you're done."],
      rfq: ["The current MOU price is shown for reference. If the price doesn't change, click Same as MOU.", "Enter the HNA per pack (before PPN), the pieces per pack and the discount in %.", "If the price per piece goes up, please give the reason. Everything saves automatically."],
      feedback1: ["Siloam sent a counter-offer discount for each SKU.", "Click Accept, or enter your own discount and the reason.", "Click Send to Siloam when you're done."],
    },
    contract: "Contract", due: "Due", progress: (d, a) => `${d} of ${a} SKUs done`,
    download: "Download Excel", upload: "Upload Excel", send: "Send to Siloam",
    f: { todo: "To fill", check: "To check", up: "Price up", all: "All", done: "Done" },
    search: "Search name, code, brand, REF", view_list: "List", view_one: "One at a time",
    fill_rfq: "Fill every blank SKU with the MOU price", fill_fb1: "Accept every counter offer", next_todo: "Next SKU to fill",
    fill_confirm_rfq: () => "Fill every blank SKU with the current MOU price? You can still change them.",
    fill_confirm_fb1: () => "Accept Siloam's counter offer for every SKU you haven't answered?",
    filled: (n) => `${n} SKUs filled`, none: "No SKUs here.", more: "Show more", prev: "← Previous", next: "Next →",
    state: { missing: "To fill", check: "To check", done: "Done" },
    mou_now: "Current MOU", same_mou: "Same as MOU", status: "Status", active: "Active", disc_btn: "Discontinued",
    qty: (pack) => `Pieces per ${pack}`, hna: (pack) => `HNA per ${pack}, before PPN`, disc: "Discount",
    per_pcs: "Price per piece incl. PPN", vs_mou: "vs MOU", same: "same as MOU",
    brand: "Brand / manufacturer", ref: "Catalogue no. (REF)", remarks: "Remarks (optional)",
    rfq_price: "Your RFQ price", co: "Siloam's counter offer", accept: "Accept counter offer", own: "Or enter your discount",
    reason: "Reason", reason_pick: "Choose a reason…", reason_detail: "Short detail (optional)", yes: "Yes, that's right", confirmed: "Confirmed",
    saved: "Saved ✓", saving: "Saving…", unsaved: "Not saved. Try again", retry: "Try again",
    waiting_t: "Waiting for Siloam", waiting_p: "There's nothing to fill right now. Siloam's team will contact you for the next step.",
    sent_t: "Thank you, it's sent", sent_p: (at) => `Sent on ${at}. Siloam's team is reviewing it and will contact you for the next step.`,
    sum_t: "Check before sending", sum_items: "SKUs", sum_done: "Done", sum_missing: "To fill", sum_check: "To check", sum_disc: "Discontinued",
    sum_left: "Complete these first", sum_fix: "Fix", sum_up: "Price increases (with reasons)", sum_ok: "Every SKU is complete.",
    sum_agree: "I've checked this and it's correct.", sum_send: "Send now", cancel: "Cancel", close: "Close",
    up_t: "Upload Excel", up_p: "Use the Excel file downloaded from this page. The cells you can fill are orange. Rows with mistakes aren't saved and are listed below.",
    up_check: "Check file", up_res: (a, r) => `${a} rows ready to save, ${r} rows need fixing.`, up_save: (n) => `Save ${n} rows`,
    up_done: (n) => `${n} rows saved`, row: "Row", not_found: (n) => `${n} rows not recognised (ERP code not in the list).`,
    sent_ok: "Sent. Thank you!", err: "Something went wrong",
  },
};
const t = (k, ...a) => { const v = k.split(".").reduce((o, p) => (o == null ? o : o[p]), T[P.lang]); return typeof v === "function" ? v(...a) : v ?? k; };
const msg = (o) => (o ? o[P.lang] || o.id : "");

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(3)) if (c != null && c !== false) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
// Append children, skipping null/false (Element.append would print them as text).
const put = (n, ...kids) => { n.append(...kids.flat(3).filter((k) => k != null && k !== false)); return n; };
const nf = (d) => new Intl.NumberFormat("id-ID", { minimumFractionDigits: d, maximumFractionDigits: d });
const num = (v, d = 0) => (v == null ? "—" : nf(d).format(v));
const rp = (v) => (v == null ? "—" : `Rp ${num(v, v < 100 && v % 1 ? 2 : 0)}`);
const pctTxt = (f) => (f == null ? "" : `${num(f * 100, Math.abs(f * 100 - Math.round(f * 100)) > 1e-6 ? 2 : 0)}%`);
const fmtDate = (iso) => new Intl.DateTimeFormat(P.lang === "id" ? "id-ID" : "en-GB", { day: "numeric", month: "long", year: "numeric" }).format(new Date(iso.length <= 10 ? iso + "T00:00:00" : iso));
const unitPrice = (hna, qty, disc) => (hna == null || !qty ? null : (hna / qty) * (1 - (disc || 0)) * (1 + (P.me?.ppn ?? 0.11)));
function toast(text) {
  const n = $("ptoast"); n.textContent = text; n.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { n.hidden = true; }, 3000);
}
async function call(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-Requested-With": "nego" }, credentials: "same-origin" };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opts);
  let j = {};
  try { j = await r.json(); } catch (_) { /* not json */ }
  if (r.status === 401) { showInvalid(); throw new Error(j.error || "401"); }
  if (!r.ok) {
    const e = String(j.error || r.statusText);
    throw new Error(e.includes(" / ") ? e.split(" / ")[P.lang === "id" ? 0 : 1] : e);
  }
  return j;
}
// Typed numbers: HNA and Qty keep digits (and a decimal comma for HNA); discounts are percent.
const digits = (s) => String(s || "").replace(/[^\d]/g, "");
// Values go to the server with a decimal comma and no thousands dots, so "1,25" can never be read as 1.250.
function hnaValue(s) { const [a, b] = String(s || "").replace(/\./g, "").split(","); const d = digits(a); return d ? (b !== undefined ? `${d},${digits(b)}` : d) : ""; }
function hnaShow(v) {
  if (v == null || v === "") return "";
  const [a, b] = typeof v === "number" ? String(v).split(".") : String(v).split(",");
  return nf(0).format(+a) + (b !== undefined ? "," + b : "");
}
function discValue(s) { return String(s || "").replace("%", "").trim().replace(/\./g, ","); }
const discShow = (f) => (f == null ? "" : String(+(f * 100).toFixed(4)).replace(".", ","));

// ---------- boot ----------
function showInvalid() {
  put(clear($("app")), el("section", { class: "pcard center" }, el("h1", { text: t("invalid_t") }), el("p", { text: t("invalid_p") })));
}

async function boot() {
  document.querySelectorAll(".lang button").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
  setLang(P.lang, false);
  if (new URLSearchParams(location.search).get("invalid")) { showInvalid(); return; }
  try { P.me = await call("/api/p/me"); } catch (_) { return; }
  $("who").textContent = P.me.principal.name;
  render();
}

function setLang(l, redraw = true) {
  P.lang = l === "en" ? "en" : "id";
  try { localStorage.setItem("nego-p-lang", P.lang); } catch (_) { /* ignore */ }
  document.documentElement.lang = P.lang;
  document.querySelectorAll(".lang button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === P.lang)));
  $("t-portal").textContent = t("portal");
  document.title = t("portal");
  if (redraw && P.me) render();
}

// ---------- page ----------
function render() {
  const app = clear($("app"));
  const me = P.me;
  if (!me.step) {
    const sent = me.submitted;
    put(app, el("section", { class: "pcard center" },
      el("h1", { text: sent ? t("sent_t") : t("waiting_t") }),
      el("p", { text: sent ? t("sent_p", fmtDate(sent.at)) : t("waiting_p") })));
    return;
  }
  const howto = el("ol", { class: "howto" }, t(`howto.${me.step}`).map((s) => el("li", { text: s })));
  const prog = el("div", { class: "prog" }, el("div", { class: "bar" }, el("i", { id: "prog-fill" })), el("span", { id: "prog-text" }));
  put(app, 
    el("section", { class: "pcard stepcard" },
      el("p", { class: "eyebrow", text: t("step_of", me.step_no) }),
      el("h1", { text: t(`steps.${me.step}`) }),
      el("p", { class: "muted", text: `${t("contract")} ${fmtDate(me.contract.start)} – ${fmtDate(me.contract.end)}${me.due ? ` · ${t("due")} ${fmtDate(me.due)}` : ""}` }),
      howto, prog,
      el("div", { class: "pactions" },
        el("button", { type: "button", class: "pbtn primary", text: t("send"), onclick: openSummary }),
        el("a", { class: "pbtn", href: "/api/p/template.xlsx", text: t("download") }),
        el("button", { type: "button", class: "pbtn", text: t("upload"), onclick: openUpload }))),
    toolbar(),
    el("div", { id: "list", class: `skus view-${P.view}` }),
    el("div", { id: "more" }));
  load(true);
}

function toolbar() {
  const chips = el("div", { class: "chips", role: "tablist", id: "chips" });
  const search = el("input", { type: "search", class: "psearch", placeholder: t("search"), value: P.q || null, "aria-label": t("search") });
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { P.q = search.value.trim(); load(true); }, 250); });
  const views = el("div", { class: "seg", role: "group" },
    ["list", "one"].map((v) => el("button", { type: "button", "aria-pressed": String(P.view === v), text: t(`view_${v}`),
      onclick: () => { P.view = v; try { localStorage.setItem("nego-p-view", v); } catch (_) { /* ignore */ } P.one = 0; renderList(); document.querySelectorAll(".seg button").forEach((b, i) => b.setAttribute("aria-pressed", String(["list", "one"][i] === v))); } })));
  const bulk = P.me.step === "identification" ? null :
    el("button", { type: "button", class: "pbtn ghost", text: t(P.me.step === "rfq" ? "fill_rfq" : "fill_fb1"), onclick: bulkFill });
  return el("section", { class: "toolbar" }, chips,
    el("div", { class: "tools" }, search, views, bulk,
      el("button", { type: "button", class: "pbtn ghost", text: t("next_todo"), onclick: nextTodo })));
}

function renderChips() {
  const box = $("chips");
  if (!box) return;
  put(clear(box), ...["todo", "check", "up", "all", "done"].filter((k) => k !== "up" || P.me.step === "rfq").map((k) =>
    el("button", { type: "button", role: "tab", class: `chip ${P.filter === k ? "on" : ""} c-${k}`, "aria-selected": String(P.filter === k),
      onclick: () => { P.filter = k; load(true); } }, t(`f.${k}`), el("b", { text: num(P.counts[k] ?? 0) }))));
  const c = P.counts;
  const done = c.done ?? 0, all = c.all ?? 0;
  $("prog-fill").style.width = `${all ? (done / all) * 100 : 0}%`;
  $("prog-text").textContent = t("progress", num(done), num(all));
}

async function load(reset) {
  if (reset) { P.items = []; P.one = 0; }
  P.loading = true;
  const qs = new URLSearchParams({ filter: P.filter, q: P.q, offset: P.items.length, limit: 50 });
  let d;
  try { d = await call(`/api/p/items?${qs}`); } catch (e) { toast(e.message); return; } finally { P.loading = false; }
  P.items = P.items.concat(d.items);
  P.total = d.total;
  P.counts = d.counts;
  renderChips();
  renderList();
}

async function refreshCounts() {
  clearTimeout(refreshCounts.t);
  refreshCounts.t = setTimeout(async () => {
    try { const d = await call(`/api/p/items?${new URLSearchParams({ filter: "all", limit: 1 })}`); P.counts = d.counts; renderChips(); } catch (_) { /* next save retries */ }
  }, 500);
}

function renderList() {
  const list = clear($("list"));
  list.className = `skus view-${P.view}`;
  const more = clear($("more"));
  if (!P.items.length) { put(list, el("p", { class: "muted center pad", text: t("none") })); return; }
  if (P.view === "one") {
    P.one = Math.max(0, Math.min(P.one, P.items.length - 1));
    put(list, card(P.items[P.one]));
    put(more, el("div", { class: "onenav" },
      el("button", { type: "button", class: "pbtn", text: t("prev"), disabled: P.one === 0, onclick: () => { P.one--; renderList(); focusFirst(); } }),
      el("span", { class: "muted", text: `${P.one + 1} / ${num(P.total)}` }),
      el("button", { type: "button", class: "pbtn primary", text: t("next"), disabled: P.one >= P.total - 1, onclick: goNext })));
    return;
  }
  P.items.forEach((it) => put(list, card(it)));
  if (P.items.length < P.total) put(more, el("button", { type: "button", class: "pbtn wide", text: t("more"), onclick: () => load(false) }));
}

async function goNext() {
  if (P.one >= P.items.length - 1 && P.items.length < P.total) await load(false);
  P.one = Math.min(P.one + 1, P.items.length - 1);
  renderList();
  focusFirst();
}
function focusFirst() { const i = $("list").querySelector("input:not([disabled]), button.sbtn"); if (i) i.focus(); }

function nextTodo() {
  if (P.view === "one") {
    const i = P.items.findIndex((x, k) => k > P.one && x.state === "missing");
    if (i >= 0) { P.one = i; renderList(); focusFirst(); return; }
  } else {
    const a = $("list").querySelector("article.st-missing");
    if (a) { a.scrollIntoView({ behavior: "smooth", block: "center" }); const i = a.querySelector("input, button.sbtn"); if (i) setTimeout(() => i.focus(), 300); return; }
  }
  P.filter = "todo"; load(true);
}

async function bulkFill() {
  if (!confirm(t(P.me.step === "rfq" ? "fill_confirm_rfq" : "fill_confirm_fb1"))) return;
  try { const r = await call("/api/p/fill", { method: "POST", body: {} }); toast(t("filled", r.filled)); load(true); } catch (e) { toast(e.message); }
}

// ---------- one SKU ----------
function card(it) {
  const a = el("article", { class: `sku st-${it.state}`, "data-id": it.id });
  const head = el("header", { class: "sku-head" },
    el("div", {}, el("h2", { text: it.item_name || "—" }),
      el("p", { class: "meta", text: [it.erp_code, it.brand, it.catalog_no, it.po_unit_text].filter(Boolean).join(" · ") })),
    el("span", { class: `state s-${it.state}`, text: t(`state.${it.state}`) }));
  put(a, head);
  const body = el("div", { class: "sku-body" });
  put(a, body);
  ({ identification: identBody, rfq: rfqBody, feedback1: fbBody })[P.me.step](body, it, a);
  put(a, el("div", { class: "issues" }), el("div", { class: "savestate", "aria-live": "polite" }));
  paintIssues(a, it);
  a.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.target.tagName !== "INPUT") return;
    e.preventDefault();
    e.target.dispatchEvent(new Event("change"));
    const inputs = [...$("list").querySelectorAll("input:not([disabled]):not([type=search])")];
    const i = inputs.indexOf(e.target);
    if (i >= 0 && i < inputs.length - 1) inputs[i + 1].focus();
    else if (P.view === "one") goNext();
  });
  return a;
}

function statusButtons(a, it) {
  const mk = (val, label) => el("button", { type: "button", class: `sbtn ${it.item_status === val ? "on" : ""}`, "aria-pressed": String(it.item_status === val), text: label,
    onclick: () => save(a, it, { item_status: val }) });
  return el("div", { class: "field status" }, el("span", { class: "lbl", text: t("status") }),
    el("div", { class: "seg big" }, mk("Active", t("active")), mk("Discontinue", t("disc_btn"))));
}

function identBody(body, it, a) {
  const input = (field, label, attrs = {}) => {
    const i = el("input", { value: it[field] || null, ...attrs });
    i.addEventListener("change", () => { if ((i.value.trim() || null) !== (it[field] || null)) save(a, it, { [field]: i.value.trim() }); });
    return el("label", { class: "field" }, el("span", { class: "lbl", text: label }), i);
  };
  put(body, statusButtons(a, it), el("div", { class: "grid3" }, input("brand", t("brand")), input("catalog_no", t("ref")), input("remarks", t("remarks"))));
}

function rfqBody(body, it, a) {
  const pack = it.pack_name && it.pack_name !== "PCS" ? it.pack_name : (P.lang === "id" ? "kemasan" : "pack");
  const ref = el("div", { class: "ref" },
    el("span", { class: "lbl", text: t("mou_now") }),
    it.mou_hna != null ? el("span", {}, `${it.mou_qty ? `${pack} ${num(it.mou_qty)} pcs · ` : ""}HNA ${rp(it.mou_hna)} · ${t("disc")} ${pctTxt(it.mou_disc || 0)} → `, el("b", { text: `${rp(it.mou_unit_price)}/pcs` })) : el("span", { class: "muted", text: "—" }),
    it.mou_hna != null && it.item_status !== "Discontinue" ? el("button", { type: "button", class: "pbtn small", text: t("same_mou"),
      onclick: () => { save(a, it, { rfq_qty: it.mou_qty ?? 1, rfq_hna: String(it.mou_hna).replace(".", ","), rfq_disc: String(+((it.mou_disc || 0) * 100).toFixed(4)).replace(".", ","), ...(it.item_status ? {} : { item_status: "Active" }) }, true); } }) : null);
  put(body, ref, statusButtons(a, it));
  if (it.item_status === "Discontinue") return;
  const qty = el("input", { inputmode: "numeric", value: it.rfq_qty != null ? String(it.rfq_qty) : null, "aria-label": t("qty", pack) });
  const hna = el("input", { inputmode: "decimal", value: hnaShow(it.rfq_hna) || null, "aria-label": t("hna", pack) });
  const disc = el("input", { inputmode: "decimal", value: it.rfq_disc != null ? discShow(it.rfq_disc) : null, "aria-label": t("disc") });
  const result = el("div", { class: "result" });
  const live = () => {
    const n = (x) => +String(x).replace(",", ".");
    const p = unitPrice(n(hnaValue(hna.value)) || null, +digits(qty.value) || null, (n(discValue(disc.value)) || 0) / 100);
    put(clear(result), el("span", { class: "lbl", text: t("per_pcs") }), el("b", { text: p == null ? "—" : rp(p) }));
    if (p != null && it.mou_unit_price) {
      const ch = p / it.mou_unit_price - 1;
      put(result, el("span", { class: `chg ${ch > 0.0005 ? "up" : ch < -0.0005 ? "down" : ""}`, text: Math.abs(ch) < 0.0005 ? t("same") : `${ch > 0 ? "+" : ""}${pctTxt(ch)} ${t("vs_mou")}` }));
    }
  };
  qty.addEventListener("input", () => { qty.value = digits(qty.value); live(); });
  hna.addEventListener("input", () => { const v = hnaValue(hna.value); hna.value = v ? hnaShow(v) : ""; live(); });
  disc.addEventListener("input", () => { disc.value = disc.value.replace(/[^\d.,]/g, ""); live(); });
  qty.addEventListener("change", () => save(a, it, { rfq_qty: digits(qty.value) || null }));
  hna.addEventListener("change", () => save(a, it, { rfq_hna: hnaValue(hna.value) || null }));
  disc.addEventListener("change", () => save(a, it, { rfq_disc: discValue(disc.value) || null }));
  put(body, el("div", { class: "grid3" },
    el("label", { class: "field" }, el("span", { class: "lbl", text: t("hna", pack) }), el("div", { class: "affix" }, el("span", { text: "Rp" }), hna)),
    el("label", { class: "field" }, el("span", { class: "lbl", text: t("qty", pack) }), el("div", { class: "affix" }, qty, el("span", { text: "pcs" }))),
    el("label", { class: "field" }, el("span", { class: "lbl", text: t("disc") }), el("div", { class: "affix" }, disc, el("span", { text: "%" })))),
  result);
  live();
}

function fbBody(body, it, a) {
  if (it.co_disc == null) { put(body, el("p", { class: "muted", text: "—" })); return; }
  const disc = el("input", { inputmode: "decimal", value: it.fb1_disc != null ? discShow(it.fb1_disc) : null, "aria-label": t("own") });
  disc.addEventListener("input", () => { disc.value = disc.value.replace(/[^\d.,]/g, ""); });
  disc.addEventListener("change", () => save(a, it, { fb1_disc: discValue(disc.value) || null }));
  const accepted = it.fb1_disc != null && Math.abs(it.fb1_disc - it.co_disc) < 1e-9;
  put(body, 
    el("div", { class: "ref" }, el("span", { class: "lbl", text: t("rfq_price") }), el("span", {}, `HNA ${rp(it.rfq_hna)} · ${t("disc")} ${pctTxt(it.rfq_disc || 0)} → `, el("b", { text: `${rp(it.rfq_unit_price)}/pcs` }))),
    el("div", { class: "ref co" }, el("span", { class: "lbl", text: t("co") }), el("span", {}, `${t("disc")} ${pctTxt(it.co_disc)} → `, el("b", { text: `${rp(it.co_unit_price)}/pcs` })),
      el("button", { type: "button", class: `pbtn small ${accepted ? "on" : "primary"}`, text: accepted ? `✓ ${t("accept")}` : t("accept"),
        onclick: () => save(a, it, { fb1_disc: String(+(it.co_disc * 100).toFixed(4)).replace(".", ",") }, true) })),
    el("label", { class: "field narrow" }, el("span", { class: "lbl", text: t("own") }), el("div", { class: "affix" }, disc, el("span", { text: "%" }))),
    el("div", { class: "result" }, el("span", { class: "lbl", text: t("per_pcs") }), el("b", { text: rp(it.fb1_unit_price) })));
}

function paintIssues(a, it) {
  const box = clear(a.querySelector(".issues"));
  const open = new Set(it.outstanding);
  const confirmed = new Set((it.principal_confirmed || "").split(",").filter(Boolean));
  for (const i of it.issues) {
    if (i.level === "missing") continue; // the state badge says it
    const row = el("div", { class: `issue lv-${i.level} ${open.has(i.code) ? "" : "resolved"}` }, el("p", { text: msg(i) }));
    if (i.level === "reason") put(row, reasonPicker(a, it));
    if (i.level === "confirm") {
      put(row, confirmed.has(i.code) ? el("span", { class: "ok", text: `✓ ${t("confirmed")}` }) :
        el("button", { type: "button", class: "pbtn small", text: t("yes"), onclick: () => save(a, it, {}, true, i.code) }));
    }
    put(box, row);
  }
}

function reasonPicker(a, it) {
  const [pick0, ...rest] = (it.price_reason || "").split(" — ");
  const known = P.me.reasons.find((r) => r.id === pick0 || r.en === pick0);
  const sel = el("select", { "aria-label": t("reason") }, el("option", { value: "", text: t("reason_pick") }),
    P.me.reasons.map((r) => el("option", { value: r.id, text: r[P.lang], selected: known && known.id === r.id })));
  const detail = el("input", { placeholder: t("reason_detail"), value: known ? rest.join(" — ") || null : it.price_reason || null, maxlength: 200 });
  const push = () => {
    const v = [sel.value, detail.value.trim()].filter(Boolean).join(" — ");
    if (v !== (it.price_reason || "")) save(a, it, { price_reason: v || null });
  };
  sel.addEventListener("change", push);
  detail.addEventListener("change", push);
  return el("div", { class: "reason" }, sel, detail);
}

// Saves one SKU. Unsaved changes are kept in this browser and retried, so nothing typed is lost.
const PENDING = "nego-p-pending";
function pending() { try { return JSON.parse(localStorage.getItem(PENDING) || "{}"); } catch (_) { return {}; } }
function setPending(p) { try { localStorage.setItem(PENDING, JSON.stringify(p)); } catch (_) { /* ignore */ } }

async function save(a, it, changes, rerender = false, confirmCode = null) {
  const st = a.querySelector(".savestate");
  st.className = "savestate saving"; st.textContent = t("saving");
  const p = pending(); p[it.id] = { ...(p[it.id] || {}), ...changes }; setPending(p);
  try {
    const fresh = await call(`/api/p/items/${it.id}`, { method: "PATCH", body: { changes: p[it.id], confirm: confirmCode } });
    const q = pending(); delete q[it.id]; setPending(q);
    Object.assign(it, fresh);
    const statusChanged = "item_status" in changes;
    if (rerender || statusChanged) {
      const next = card(it);
      a.replaceWith(next);
      a = next;
    } else {
      a.className = `sku st-${it.state}`;
      const s = a.querySelector(".state"); s.className = `state s-${it.state}`; s.textContent = t(`state.${it.state}`);
      paintIssues(a, it);
    }
    const st2 = a.querySelector(".savestate"); st2.className = "savestate ok"; st2.textContent = t("saved");
    refreshCounts();
  } catch (e) {
    const st2 = a.isConnected ? a.querySelector(".savestate") : st;
    st2.className = "savestate bad"; put(clear(st2), `${t("unsaved")}: ${e.message} `,
      el("button", { type: "button", class: "pbtn small", text: t("retry"), onclick: () => save(a, it, {}, rerender, confirmCode) }));
  }
}

// ---------- send ----------
async function openSummary() {
  let s;
  try { s = await call("/api/p/summary"); } catch (e) { toast(e.message); return; }
  const agree = el("input", { type: "checkbox", id: "agree" });
  const sendBtn = el("button", { type: "button", class: "pbtn primary", text: t("sum_send"), disabled: true });
  agree.addEventListener("change", () => { sendBtn.disabled = !(agree.checked && s.can_send); });
  sendBtn.addEventListener("click", async () => {
    sendBtn.disabled = true;
    try {
      await call("/api/p/submit", { method: "POST", body: {} });
      $("dlg").close();
      toast(t("sent_ok"));
      P.me = await call("/api/p/me");
      render();
    } catch (e) { toast(e.message); sendBtn.disabled = false; }
  });
  const facts = [["sum_items", s.items], ["sum_done", s.done], ["sum_missing", s.missing], ["sum_check", s.to_check], ["sum_disc", s.discontinued]];
  const body = clear($("dlg-body"));
  put(body, el("h2", { text: t("sum_t") }),
    el("div", { class: "facts" }, facts.map(([k, v]) => el("div", { class: k === "sum_missing" && v ? "bad" : k === "sum_check" && v ? "warn" : "" }, el("span", { text: t(k) }), el("b", { text: num(v) })))),
    s.outstanding_count ? el("div", {}, el("h3", { text: `${t("sum_left")} (${num(s.outstanding_count)})` }),
      el("ul", { class: "left" }, s.outstanding.map((o) => el("li", {},
        el("div", {}, el("b", { text: o.item_name }), el("span", { class: "muted", text: ` ${o.erp_code || ""}` }),
          el("div", { class: "small", text: o.issues.map((i) => msg(i)).join(" ") })),
        el("button", { type: "button", class: "pbtn small", text: t("sum_fix"), onclick: () => { $("dlg").close(); P.filter = "all"; P.q = o.erp_code || o.item_name; render(); } }))))) :
      el("p", { class: "okline", text: `✓ ${t("sum_ok")}` }),
    s.increase_count ? el("details", {}, el("summary", { text: `${t("sum_up")} (${num(s.increase_count)})` }),
      el("ul", { class: "left" }, s.increases.map((x) => el("li", {}, el("div", {}, el("b", { text: x.item_name }),
        el("div", { class: "small", text: `${rp(x.mou_unit_price)} → ${rp(x.rfq_unit_price)} · ${x.reason || "—"}` })))))) : null,
    el("label", { class: "agree" }, agree, t("sum_agree")),
    el("div", { class: "pactions" }, sendBtn, el("button", { type: "button", class: "pbtn", text: t("cancel"), onclick: () => $("dlg").close() })));
  if (!s.can_send) agree.disabled = true;
  $("dlg").showModal();
}

// ---------- Excel ----------
function openUpload() {
  const file = el("input", { type: "file", accept: ".xlsx", id: "upfile" });
  const out = el("div", { "aria-live": "polite" });
  const check = el("button", { type: "button", class: "pbtn primary", text: t("up_check") });
  check.addEventListener("click", async () => {
    if (!file.files.length) { file.focus(); return; }
    const fd = new FormData(); fd.append("file", file.files[0]); fd.append("apply", "0");
    put(clear(out), el("p", { class: "muted", text: t("loading") }));
    try { showUpload(out, await call("/api/p/template", { method: "POST", form: fd }), file.files[0]); }
    catch (e) { put(clear(out), el("p", { class: "error", text: e.message })); }
  });
  put(clear($("dlg-body")), el("h2", { text: t("up_t") }), el("p", { class: "muted", text: t("up_p") }),
    el("div", { class: "pactions" }, file, check), out,
    el("div", { class: "pactions" }, el("button", { type: "button", class: "pbtn", text: t("close"), onclick: () => $("dlg").close() })));
  $("dlg").showModal();
}

function showUpload(out, r, f) {
  const bad = r.rejected.length + r.read_errors.length;
  put(clear(out), el("p", { class: bad ? "warnline" : "okline", text: t("up_res", r.accepted, bad) }),
    r.unmatched_count ? el("p", { class: "small muted", text: t("not_found", r.unmatched_count) }) : null,
    bad ? el("ul", { class: "left errs" }, [...r.read_errors, ...r.rejected].map((x) => el("li", {},
      el("b", { text: `${t("row")} ${x.row}` }), x.item_name ? el("span", { text: ` ${x.item_name}` }) : null,
      el("div", { class: "small", text: x.messages.map((m) => msg(m)).join(" ") })))) : null,
    r.notes.length ? el("details", {}, el("summary", { text: `${r.notes.length} catatan / notes` }),
      el("ul", { class: "small" }, r.notes.map((n) => el("li", { text: `${t("row")} ${n.row}: ${msg(n)}` })))) : null,
    r.accepted ? el("button", { type: "button", class: "pbtn primary", text: t("up_save", r.accepted), onclick: async () => {
      const fd = new FormData(); fd.append("file", f); fd.append("apply", "1");
      try { const res = await call("/api/p/template", { method: "POST", form: fd }); toast(t("up_done", res.applied)); $("dlg").close(); load(true); }
      catch (e) { toast(e.message); }
    } }) : null);
}

boot();
