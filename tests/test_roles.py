"""The negotiator role: runs negotiations, never touches master data, users or settings."""

from __future__ import annotations

import sqlite3

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.dashboard import auth
from negotiation_mcp.dashboard.app import create_app
from negotiation_mcp.nego import store

H = {"X-Requested-With": "nego"}

# Everything a negotiator needs to run a negotiation, and the master-data routes they must not reach.
ALLOWED = [
    "/api/admin/nego/notify", "/api/admin/nego/assist",
    "/api/admin/nego/cycles/7/items/12", "/api/admin/nego/cycles/7/scan", "/api/admin/nego/cycles/7/step",
    "/api/admin/nego/cycles/7/anomalies/3", "/api/admin/nego/cycles/7/links", "/api/admin/nego/cycles/7/links/send",
    "/api/admin/nego/cycles/7/links/2/revoke", "/api/admin/nego/cycles/7/messages",
    "/api/admin/nego/cycles/7/messages/4/retry", "/api/admin/nego/cycles/7/assist",
    "/api/admin/nego/cycles/7/benchmarks/match", "/api/admin/nego/cycles/7/benchmarks/5",
    "/api/admin/nego/cycles/7/co", "/api/admin/nego/cycles/7/on-fill", "/api/admin/nego/cycles/7/package.xlsx",
    "/api/admin/nego/cycles/7/documents", "/api/admin/nego/cycles/7/documents/9",
]
DENIED = [
    "/api/admin/users", "/api/admin/users/1", "/api/admin/users/1/setup-link", "/api/admin/settings",
    "/api/admin/uploads", "/api/admin/uploads/x/apply", "/api/admin/versions", "/api/admin/versions/x/activate",
    "/api/admin/documents", "/api/admin/activity", "/api/admin/templates/prices",
    "/api/admin/nego/principals", "/api/admin/nego/principals/import", "/api/admin/nego/principals/3",
    "/api/admin/nego/sample", "/api/admin/nego/cycles", "/api/admin/nego/cycles/7",
    "/api/admin/nego/cycles/7/prepare", "/api/admin/nego/cycles/7/import", "/api/admin/nego/notify/test",
    "/api/admin/nego/benchmarks", "/api/admin/nego/cycles/7/other",
]


def test_negotiator_path_rules():
    assert all(auth.negotiator_may(p) for p in ALLOWED)
    assert not any(auth.negotiator_may(p) for p in DENIED)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    store.init()
    appdb.init()
    appdb.create_user("nina@example.com", "Nina Negotiator", "negotiator", "test")
    appdb.create_user("vera@example.com", "Vera Viewer", "viewer", "test")
    return create_app()


def sign_in(app, email):
    c = TestClient(app)
    assert c.post("/api/auth/signin", json={"email": email}, headers=H).status_code == 200
    return c


def call(c, path):
    # GET is enough to see the gate: a blocked route answers 403 before it reaches the handler.
    return c.get(path, headers=H).status_code


def test_negotiator_reaches_negotiation_routes_but_not_master_data(app):
    nina = sign_in(app, "nina@example.com")
    assert nina.get("/api/auth/me").json()["user"]["role"] == "negotiator"
    assert nina.get("/api/catalog").status_code == 200
    for path in ALLOWED:
        assert call(nina, path) != 403, path
    for path in DENIED:
        assert call(nina, path) == 403, path
    # writes are gated the same way
    assert nina.post("/api/admin/nego/principals", json={}, headers=H).status_code == 403
    assert nina.put("/api/admin/settings", json={}, headers=H).status_code == 403
    assert nina.post("/api/admin/users", json={}, headers=H).status_code == 403


def test_viewer_still_blocked_everywhere_under_admin(app):
    vera = sign_in(app, "vera@example.com")
    for path in ALLOWED + DENIED:
        assert call(vera, path) == 403, path


def test_admin_can_make_and_demote_negotiators(app):
    admin = sign_in(app, "admin@example.com")
    r = admin.post("/api/admin/users", json={"email": "bob@example.com", "name": "Bob", "role": "negotiator"}, headers=H)
    assert r.status_code == 200, r.text
    assert appdb.user_by_email("bob@example.com")["role"] == "negotiator"
    uid = appdb.user_by_email("bob@example.com")["id"]
    assert admin.patch(f"/api/admin/users/{uid}", json={"role": "viewer"}, headers=H).status_code == 200
    assert appdb.user_by_email("bob@example.com")["role"] == "viewer"
    assert admin.post("/api/admin/users", json={"email": "x@example.com", "name": "X", "role": "boss"}, headers=H).status_code == 400
    # the last admin cannot be demoted to negotiator
    me = appdb.user_by_email("admin@example.com")["id"]
    assert admin.patch(f"/api/admin/users/{me}", json={"role": "negotiator"}, headers=H).status_code >= 400
    assert appdb.user_by_email("admin@example.com")["role"] == "admin"


def test_old_database_is_widened_once_and_keeps_its_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("NEGO_HOME", str(tmp_path))
    old = sqlite3.connect(tmp_path / "app.db")
    old.executescript("""
        CREATE TABLE users (
          id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          role TEXT NOT NULL CHECK (role IN ('viewer','admin')), active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, created_by TEXT, last_login TEXT);
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        INSERT INTO users (id, email, name, role, created_at) VALUES (1, 'old@example.com', 'Old Admin', 'admin', 'x');
        INSERT INTO users (id, email, name, role, created_at) VALUES (2, 'v@example.com', 'Old Viewer', 'viewer', 'x');
        INSERT INTO sessions VALUES ('s1', 1, 'x', '2999-01-01');""")
    old.commit()
    old.close()
    appdb.init()
    appdb.init()  # idempotent
    assert appdb.user_by_email("old@example.com")["role"] == "admin"
    assert appdb.user_by_email("v@example.com")["name"] == "Old Viewer"
    appdb.create_user("new@example.com", "New Negotiator", "negotiator", "test")
    with appdb.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM sessions WHERE user_id=1").fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    assert {"password_hash", "setup_token", "setup_expires"} <= cols
