"""scripts/demo.py prepares sample data, one person per role, labelled brands and a working vendor link."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.dashboard.app import create_app
from negotiation_mcp.nego import groups, store

H = {"X-Requested-With": "nego"}


def test_demo_prepares_everything_a_walkthrough_needs(tmp_path, monkeypatch):
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("demo_script", Path(__file__).resolve().parent.parent / "scripts" / "demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    link = demo.prepare()
    again = demo.prepare()  # running it twice must not fail or duplicate people
    assert link.startswith("/p/") and again.startswith("/p/")
    assert {appdb.user_by_email(e)["role"] for e, _, _ in demo.PEOPLE} == {"admin", "negotiator", "viewer"}
    assert len(store.list_principals()) == 6
    assert groups.analyse(store.items(2), 0.11, 0.2)["groups"], "the Brands tab should have groups to show"
    vendor = TestClient(create_app())
    r = vendor.get(link, follow_redirects=False)
    assert r.status_code == 303
    vendor.cookies.set("nego_principal", r.cookies.get("nego_principal"))
    assert vendor.get("/api/p/me", headers=H).json()["step"] == "rfq"
