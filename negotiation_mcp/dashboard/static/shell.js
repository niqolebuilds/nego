"use strict";
// App shell: who is signed in, which page shows, the header. Pages: welcome, signin,
// chat, nego (principal negotiations), renewals, admin. Pages behind sign-in redirect there; the server enforces the
// same rules on every API call, so hiding a tab is convenience, not security.

const shell = { user: null, catalog: null, intended: null, provider: null };
const PAGES = ["welcome", "signin", "setpw", "chat", "nego", "renewals", "admin"];
const NEEDS_AUTH = new Set(["chat", "nego", "renewals", "admin"]);

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
  $("admin-tab").hidden = !(u && (u.role === "admin" || u.role === "negotiator"));
  $("admin-tab").textContent = u && u.role === "negotiator" ? "Master data" : "Admin";
  if (u) {
    $("who-name").textContent = u.name;
    $("who-role").textContent = { admin: "Administrator", negotiator: "Negotiator" }[u.role] || "Viewer";
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
  if (page === "admin" && shell.user.role === "negotiator" && rest[0] !== "master") { location.hash = "#admin/master"; return; }  // read-only master data
  if (page === "admin" && !["admin", "negotiator"].includes(shell.user.role)) { location.hash = "#chat"; return; }
  if (page === "signin" && shell.user) { location.hash = "#chat"; return; }
  if (page === "setpw") setpwEnter(rest[0] || "");
  PAGES.forEach((p) => { $(p).hidden = p !== page; });
  document.querySelectorAll(".apptabs a").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
  document.body.dataset.page = page;
  hideTip();
  if (page === "signin") setTimeout(() => $("signin-email").focus(), 30);
  if (page === "chat") chatEnter(rest);
  if (page === "nego") ngEnter(rest);
  if (page === "renewals") boardEnter(rest);
  if (page === "admin") adminEnter(rest[0] || "data");
  if (page !== "chat") window.scrollTo(0, 0);
}

async function signIn(e) {
  e.preventDefault();
  const email = $("signin-email").value.trim();
  const password = $("signin-password").value;
  const err = $("signin-error");
  err.hidden = true;
  if (!email) { $("signin-email").focus(); return; }
  if (!password && shell.provider && shell.provider.password_required) { $("signin-password").focus(); return; }
  try {
    const d = await send("/api/auth/signin", "POST", { email, password });
    $("signin-password").value = "";
    await afterSignIn(d);
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
}

async function afterSignIn(d) {
  shell.user = d.user;
  await loadCatalog();
  renderHeader();
  toast(`Welcome, ${d.user.name.split(" ")[0]}`);
  const go = shell.intended && shell.intended !== "#signin" ? shell.intended : "#chat";
  shell.intended = null;
  location.hash = go;
}

// One-time link from an admin: #setpw/<token>. The person chooses their own password.
async function setpwEnter(token) {
  const who = $("setpw-who"), err = $("setpw-error"), btn = $("setpw-submit");
  err.hidden = true;
  btn.disabled = true;
  setpwEnter.token = token;
  try {
    const d = await api(`/api/auth/setup?token=${encodeURIComponent(token)}`);
    who.textContent = `For ${d.name} (${d.email}). Use at least ${d.min_length} characters.`;
    btn.disabled = false;
    setTimeout(() => $("setpw-password").focus(), 30);
  } catch (ex) {
    who.textContent = "";
    err.textContent = ex.message;
    err.hidden = false;
  }
}

async function setPassword(e) {
  e.preventDefault();
  const pw = $("setpw-password").value, again = $("setpw-confirm").value, err = $("setpw-error");
  err.hidden = true;
  if (pw !== again) { err.textContent = "The two passwords don't match."; err.hidden = false; return; }
  try {
    const d = await send("/api/auth/setup", "POST", { token: setpwEnter.token, password: pw });
    $("setpw-password").value = ""; $("setpw-confirm").value = "";
    setpwEnter.token = null;
    history.replaceState(null, "", "#chat");
    await afterSignIn(d);
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
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
    $("signin-password-row").hidden = !(d.provider && d.provider.password_required);
    if (shell.user) await loadCatalog();
  } catch (e) {
    $("welcome").prepend(el("p", { class: "error", text: `Can't reach the server: ${e.message}` }));
  }
  renderHeader();
  $("start").addEventListener("click", () => { location.hash = shell.user ? "#chat" : "#signin"; });
  $("signin-form").addEventListener("submit", signIn);
  $("setpw-form").addEventListener("submit", setPassword);
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
