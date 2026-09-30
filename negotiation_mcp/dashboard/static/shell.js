"use strict";
// App shell: who is signed in, which page shows, the header. Pages: welcome, signin,
// chat, renewals, admin. Pages behind sign-in redirect there; the server enforces the
// same rules on every API call, so hiding a tab is convenience, not security.

const shell = { user: null, catalog: null, intended: null, provider: null };
const PAGES = ["welcome", "signin", "chat", "renewals", "admin"];
const NEEDS_AUTH = new Set(["chat", "renewals", "admin"]);

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 3200);
}

function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
}

function renderHeader() {
  const u = shell.user;
  $("apptabs").hidden = !u;
  $("who").hidden = !u;
  $("admin-tab").hidden = !(u && u.role === "admin");
  if (u) {
    $("who-name").textContent = u.name;
    $("who-role").textContent = u.role === "admin" ? "Admin" : "Viewer";
    $("who-initials").textContent = initials(u.name);
  }
}

async function loadCatalog() {
  shell.catalog = await api("/api/catalog");
  state.currency = shell.catalog.currency || "IDR";
  if (shell.catalog.banner) { $("banner").textContent = shell.catalog.banner; $("banner").hidden = false; }
  else $("banner").hidden = true;
}

function currentRoute() {
  const [page, ...rest] = (location.hash.slice(1) || "welcome").split("/").map(decodeURIComponent);
  return { page: PAGES.includes(page) ? page : "welcome", rest };
}

async function route() {
  const { page, rest } = currentRoute();
  if (NEEDS_AUTH.has(page) && !shell.user) {
    shell.intended = location.hash;
    location.hash = "#signin";
    return;
  }
  if (page === "admin" && shell.user.role !== "admin") { location.hash = "#chat"; return; }
  if (page === "signin" && shell.user) { location.hash = "#chat"; return; }
  PAGES.forEach((p) => { $(p).hidden = p !== page; });
  document.querySelectorAll(".apptabs a").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
  document.body.dataset.page = page;
  hideTip();
  if (page === "signin") setTimeout(() => $("signin-email").focus(), 30);
  if (page === "chat") chatEnter(rest);
  if (page === "renewals") boardEnter(rest);
  if (page === "admin") adminEnter(rest[0] || "data");
  if (page !== "chat") window.scrollTo(0, 0);
}

async function signIn(e) {
  e.preventDefault();
  const email = $("signin-email").value.trim();
  const err = $("signin-error");
  err.hidden = true;
  if (!email) { $("signin-email").focus(); return; }
  try {
    const d = await send("/api/auth/signin", "POST", { email });
    shell.user = d.user;
    await loadCatalog();
    renderHeader();
    toast(`Welcome, ${d.user.name.split(" ")[0]}`);
    const go = shell.intended && shell.intended !== "#signin" ? shell.intended : "#chat";
    shell.intended = null;
    location.hash = go;
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
}

async function signOut() {
  try { await send("/api/auth/signout", "POST", {}); } catch (_) { /* already signed out */ }
  shell.user = null;
  resetChat();
  renderHeader();
  location.hash = "#welcome";
}

async function shellInit() {
  try {
    const d = await api("/api/auth/me");
    shell.user = d.user;
    shell.provider = d.provider;
    $("signin-notice").textContent = d.provider ? d.provider.notice : "";
    if (shell.user) await loadCatalog();
  } catch (e) {
    $("welcome").prepend(el("p", { class: "error", text: `Can't reach the server: ${e.message}` }));
  }
  renderHeader();
  $("start").addEventListener("click", () => { location.hash = shell.user ? "#chat" : "#signin"; });
  $("signin-form").addEventListener("submit", signIn);
  $("signout").addEventListener("click", signOut);
  window.addEventListener("nego:signed-out", () => {
    if (!shell.user) return;
    shell.user = null;
    renderHeader();
    shell.intended = location.hash;
    toast("Your session ended. Please sign in again.");
    location.hash = "#signin";
  });
  window.addEventListener("hashchange", route);
  route();
}
shellInit();
