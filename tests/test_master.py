"""Item master data: auto-registration, labelling, bulk labelling, Excel import/export, access."""

from __future__ import annotations

import io

import openpyxl
import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.dashboard.app import create_app
from negotiation_mcp.nego import master, store

H = {"X-Requested-With": "nego"}


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    store.init()
    appdb.init()
    return tmp_path


def open_cycle(rows, name="PT Master Uji"):
    p = store.create_principal({"name": name}, "a@x")
    c = store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    store.add_items(c["id"], rows, "a@x")
    return c


def sheet(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_new_item_codes_are_registered_once_as_new(ws):
    open_cycle([{"erp_code": "P001", "item_name": "Paracetamol 500mg A", "brand": "A", "po_unit_text": "BOX"},
                {"erp_code": "P002", "item_name": "Paracetamol 500mg B", "brand": "B"}, {"erp_code": "", "item_name": "No code"}])
    open_cycle([{"erp_code": "p001", "item_name": "Paracetamol again"}], name="PT Dua")  # same code, other case
    r = master.list_items("all")
    assert r["total"] == 2 and r["counts"]["new"] == 2 and r["counts"]["unlabelled"] == 2
    first = master.get("P001")
    assert first["item_name"] == "Paracetamol 500mg A" and first["brand"] == "A" and first["uom"] == "BOX" and first["status"] == "new"


def test_existing_cycle_items_are_backfilled_on_init(ws):
    open_cycle([{"erp_code": "X1", "item_name": "Old item"}])
    with store.connect() as con:
        con.execute("DELETE FROM item_master")
    assert master.counts()["all"] == 0
    store.init()
    assert master.get("X1")["item_name"] == "Old item"


def test_labelling_marks_reviewed_and_filters(ws):
    open_cycle([{"erp_code": "P001", "item_name": "Paracetamol A"}, {"erp_code": "P002", "item_name": "Paracetamol B"},
                {"erp_code": "I001", "item_name": "Ibuprofen"}])
    res = master.update("P001", {"generic_name": "  Paracetamol 500 mg ", "tags": "analgesic; Fornas,analgesic"}, "n@x")
    assert res["item"]["generic_name"] == "Paracetamol 500 mg" and res["item"]["tags"] == "analgesic, Fornas"
    assert res["item"]["status"] == "reviewed" and "generic_name" in res["changed"]
    assert master.update("P001", {"generic_name": "Paracetamol 500 mg"}, "n@x")["changed"] == {}  # no-op
    assert [i["erp_code"] for i in master.list_items("unlabelled")["items"]] == ["I001", "P002"]
    assert master.list_items("new")["total"] == 2
    assert [i["erp_code"] for i in master.list_items("all", "fornas")["items"]] == ["P001"]
    assert master.counts()["groups"] == 1
    with pytest.raises(Exception, match="can't be changed"):
        master.update("P001", {"first_seen": "x"}, "n@x")


def test_bulk_label_adds_tags_without_losing_old_ones(ws):
    open_cycle([{"erp_code": f"P00{i}", "item_name": f"Paracetamol {i}"} for i in range(1, 4)])
    master.update("P001", {"tags": "analgesic"}, "n@x")
    r = master.bulk_update(["P001", "P002", "NOPE"], "n@x", group_key="Paracetamol 500 mg", tags_add="Fornas")
    assert r == {"updated": 2, "selected": 3}
    assert master.get("P001")["tags"] == "analgesic, Fornas" and master.get("P002")["group_key"] == "Paracetamol 500 mg"
    assert master.get("P002")["status"] == "reviewed" and master.get("P003")["status"] == "new"
    with pytest.raises(Exception, match="Choose what to set"):
        master.bulk_update(["P001"], "n@x")


def test_import_reads_any_headers_and_never_blanks_a_label(ws):
    open_cycle([{"erp_code": "P001", "item_name": "Paracetamol A"}, {"erp_code": "P002", "item_name": "Paracetamol B"}])
    master.update("P002", {"generic_name": "Paracetamol 500 mg"}, "n@x")
    data = sheet(["Kode Barang", "Nama Generik", "Kelompok", "Label"],
                 [["P001", "Paracetamol 500 mg", "Analgesik", "fornas"], ["P002", None, None, None], [1001.0, "Ibuprofen 400 mg", None, None],
                  [None, "skip me", None, None], ["P001", "dup", None, None]])
    preview = master.import_file("master.xlsx", data, False, "a@x")
    assert preview["mapping"]["erp_code"] == "Kode Barang" and preview["mapping"]["generic_name"] == "Nama Generik"
    assert (preview["new"], preview["changed"], preview["unchanged"], preview["skipped"]) == (1, 1, 1, 2)
    assert master.get("1001") is None  # a preview writes nothing
    master.import_file("master.xlsx", data, True, "a@x")
    assert master.get("P001")["group_key"] == "Analgesik" and master.get("P001")["status"] == "reviewed"
    assert master.get("P002")["generic_name"] == "Paracetamol 500 mg"  # blank cell kept the label
    assert master.get("1001")["generic_name"] == "Ibuprofen 400 mg" and master.get("1001")["source"] == "import"


def test_import_needs_an_item_code_column(ws):
    with pytest.raises(Exception, match="Couldn't find these columns"):
        master.import_file("x.xlsx", sheet(["Name", "Colour"], [["a", "b"]]), False, "a@x")


def test_export_round_trips_through_import(ws):
    open_cycle([{"erp_code": "P001", "item_name": "Paracetamol A"}])
    master.update("P001", {"generic_name": "Paracetamol", "tags": "x"}, "n@x")
    blob = master.export_xlsx()
    assert openpyxl.load_workbook(io.BytesIO(blob)).active["C2"].value == "Paracetamol"
    again = master.import_file("item_master.xlsx", blob, False, "a@x")
    assert (again["new"], again["changed"], again["unchanged"]) == (0, 0, 1)


def test_routes_negotiators_read_admins_change(ws):
    open_cycle([{"erp_code": "P001", "item_name": "Paracetamol A"}])
    appdb.create_user("nina@example.com", "Nina", "negotiator", "t")
    app = create_app()
    nina = TestClient(app)
    nina.post("/api/auth/signin", json={"email": "nina@example.com"}, headers=H)
    # a negotiator may read master data but not change it
    for path in ("/api/admin/master/overview", "/api/admin/master/items", "/api/admin/master/items.xlsx"):
        assert nina.get(path, headers=H).status_code == 200, path
    for method, path in [("patch", "/api/admin/master/items/P001"), ("post", "/api/admin/master/items/bulk"), ("post", "/api/admin/master/items/import")]:
        assert getattr(nina, method)(path, headers=H, json={}).status_code == 403, path
    admin = TestClient(app)
    admin.post("/api/auth/signin", json={"email": "admin@example.com"}, headers=H)
    ov = admin.get("/api/admin/master/overview").json()
    assert {s["key"] for s in ov["sets"]} == {"items", "principals", "negotiations", "benchmarks", "price_data"}
    assert ov["sets"][0]["attention"] == 1
    r = admin.patch("/api/admin/master/items/P001", json={"changes": {"generic_name": "Paracetamol"}}, headers=H)
    assert r.status_code == 200 and r.json()["item"]["status"] == "reviewed"
    assert admin.patch("/api/admin/master/items/NOPE", json={"changes": {"brand": "x"}}, headers=H).status_code == 404
    assert admin.patch("/api/admin/master/items/P001", json={}, headers=H).status_code == 400
    assert admin.post("/api/admin/master/items/bulk", json={"erp_codes": ["P001"], "tags_add": ["a", "b"]}, headers=H).json()["updated"] == 1
    assert master.get("P001")["tags"] == "a, b"
    files = {"file": ("m.xlsx", sheet(["ERP Code", "Generic"], [["P009", "Ibuprofen"]]))}
    assert admin.post("/api/admin/master/items/import", files=files, data={"apply": "1"}, headers=H).json()["new"] == 1
    assert admin.get("/api/admin/master/items?filter=new").json()["total"] == 0  # both labelled, so both reviewed
    assert admin.get("/api/admin/master/items.xlsx").headers["content-type"].startswith("application/vnd.openxmlformats")
    assert any(a["action"].startswith("master.") for a in appdb.audit_log())
