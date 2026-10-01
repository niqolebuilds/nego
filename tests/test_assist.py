"""Optional Claude drafting: off by default, minimal facts sent, refusals handled.
The Anthropic client is replaced by a fake; no network calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp import settings as S
from negotiation_mcp.nego import assist, negotiate, store

H = {"X-Requested-With": "nego"}


class FakeClient:
    calls: list[dict] = []
    stop_reason = "end_turn"

    def __init__(self, *a, **k):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        FakeClient.calls.append(kw)
        return SimpleNamespace(stop_reason=FakeClient.stop_reason, model=kw["model"],
                               content=[SimpleNamespace(type="text", text="Draf pesan.")])


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    store.init()
    appdb.init()
    import anthropic

    FakeClient.calls = []
    FakeClient.stop_reason = "end_turn"
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return tmp_path


def cycle():
    p = store.create_principal({"name": "PT Draf", "contact_name": "Ibu Sari"}, "a@x")
    c = store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    store.add_items(c["id"], [{"erp_code": f"D{i}", "item_name": f"Item {i}", "item_status": "Active", "mou_qty": 10,
                               "mou_hna": 100_000, "mou_disc": 0.1, "rfq_qty": 10, "rfq_hna": 110_000, "rfq_disc": 0.1,
                               "po_qty_12m": 10, "po_unit_text": "BOX@10"} for i in range(3)], "a@x")
    return c


def test_off_by_default(ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    c = cycle()
    with pytest.raises(Exception, match="Engine settings"):
        assist.draft(c["id"], "co_cover", "a@x")
    assert not FakeClient.calls


def test_drafts_send_only_aggregates(ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    S.save({"llm_assist": True})
    c = cycle()
    negotiate.apply_co(c["id"], "a@x")
    r = assist.draft(c["id"], "co_cover", "a@x")
    assert r["text"] == "Draf pesan." and r["facts"]["items_with_counter_offer"] == 3
    call = FakeClient.calls[-1]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    sent = call["messages"][0]["content"]
    assert "110000" not in sent and "100000" not in sent and "D1" not in sent  # no item prices or codes
    with pytest.raises(Exception, match="meeting notes"):
        assist.draft(c["id"], "meeting_summary", "a@x")
    store.update_cycle(c["id"], {"meeting_notes": "Sepakat diskon 12% untuk semua item."}, "a@x")
    assert assist.draft(c["id"], "meeting_summary", "a@x")["facts"]["notes"].startswith("Sepakat")
    negotiate.fill_on(c["id"], "a@x")
    assert assist.draft(c["id"], "escalation_note", "a@x")["facts"]["items_agreed"] == 3
    assert any(e["action"] == "assist.draft" for e in store.cycle_events(c["id"]))


def test_refusal_is_reported(ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    S.save({"llm_assist": True})
    FakeClient.stop_reason = "refusal"
    c = cycle()
    with pytest.raises(Exception, match="declined"):
        assist.draft(c["id"], "co_cover", "a@x")


def test_route_admin_only(ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    S.save({"llm_assist": True})
    from negotiation_mcp.dashboard.app import create_app

    a = TestClient(create_app())
    a.post("/api/auth/signin", json={"email": "admin@example.com"}, headers=H)
    c = cycle()
    r = a.post(f"/api/admin/nego/cycles/{c['id']}/assist", json={"kind": "co_cover"}, headers=H)
    assert r.status_code == 200 and r.json()["text"] == "Draf pesan."
    assert a.get("/").headers["Content-Security-Policy"].startswith("default-src 'self'")
    appdb.create_user("v@example.com", "Vee", "viewer", "admin@example.com")
    v = TestClient(a.app)
    v.post("/api/auth/signin", json={"email": "v@example.com"}, headers=H)
    assert v.post(f"/api/admin/nego/cycles/{c['id']}/assist", json={"kind": "co_cover"}, headers=H).status_code == 403


def test_bad_links_are_rate_limited(ws):
    from negotiation_mcp.dashboard.app import create_app

    p = TestClient(create_app())
    for _ in range(20):
        assert p.get("/p/wrong-token", follow_redirects=False).status_code == 303
    assert p.get("/p/wrong-token", follow_redirects=False).status_code == 429
    assert p.get("/api/p/me").status_code == 429
