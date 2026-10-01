"""Principal portal: checks while filling, what a principal may see and change, links,
sending, and the principal's Excel copy."""

from __future__ import annotations

import io

import openpyxl
import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.nego import checks as C
from negotiation_mcp.nego import portal, service, store, template_io

H = {"X-Requested-With": "nego"}
FORBIDDEN_KEYS = {"po_qty_12m", "po_value_12m", "last_po_price", "impact", "annual_pcs", "anomalies", "flags",
                  "base_price", "po_unit_price", "co_disc", "on_disc", "history"}


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    store.init()
    appdb.init()
    return tmp_path


def make_cycle(name="PT Uji Portal", n=3, step="rfq"):
    p = store.create_principal({"name": name}, "a@x")
    c = store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    rows = [{"erp_code": f"{name[-3:]}{i}", "item_name": f"Item {i}", "item_status": "Active", "mou_qty": 10,
             "mou_hna": 100_000, "mou_disc": 0.1, "po_qty_12m": 50, "po_value_12m": 4_500_000, "last_po_price": 90_000}
            for i in range(n)]
    store.add_items(c["id"], rows, "a@x")
    store.set_step(c["id"], step, "a@x")
    return store.get_cycle(c["id"])


def codes(issues):
    return {(i.level, i.code) for i in issues}


# ---------------------------------------------------------------- checks

BASE = {"item_status": "Active", "mou_qty": 10, "mou_hna": 100_000, "mou_disc": 0.1}


def test_rfq_checks_levels():
    assert codes(C.check_item(dict(BASE), "rfq")) == {("missing", "rfq_missing")}
    same = {**BASE, "rfq_qty": 10, "rfq_hna": 100_000, "rfq_disc": 0.1}
    assert C.check_item(same, "rfq") == []
    assert ("error", "disc_range") in codes(C.check_item({**same, "rfq_disc": 1.5}, "rfq"))
    assert ("error", "qty_whole") in codes(C.check_item({**same, "rfq_qty": 2.5}, "rfq"))
    assert ("error", "hna_zero") in codes(C.check_item({**same, "rfq_hna": 0}, "rfq"))
    assert ("missing", "qty_missing") in codes(C.check_item({**same, "rfq_qty": None}, "rfq"))
    assert codes(C.check_item({**same, "rfq_hna": 110_000}, "rfq")) == {("reason", "increase")}
    assert ("confirm", "pack_multiple") in codes(C.check_item({**same, "rfq_qty": 1}, "rfq"))
    assert ("confirm", "pack_changed") in codes(C.check_item({**same, "rfq_qty": 20, "rfq_hna": 200_000}, "rfq"))
    assert ("confirm", "big_drop") in codes(C.check_item({**same, "rfq_hna": 50_000}, "rfq"))
    assert ("confirm", "disc_high") in codes(C.check_item({**same, "rfq_disc": 0.7, "rfq_hna": 300_000}, "rfq"))
    assert C.check_item({**BASE, "item_status": "Discontinue"}, "rfq") == []


def test_outstanding_needs_reason_and_confirm():
    up = {**BASE, "rfq_qty": 10, "rfq_hna": 110_000, "rfq_disc": 0.1}
    assert [i.code for i in C.outstanding(up, C.check_item(up, "rfq"))] == ["increase"]
    up["price_reason"] = "Kenaikan bahan baku"
    assert C.outstanding(up, C.check_item(up, "rfq")) == []
    pack = {**BASE, "rfq_qty": 20, "rfq_hna": 200_000, "rfq_disc": 0.1}
    assert C.outstanding(pack, C.check_item(pack, "rfq"))
    pack["principal_confirmed"] = "pack_changed"
    assert C.outstanding(pack, C.check_item(pack, "rfq")) == []


def test_identification_and_feedback_checks():
    assert {c for _, c in codes(C.check_item({"item_status": "Active"}, "identification"))} == {"brand_missing", "ref_missing"}
    assert C.check_item({"item_status": "Discontinue"}, "identification") == []
    fb = {"item_status": "Active", "co_disc": 0.2}
    assert codes(C.check_item(fb, "feedback1")) == {("missing", "fb1_missing")}
    assert codes(C.check_item({**fb, "fb1_disc": 0.15}, "feedback1")) == {("reason", "below_co")}
    assert C.check_item({**fb, "fb1_disc": 0.2}, "feedback1") == []


# ---------------------------------------------------------------- portal rules

def test_save_reads_discount_as_percent_and_locks_fields(ws):
    c = make_cycle()
    it = store.items(c["id"])[0]
    for typed, want in (("15", 0.15), ("15%", 0.15), ("15,5", 0.155), (12.5, 0.125)):
        v = portal.save(c, it["id"], {"rfq_disc": typed}, "p")
        assert v["rfq_disc"] == pytest.approx(want)
    v = portal.save(c, it["id"], {"rfq_hna": "1.250.000", "rfq_qty": "10"}, "p")
    assert v["rfq_hna"] == 1_250_000
    with pytest.raises(Exception, match="Not editable"):
        portal.save(c, it["id"], {"co_disc": "10"}, "p")
    with pytest.raises(Exception, match="Not editable"):
        portal.save(c, it["id"], {"mou_hna": "1"}, "p")
    assert not FORBIDDEN_KEYS & set(v)


def test_confirm_is_cleared_when_the_price_changes(ws):
    c = make_cycle()
    it = store.items(c["id"])[0]
    portal.save(c, it["id"], {"rfq_qty": "20", "rfq_hna": "200000", "rfq_disc": "10"}, "p")
    v = portal.save(c, it["id"], {}, "p", confirm="pack_changed")
    assert v["state"] == "done"
    v = portal.save(c, it["id"], {"rfq_hna": "190000"}, "p")
    assert "pack_changed" in v["outstanding"]


def test_fill_and_submit(ws):
    c = make_cycle()
    assert portal.fill_from_reference(c, "p") == 3
    items = store.items(c["id"])
    assert all(i["rfq_hna"] == 100_000 and i["rfq_qty"] == 10 for i in items)
    portal.save(c, items[0]["id"], {"rfq_hna": "120000"}, "p")
    s = portal.summary(c)
    assert not s["can_send"] and s["outstanding"][0]["issues"][0]["code"] == "increase"
    with pytest.raises(Exception, match="perlu dilengkapi"):
        portal.submit(c, "p")
    portal.save(c, items[0]["id"], {"price_reason": "Kurs / nilai tukar — USD naik"}, "p")
    s = portal.submit(c, "p")
    assert s["increase_count"] == 1
    c2 = store.get_cycle(c["id"])
    assert "rfq" in c2["submitted_steps"] and portal.open_step(c2) is None
    with pytest.raises(Exception):
        portal.save(c2, items[0]["id"], {"rfq_hna": "1"}, "p")
    assert any(e["action"] == "principal.submit" for e in store.cycle_events(c["id"]))
    # Siloam sees the reason in its own findings
    inc = [a for a in store.anomalies(c["id"], "open") if a["rule"] == "price_increase"]
    assert inc and "USD naik" in inc[0]["message"]
    # moving back to RFQ opens it again
    c3 = store.set_step(c["id"], "rfq", "a@x")
    assert portal.open_step(c3) == "rfq"


def test_links(ws):
    c = make_cycle()
    link = portal.create_link(c["id"], "a@x", 3)
    token = link["path"].rsplit("/", 1)[1]
    assert portal.resolve(token)["cycle"]["id"] == c["id"]
    assert portal.resolve("nope") is None
    store.revoke_link(c["id"], link["id"], "a@x")
    assert portal.resolve(token) is None
    link2 = portal.create_link(c["id"], "a@x", 3)
    with store.connect() as con:
        con.execute("UPDATE principal_links SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (link2["id"],))
    assert portal.resolve(link2["path"].rsplit("/", 1)[1]) is None
    with store.connect() as con:  # only the hash is stored
        assert not con.execute("SELECT 1 FROM principal_links WHERE token_hash = ?", (token,)).fetchone()


# ---------------------------------------------------------------- Excel for the principal

def test_principal_workbook_is_locked_and_guided(ws):
    c = make_cycle()
    _, data = portal.export(c)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    sh = wb["Form"]
    assert sh.protection.sheet
    assert not sh["K8"].protection.locked and not sh["L8"].protection.locked and not sh["M8"].protection.locked
    assert sh["A8"].protection.locked and sh["H8"].protection.locked and sh["O8"].protection.locked
    assert not sh["V8"].protection.locked and sh["U8"].value.startswith("=IFERROR")
    disc = next(d for d in sh.data_validations.dataValidation if str(d.sqref).startswith("M"))
    assert disc.formula2 == "0.99" and "15%" in disc.prompt
    assert sh["V7"].value.startswith("Alasan")


def test_principal_excel_upload_keeps_good_rows(ws):
    c = make_cycle()
    _, data = portal.export(c)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    sh = wb["Form"]
    sh["K8"], sh["L8"], sh["M8"], sh["V8"] = 10, 105_000, 0.1, "Kenaikan bahan baku"
    sh["K9"], sh["L9"], sh["M9"] = 10, 100_000, 15          # typed 15: read as 15%
    sh["K10"], sh["L10"], sh["M10"] = 10, 100_000, 1.5     # 150%: rejected
    buf = io.BytesIO()
    wb.save(buf)
    r = portal.import_excel(c, buf.getvalue(), "p", apply=True)
    assert r["accepted"] == 2 and len(r["rejected"]) == 1 and r["rejected"][0]["row"] == 10
    assert r["notes"] and "15%" in r["notes"][0]["id"]
    got = {i["erp_code"]: i for i in store.items(c["id"])}
    first, second, third = (got[f"tal{i}"] for i in range(3))
    assert first["price_reason"] == "Kenaikan bahan baku"
    assert second["rfq_disc"] == pytest.approx(0.15)
    assert third["rfq_hna"] is None


def test_admin_template_ignores_reason_column(ws):
    """Siloam's own import still reads the template columns only."""
    c = make_cycle()
    _, data = portal.export(c)
    assert template_io.read_template(data)["rows"]
    plan = service.import_template(c["id"], data, "a@x", apply=False)
    assert all(ch["field"] != "price_reason" for ch in plan["changes"])


# ---------------------------------------------------------------- routes

@pytest.fixture()
def admin(ws):
    from negotiation_mcp.dashboard.app import create_app

    a = TestClient(create_app())
    assert a.post("/api/auth/signin", json={"email": "admin@example.com"}, headers=H).status_code == 200
    return a


def open_portal(admin, cid):
    link = admin.post(f"/api/admin/nego/cycles/{cid}/links", json={"days": 2}, headers=H).json()
    p = TestClient(admin.app)
    r = p.get(link["path"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p"
    p.cookies.set("nego_principal", r.cookies.get("nego_principal"))
    return p, link


def walk(o, keys=set()):
    if isinstance(o, dict):
        for k, v in o.items():
            keys.add(k)
            walk(v, keys)
    elif isinstance(o, list):
        for v in o:
            walk(v, keys)
    return keys


def test_portal_routes_isolated(admin):
    a = make_cycle("PT Satu Portal")
    b = make_cycle("PT Dua Portal")
    pa, link = open_portal(admin, a["id"])
    me = pa.get("/api/p/me").json()
    assert me["principal"]["name"] == "PT Satu Portal" and me["step"] == "rfq"
    page = pa.get("/api/p/items").json()
    assert page["counts"]["all"] == 3
    assert not FORBIDDEN_KEYS & walk(page, set())
    b_item = store.items(b["id"])[0]["id"]
    assert pa.patch(f"/api/p/items/{b_item}", json={"changes": {"rfq_hna": "1"}}, headers=H).status_code == 404
    assert pa.get(f"/api/nego/cycles/{a['id']}").status_code == 401  # no Siloam access
    assert pa.get("/api/nego/principals").status_code == 401
    assert pa.patch(f"/api/p/items/{page['items'][0]['id']}", json={"changes": {"rfq_hna": "1"}}).status_code == 403  # app header
    assert TestClient(admin.app).get("/api/p/me").status_code == 401
    assert pa.post("/api/p/fill", json={}, headers=H).json()["filled"] == 3
    assert pa.post("/api/p/submit", json={}, headers=H).status_code == 200
    ov = admin.get(f"/api/nego/cycles/{a['id']}").json()
    assert "rfq" in ov["cycle"]["submitted_steps"]
    x = pa.get("/api/p/template.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    admin.post(f"/api/admin/nego/cycles/{a['id']}/links/{link['id']}/revoke", headers=H)
    assert pa.get("/api/p/me").status_code == 401
    assert TestClient(admin.app).get("/p/not-a-token", follow_redirects=False).headers["location"] == "/p?invalid=1"


def test_link_routes_admin_only(admin):
    c = make_cycle()
    appdb.create_user("v@example.com", "Vee", "viewer", "admin@example.com")
    v = TestClient(admin.app)
    v.post("/api/auth/signin", json={"email": "v@example.com"}, headers=H)
    assert v.post(f"/api/admin/nego/cycles/{c['id']}/links", json={}, headers=H).status_code == 403
