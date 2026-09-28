"""Dashboard API: same numbers as the kernel, JSON-safe, read-only."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import intelligence as I
from negotiation_mcp.dashboard.app import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(breakage=0.10))


def test_index_and_static(client):
    assert "Negotiation Intelligence" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


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
