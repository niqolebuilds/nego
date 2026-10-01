"""Admin data management: upload -> preview -> apply -> rollback, documents, settings."""

from __future__ import annotations

import csv
import io

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp import settings as S
from negotiation_mcp.dashboard.app import create_app
from negotiation_mcp.pricebook import PRICE_HISTORY_COLUMNS, cached_book

H = {"X-Requested-With": "nego"}

NEW_ROW = {
    "date": "2026-08-10", "hospital": "Site Bali", "vendor": "Nova Medika", "sku": "NEW-GLOVE",
    "sku_name": "Nitrile exam gloves M (Nova)", "equivalence_group": "Nitrile gloves M", "quoted_unit": "box of 100",
    "clinical_units_per_quoted_unit": "100", "quantity": "50", "list_price": "", "discount": "",
    "net_price": "70000", "source": "quote", "payment_terms_days": "45", "contract_end": "",
}


def csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=PRICE_HISTORY_COLUMNS)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture()
def admin():
    c = TestClient(create_app())
    assert c.post("/api/auth/signin", json={"email": "admin@example.com"}, headers=H).status_code == 200
    yield c
    c.post("/api/admin/versions/sample/activate", headers=H)  # leave the sample active for other tests


def stage(c, content: bytes, name="prices.csv", kind="price_history"):
    return c.post("/api/admin/uploads", files={"file": (name, content)}, data={"kind": kind}, headers=H)


def test_upload_preview_apply_and_rollback(admin):
    before = len(cached_book().observations)
    r = stage(admin, csv_bytes([NEW_ROW]))
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["status"] == "staged" and up["rows"] == 1
    assert up["new_skus"] == ["NEW-GLOVE"] and up["new_vendors"] == ["Nova Medika"]

    applied = admin.post(f"/api/admin/uploads/{up['id']}/apply", json={"mode": "add"}, headers=H).json()
    assert applied["rows"] == before + 1 and applied["is_sample"]  # added to sample, so still sample
    assert "NEW-GLOVE" in cached_book().skus
    cat = admin.get("/api/catalog").json()
    assert any(s["sku"] == "NEW-GLOVE" for s in cat["skus"])

    vers = admin.get("/api/admin/versions").json()
    assert vers["active"] == applied["version"]
    admin.post("/api/admin/versions/sample/activate", headers=H)
    assert "NEW-GLOVE" not in cached_book().skus
    admin.post(f"/api/admin/versions/{applied['version']}/activate", headers=H)
    assert "NEW-GLOVE" in cached_book().skus


def test_bad_rows_are_named_and_cannot_be_applied(admin):
    bad = dict(NEW_ROW, clinical_units_per_quoted_unit="")
    up = stage(admin, csv_bytes([NEW_ROW, bad])).json()
    assert up["status"] == "invalid"
    assert any("row 3" in e and "D-13" in e for e in up["errors"])
    r = admin.post(f"/api/admin/uploads/{up['id']}/apply", json={"mode": "add"}, headers=H)
    assert r.status_code == 400


def test_missing_columns_are_refused(admin):
    r = stage(admin, b"date,vendor\n2026-01-01,X\n")
    assert r.status_code == 400 and "Missing column" in r.json()["error"]


def test_replace_with_real_data_drops_the_sample_flag(admin):
    up = stage(admin, csv_bytes([NEW_ROW, dict(NEW_ROW, date="2026-07-10")])).json()
    res = admin.post(f"/api/admin/uploads/{up['id']}/apply", json={"mode": "replace"}, headers=H).json()
    assert res["rows"] == 2 and res["is_sample"] is False
    assert cached_book().is_sample is False


def test_xlsx_upload(admin):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(PRICE_HISTORY_COLUMNS)
    ws.append([NEW_ROW[c] for c in PRICE_HISTORY_COLUMNS])
    buf = io.BytesIO()
    wb.save(buf)
    up = stage(admin, buf.getvalue(), name="prices.xlsx").json()
    assert up["status"] == "staged" and up["rows"] == 1


def test_only_csv_or_xlsx(admin):
    r = stage(admin, b"hello", name="prices.txt")
    assert r.status_code == 400


def test_templates_download(admin):
    r = admin.get("/api/admin/templates/price_history")
    assert r.status_code == 200 and r.text.startswith("date,hospital,vendor")


def test_documents_upload_list_download_delete(admin):
    pdf = b"%PDF-1.4\n% test\n"
    r = admin.post("/api/admin/documents", files={"file": ("../../evil name.pdf", pdf)},
                   data={"vendor": "Prima Alkes", "sku": "IVC22-PRI", "note": "2025 contract"}, headers=H)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert "/" not in doc["filename"] and doc["filename"].endswith(".pdf")
    assert any(d["id"] == doc["id"] for d in admin.get("/api/documents", params={"vendor": "Prima Alkes"}).json())
    dl = admin.get(f"/api/documents/{doc['id']}/download")
    assert dl.status_code == 200 and dl.content == pdf and "attachment" in dl.headers["content-disposition"]
    assert admin.delete(f"/api/admin/documents/{doc['id']}", headers=H).status_code == 200
    assert admin.get(f"/api/documents/{doc['id']}/download").status_code == 404


def test_fake_pdf_and_bad_types_refused(admin):
    assert admin.post("/api/admin/documents", files={"file": ("x.pdf", b"not a pdf")}, headers=H).status_code == 400
    assert admin.post("/api/admin/documents", files={"file": ("x.exe", b"MZ")}, headers=H).status_code == 400


def test_settings_change_moves_the_target_and_is_audited(admin):
    q = {"sku": "IVC22-PRI", "vendor": "Prima Alkes"}
    before = admin.get("/api/options", params=q).json()["targets"]["target_price"]
    r = admin.put("/api/admin/settings", json={"changes": {"beat_margin": 0.03}}, headers=H)
    assert r.status_code == 200 and r.json()["changed"] == ["beat_margin"]
    after = admin.get("/api/options", params=q).json()["targets"]["target_price"]
    assert after < before
    assert any(e["action"] == "settings.update" for e in admin.get("/api/admin/activity").json()["events"])
    admin.put("/api/admin/settings", json={"changes": {"beat_margin": 0.01}}, headers=H)


def test_settings_validation(admin):
    assert admin.put("/api/admin/settings", json={"changes": {"beat_margin": 2}}, headers=H).status_code == 400
    assert admin.put("/api/admin/settings", json={"changes": {"nope": 1}}, headers=H).status_code == 400
    assert admin.put("/api/admin/settings", json={"changes": {"renewal_min_days": 200, "renewal_max_days": 100}},
                     headers=H).status_code == 400
    assert S.load()["beat_margin"] == 0.01


def test_invite_user_and_duplicate(admin):
    r = admin.post("/api/admin/users", json={"email": "Nina@Example.com", "name": "Nina", "role": "viewer"}, headers=H)
    assert r.status_code == 200 and r.json()["email"] == "nina@example.com"
    assert admin.post("/api/admin/users", json={"email": "nina@example.com", "name": "N"}, headers=H).status_code == 400
    assert admin.post("/api/admin/users", json={"email": "not-an-email", "name": "N"}, headers=H).status_code == 400
    assert appdb.user_by_email("nina@example.com")["role"] == "viewer"
