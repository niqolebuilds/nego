"""Renewal progress: stages, events, offer check, realised savings, access."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp import intelligence as I
from negotiation_mcp.dashboard.app import create_app

H = {"X-Requested-With": "nego"}


@pytest.fixture(scope="module")
def viewer():
    app = create_app()
    if not appdb.user_by_email("rena@example.com"):
        appdb.create_user("rena@example.com", "Rena", "viewer", "test")
    c = TestClient(app)
    assert c.post("/api/auth/signin", json={"email": "rena@example.com"}, headers=H).status_code == 200
    return c


def first_renewal(c):
    return c.get("/api/pipeline").json()["renewals"][0]


def test_pipeline_has_stages_and_kpis(viewer):
    d = viewer.get("/api/pipeline").json()
    assert [s["key"] for s in d["stages"]][0] == "not_started"
    assert d["renewals"] and all("progress" in r and r["key"] for r in d["renewals"])
    assert d["kpis"]["open"] == len(d["renewals"])


def test_viewer_updates_progress_and_events_are_recorded(viewer):
    r = first_renewal(viewer)
    res = viewer.patch("/api/renewals/progress", json={"key": r["key"], "stage": "negotiating", "owner": "rena@example.com",
                                                        "notes": "Call booked"}, headers=H)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["progress"]["stage"] == "negotiating"
    assert {e["field"] for e in body["events"]} >= {"stage", "owner", "notes"}
    assert all(e["user_email"] == "rena@example.com" for e in body["events"])
    mine = viewer.get("/api/pipeline", params={"mine": "1"}).json()["renewals"]
    assert [m["key"] for m in mine] == [r["key"]]


def test_logging_an_offer_returns_the_next_move(viewer):
    r = first_renewal(viewer)
    high = r["proposed_walk_away"] * 1.05
    res = viewer.patch("/api/renewals/progress", json={"key": r["key"], "latest_offer": high}, headers=H).json()
    assert res["offer_check"]["zone"] == "walk"
    low = r["target_price"] * 0.999
    res = viewer.patch("/api/renewals/progress", json={"key": r["key"], "latest_offer": low}, headers=H).json()
    assert res["offer_check"]["zone"] in ("at_target", "below_stretch")


def test_agreed_price_counts_as_realised_saving(viewer):
    r = first_renewal(viewer)
    agreed = r["contract_price_per_clinical_unit"] * 0.9
    viewer.patch("/api/renewals/progress", json={"key": r["key"], "stage": "agreed", "agreed_price": agreed}, headers=H)
    d = viewer.get("/api/pipeline").json()
    got = next(x for x in d["renewals"] if x["key"] == r["key"])
    expected = (r["contract_price_per_clinical_unit"] - agreed) * r["site_annual_units"]
    assert got["realised_saving"] == pytest.approx(expected)
    assert d["kpis"]["realised_saving"] == pytest.approx(expected)
    assert viewer.get("/api/home").json()["realised_saving"] == pytest.approx(expected)


def test_bad_updates_are_refused(viewer):
    r = first_renewal(viewer)
    for bad in ({"stage": "won"}, {"latest_offer": -5}, {"due_date": "tomorrow"}, {"colour": "red"}):
        assert viewer.patch("/api/renewals/progress", json={"key": r["key"], **bad}, headers=H).status_code == 400
    assert viewer.patch("/api/renewals/progress", json={"key": "nope|x|y|z", "stage": "agreed"}, headers=H).status_code == 400


def test_assess_offer_zones():
    a = lambda p: I.assess_offer(p, target_price=100, fallback_price=102, walk_away=120, opening_ask=95)["zone"]  # noqa: E731
    assert [a(90), a(99), a(101), a(110), a(130)] == ["below_stretch", "at_target", "near_target", "push", "walk"]


def test_mou_board_is_one_card_per_principal_with_stage_from_negotiation():
    from datetime import date

    from negotiation_mcp.nego import service, store

    a = store.create_principal({"name": "PT Board Alpha", "mou_end": "2027-01-31"}, "t")
    b = store.create_principal({"name": "PT Board Beta", "mou_end": "2026-12-31"}, "t")
    store.create_cycle(b["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "t")
    d = service.mou_board(None, today=date(2026, 10, 2))
    cards = {m["name"]: m for m in d["mous"]}
    assert cards["PT Board Alpha"]["stage"] == "not_started"
    assert cards["PT Board Beta"]["stage"] == "preparing"
    assert [s["key"] for s in d["stages"]] == ["not_started", "preparing", "with_principal", "negotiating", "renewed"]
    # Window: Alpha ends in 121 days, Beta in 90.
    names = {m["name"] for m in service.mou_board(100, today=date(2026, 10, 2))["mous"]}
    assert "PT Board Beta" in names and "PT Board Alpha" not in names
    assert service.mou_stage({"cycle": {"status": "closed", "contract_end": "2029-12-31"}, "mou_end": "2026-12-31"}) == "renewed"
    assert a["id"] != b["id"]


def test_mou_board_links_price_book_skus_by_vendor_name(viewer):
    from negotiation_mcp.nego import store

    vendor = first_renewal(viewer)["vendor"]
    # Legal form and word order are ignored: "PT <words reversed>" still matches.
    name = "PT " + " ".join(reversed(vendor.split()))
    store.create_principal({"name": name, "mou_end": "2026-12-31"}, "t")
    d = viewer.get("/api/nego/mou-board").json()
    card = next(m for m in d["mous"] if m["name"] == name)
    assert card["price_targets"]["skus"] >= 1
    assert all(i["vendor"] == vendor for i in card["price_targets"]["items"])
