"""Principal negotiation cycles: UOM, template formula, anomaly rules, prepare,
Template_Nego round trip, and the routes with their roles."""

from __future__ import annotations

import io

import openpyxl
import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.nego import anomalies as A
from negotiation_mcp.nego import model as M
from negotiation_mcp.nego import prepare as P
from negotiation_mcp.nego import service, store, template_io, uom
from negotiation_mcp.nego.impact import enrich

H = {"X-Requested-With": "nego"}


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """A fresh workspace (nego.db, app.db, settings) for each test."""
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    store.init()
    appdb.init()
    return tmp_path


# ---------------------------------------------------------------- units and formula

@pytest.mark.parametrize("text,qty,container,base", [
    ("BOX@50", 50, "BOX", "PCS"),
    ("BX 10 VIAL", 10, "BOX", "VIAL"),
    ("Box isi 100", 100, "BOX", "PCS"),
    ("1 BOX = 100 PCS", 100, "BOX", "PCS"),
    ("STRIP 10 TAB", 10, "STRIP", "TAB"),
    ("PACK/12", 12, "PACK", "PCS"),
    ("AMP", 1, None, "AMP"),
    ("pcs", 1, None, "PCS"),
    ("BOX@1.000", 1000, "BOX", "PCS"),
])
def test_uom_parse(text, qty, container, base):
    u = uom.parse(text)
    assert (u.qty, u.container, u.base) == (qty, container, base)
    assert u.confidence == 1.0


def test_uom_unclear_and_hint():
    assert uom.parse("BOX").confidence == 0.0 and uom.parse("BOX").qty is None
    assert uom.parse("BOX", qty_hint=50).qty == 50
    assert uom.parse("BOX@100", qty_hint=50).confidence == 0.6  # disagreement lowers confidence


def test_pack_multiple():
    assert uom.pack_multiple(9.6) == 10
    assert uom.pack_multiple(0.0102) == 100
    assert uom.pack_multiple(1.3) is None


def test_unit_price_matches_template_formula():
    # =IFERROR(IF(H8="","",H8/G8*(1-I8)*1.11),"")
    assert M.unit_price(500_000, 50, 0.2) == pytest.approx(500_000 / 50 * 0.8 * 1.11)
    assert M.unit_price(None, 50, 0.2) is None
    assert M.unit_price(500_000, 0, 0.2) is None
    item = {"mou_hna": 100_000, "mou_qty": 10, "mou_disc": 0.1, "rfq_hna": 110_000, "rfq_qty": 10, "rfq_disc": 0.1,
            "co_disc": 0.2, "fb1_disc": None, "on_disc": 0.18}
    p = M.priced(item)
    assert p["co_unit_price"] == pytest.approx(110_000 / 10 * 0.8 * 1.11)  # CO prices the RFQ HNA
    assert p["fb1_unit_price"] is None
    assert M.latest_price(p) == ("on", pytest.approx(110_000 / 10 * 0.82 * 1.11))


# ---------------------------------------------------------------- anomaly rules

def item(i, **kw):
    base = {"id": i, "erp_code": f"E{i}", "item_name": f"Item {i}", "item_status": "Active", "mou_qty": 10, "mou_hna": 100_000,
            "mou_disc": 0.1, "rfq_qty": 10, "rfq_hna": 102_000, "rfq_disc": 0.1}
    base.update(kw)
    return base


def rules(findings, item_id=None):
    return {f["rule"] for f in findings if item_id is None or f["item_id"] == item_id}


def test_scan_rules():
    items = [item(i) for i in range(1, 9)] + [
        item(20, rfq_disc=15),                         # typed 15 instead of 15%
        item(21, rfq_qty=1),                           # HNA per box, qty per piece: 10x
        item(22, rfq_qty=20, rfq_hna=210_000),         # pack changed
        item(23, rfq_hna=140_000),                     # big increase
        item(24, erp_code="E1"),                       # duplicate of item 1
        item(25, item_status="Discontinue"),           # discontinued but priced
        item(26, rfq_hna=None, rfq_qty=None, rfq_disc=None),  # no RFQ price
        item(27, po_unit_text="BOX@20"),               # PO unit disagrees with pack 10
        item(28, rfq_hna=60_000),                      # big decrease
    ]
    f = A.scan(items, step="counter_offer")
    assert "disc_whole:rfq_disc" in rules(f, 20)
    fix = next(x for x in f if x["rule"] == "disc_whole:rfq_disc")
    assert fix["field"] == "rfq_disc" and fix["suggestion"] == pytest.approx(0.15)
    assert "pack_multiple" in rules(f, 21) and "price_increase" not in rules(f, 21)
    assert "pack_changed" in rules(f, 22)
    inc = next(x for x in f if x["rule"] == "price_increase" and x["item_id"] == 23)
    # the suggested CO discount brings the CO price back to the MOU price
    held = M.unit_price(140_000, 10, inc["suggestion"])
    assert held == pytest.approx(M.unit_price(100_000, 10, 0.1))
    assert inc["field"] == "co_disc"
    assert "extreme_change" in rules(f, 23) and "extreme_change" in rules(f, 28)
    assert "duplicate_erp" in rules(f, 1) and "duplicate_erp" in rules(f, 24)
    assert "discontinued_priced" in rules(f, 25)
    assert "rfq_missing" in rules(f, 26)
    assert "po_unit_mismatch" in rules(f, 27)
    # before the RFQ is back, a missing RFQ price is not a finding
    assert "rfq_missing" not in rules(A.scan(items, step="rfq"), 26)


def test_no_increase_rule_uses_online_nego_field_late():
    f = A.scan([item(1, rfq_hna=120_000, on_disc=0.12)], step="online_nego")
    inc = next(x for x in f if x["rule"] == "price_increase")
    assert inc["field"] == "on_disc"


def test_impact_per_piece_and_volume():
    it = enrich(item(1, po_unit_text="BOX@10", po_qty_12m=30, last_po_price=90_000))
    assert it["annual_pcs"] == 300
    assert it["impact"] == pytest.approx((it["rfq_unit_price"] - it["mou_unit_price"]) * 300)


# ---------------------------------------------------------------- store: decisions survive rescans

def test_decisions_survive_rescan(ws):
    p = store.create_principal({"name": "PT Uji"}, "a@x")
    c = store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    store.add_items(c["id"], [{k: v for k, v in item(0, rfq_hna=150_000).items() if k != "id"},
                              {k: v for k, v in item(0, erp_code="E9", rfq_disc=15).items() if k != "id"}], "a@x")
    service.set_step(c["id"], "counter_offer", "a@x")
    open_ = store.anomalies(c["id"], "open")
    inc = next(a for a in open_ if a["rule"] == "price_increase")
    with pytest.raises(Exception):
        store.decide_anomaly(c["id"], inc["id"], "kept", "", "a@x")  # a reason is required
    service.decide(c["id"], inc["id"], "keep", "Raw material cost, agreed by category head", "a@x")
    slip = next(a for a in open_ if a["rule"] == "disc_whole:rfq_disc")
    service.decide(c["id"], slip["id"], "fix", "", "a@x")  # applies 15 -> 0.15
    service.scan(c["id"])
    after = {a["rule"]: a for a in store.anomalies(c["id"], None)}
    assert after["price_increase"]["status"] == "kept"
    assert after["disc_whole:rfq_disc"]["status"] == "fixed"
    fixed_item = store.get_item(c["id"], slip["item_id"])
    assert fixed_item["rfq_disc"] == pytest.approx(0.15)
    assert store.item_history(slip["item_id"])[0]["via"].startswith("anomaly:")


def test_one_open_cycle_per_principal(ws):
    p = store.create_principal({"name": "PT Satu"}, "a@x")
    store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    with pytest.raises(Exception, match="already has an open negotiation"):
        store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")


# ---------------------------------------------------------------- prepare

PO_CSV = """PO Date,Vendor Name,Item Number,Item Description,Purch Unit,Quantity,Unit Price,Line Amount
15/09/2026,PT ALFA MEDIKA,A1,Spuit 3cc,BOX@100,10,150000,1500000
15/08/2026,PT Alfa Medika,A1,Spuit 3cc,BOX@100,5,151000,755000
15/08/2026,PT Alfa Medika,A2,Kasa 16x16,PAK 10,20,60000,1200000
15/01/2025,PT Alfa Medika,A3,Old item,PCS,1,1000,1000
15/08/2026,PT Beta,B1,Not ours,PCS,1,1000,1000
"""
FORMULARY_CSV = """Kode Barang,Nama Barang,Principal,Status
A1,Spuit 3cc,PT Alfa Medika,Active
A9,Formulary only,PT Alfa Medika,Active
"""
MOU_CSV = """Principal,ERP Code,Item Name,MOU_Qty/PO Unit,MOU_HNA/PO Unit (excl. PPN),MOU_Disc%
PT Alfa Medika,A1,Spuit 3cc,100,160000,0.05
"""


def test_build_items_from_exports():
    po, mapping = P.read_upload("po", "po.csv", PO_CSV.encode())
    assert mapping["erp_code"] == "Item Number" and mapping["po_unit_text"] == "Purch Unit"
    form, _ = P.read_upload("formulary", "f.csv", FORMULARY_CSV.encode())
    mou, _ = P.read_upload("mou", "m.csv", MOU_CSV.encode())
    items, summ = P.build_items("PT Alfa Medika", po, form, mou)
    by = {i["erp_code"]: i for i in items}
    assert set(by) == {"A1", "A2", "A9"}  # A3 is older than 12 months, B1 is another principal
    assert by["A1"]["po_qty_12m"] == 15 and by["A1"]["last_po_price"] == 150000
    assert by["A1"]["mou_hna"] == 160000 and by["A1"]["source"] == "po"
    assert by["A9"]["source"] == "formulary"
    assert summ["po"]["skipped_older"] == 1 and summ["po"]["other_principals_rows"] == 1
    assert [i["item_name"] for i in items] == sorted((i["item_name"] for i in items), key=str.lower)


def test_prepare_rejects_wrong_principal():
    po, _ = P.read_upload("po", "po.csv", PO_CSV.encode())
    with pytest.raises(Exception, match="no rows for"):
        P.build_items("PT Gamma", po)


# ---------------------------------------------------------------- Template_Nego round trip

def test_template_export_grows_and_reads_back(ws):
    p = store.create_principal({"name": "PT Banyak", "distributor": "PT Dist"}, "a@x")
    c = store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    rows = [{"erp_code": f"X{i:03d}", "item_name": f"Item {i:03d}", "mou_qty": 10, "mou_hna": 1000 + i, "mou_disc": 0.1}
            for i in range(160)]
    store.add_items(c["id"], rows, "a@x")
    name, data = service.export_xlsx(c["id"])
    assert name.startswith("Template_Nego_PT_Banyak")
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws_ = wb["Form"]
    assert ws_["A1"].value == "Contract Period: 01 Januari 2027  -  31 Desember 2029"
    assert ws_["B4"].value == "PT Banyak" and ws_["B5"].value == "PT Dist"
    last = 8 + 160 - 1
    assert ws_[f"A{last}"].value == "X159"
    assert ws_[f"J{last}"].value == f'=IFERROR(IF(H{last}="","",H{last}/G{last}*(1-I{last})*1.11),"")'
    assert ws_[f"P{last}"].value == f'=IFERROR(IF($L{last}="","",$L{last}/$K{last}*(1-O{last})*1.11),"")'
    assert ws_[f"K{last}"].fill.fgColor.rgb == ws_["K8"].fill.fgColor.rgb
    assert any(str(d.sqref).endswith(f"E{last}") for d in ws_.data_validations.dataValidation)
    assert "A8:T167" in [str(cf.sqref) for cf in ws_.conditional_formatting]

    # the principal fills RFQ for two rows; a blank cell never erases
    ws_["K8"], ws_["L8"], ws_["M8"] = 10, 1100, 0.1
    ws_["E9"], ws_["C9"] = "Discontinue", "Brand Z"
    ws_["H10"] = None  # cleared in the file, kept in the app
    ws_[f"A{last + 1}"], ws_[f"B{last + 1}"] = "NEW1", "Not in this negotiation"
    buf = io.BytesIO()
    wb.save(buf)
    plan = service.import_template(c["id"], buf.getvalue(), "principal", apply=False)
    assert plan["matched"] == 160 and plan["unmatched_count"] == 1
    fields = {(ch["erp_code"], ch["field"]) for ch in plan["changes"]}
    assert fields == {("X000", "rfq_qty"), ("X000", "rfq_hna"), ("X000", "rfq_disc"), ("X001", "item_status"), ("X001", "brand")}
    res = service.import_template(c["id"], buf.getvalue(), "principal", apply=True)
    assert res["applied_items"] == 2
    got = {i["erp_code"]: i for i in store.items(c["id"])}
    assert got["X000"]["rfq_hna"] == 1100 and got["X001"]["item_status"] == "Discontinue"
    assert got["X002"]["mou_hna"] == 1002


def test_import_refuses_another_principals_file(ws):
    a = store.create_principal({"name": "PT Pertama"}, "a@x")
    b = store.create_principal({"name": "PT Kedua"}, "a@x")
    ca = store.create_cycle(a["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    cb = store.create_cycle(b["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    store.add_items(ca["id"], [{"erp_code": "Q1", "item_name": "Q"}], "a@x")
    store.add_items(cb["id"], [{"erp_code": "Q1", "item_name": "Q"}], "a@x")
    _, data = service.export_xlsx(ca["id"])
    with pytest.raises(Exception, match="not PT Kedua"):
        service.import_template(cb["id"], data, "a@x", apply=True)


def test_last_template_becomes_current_mou():
    row = {"_row": 8, "erp_code": "A", "item_name": "A", "rfq_qty": 10, "rfq_hna": 5000, "rfq_disc": 0.1, "co_disc": 0.2,
           "on_disc": 0.15, "mou_hna": 1}
    assert service.mou_from_template_row(row) == {"erp_code": "A", "item_name": "A", "mou_qty": 10, "mou_hna": 5000,
                                                  "mou_disc": 0.15}


# ---------------------------------------------------------------- routes and roles

@pytest.fixture()
def clients(ws):
    admin = TestClient(__import__("negotiation_mcp.dashboard.app", fromlist=["create_app"]).create_app())
    assert admin.post("/api/auth/signin", json={"email": "admin@example.com"}, headers=H).status_code == 200
    appdb.create_user("viewer@example.com", "Vera Viewer", "viewer", "admin@example.com")
    viewer = TestClient(admin.app)
    assert viewer.post("/api/auth/signin", json={"email": "viewer@example.com"}, headers=H).status_code == 200
    return admin, viewer


def test_routes_and_roles(clients):
    admin, viewer = clients
    assert viewer.post("/api/admin/nego/principals", json={"name": "PT X"}, headers=H).status_code == 403
    r = admin.post("/api/admin/nego/principals", json={"name": "PT Alfa Medika", "mou_end": "2027-01-31"}, headers=H)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    lst = viewer.get("/api/nego/principals").json()
    assert lst["principals"][0]["name"] == "PT Alfa Medika"
    c = admin.post("/api/admin/nego/cycles", json={"principal_id": pid, "contract_start": "2027-02-01",
                                                   "contract_end": "2030-01-31"}, headers=H).json()
    cid = c["id"]
    files = {"po": ("po.csv", PO_CSV.encode()), "formulary": ("f.csv", FORMULARY_CSV.encode()), "mou": ("m.csv", MOU_CSV.encode())}
    assert viewer.post(f"/api/admin/nego/cycles/{cid}/prepare", files=files, headers=H).status_code == 403
    r = admin.post(f"/api/admin/nego/cycles/{cid}/prepare", files=files, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["items"] == 3
    page = viewer.get(f"/api/nego/cycles/{cid}/items?limit=2").json()
    assert page["total"] == 3 and len(page["items"]) == 2
    ov = viewer.get(f"/api/nego/cycles/{cid}").json()
    assert ov["kpis"]["items"] == 3 and ov["cycle"]["current_step"] == "prepare"
    x = viewer.get(f"/api/nego/cycles/{cid}/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    iid = page["items"][0]["id"]
    assert viewer.patch(f"/api/admin/nego/cycles/{cid}/items/{iid}", json={"changes": {"rfq_disc": 0.1}}, headers=H).status_code == 403
    r = admin.patch(f"/api/admin/nego/cycles/{cid}/items/{iid}", json={"changes": {"rfq_disc": "12%", "rfq_qty": 10, "rfq_hna": "1.500"}}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["item"]["rfq_disc"] == pytest.approx(0.12) and r.json()["item"]["rfq_hna"] == 1500
    assert admin.patch(f"/api/admin/nego/cycles/{cid}/items/{iid}", json={"changes": {"erp_code_x": 1}}, headers=H).status_code == 400
    r = admin.post(f"/api/admin/nego/cycles/{cid}/step", json={"step": "rfq"}, headers=H)
    assert r.json()["current_step"] == "rfq"
    assert admin.post(f"/api/admin/nego/cycles/{cid}/step", json={"step": "nope"}, headers=H).status_code == 400
    assert viewer.get("/api/nego/cycles/999").status_code == 404
    assert admin.post("/api/admin/nego/principals", json={"name": "PT X"}).status_code == 403  # missing app header


def test_sample_loads_end_to_end(clients):
    admin, _ = clients
    r = admin.post("/api/admin/nego/sample", headers=H)
    assert r.status_code == 200, r.text
    first = r.json()["opened"][0]
    ov = admin.get(f"/api/nego/cycles/{first['cycle_id']}").json()
    assert ov["cycle"]["current_step"] == "counter_offer"
    assert ov["kpis"]["rfq_filled"] > 100
    found = admin.get(f"/api/nego/cycles/{first['cycle_id']}/anomalies").json()
    labels = set(found["by_rule"])
    assert {"Discount typed as a whole number", "Price increase", "Largest / smallest change"} <= labels
