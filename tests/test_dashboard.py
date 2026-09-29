"""Dashboard API: same numbers as the kernel, JSON-safe, read-only."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import intelligence as I
from negotiation_mcp.dashboard.app import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(breakage=0.10))


def test_two_page_app_and_analytics(client):
    home = client.get("/").text
    assert "Get started" in home and "chat.js" in home
    assert "analytics.js" in client.get("/analytics").text
    for f in ("common.js", "chat.js", "analytics.js", "chat.css", "style.css"):
        assert client.get(f"/static/{f}").status_code == 200, f


def test_status_carries_the_sample_banner(client):
    st = client.get("/api/status").json()
    assert st["is_sample"] and "SAMPLE DATA" in st["banner"]


def test_overview_matches_kernel(client, sample_book):
    d = client.get("/api/overview").json()
    expected = sum(r["total_opportunity"] for r in I.savings_opportunities(sample_book, top_n=0))
    assert d["kpis"]["savings_identified"] == pytest.approx(expected)


def test_brief_matches_kernel_and_is_json_safe(client, sample_book):
    d = client.get("/api/brief", params={"sku": "IVC22-PRI", "vendor": "Prima", "approved_by": ""}).json()
    t = I.recommend_targets(sample_book, "IVC22-PRI", "Prima")
    assert d["targets"]["target_price"] == pytest.approx(t.target_price)
    assert d["verdict"] is None


def test_sku_endpoint_and_errors(client):
    d = client.get("/api/sku/IVC22-PRI", params={"by": "hospital"}).json()
    assert d["trend"]["by"] == "hospital" and d["trend"]["series"]
    assert client.get("/api/sku/NOPE").status_code == 404
    assert client.get("/api/brief").status_code == 400


def test_no_write_methods(client):
    assert client.post("/api/brief").status_code == 405


def test_catalog_lists_suppliers_and_competitors(client):
    d = client.get("/api/catalog").json()
    ivc = next(s for s in d["skus"] if s["sku"] == "IVC22-PRI")
    assert ivc["vendors"] == ["Prima Alkes"]
    assert {"Sehat Medika", "Medisindo"} <= set(ivc["group_vendors"])


def test_options_are_three_and_ordered(client):
    d = client.get("/api/options", params={"sku": "IV cannula 22G (Prima)", "vendor": "Prima Alkes"}).json()
    prices = [o["price_per_clinical_unit"] for o in d["options"]]
    assert [o["key"] for o in d["options"]] == ["stretch", "target", "fallback"]
    assert prices == sorted(prices)
    assert d["options"][1]["price_per_clinical_unit"] == pytest.approx(d["targets"]["target_price"])


def test_negotiate_rebuilds_the_current_deal(client):
    d = client.get("/api/negotiate", params={"sku": "IVC22-PRI", "vendor": "Prima"}).json()
    assert d["offer_from_history"] and d["current_offer"]["quoted_unit"] == "box of 50"
    assert all(p["meets_target"] for p in d["counter_offer"]["packages"])
    assert d["verdict"] is None
    signed = client.get("/api/negotiate", params={"sku": "IVC22-PRI", "vendor": "Prima", "approved_by": "Heldra"}).json()
    assert signed["verdict"]["verdict"] in ("ACCEPT", "PUSH_ABOVE_TARGET", "PUSH_ALTERNATIVE_CHEAPER", "WALK")


def test_negotiate_with_a_new_vendor_has_no_current_deal(client):
    d = client.get("/api/negotiate", params={"sku": "IVC22-PRI", "vendor": "Sehat Medika"}).json()
    assert d["offer_from_history"] is False and d["counter_offer"] is None


def test_renewal_window(client):
    d = client.get("/api/renewals", params={"min_days": 30, "max_days": 90}).json()
    assert d["renewals"] and all(30 <= r["days_left"] <= 90 for r in d["renewals"])
    assert all(r["target_price"] < r["proposed_walk_away"] or r["target_price"] == r["proposed_walk_away"] for r in d["renewals"])
    assert client.get("/api/renewals", params={"min_days": 90, "max_days": 30}).status_code == 400


def test_options_need_both_fields(client):
    assert client.get("/api/options", params={"sku": "IVC22-PRI"}).status_code == 400
