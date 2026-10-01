"""Automatic link sending through a webhook (Power Automate in production; a local stub here)."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.nego import notify, portal, service, store

H = {"X-Requested-With": "nego"}


class Stub:
    def __init__(self):
        self.received: list[tuple[dict, dict]] = []
        self.status = 200
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                stub.received.append((json.loads(body), dict(self.headers), body))
                self.send_response(stub.status)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/flow"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    store.init()
    appdb.init()
    stub = Stub()
    monkeypatch.setenv("NEGO_NOTIFY_WEBHOOK_URL", stub.url)
    monkeypatch.setenv("NEGO_PUBLIC_URL", "https://nego.example.com")
    monkeypatch.setenv("NEGO_NOTIFY_SECRET", "s3cret")
    # The app's background sender (started by other tests' apps) mustn't race these tests,
    # which call deliver_due() themselves.
    monkeypatch.setattr(notify, "wake", lambda: None)
    yield stub
    stub.server.shutdown()


def cycle(email="tender@alfa.example", phone="0812-3456-7890"):
    p = store.create_principal({"name": "PT Alfa Notif", "contact_name": "Ibu Sari", "contact_email": email,
                                "contact_phone": phone}, "a@x")
    c = store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    store.add_items(c["id"], [{"erp_code": "N1", "item_name": "Item", "mou_qty": 1, "mou_hna": 1000}], "a@x")
    return c


def test_whatsapp_number():
    assert notify.whatsapp_number("0812-3456-7890") == "6281234567890"
    assert notify.whatsapp_number("+62 812 3456 7890") == "6281234567890"
    assert notify.whatsapp_number("812 3456 7890") == "6281234567890"
    assert notify.whatsapp_number("123") is None and notify.whatsapp_number(None) is None


def test_step_opened_sends_link(env):
    c = cycle()
    old = portal.create_link(c["id"], "a@x")
    res = service.set_step(c["id"], "rfq", "a@x", send_link=True)["message"]
    assert res["status"] == "queued" and "tender@alfa.example" in res["recipient"]
    assert notify.deliver_due()["sent"] == 1
    payload, headers, body = env.received[0]
    assert payload["event"] == "principal.step_opened"
    assert payload["recipient"] == {"name": "Ibu Sari", "email": "tender@alfa.example", "whatsapp": "6281234567890"}
    assert payload["step"]["key"] == "rfq" and payload["step"]["number"] == 3
    assert payload["link"] == "https://nego.example.com/p/" + payload["link_token"]
    assert payload["whatsapp"]["template"] == "siloam_nego_step_open" and payload["whatsapp"]["button_param"] == payload["link_token"]
    assert "Buka halaman pengisian" in payload["email"]["html"]
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert headers["X-Nego-Signature"] == expected
    # the sent link works, the older one was revoked, and the stored copy no longer holds the token
    assert portal.resolve(payload["link_token"])["cycle"]["id"] == c["id"]
    assert portal.resolve(old["path"].rsplit("/", 1)[1]) is None
    msg = store.get_message(res["id"])
    assert msg["status"] == "sent" and msg["payload"]["link_token"] == "(sent)"
    assert payload["link_token"] not in json.dumps(msg["payload"])


def test_failure_then_retry(env):
    c = cycle()
    env.status = 500
    mid = service.set_step(c["id"], "rfq", "a@x", send_link=True)["message"]["id"]
    assert notify.deliver_due() == {"sent": 0, "failed": 1}
    m = store.get_message(mid)
    assert m["status"] == "failed" and m["last_error"] == "HTTP 500"
    assert notify.deliver_due() == {"sent": 0, "failed": 0}  # waits before trying again
    env.status = 200
    notify.retry(c["id"], mid)
    assert notify.deliver_due()["sent"] == 1
    assert store.get_message(mid)["status"] == "sent"


def test_skipped_without_contact_or_config(env, monkeypatch):
    c = cycle(email="", phone="")
    res = service.set_step(c["id"], "rfq", "a@x", send_link=True)["message"]
    assert res == {"status": "skipped", "reason": "no_contact", "recipient": "—"}
    monkeypatch.delenv("NEGO_NOTIFY_WEBHOOK_URL")
    store.update_principal(c["principal_id"], {"contact_email": "x@y.example"}, "a@x")
    res = service.set_step(c["id"], "rfq", "a@x", send_link=True)["message"]
    assert res["reason"] == "not_configured"
    assert [m["status"] for m in store.messages(c["id"])] == ["skipped", "skipped"]
    assert not env.received


def test_siloam_steps_send_nothing(env):
    c = cycle()
    out = service.set_step(c["id"], "counter_offer", "a@x", send_link=True)
    assert "message" not in out
    assert not store.messages(c["id"])
    with pytest.raises(Exception, match="Siloam's turn"):
        notify.step_opened(c["id"], "a@x")


def test_routes_admin_only(env):
    from negotiation_mcp.dashboard.app import create_app

    a = TestClient(create_app())
    a.post("/api/auth/signin", json={"email": "admin@example.com"}, headers=H)
    c = cycle()
    r = a.post(f"/api/admin/nego/cycles/{c['id']}/step", json={"step": "identification", "send_link": True}, headers=H)
    assert r.json()["message"]["status"] == "queued"
    notify.deliver_due()
    msgs = a.get(f"/api/admin/nego/cycles/{c['id']}/messages").json()
    assert msgs["automation"]["enabled"] and msgs["messages"][0]["status"] in ("sent", "sending", "queued")
    assert a.post("/api/admin/nego/notify/test", headers=H).json()["ok"]
    appdb.create_user("v@example.com", "Vee", "viewer", "admin@example.com")
    v = TestClient(a.app)
    v.post("/api/auth/signin", json={"email": "v@example.com"}, headers=H)
    assert v.post(f"/api/admin/nego/cycles/{c['id']}/links/send", headers=H).status_code == 403
    assert v.get("/api/admin/nego/notify").status_code == 403


# ---------------------------------------------------------------- reminders, submit notice, MOU alert

from datetime import date, timedelta  # noqa: E402


def test_reminders_before_and_after_the_deadline(env):
    c = cycle()
    today = date.today()
    due = today + timedelta(days=3)
    service.set_step(c["id"], "rfq", "a@x", send_link=True, due=due.isoformat())
    notify.deliver_due()
    opened = env.received[-1][0]
    assert opened["step"]["due"] == notify.tanggal(due.isoformat())
    # H-3 today, nothing tomorrow (H-2), H-1, nothing on the day, then daily when overdue
    assert notify.daily(today)["reminders"] == 1
    assert notify.daily(today)["reminders"] == 0  # same day again: no duplicate
    assert notify.daily(today + timedelta(days=1))["reminders"] == 0
    assert notify.daily(today + timedelta(days=2))["reminders"] == 1
    assert notify.daily(today + timedelta(days=3))["reminders"] == 0
    assert notify.daily(today + timedelta(days=4))["reminders"] == 1
    assert notify.daily(today + timedelta(days=5))["reminders"] == 1
    notify.deliver_due()
    rem = [p for p, _, _ in env.received if p["event"] == "principal.reminder"]
    assert [r["reminder"]["kind"] for r in rem] == ["h-3", "h-1", "overdue", "overdue"]
    # reminders carry the same link that opened the step, and it still works
    assert {r["link_token"] for r in rem} == {opened["link_token"]}
    assert portal.resolve(opened["link_token"]) is not None
    assert rem[0]["whatsapp"]["template"] == "siloam_nego_reminder"


def test_no_reminder_after_sending_and_notice_to_siloam(env, monkeypatch):
    monkeypatch.setenv("NEGO_NOTIFY_ADMINS", "buyer@siloamhospitals.example, head@siloamhospitals.example")
    c = cycle()
    today = date.today()
    service.set_step(c["id"], "rfq", "a@x", due=(today + timedelta(days=1)).isoformat())
    cyc = store.get_cycle(c["id"])
    portal.fill_from_reference(cyc, "p")
    portal.submit(store.get_cycle(c["id"]), "principal:x")
    assert notify.daily(today)["reminders"] == 0
    notify.deliver_due()
    sub = [p for p, _, _ in env.received if p["event"] == "siloam.step_submitted"]
    assert len(sub) == 1 and sub[0]["recipients"] == ["buyer@siloamhospitals.example", "head@siloamhospitals.example"]
    assert sub[0]["link"].endswith(f"/#nego/{c['id']}") and "PT Alfa Notif" in sub[0]["email"]["subject"]


def test_deadline_cannot_be_in_the_past(env):
    c = cycle()
    with pytest.raises(Exception, match="past"):
        service.set_step(c["id"], "rfq", "a@x", due="2000-01-01")


def test_weekly_mou_alert(env, monkeypatch):
    monkeypatch.setenv("NEGO_NOTIFY_ADMINS", "buyer@siloamhospitals.example")
    today = date.today()
    store.create_principal({"name": "PT Hampir Habis", "mou_end": (today + timedelta(days=60)).isoformat()}, "a@x")
    store.create_principal({"name": "PT Masih Lama", "mou_end": (today + timedelta(days=400)).isoformat()}, "a@x")
    monday = today + timedelta(days=(0 - today.weekday()) % 7)
    res = notify.mou_alert(today)
    assert res["status"] == "queued" and res["count"] == 1
    assert notify.mou_alert(today)["status"] == "duplicate"
    notify.deliver_due()
    alert = [p for p, _, _ in env.received if p["event"] == "siloam.mou_alert"][0]
    assert [p["name"] for p in alert["principals"]] == ["PT Hampir Habis"]
    assert notify.daily(monday + timedelta(days=1))["mou_alert"] is None  # only on the set weekday
