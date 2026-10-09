"""Brand comparison: items with the same generic name or group, side by side per piece."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.dashboard.app import create_app
from negotiation_mcp.nego import anomalies, groups, master, service, store

H = {"X-Requested-With": "nego"}
PPN = 0.11


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    store.init()
    appdb.init()
    return tmp_path


def item(code, name, brand, hna, qty=10, disc=0.0, vol=100, status="Active"):
    return {"erp_code": code, "item_name": name, "brand": brand, "item_status": status, "mou_qty": qty, "mou_hna": hna, "mou_disc": disc,
            "rfq_qty": qty, "rfq_hna": hna, "rfq_disc": disc, "po_qty_12m": vol, "po_unit_text": f"BOX@{qty}"}


def cycle(rows, name="PT Brand Uji"):
    p = store.create_principal({"name": name}, "a@x")
    c = store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    store.add_items(c["id"], rows, "a@x")
    return c


def per_pc(hna, qty=10, disc=0.0):
    return hna / qty * (1 - disc) * (1 + PPN)


def test_cheapest_brand_gap_and_yearly_cost(ws):
    c = cycle([item("P1", "Paracetamol 500 A", "A", 10_000), item("P2", "Paracetamol 500 B", "B", 15_000, vol=200),
               item("P3", "Paracetamol 500 C", "C", 11_000), item("I1", "Ibuprofen", "D", 20_000)])
    for code in ("P1", "P2", "P3"):
        master.update(code, {"generic_name": "Paracetamol 500 mg"}, "n@x")
    r = groups.analyse(store.items(c["id"]), PPN, 0.20)
    assert len(r["groups"]) == 1 and (r["grouped_items"], r["ungrouped_items"]) == (3, 1)
    g = r["groups"][0]
    assert g["label"] == "Paracetamol 500 mg" and g["cheapest"]["erp_code"] == "P1" and g["flagged"] and r["flagged"] == 1
    assert [m["erp_code"] for m in g["members"]] == ["P1", "P3", "P2"]
    b = g["members"][2]
    assert b["price"] == pytest.approx(per_pc(15_000)) and b["gap"] == pytest.approx(0.5)
    # 200 boxes of 10 pieces, each piece dearer by the gap
    assert b["extra_cost"] == pytest.approx((per_pc(15_000) - per_pc(10_000)) * 200 * 10)
    assert g["potential_saving"] == pytest.approx(b["extra_cost"] + g["members"][1]["extra_cost"])
    assert r["potential_saving"] == pytest.approx(g["potential_saving"])


def test_group_key_beats_generic_name_and_small_groups_and_discontinued_are_left_out(ws):
    c = cycle([item("A1", "X tab A", "A", 10_000), item("A2", "X tab B", "B", 30_000), item("A3", "Y old", "C", 1_000, status="Discontinue"),
               item("A4", "Lonely", "D", 5_000)])
    master.update("A1", {"generic_name": "Different generic", "group_key": "Shared Group"}, "n@x")
    master.update("A2", {"generic_name": "shared  group"}, "n@x")  # key ignores case and spaces; no group key of its own
    master.update("A3", {"generic_name": "Shared Group"}, "n@x")
    master.update("A4", {"generic_name": "Only one"}, "n@x")
    r = groups.analyse(store.items(c["id"]), PPN, 0.20)
    assert [g["label"] for g in r["groups"]] == ["Shared Group"]
    assert {m["erp_code"] for m in r["groups"][0]["members"]} == {"A1", "A2"}


def test_threshold_decides_flagged(ws):
    c = cycle([item("T1", "T a", "A", 10_000), item("T2", "T b", "B", 11_000)])
    master.bulk_update(["T1", "T2"], "n@x", group_key="T group")
    assert groups.analyse(store.items(c["id"]), PPN, 0.20)["flagged"] == 0
    assert groups.analyse(store.items(c["id"]), PPN, 0.05)["flagged"] == 1


def test_scan_flags_only_the_dearer_items(ws):
    c = cycle([item("S1", "S a", "A", 10_000), item("S2", "S b", "B", 14_000), item("S3", "S c", "C", 10_500)])
    master.bulk_update(["S1", "S2", "S3"], "n@x", generic_name="S generic")
    service.scan(c["id"])
    found = store.anomalies(c["id"])
    flagged = [a for a in found if a["rule"] == "above_equivalent"]
    by_code = {i["id"]: i["erp_code"] for i in store.items(c["id"])}
    assert [by_code[a["item_id"]] for a in flagged] == ["S2"]
    assert "above the cheapest equivalent" in flagged[0]["message"] and "S a" in flagged[0]["message"]
    assert anomalies.rule_label("above_equivalent") == "Above cheapest equivalent brand"


def test_unlabelled_items_raise_nothing(ws):
    c = cycle([item("U1", "U a", "A", 10_000), item("U2", "U b", "B", 40_000)])
    service.scan(c["id"])
    assert not [a for a in store.anomalies(c["id"]) if a["rule"] == "above_equivalent"]
    assert groups.analyse(store.items(c["id"]), PPN, 0.2)["ungrouped_items"] == 2


def test_route_is_readable_by_every_signed_in_role_and_not_signed_out(ws):
    c = cycle([item("R1", "R a", "A", 10_000), item("R2", "R b", "B", 20_000)])
    master.bulk_update(["R1", "R2"], "n@x", group_key="R group")
    appdb.create_user("vera@example.com", "Vera", "viewer", "t")
    app = create_app()
    assert TestClient(app).get(f"/api/nego/cycles/{c['id']}/groups").status_code == 401
    for email in ("vera@example.com", "admin@example.com"):
        cl = TestClient(app)
        cl.post("/api/auth/signin", json={"email": email}, headers=H)
        r = cl.get(f"/api/nego/cycles/{c['id']}/groups")
        assert r.status_code == 200 and r.json()["flagged"] == 1 and r.json()["groups"][0]["cheapest"]["erp_code"] == "R1"
        assert cl.get("/api/nego/cycles/9999/groups").status_code == 404
