"""The app's own records: users, sessions, renewal progress, uploads, documents, audit.

One SQLite file in the workspace (``app.db``), standard library only. It is separate
from the analytics warehouse, which is rebuilt from the price data and never holds
anything a person typed. Every write that matters also lands in ``audit_log``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .engine import EngineError
from .settings import workspace

ROLES = ("viewer", "admin")
STAGES = (
    ("not_started", "Not started"),
    ("preparing", "Preparing"),
    ("negotiating", "In negotiation"),
    ("offer_received", "Offer received"),
    ("agreed", "Agreed"),
    ("escalated", "Escalated"),
    ("lost", "Lost / re-tender"),
)
STAGE_KEYS = tuple(k for k, _ in STAGES)
SESSION_HOURS = 12
SETUP_LINK_DAYS = 7
MIN_PASSWORD = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('viewer','admin')), active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, created_by TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS renewal_progress (
  key TEXT PRIMARY KEY, stage TEXT NOT NULL DEFAULT 'not_started', owner TEXT, next_step TEXT,
  due_date TEXT, notes TEXT, latest_offer REAL, agreed_price REAL,
  updated_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS renewal_events (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, at TEXT NOT NULL, user_email TEXT NOT NULL,
  field TEXT NOT NULL, old TEXT, new TEXT);
CREATE TABLE IF NOT EXISTS versions (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, user_email TEXT NOT NULL, base TEXT,
  note TEXT, rows INTEGER, is_sample INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS uploads (
  id TEXT PRIMARY KEY, at TEXT NOT NULL, user_email TEXT NOT NULL, kind TEXT NOT NULL,
  filename TEXT NOT NULL, rows INTEGER, status TEXT NOT NULL, version_id TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, at TEXT NOT NULL, user_email TEXT NOT NULL, filename TEXT NOT NULL,
  stored_name TEXT NOT NULL, content_type TEXT, size INTEGER, vendor TEXT, sku TEXT,
  renewal_key TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_email TEXT NOT NULL, action TEXT NOT NULL,
  detail TEXT);
CREATE INDEX IF NOT EXISTS ix_events_key ON renewal_events(key);
CREATE INDEX IF NOT EXISTS ix_docs_links ON documents(vendor, sku, renewal_key);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path():
    return workspace() / "app.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    if not path.exists():
        _create(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _create(path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


# Columns added after the first release; added in place so existing app.db files keep working.
USER_COLUMNS = {"password_hash": "TEXT", "setup_token": "TEXT", "setup_expires": "TEXT"}


def init() -> str | None:
    """Create tables, and the first admin if there are no users yet. Safe to call again.

    Returns a one-time password-setup token for the first admin when one was just created
    and ``NEGO_ADMIN_PASSWORD`` wasn't given, so the caller can print the link.
    """
    with connect() as con:
        con.executescript(SCHEMA)
        have = {r[1] for r in con.execute("PRAGMA table_info(users)")}
        for col, kind in USER_COLUMNS.items():
            if col not in have:
                con.execute(f"ALTER TABLE users ADD COLUMN {col} {kind}")
        if con.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return None
        email = (os.environ.get("NEGO_ADMIN_EMAIL") or "admin@example.com").strip().lower()
        con.execute(
            "INSERT INTO users (email, name, role, created_at, created_by) VALUES (?,?,?,?,?)",
            (email, os.environ.get("NEGO_ADMIN_NAME") or "Administrator", "admin", now(), "system"),
        )
    first = user_by_email(email)
    if os.environ.get("NEGO_ADMIN_PASSWORD"):
        set_password(first["id"], os.environ["NEGO_ADMIN_PASSWORD"], "system")
        return None
    return issue_setup_token(first["id"], "system")


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r else None


# ---------------------------------------------------------------- audit

def audit(user_email: str, action: str, detail: Any = None) -> None:
    with connect() as con:
        con.execute("INSERT INTO audit_log (at, user_email, action, detail) VALUES (?,?,?,?)",
                    (now(), user_email, action, json.dumps(detail, default=str) if detail is not None else None))


def audit_log(limit: int = 100) -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        out.append(d)
    return out


# ---------------------------------------------------------------- users

def _clean_email(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e or "." not in e.split("@")[-1] or len(e) > 200 or any(c.isspace() for c in e):
        raise EngineError("Enter a valid email address")
    return e


PRIVATE_USER_FIELDS = ("password_hash", "setup_token", "setup_expires")


def list_users() -> list[dict]:
    with connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM users ORDER BY role DESC, name")]
    return [safe_user(r) for r in rows]


def safe_user(u: dict) -> dict:
    """A user row without password or setup-link hashes, for the admin screens."""
    out = {k: v for k, v in u.items() if k not in PRIVATE_USER_FIELDS}
    out["has_password"] = bool(u.get("password_hash"))
    return out


def get_user(user_id: int) -> dict | None:
    with connect() as con:
        return _row(con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


def user_by_email(email: str) -> dict | None:
    with connect() as con:
        return _row(con.execute("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),)).fetchone())


def create_user(email: str, name: str, role: str, by: str) -> dict:
    email = _clean_email(email)
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise EngineError("Enter the person's name")
    if role not in ROLES:
        raise EngineError("Role must be viewer or admin")
    with connect() as con:
        if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise EngineError(f"{email} is already registered")
        con.execute("INSERT INTO users (email, name, role, created_at, created_by) VALUES (?,?,?,?,?)",
                    (email, name, role, now(), by))
    audit(by, "user.invite", {"email": email, "role": role})
    return user_by_email(email)  # type: ignore[return-value]


def update_user(user_id: int, by: str, role: str | None = None, active: bool | None = None, name: str | None = None) -> dict:
    user = get_user(user_id)
    if not user:
        raise EngineError("No such user")
    changes: dict[str, Any] = {}
    if role is not None:
        if role not in ROLES:
            raise EngineError("Role must be viewer or admin")
        changes["role"] = role
    if active is not None:
        changes["active"] = 1 if active else 0
    if name is not None and name.strip():
        changes["name"] = name.strip()[:120]
    if not changes:
        return user
    losing_admin = user["role"] == "admin" and (changes.get("role") == "viewer" or changes.get("active") == 0)
    if losing_admin:
        with connect() as con:
            admins = con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
        if admins <= 1:
            raise EngineError("There must always be at least one active admin")
    with connect() as con:
        con.execute(f"UPDATE users SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?", (*changes.values(), user_id))
        if changes.get("active") == 0:
            con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    audit(by, "user.update", {"email": user["email"], **changes})
    return get_user(user_id)  # type: ignore[return-value]


# ---------------------------------------------------------------- passwords

# scrypt from the standard library: memory-hard, salted, no extra dependency.
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not password:
        # Same work as a real check, so a missing account doesn't answer faster.
        hashlib.scrypt(b"x", salt=b"0" * 16, dklen=32, **_SCRYPT)
        return False
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), dklen=32, n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, _unb64(dk))


def check_password_rules(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise EngineError(f"Use at least {MIN_PASSWORD} characters")
    if len(password) > 200:
        raise EngineError("That password is too long")


def set_password(user_id: int, password: str, by: str) -> None:
    check_password_rules(password)
    with connect() as con:
        con.execute("UPDATE users SET password_hash=?, setup_token=NULL, setup_expires=NULL WHERE id=?",
                    (hash_password(password), user_id))
        con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    user = get_user(user_id)
    audit(by, "user.password_set", {"email": user["email"] if user else user_id})


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_setup_token(user_id: int, by: str) -> str:
    """A one-time link token to set (or reset) a password. Only its hash is stored."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SETUP_LINK_DAYS)).isoformat(timespec="seconds")
    with connect() as con:
        con.execute("UPDATE users SET setup_token=?, setup_expires=? WHERE id=?", (_token_hash(token), expires, user_id))
    user = get_user(user_id)
    audit(by, "user.setup_link", {"email": user["email"] if user else user_id})
    return token


def user_by_setup_token(token: str) -> dict | None:
    if not token or len(token) > 200:
        return None
    with connect() as con:
        r = con.execute("SELECT * FROM users WHERE setup_token=? AND setup_expires > ? AND active=1",
                        (_token_hash(token), now())).fetchone()
    return _row(r)


# ---------------------------------------------------------------- sessions

def create_session(user_id: int) -> str:
    sid = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    with connect() as con:
        con.execute("INSERT INTO sessions (id, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                    (sid, user_id, now(), expires))
        con.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user_id))
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
    return sid


def session_user(sid: str) -> dict | None:
    with connect() as con:
        r = con.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.id=? AND s.expires_at > ? AND u.active = 1",
            (sid, now()),
        ).fetchone()
    return _row(r)


def delete_session(sid: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE id=?", (sid,))


# ---------------------------------------------------------------- renewal progress

PROGRESS_FIELDS = ("stage", "owner", "next_step", "due_date", "notes", "latest_offer", "agreed_price")


def all_progress() -> dict[str, dict]:
    with connect() as con:
        return {r["key"]: dict(r) for r in con.execute("SELECT * FROM renewal_progress")}


def get_progress(key: str) -> dict:
    with connect() as con:
        r = con.execute("SELECT * FROM renewal_progress WHERE key=?", (key,)).fetchone()
    return dict(r) if r else {"key": key, "stage": "not_started"}


def _clean_progress(changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in PROGRESS_FIELDS:
            raise EngineError(f"Unknown field '{k}'")
        if k == "stage":
            if v not in STAGE_KEYS:
                raise EngineError("Unknown stage")
        elif k in ("latest_offer", "agreed_price"):
            if v in (None, ""):
                v = None
            else:
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise EngineError("Prices must be numbers") from None
                if v <= 0:
                    raise EngineError("Prices must be positive")
        elif k == "due_date":
            if v:
                try:
                    datetime.strptime(v, "%Y-%m-%d")
                except ValueError:
                    raise EngineError("Due date must be YYYY-MM-DD") from None
            v = v or None
        else:
            v = (str(v).strip()[:2000] or None) if v is not None else None
        clean[k] = v
    return clean


def update_progress(key: str, changes: dict[str, Any], user_email: str) -> tuple[dict, list[dict]]:
    """Apply changes, record one event per changed field, return (progress, events)."""
    if not key or len(key) > 400:
        raise EngineError("Unknown renewal")
    clean = _clean_progress(changes)
    current = get_progress(key)
    diff = {k: v for k, v in clean.items() if current.get(k) != v}
    if not diff:
        return current, []
    at = now()
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO renewal_progress (key, stage) VALUES (?, 'not_started')", (key,))
        sets = ", ".join(f"{k}=?" for k in diff)
        con.execute(f"UPDATE renewal_progress SET {sets}, updated_by=?, updated_at=? WHERE key=?",
                    (*diff.values(), user_email, at, key))
        for k, v in diff.items():
            con.execute("INSERT INTO renewal_events (key, at, user_email, field, old, new) VALUES (?,?,?,?,?,?)",
                        (key, at, user_email, k, None if current.get(k) is None else str(current.get(k)),
                         None if v is None else str(v)))
    return get_progress(key), events(key)


def events(key: str) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM renewal_events WHERE key=? ORDER BY id DESC", (key,))]


# ---------------------------------------------------------------- versions, uploads, documents

def add_version(vid: str, user_email: str, base: str | None, note: str, rows: int, is_sample: bool) -> None:
    with connect() as con:
        con.execute("INSERT INTO versions (id, created_at, user_email, base, note, rows, is_sample) VALUES (?,?,?,?,?,?,?)",
                    (vid, now(), user_email, base, note, rows, 1 if is_sample else 0))


def list_versions() -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM versions ORDER BY created_at DESC, id DESC")]


def add_upload(uid: str, user_email: str, kind: str, filename: str, rows: int, status: str, summary: dict) -> None:
    with connect() as con:
        con.execute("INSERT INTO uploads (id, at, user_email, kind, filename, rows, status, summary) VALUES (?,?,?,?,?,?,?,?)",
                    (uid, now(), user_email, kind, filename, rows, status, json.dumps(summary, default=str)))


def get_upload(uid: str) -> dict | None:
    with connect() as con:
        r = _row(con.execute("SELECT * FROM uploads WHERE id=?", (uid,)).fetchone())
    if r and r["summary"]:
        r["summary"] = json.loads(r["summary"])
    return r


def set_upload_status(uid: str, status: str, version_id: str | None = None) -> None:
    with connect() as con:
        con.execute("UPDATE uploads SET status=?, version_id=COALESCE(?, version_id) WHERE id=?", (status, version_id, uid))


def list_uploads(limit: int = 50) -> list[dict]:
    with connect() as con:
        rows = [dict(r) for r in con.execute("SELECT id, at, user_email, kind, filename, rows, status, version_id "
                                              "FROM uploads ORDER BY at DESC LIMIT ?", (limit,))]
    return rows


def add_document(doc: dict) -> dict:
    with connect() as con:
        con.execute("INSERT INTO documents (id, at, user_email, filename, stored_name, content_type, size, vendor, sku, "
                    "renewal_key, note) VALUES (:id,:at,:user_email,:filename,:stored_name,:content_type,:size,:vendor,"
                    ":sku,:renewal_key,:note)", doc)
    return doc


def list_documents(vendor: str | None = None, sku: str | None = None, renewal_key: str | None = None,
                   text: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM documents WHERE 1=1", []
    for col, val in (("vendor", vendor), ("sku", sku), ("renewal_key", renewal_key)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    if text:
        q += " AND (filename LIKE ? OR note LIKE ? OR vendor LIKE ? OR sku LIKE ?)"
        args += [f"%{text}%"] * 4
    with connect() as con:
        return [dict(r) for r in con.execute(q + " ORDER BY at DESC", args)]


def get_document(doc_id: str) -> dict | None:
    with connect() as con:
        return _row(con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())


def delete_document(doc_id: str) -> dict | None:
    doc = get_document(doc_id)
    if doc:
        with connect() as con:
            con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    return doc
