#!/usr/bin/env python3
"""Walk every role through the real app and write what each one sees to docs/ROLES.md.

Starts a throwaway server on sample data, signs in as a viewer, a negotiator and an admin,
opens the vendor link as a principal would, and records which tabs and buttons are on each
screen. The table is generated, never hand-written, so it can't drift from the app.

    pip install playwright && python scripts/role_walkthrough.py

Needs Chromium (set CHROMIUM=/path/to/chrome if Playwright's own isn't installed).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = tempfile.mkdtemp(prefix="nego-walk-")
os.environ.update(NEGO_HOME=HOME, NEGO_AUTH="dev", NEGO_SCHEDULER="0", PYTHONPATH=str(ROOT))
sys.path.insert(0, str(ROOT))

from negotiation_mcp import appdb  # noqa: E402
from negotiation_mcp.nego import demo, portal, service, store  # noqa: E402

STAFF = (("viewer", "viewer@walk.test"), ("negotiator", "negotiator@walk.test"), ("admin", "admin@example.com"))
ROLES = ("viewer", "negotiator", "admin")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed() -> dict:
    store.init()
    appdb.init()
    demo.seed("walk@x")
    appdb.create_user("viewer@walk.test", "Viola Viewer", "viewer", "walk")
    appdb.create_user("negotiator@walk.test", "Nico Negotiator", "negotiator", "walk")
    cid = 2  # PT Farmasi Sejahtera: items prepared, not yet quoted
    store.set_step(cid, "rfq", "walk")
    for it in [i for i in store.items(cid) if i.get("mou_hna")][:3]:  # a few quoted increases, so findings exist to act on
        store.update_item(cid, it["id"], {"rfq_qty": it["mou_qty"], "rfq_hna": it["mou_hna"] * 1.3, "rfq_disc": it["mou_disc"] or 0}, "walk")
    service.scan(cid)
    return {"cid": cid, "link": portal.create_link(cid, "walk")["path"]}


def texts(page, selector: str) -> list[str]:
    out = page.eval_on_selector_all(selector, "ns => ns.filter(n => n.offsetParent !== null).map(n => n.textContent.replace(/\\s+/g, ' ').trim())")
    return sorted({t for t in out if t and len(t) < 60})


def walk_staff(pw, base: str, ctx: dict) -> dict:
    found: dict[str, dict[str, list[str]]] = {}
    for role, email in STAFF:
        b = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        page = b.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(f"{base}/#signin")
        page.wait_for_selector("#signin-email")
        page.fill("#signin-email", email)
        page.keyboard.press("Enter")
        page.wait_for_selector("#apptabs a", state="visible")
        screens: dict[str, list[str]] = {}
        screens["Top navigation"] = texts(page, "#apptabs a")
        page.goto(f"{base}/#nego"); page.wait_for_timeout(900)
        screens["Negotiations list: buttons"] = texts(page, "#nego button.act, #nego a.act")
        cid = ctx["cid"]
        page.goto(f"{base}/#nego/{cid}"); page.wait_for_timeout(1200)
        screens["A negotiation: tabs"] = texts(page, ".subtabs a[data-tab]")
        screens["A negotiation: buttons"] = texts(page, "#nego button.act, #nego a.act")
        page.goto(f"{base}/#nego/{cid}/findings"); page.wait_for_timeout(1200)
        screens["Findings: buttons"] = texts(page, "#ng-body button.act")
        page.goto(f"{base}/#nego/{cid}/brands"); page.wait_for_timeout(900)
        screens["Brands: buttons"] = texts(page, "#ng-body button.act")
        page.goto(f"{base}/#nego/{cid}/negotiate"); page.wait_for_timeout(1500)
        screens["Negotiate: buttons"] = texts(page, "#ng-body button.act, #ng-body a.act")
        page.goto(f"{base}/#nego/{cid}/files"); page.wait_for_timeout(1200)
        screens["Files & prepare: buttons"] = texts(page, "#ng-body button.act, #ng-body a.act")
        page.goto(f"{base}/#admin/master"); page.wait_for_timeout(1200)
        screens["Admin area: sections"] = texts(page, "#admin-subtabs a") if page.is_visible("#admin-subtabs") else []
        screens["Master data: what can be done"] = (["Read items and groups"] if page.locator(".mtable").count() else []) \
            + (["Edit generic name, group, tags"] if page.locator(".mtable input:not([type=checkbox])").count() else []) \
            + (["Select rows for bulk labelling"] if page.locator(".mtable tbody input[type=checkbox]").count() else []) \
            + texts(page, "#mchips ~ a.act, #mchips ~ label.act")
        screens["Opening #admin lands on"] = [page.evaluate("location.hash")]
        found[role] = screens
        b.close()
    return found


def walk_vendor(pw, base: str, link: str, step: str, cid: int) -> dict[str, list[str]]:
    store.set_step(cid, step, "walk")
    link = portal.create_link(cid, "walk")["path"]
    b = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
    page = b.new_page(viewport={"width": 1400, "height": 1000})
    page.goto(base + link)
    page.wait_for_selector(".trow, .sku, .docrow", timeout=15000)
    out = {
        "Headings": texts(page, "h1, .eyebrow"),
        "Table columns": texts(page, ".thead span"),
        "Buttons": texts(page, "#app button.pbtn, #app a.pbtn, .sendbar button, #helpbtn"),
        "Language": [page.evaluate("document.documentElement.lang")],
    }
    page.goto(f"{base}/#nego"); page.wait_for_timeout(800)
    out["Opening the staff app with that link lands on"] = [page.evaluate("location.hash"), "staff tabs visible" if page.is_visible("#apptabs a") else "no staff tabs"]
    staff_api = page.evaluate("fetch('/api/nego/principals').then(r => r.status)")
    out["Staff data API answers with status"] = [str(staff_api)]
    b.close()
    return out


def table(screens: dict[str, dict[str, list[str]]]) -> str:
    lines = []
    for screen in next(iter(screens.values())):
        labels = sorted({x for r in ROLES for x in screens[r].get(screen, [])})
        lines += [f"### {screen}", ""]
        if not labels:
            lines += ["_Nothing shown to any role._", ""]
            continue
        lines += ["| Shown | Viewer | Negotiator | Admin |", "|---|:-:|:-:|:-:|"]
        for x in labels:
            lines.append(f"| {x} | " + " | ".join("✓" if x in screens[r].get(screen, []) else "—" for r in ROLES) + " |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    from playwright.sync_api import sync_playwright

    ctx = seed()
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "negotiation_mcp.dashboard", "--port", str(port)], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=1)
                break
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(0.25)
        with sync_playwright() as pw:
            staff = walk_staff(pw, base, ctx)
            vendor = {s: walk_vendor(pw, base, ctx["link"], s, ctx["cid"]) for s in ("rfq", "feedback1")}
    finally:
        server.terminate()
    doc = ["# What each role sees", "",
           "Generated by `scripts/role_walkthrough.py` from the running app on sample data; do not edit by hand.",
           "`tests/test_role_matrix.py` is the executable version of the access rules (every route, every role).", "",
           "Viewer: reads everything and updates renewal progress. Negotiator: also runs a negotiation. Admin: also master data, "
           "settings and people. Vendors have no account and see only their own link (below).", "",
           "## Staff", "", table(staff), "## Vendor (principal link)", ""]
    for step, rows in vendor.items():
        doc += [f"### Step: {step}", ""] + [f"- **{k}:** {', '.join(v) if v else '—'}" for k, v in rows.items()] + [""]
    out = ROOT / "docs" / "ROLES.md"
    out.write_text("\n".join(doc), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
