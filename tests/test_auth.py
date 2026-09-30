"""Sign-in, sessions and role enforcement on every API route."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.dashboard import auth
from negotiation_mcp.dashboard.app import create_app

H = {"X-Requested-With": "nego"}


@pytest.fixture(scope="module")
def app():
    a = create_app()
    if not appdb.user_by_email("viewer@example.com"):
        appdb.create_user("viewer@example.com", "Vera Viewer", "viewer", "test")
    return a


def client_as(app, email: str | None) -> TestClient:
    c = TestClient(app)
    if email:
        r = c.post("/api/auth/signin", json={"email": email}, headers=H)
        assert r.status_code == 200, r.text
    return c


def test_signed_out_user_is_sent_to_sign_in(app):
    c = client_as(app, None)
    assert c.get("/api/catalog").status_code == 401
    assert c.get("/api/admin/users").status_code == 401
    assert c.get("/api/auth/me").json()["user"] is None
    assert c.get("/").status_code == 200  # the page itself loads and shows sign-in


def test_unregistered_email_cannot_sign_in(app):
    r = TestClient(app).post("/api/auth/signin", json={"email": "stranger@example.com"}, headers=H)
    assert r.status_code == 401 and "isn't registered" in r.json()["error"]


def test_viewer_can_read_but_not_administer(app):
    c = client_as(app, "viewer@example.com")
    assert c.get("/api/catalog").status_code == 200
    assert c.get("/api/auth/me").json()["user"]["role"] == "viewer"
    for method, path in [("get", "/api/admin/users"), ("get", "/api/admin/versions"), ("get", "/api/admin/activity"),
                         ("post", "/api/admin/users"), ("put", "/api/admin/settings"),
                         ("post", "/api/admin/uploads"), ("post", "/api/admin/documents")]:
        r = getattr(c, method)(path, headers=H, **({"json": {}} if method in ("post", "put") else {}))
        assert r.status_code == 403, (method, path, r.status_code)


def test_admin_can_administer(app):
    c = client_as(app, "admin@example.com")
    assert c.get("/api/admin/users").status_code == 200
    assert c.get("/api/admin/activity").status_code == 200


def test_writes_need_the_app_header(app):
    c = client_as(app, "admin@example.com")
    assert c.post("/api/admin/users", json={"email": "x@example.com", "name": "X"}).status_code == 403
    r = TestClient(app).post("/api/auth/signin", json={"email": "admin@example.com"})
    assert r.status_code == 403


def test_tampered_cookie_is_rejected(app):
    c = client_as(app, "viewer@example.com")
    value = c.cookies.get(auth.COOKIE)
    sid, _, mac = value.rpartition(".")
    c.cookies.set(auth.COOKIE, f"{sid}.{'A' * len(mac)}")
    assert c.get("/api/catalog").status_code == 401
    assert auth.unsign("garbage") is None


def test_sign_out_ends_the_session(app):
    c = client_as(app, "viewer@example.com")
    assert c.post("/api/auth/signout", headers=H).status_code == 200
    assert c.get("/api/catalog").status_code == 401


def test_disabled_user_loses_access_immediately(app):
    admin = client_as(app, "admin@example.com")
    u = admin.post("/api/admin/users", json={"email": "temp@example.com", "name": "Temp", "role": "viewer"}, headers=H).json()
    c = client_as(app, "temp@example.com")
    assert c.get("/api/catalog").status_code == 200
    assert admin.patch(f"/api/admin/users/{u['id']}", json={"active": False}, headers=H).status_code == 200
    assert c.get("/api/catalog").status_code == 401


def test_last_admin_cannot_be_removed(app):
    admin = client_as(app, "admin@example.com")
    me = admin.get("/api/auth/me").json()["user"]
    r = admin.patch(f"/api/admin/users/{me['id']}", json={"role": "viewer"}, headers=H)
    assert r.status_code == 400
