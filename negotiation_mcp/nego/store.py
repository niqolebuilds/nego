"""Principals, negotiation cycles, their items and anomaly decisions.

One SQLite file in the workspace (``nego.db``), standard library only, separate from
``app.db`` so the confidential commercial data can be backed up, encrypted or moved on
its own. Every write records who made it; item edits also land in ``item_changes`` so
a price can always be traced back to the person or file that set it.

A *cycle* is one negotiation with one principal for one contract period. Its items are
the rows of Template_Nego; the template's input columns are stored as they are, and
the unit-price columns are always recomputed (``model.priced``), never stored.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterable, Iterator

from ..engine import EngineError
from ..settings import workspace
from . import model as M

ITEM_FIELDS = M.INPUT_FIELDS + ("po_unit_text", "base_unit", "po_qty_12m", "po_value_12m", "last_po_price", "source", "sort",
                                 "price_reason", "principal_confirmed", "co_note")
NUMERIC_FIELDS = {c.field for c in M.COLUMNS if c.kind in ("int", "money", "pct")} | {"po_qty_12m", "po_value_12m", "last_po_price"}
PRINCIPAL_FIELDS = ("name", "distributor", "category", "binding", "mou_start", "mou_end",
                    "contact_name", "contact_email", "contact_phone", "notes")
BINDINGS = ("Nett", "Disc")
DECISIONS = ("open", "fixed", "kept", "cleared")

SCHEMA = """
CREATE TABLE IF NOT EXISTS principals (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE NOCASE, distributor TEXT, category TEXT,
  binding TEXT NOT NULL DEFAULT 'Nett', mou_start TEXT, mou_end TEXT,
  contact_name TEXT, contact_email TEXT, contact_phone TEXT, notes TEXT,
  created_at TEXT NOT NULL, created_by TEXT, updated_at TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS cycles (
  id INTEGER PRIMARY KEY, principal_id INTEGER NOT NULL REFERENCES principals(id),
  contract_start TEXT NOT NULL, contract_end TEXT NOT NULL, binding TEXT NOT NULL DEFAULT 'Nett',
  delivery_fee TEXT NOT NULL DEFAULT 'Free for all Siloam Hospitals units',
  status TEXT NOT NULL DEFAULT 'open', current_step TEXT NOT NULL DEFAULT 'prepare',
  step_due TEXT, prepared_at TEXT, prepare_summary TEXT, submitted_steps TEXT, meeting_at TEXT, meeting_notes TEXT,
  created_at TEXT NOT NULL, created_by TEXT, updated_at TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS cycle_items (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL REFERENCES cycles(id) ON DELETE CASCADE,
  sort INTEGER NOT NULL DEFAULT 0,
  erp_code TEXT, item_name TEXT, brand TEXT, catalog_no TEXT, item_status TEXT, remarks TEXT,
  mou_qty REAL, mou_hna REAL, mou_disc REAL,
  rfq_qty REAL, rfq_hna REAL, rfq_disc REAL,
  co_disc REAL, fb1_disc REAL, on_disc REAL,
  po_unit_text TEXT, base_unit TEXT, po_qty_12m REAL, po_value_12m REAL, last_po_price REAL,
  source TEXT, price_reason TEXT, principal_confirmed TEXT, co_note TEXT, updated_at TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS principal_links (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL REFERENCES cycles(id) ON DELETE CASCADE,
  token_hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  expires_at TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT, last_used_at TEXT, token_enc TEXT);
CREATE TABLE IF NOT EXISTS benchmarks (
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, name TEXT NOT NULL, brand TEXT, catalog_no TEXT, unit_text TEXT,
  pack_qty REAL, price REAL NOT NULL, incl_ppn INTEGER NOT NULL DEFAULT 1, price_pp REAL, price_date TEXT,
  file TEXT, uploaded_at TEXT NOT NULL, uploaded_by TEXT);
CREATE TABLE IF NOT EXISTS benchmark_matches (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL REFERENCES cycles(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES cycle_items(id) ON DELETE CASCADE,
  benchmark_id INTEGER NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
  confidence REAL NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'suggested',
  decided_by TEXT, decided_at TEXT, UNIQUE (item_id, benchmark_id));
CREATE TABLE IF NOT EXISTS cycle_documents (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL REFERENCES cycles(id) ON DELETE CASCADE, doc_type TEXT NOT NULL,
  filename TEXT NOT NULL, stored_name TEXT NOT NULL, content_type TEXT, size INTEGER, sha256 TEXT,
  uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS ix_bm_match ON benchmark_matches(cycle_id, status);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY, cycle_id INTEGER REFERENCES cycles(id) ON DELETE CASCADE, event TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'webhook', recipient TEXT, payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  created_at TEXT NOT NULL, created_by TEXT, next_try_at TEXT, sent_at TEXT, dedupe TEXT);
CREATE TABLE IF NOT EXISTS item_changes (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL, item_id INTEGER NOT NULL, at TEXT NOT NULL,
  user_email TEXT NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT, via TEXT);
CREATE TABLE IF NOT EXISTS anomalies (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL REFERENCES cycles(id) ON DELETE CASCADE,
  item_id INTEGER, rule TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL,
  field TEXT, suggestion REAL, suggestion_text TEXT, detail TEXT,
  status TEXT NOT NULL DEFAULT 'open', decided_by TEXT, decided_at TEXT, reason TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  UNIQUE (cycle_id, item_id, rule));
CREATE TABLE IF NOT EXISTS cycle_events (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL, at TEXT NOT NULL, user_email TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS item_master (
  erp_code TEXT PRIMARY KEY COLLATE NOCASE, item_name TEXT, generic_name TEXT, group_key TEXT, brand TEXT,
  catalog_no TEXT, uom TEXT, tags TEXT, status TEXT NOT NULL DEFAULT 'new', source TEXT,
  first_seen TEXT NOT NULL, updated_at TEXT, updated_by TEXT);
CREATE INDEX IF NOT EXISTS ix_master_status ON item_master(status);
CREATE INDEX IF NOT EXISTS ix_items_cycle ON cycle_items(cycle_id, sort);
CREATE INDEX IF NOT EXISTS ix_items_erp ON cycle_items(cycle_id, erp_code);
CREATE INDEX IF NOT EXISTS ix_anom_cycle ON anomalies(cycle_id, status);
CREATE INDEX IF NOT EXISTS ix_changes_item ON item_changes(item_id);
CREATE INDEX IF NOT EXISTS ix_cycles_principal ON cycles(principal_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path():
    return workspace() / "nego.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    fresh = not path.exists()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        if fresh:
            con.executescript(SCHEMA)
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_dedupe ON outbox(dedupe)")
        yield con
        con.commit()
    finally:
        con.close()


# Columns added after the first release, created on existing databases by ``init``.
MIGRATIONS = (
    ("cycle_items", "price_reason", "TEXT"),
    ("cycle_items", "principal_confirmed", "TEXT"),
    ("cycles", "submitted_steps", "TEXT"),
    ("cycle_items", "co_note", "TEXT"),
    ("cycles", "meeting_at", "TEXT"),
    ("cycles", "meeting_notes", "TEXT"),
    ("principal_links", "token_enc", "TEXT"),
    ("outbox", "dedupe", "TEXT"),
)


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        for table, col, kind in MIGRATIONS:
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
        # one message per de-duplication key (daily reminders survive restarts without repeating)
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_dedupe ON outbox(dedupe)")
        # every item code already in a negotiation is master data too; new ones wait for review
        con.execute("INSERT OR IGNORE INTO item_master (erp_code, item_name, brand, catalog_no, uom, status, source, first_seen) "
                    "SELECT TRIM(erp_code), MAX(item_name), MAX(brand), MAX(catalog_no), MAX(po_unit_text), 'new', 'negotiation', ? "
                    "FROM cycle_items WHERE TRIM(COALESCE(erp_code, '')) != '' GROUP BY TRIM(erp_code) COLLATE NOCASE", (now(),))


def _d(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r else None


def _date(v: Any, label: str) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, (date, datetime)):
        return (v.date() if isinstance(v, datetime) else v).isoformat()
    try:
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        raise EngineError(f"{label} must be a date like 2027-01-31") from None


def _num(v: Any, label: str) -> float | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("Rp", "").replace(" ", "")
    pct = s.endswith("%")
    s = s.rstrip("%")
    # Indonesian style 1.234.567,89 or plain 1234567.89
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1 or re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", s):
        s = s.replace(".", "")  # 1.500 or 1.250.000: Indonesian thousands
    try:
        x = float(s)
    except ValueError:
        raise EngineError(f"{label}: '{v}' is not a number") from None
    return x / 100.0 if pct else x


def clean_value(field: str, value: Any) -> Any:
    """Normalise one item value. Numbers accept Indonesian formatting and '15%'."""
    label = M.BY_FIELD[field].header if field in M.BY_FIELD else field
    if field in NUMERIC_FIELDS:
        x = _num(value, label)
        if x is not None and x < 0:
            raise EngineError(f"{label} can't be negative")
        return x
    if field == "item_status":
        if value in (None, ""):
            return None
        v = str(value).strip().lower()
        if v.startswith("disc") or v.startswith("tidak") or v in ("inactive", "non active", "nonaktif"):
            return "Discontinue"
        if v.startswith("act") or v in ("aktif", "ya", "yes"):
            return "Active"
        raise EngineError("Item Status must be Active or Discontinue")
    if field == "sort":
        return int(value or 0)
    if value is None:
        return None
    s = str(value).strip()
    return s[:500] or None


# ---------------------------------------------------------------- principals

def _principal_values(data: dict, partial: bool) -> dict:
    out: dict[str, Any] = {}
    for k in PRINCIPAL_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k in ("mou_start", "mou_end"):
            out[k] = _date(v, "MOU start" if k == "mou_start" else "MOU end")
        elif k == "binding":
            b = str(v or "Nett").strip().title()
            if b not in BINDINGS:
                raise EngineError("Binding must be Nett or Disc")
            out[k] = b
        else:
            out[k] = (str(v).strip()[:300] or None) if v is not None else None
    if not partial and not out.get("name"):
        raise EngineError("Principal name is required")
    if "name" in out and not out["name"]:
        raise EngineError("Principal name can't be empty")
    if out.get("contact_email") and "@" not in out["contact_email"]:
        raise EngineError("Contact email doesn't look like an email address")
    return out


def create_principal(data: dict, by: str) -> dict:
    vals = _principal_values(data, partial=False)
    with connect() as con:
        if con.execute("SELECT 1 FROM principals WHERE name = ?", (vals["name"],)).fetchone():
            raise EngineError(f"'{vals['name']}' already exists")
        cols = list(vals)
        cur = con.execute(
            f"INSERT INTO principals ({', '.join(cols)}, created_at, created_by) VALUES ({', '.join('?' * len(cols))}, ?, ?)",
            [vals[c] for c in cols] + [now(), by])
        return get_principal(cur.lastrowid, con)


def update_principal(pid: int, data: dict, by: str) -> dict:
    vals = _principal_values(data, partial=True)
    with connect() as con:
        if not get_principal(pid, con):
            raise EngineError("No such principal")
        if vals:
            sets = ", ".join(f"{k} = ?" for k in vals)
            try:
                con.execute(f"UPDATE principals SET {sets}, updated_at = ?, updated_by = ? WHERE id = ?",
                            [*vals.values(), now(), by, pid])
            except sqlite3.IntegrityError:
                raise EngineError("Another principal already has that name") from None
        return get_principal(pid, con)


def upsert_principals(rows: Iterable[dict], by: str) -> dict:
    """Bulk import by name: new names are added, existing ones updated. Returns counts."""
    added = updated = 0
    errors: list[str] = []
    for i, row in enumerate(rows, start=2):
        try:
            vals = _principal_values(row, partial=False)
            with connect() as con:
                existing = con.execute("SELECT id FROM principals WHERE name = ?", (vals["name"],)).fetchone()
            if existing:
                update_principal(existing["id"], vals, by)
                updated += 1
            else:
                create_principal(vals, by)
                added += 1
        except EngineError as e:
            errors.append(f"Row {i}: {e}")
    return {"added": added, "updated": updated, "errors": errors[:50]}


def get_principal(pid: int, con: sqlite3.Connection | None = None) -> dict | None:
    if con is None:
        with connect() as c:
            return get_principal(pid, c)
    return _d(con.execute("SELECT * FROM principals WHERE id = ?", (pid,)).fetchone())


def list_principals() -> list[dict]:
    """Every principal with its latest cycle and that cycle's counts."""
    with connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM principals ORDER BY name")]
        latest = {r["principal_id"]: dict(r) for r in con.execute(
            "SELECT c.* FROM cycles c JOIN (SELECT principal_id, MAX(id) AS id FROM cycles GROUP BY principal_id) m "
            "ON m.id = c.id")}
        counts = {r["cycle_id"]: dict(r) for r in con.execute(
            "SELECT cycle_id, COUNT(*) AS items FROM cycle_items GROUP BY cycle_id")}
        open_anoms = {r["cycle_id"]: r["n"] for r in con.execute(
            "SELECT cycle_id, COUNT(*) AS n FROM anomalies WHERE status = 'open' GROUP BY cycle_id")}
    for p in rows:
        c = latest.get(p["id"])
        if c:
            c["items"] = counts.get(c["id"], {}).get("items", 0)
            c["open_anomalies"] = open_anoms.get(c["id"], 0)
            c["step_label"] = M.STEP_LABEL.get(c["current_step"], c["current_step"])
            c["submitted_steps"] = json.loads(c["submitted_steps"]) if c.get("submitted_steps") else {}
        p["cycle"] = c
    return rows


# ---------------------------------------------------------------- cycles

def create_cycle(principal_id: int, data: dict, by: str) -> dict:
    with connect() as con:
        p = get_principal(principal_id, con)
        if not p:
            raise EngineError("No such principal")
        if con.execute("SELECT 1 FROM cycles WHERE principal_id = ? AND status = 'open'", (principal_id,)).fetchone():
            raise EngineError(f"{p['name']} already has an open negotiation; close it before starting another")
        start = _date(data.get("contract_start"), "Contract start")
        end = _date(data.get("contract_end"), "Contract end")
        if not start or not end:
            raise EngineError("Contract start and end are required")
        if end <= start:
            raise EngineError("The contract must end after it starts")
        binding = str(data.get("binding") or p["binding"] or "Nett").title()
        if binding not in BINDINGS:
            raise EngineError("Binding must be Nett or Disc")
        fee = str(data.get("delivery_fee") or "Free for all Siloam Hospitals units").strip()[:200]
        cur = con.execute(
            "INSERT INTO cycles (principal_id, contract_start, contract_end, binding, delivery_fee, created_at, created_by) "
            "VALUES (?,?,?,?,?,?,?)", (principal_id, start, end, binding, fee, now(), by))
        cid = cur.lastrowid
        _event(con, cid, by, "cycle.create", {"contract_start": start, "contract_end": end, "binding": binding})
        return get_cycle(cid, con)


def get_cycle(cid: int, con: sqlite3.Connection | None = None) -> dict | None:
    if con is None:
        with connect() as c:
            return get_cycle(cid, c)
    c = _d(con.execute("SELECT * FROM cycles WHERE id = ?", (cid,)).fetchone())
    if c:
        c["principal"] = get_principal(c["principal_id"], con)
        c["prepare_summary"] = json.loads(c["prepare_summary"]) if c.get("prepare_summary") else None
        c["submitted_steps"] = json.loads(c["submitted_steps"]) if c.get("submitted_steps") else {}
    return c


def require_cycle(cid: int) -> dict:
    c = get_cycle(cid)
    if not c:
        raise EngineError("No such negotiation")
    return c


def update_cycle(cid: int, data: dict, by: str) -> dict:
    allowed: dict[str, Any] = {}
    if "contract_start" in data:
        allowed["contract_start"] = _date(data["contract_start"], "Contract start")
    if "contract_end" in data:
        allowed["contract_end"] = _date(data["contract_end"], "Contract end")
    if "binding" in data:
        b = str(data["binding"]).title()
        if b not in BINDINGS:
            raise EngineError("Binding must be Nett or Disc")
        allowed["binding"] = b
    if "delivery_fee" in data:
        allowed["delivery_fee"] = str(data["delivery_fee"] or "").strip()[:200]
    if "step_due" in data:
        allowed["step_due"] = _date(data["step_due"], "Due date")
    if "meeting_at" in data:
        allowed["meeting_at"] = (str(data["meeting_at"] or "").strip()[:20]) or None
    if "meeting_notes" in data:
        allowed["meeting_notes"] = (str(data["meeting_notes"] or "").strip()[:4000]) or None
    with connect() as con:
        c = get_cycle(cid, con)
        if not c:
            raise EngineError("No such negotiation")
        merged = {**c, **allowed}
        if merged["contract_end"] <= merged["contract_start"]:
            raise EngineError("The contract must end after it starts")
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            con.execute(f"UPDATE cycles SET {sets}, updated_at = ?, updated_by = ? WHERE id = ?",
                        [*allowed.values(), now(), by, cid])
            _event(con, cid, by, "cycle.update", allowed)
        return get_cycle(cid, con)


def set_step(cid: int, step: str, by: str, note: str = "", due: str | None = None) -> dict:
    if step not in M.STEP_KEYS:
        raise EngineError("Unknown step")
    with connect() as con:
        c = get_cycle(cid, con)
        if not c:
            raise EngineError("No such negotiation")
        status = "closed" if step == "closed" else "open"
        submitted = dict(c["submitted_steps"])
        submitted.pop(step, None)  # moving (back) to a step opens it again for the principal
        due = _date(due, "Deadline")
        con.execute("UPDATE cycles SET current_step = ?, status = ?, step_due = ?, submitted_steps = ?, updated_at = ?, "
                    "updated_by = ? WHERE id = ?", (step, status, due, json.dumps(submitted), now(), by, cid))
        _event(con, cid, by, "step.set", {"from": c["current_step"], "to": step, "note": note[:300] or None, "due": due})
        return get_cycle(cid, con)


def mark_submitted(cid: int, step: str, by: str, summary: dict) -> dict:
    with connect() as con:
        c = get_cycle(cid, con)
        submitted = dict(c["submitted_steps"])
        submitted[step] = {"at": now(), "by": by}
        con.execute("UPDATE cycles SET submitted_steps = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                    (json.dumps(submitted), now(), by, cid))
        _event(con, cid, by, "principal.submit", {"step": step, **summary})
        return get_cycle(cid, con)


# ---------------------------------------------------------------- principal links

def add_link(cid: int, token_hash: str, expires_at: str, by: str, token_enc: str | None = None) -> dict:
    with connect() as con:
        cur = con.execute("INSERT INTO principal_links (cycle_id, token_hash, created_at, created_by, expires_at, token_enc) "
                          "VALUES (?,?,?,?,?,?)", (cid, token_hash, now(), by, expires_at, token_enc))
        _event(con, cid, by, "link.create", {"id": cur.lastrowid, "expires_at": expires_at})
        return _d(con.execute("SELECT id, cycle_id, created_at, created_by, expires_at, revoked_at, last_used_at "
                              "FROM principal_links WHERE id = ?", (cur.lastrowid,)).fetchone())


def link_by_hash(token_hash: str) -> dict | None:
    with connect() as con:
        return _d(con.execute("SELECT * FROM principal_links WHERE token_hash = ?", (token_hash,)).fetchone())


def latest_active_link(cid: int) -> dict | None:
    with connect() as con:
        return _d(con.execute(
            "SELECT * FROM principal_links WHERE cycle_id = ? AND revoked_at IS NULL AND expires_at > ? AND token_enc IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (cid, now())).fetchone())


def open_cycles() -> list[int]:
    with connect() as con:
        return [r[0] for r in con.execute("SELECT id FROM cycles WHERE status = 'open' ORDER BY id")]


def touch_link(lid: int) -> None:
    with connect() as con:
        con.execute("UPDATE principal_links SET last_used_at = ? WHERE id = ?", (now(), lid))


def links(cid: int) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, created_at, created_by, expires_at, revoked_at, revoked_by, last_used_at FROM principal_links "
            "WHERE cycle_id = ? ORDER BY id DESC", (cid,))]


def revoke_link(cid: int, lid: int, by: str) -> None:
    with connect() as con:
        cur = con.execute("UPDATE principal_links SET revoked_at = ?, revoked_by = ? WHERE id = ? AND cycle_id = ? "
                          "AND revoked_at IS NULL", (now(), by, lid, cid))
        if not cur.rowcount:
            raise EngineError("No such active link")
        _event(con, cid, by, "link.revoke", {"id": lid})


# ---------------------------------------------------------------- outbox

def queue_message(cid: int | None, event: str, recipient: str, payload: dict, by: str, status: str = "queued",
                  error: str | None = None, dedupe: str | None = None) -> int | None:
    """Queue one message. With ``dedupe``, a second message with the same key is ignored (returns None)."""
    with connect() as con:
        try:
            cur = con.execute(
                "INSERT INTO outbox (cycle_id, event, recipient, payload, status, last_error, created_at, created_by, next_try_at, dedupe) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (cid, event, recipient, json.dumps(payload, default=str), status, error, now(), by, now(), dedupe))
        except sqlite3.IntegrityError:
            return None
        if cid:
            _event(con, cid, by, f"message.{status}", {"id": cur.lastrowid, "event": event, "to": recipient, "error": error})
        return cur.lastrowid


def claim_due_messages(limit: int = 20) -> list[dict]:
    """Messages ready to send, marked 'sending' so two senders never post the same one."""
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM outbox WHERE status IN ('queued','failed') AND attempts < 5 AND next_try_at <= ? "
            "ORDER BY id LIMIT ?", (now(), limit))]
        for r in rows:
            con.execute("UPDATE outbox SET status='sending' WHERE id = ?", (r["id"],))
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows


def message_result(mid: int, ok: bool, error: str | None, next_try_at: str | None, redacted: dict | None) -> None:
    with connect() as con:
        if ok:
            con.execute("UPDATE outbox SET status='sent', attempts=attempts+1, last_error=NULL, sent_at=?, payload=? WHERE id=?",
                        (now(), json.dumps(redacted, default=str), mid))
        else:
            con.execute("UPDATE outbox SET status='failed', attempts=attempts+1, last_error=?, next_try_at=? WHERE id=?",
                        ((error or "")[:500], next_try_at, mid))


def messages(cid: int) -> list[dict]:
    with connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, event, channel, recipient, status, attempts, last_error, created_at, created_by, sent_at "
            "FROM outbox WHERE cycle_id = ? ORDER BY id DESC LIMIT 100", (cid,))]
    return rows


def last_sent(cid: int, event: str) -> dict | None:
    """The most recent delivered message of one kind for a negotiation, with its stored payload."""
    with connect() as con:
        r = _d(con.execute("SELECT id, sent_at, payload FROM outbox WHERE cycle_id = ? AND event = ? AND status = 'sent' "
                           "ORDER BY id DESC LIMIT 1", (cid, event)).fetchone())
    if r:
        r["payload"] = json.loads(r["payload"])
    return r


def get_message(mid: int) -> dict | None:
    with connect() as con:
        r = _d(con.execute("SELECT * FROM outbox WHERE id = ?", (mid,)).fetchone())
    if r:
        r["payload"] = json.loads(r["payload"])
    return r


def reset_sending() -> None:
    """After a restart, messages caught mid-send go back to the queue."""
    with connect() as con:
        con.execute("UPDATE outbox SET status='queued' WHERE status='sending'")


def requeue_message(mid: int) -> None:
    with connect() as con:
        con.execute("UPDATE outbox SET status='queued', attempts=0, next_try_at=? WHERE id=?", (now(), mid))


def revoke_active_links(cid: int, by: str) -> int:
    with connect() as con:
        cur = con.execute("UPDATE principal_links SET revoked_at = ?, revoked_by = ? WHERE cycle_id = ? AND revoked_at IS NULL",
                          (now(), by, cid))
        return cur.rowcount


def mark_prepared(cid: int, summary: dict, by: str) -> None:
    with connect() as con:
        con.execute("UPDATE cycles SET prepared_at = ?, prepare_summary = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                    (now(), json.dumps(summary, default=str), now(), by, cid))
        _event(con, cid, by, "cycle.prepare", summary)


def _event(con: sqlite3.Connection, cid: int, by: str, action: str, detail: Any = None) -> None:
    con.execute("INSERT INTO cycle_events (cycle_id, at, user_email, action, detail) VALUES (?,?,?,?,?)",
                (cid, now(), by, action, json.dumps(detail, default=str) if detail is not None else None))


def log_event(cid: int, by: str, action: str, detail: Any = None) -> None:
    with connect() as con:
        _event(con, cid, by, action, detail)


def cycle_events(cid: int, limit: int = 100) -> list[dict]:
    with connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM cycle_events WHERE cycle_id = ? ORDER BY id DESC LIMIT ?", (cid, limit))]
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return rows


# ---------------------------------------------------------------- items

def items(cid: int) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM cycle_items WHERE cycle_id = ? ORDER BY sort, id", (cid,))]


def get_item(cid: int, item_id: int) -> dict | None:
    with connect() as con:
        return _d(con.execute("SELECT * FROM cycle_items WHERE cycle_id = ? AND id = ?", (cid, item_id)).fetchone())


def replace_items(cid: int, rows: list[dict], by: str) -> int:
    """Replace all items of a cycle (Prepare). Anomaly decisions for removed items go too."""
    with connect() as con:
        con.execute("DELETE FROM anomalies WHERE cycle_id = ?", (cid,))
        con.execute("DELETE FROM cycle_items WHERE cycle_id = ?", (cid,))
        _insert_items(con, cid, rows, by)
    return len(rows)


def add_items(cid: int, rows: list[dict], by: str) -> list[int]:
    with connect() as con:
        return _insert_items(con, cid, rows, by)


def _insert_items(con: sqlite3.Connection, cid: int, rows: list[dict], by: str) -> list[int]:
    start = con.execute("SELECT COALESCE(MAX(sort), 0) FROM cycle_items WHERE cycle_id = ?", (cid,)).fetchone()[0]
    ids = []
    for i, row in enumerate(rows, start=1):
        vals = {k: clean_value(k, row.get(k)) for k in ITEM_FIELDS if k in row and k != "sort"}
        vals["sort"] = int(row.get("sort") or start + i)
        cols = list(vals)
        cur = con.execute(
            f"INSERT INTO cycle_items (cycle_id, {', '.join(cols)}, updated_at, updated_by) "
            f"VALUES (?, {', '.join('?' * len(cols))}, ?, ?)", [cid, *[vals[c] for c in cols], now(), by])
        ids.append(cur.lastrowid)
        code = str(vals.get("erp_code") or "").strip()
        if code:  # a code we have not seen before becomes master data, waiting for an admin to label it
            con.execute("INSERT OR IGNORE INTO item_master (erp_code, item_name, brand, catalog_no, uom, status, source, first_seen) "
                        "VALUES (?,?,?,?,?, 'new', 'negotiation', ?)",
                        (code, vals.get("item_name"), vals.get("brand"), vals.get("catalog_no"), vals.get("po_unit_text"), now()))
    return ids


def update_item(cid: int, item_id: int, changes: dict, by: str, via: str = "app",
                allowed: Iterable[str] | None = None) -> tuple[dict, list[dict]]:
    """Change some fields of one item. ``allowed`` limits which fields this caller may set."""
    allow = set(allowed) if allowed is not None else set(ITEM_FIELDS)
    bad = [k for k in changes if k not in allow]
    if bad:
        raise EngineError(f"These fields can't be changed here: {', '.join(sorted(bad))}")
    clean = {k: clean_value(k, v) for k, v in changes.items()}
    with connect() as con:
        cur = _d(con.execute("SELECT * FROM cycle_items WHERE cycle_id = ? AND id = ?", (cid, item_id)).fetchone())
        if not cur:
            raise EngineError("No such item in this negotiation")
        diff = [{"field": k, "old": cur[k], "new": v} for k, v in clean.items() if cur[k] != v]
        if diff:
            sets = ", ".join(f"{d['field']} = ?" for d in diff)
            con.execute(f"UPDATE cycle_items SET {sets}, updated_at = ?, updated_by = ? WHERE id = ?",
                        [*(d["new"] for d in diff), now(), by, item_id])
            at = now()
            con.executemany(
                "INSERT INTO item_changes (cycle_id, item_id, at, user_email, field, old, new, via) VALUES (?,?,?,?,?,?,?,?)",
                [(cid, item_id, at, by, d["field"], _s(d["old"]), _s(d["new"]), via) for d in diff])
        return _d(con.execute("SELECT * FROM cycle_items WHERE id = ?", (item_id,)).fetchone()), diff


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def item_history(item_id: int) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT at, user_email, field, old, new, via FROM item_changes WHERE item_id = ? ORDER BY id DESC LIMIT 100",
            (item_id,))]


# ---------------------------------------------------------------- anomalies

def sync_anomalies(cid: int, findings: list[dict]) -> dict:
    """Store a fresh scan, keeping earlier decisions.

    * A finding seen before keeps its decision: "kept" stays kept (a person accepted it);
      "fixed" that still fires is reopened, because the fix didn't hold.
    * A finding that no longer fires is marked "cleared" (unless it was kept).
    """
    stamp = now()
    with connect() as con:
        existing = {(r["item_id"], r["rule"]): dict(r) for r in con.execute(
            "SELECT * FROM anomalies WHERE cycle_id = ?", (cid,))}
        seen = set()
        new = reopened = 0
        for f in findings:
            key = (f.get("item_id"), f["rule"])
            seen.add(key)
            detail = json.dumps(f.get("detail"), default=str) if f.get("detail") is not None else None
            old = existing.get(key)
            if old is None:
                con.execute(
                    "INSERT INTO anomalies (cycle_id, item_id, rule, severity, message, field, suggestion, suggestion_text, "
                    "detail, status, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,'open',?,?)",
                    (cid, f.get("item_id"), f["rule"], f["severity"], f["message"], f.get("field"), f.get("suggestion"),
                     f.get("suggestion_text"), detail, stamp, stamp))
                new += 1
                continue
            status = old["status"]
            if status in ("fixed", "cleared"):
                status = "open"
                reopened += old["status"] == "fixed"
            con.execute(
                "UPDATE anomalies SET severity = ?, message = ?, field = ?, suggestion = ?, suggestion_text = ?, detail = ?, "
                "status = ?, last_seen = ? WHERE id = ?",
                (f["severity"], f["message"], f.get("field"), f.get("suggestion"), f.get("suggestion_text"), detail,
                 status, stamp, old["id"]))
        cleared = 0
        for key, old in existing.items():
            if key not in seen and old["status"] == "open":
                con.execute("UPDATE anomalies SET status = 'cleared', last_seen = ? WHERE id = ?", (stamp, old["id"]))
                cleared += 1
    return {"found": len(findings), "new": new, "reopened": reopened, "cleared": cleared}


def anomalies(cid: int, status: str | None = None, item_id: int | None = None) -> list[dict]:
    q = "SELECT a.*, i.erp_code, i.item_name FROM anomalies a LEFT JOIN cycle_items i ON i.id = a.item_id WHERE a.cycle_id = ?"
    args: list[Any] = [cid]
    if status:
        q += " AND a.status = ?"
        args.append(status)
    if item_id is not None:
        q += " AND a.item_id = ?"
        args.append(item_id)
    q += " ORDER BY CASE a.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, a.rule, i.sort"
    with connect() as con:
        rows = [dict(r) for r in con.execute(q, args)]
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return rows


def get_anomaly(cid: int, aid: int) -> dict | None:
    with connect() as con:
        r = _d(con.execute("SELECT * FROM anomalies WHERE cycle_id = ? AND id = ?", (cid, aid)).fetchone())
    if r:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return r


def decide_anomaly(cid: int, aid: int, status: str, reason: str, by: str) -> dict:
    if status not in ("fixed", "kept", "open"):
        raise EngineError("Decision must be fix or keep")
    reason = (reason or "").strip()[:500]
    if status == "kept" and not reason:
        raise EngineError("Say why the value is kept as it is; the reason goes in the log")
    with connect() as con:
        if not con.execute("SELECT 1 FROM anomalies WHERE cycle_id = ? AND id = ?", (cid, aid)).fetchone():
            raise EngineError("No such finding")
        con.execute("UPDATE anomalies SET status = ?, reason = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                    (status, reason or None, by, now(), aid))
        _event(con, cid, by, f"anomaly.{status}", {"id": aid, "reason": reason or None})
    return get_anomaly(cid, aid)


def anomaly_counts(cid: int) -> dict:
    with connect() as con:
        rows = con.execute("SELECT status, severity, COUNT(*) AS n FROM anomalies WHERE cycle_id = ? GROUP BY status, severity",
                           (cid,)).fetchall()
    out = {"open": 0, "open_high": 0, "fixed": 0, "kept": 0, "cleared": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + r["n"]
        if r["status"] == "open" and r["severity"] == "high":
            out["open_high"] += r["n"]
    return out


# ---------------------------------------------------------------- benchmarks

def add_benchmarks(rows: list[dict], by: str) -> int:
    with connect() as con:
        con.executemany(
            "INSERT INTO benchmarks (source, name, brand, catalog_no, unit_text, pack_qty, price, incl_ppn, price_pp, price_date, "
            "file, uploaded_at, uploaded_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["source"], r["name"], r.get("brand"), r.get("catalog_no"), r.get("unit_text"), r.get("pack_qty"), r["price"],
              int(bool(r.get("incl_ppn", True))), r.get("price_pp"), r.get("price_date"), r.get("file"), now(), by) for r in rows])
    return len(rows)


def benchmarks() -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM benchmarks ORDER BY id")]


def benchmark_sources() -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT source, file, COUNT(*) AS rows, MAX(uploaded_at) AS uploaded_at, MIN(price_date) AS date_from, "
            "MAX(price_date) AS date_to FROM benchmarks GROUP BY source, file ORDER BY uploaded_at DESC")]


def save_matches(cid: int, matches: list[dict]) -> dict:
    """Store suggested matches; decided ones (confirmed/rejected) are never overwritten."""
    added = 0
    with connect() as con:
        for m in matches:
            cur = con.execute(
                "INSERT INTO benchmark_matches (cycle_id, item_id, benchmark_id, confidence, method, status, decided_by, decided_at) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(item_id, benchmark_id) DO UPDATE SET confidence=excluded.confidence, "
                "method=excluded.method WHERE benchmark_matches.status = 'suggested'",
                (cid, m["item_id"], m["benchmark_id"], m["confidence"], m["method"], m["status"],
                 "auto" if m["status"] != "suggested" else None, now() if m["status"] != "suggested" else None))
            added += cur.rowcount
    return {"stored": added}


def matches(cid: int, status: str | None = None) -> list[dict]:
    q = ("SELECT m.*, i.erp_code, i.item_name, i.brand AS item_brand, i.catalog_no AS item_ref, b.source, b.name AS bm_name, "
         "b.brand AS bm_brand, b.catalog_no AS bm_ref, b.unit_text AS bm_unit, b.price AS bm_price, b.price_pp, b.price_date "
         "FROM benchmark_matches m JOIN cycle_items i ON i.id = m.item_id JOIN benchmarks b ON b.id = m.benchmark_id "
         "WHERE m.cycle_id = ?")
    args: list = [cid]
    if status:
        q += " AND m.status = ?"
        args.append(status)
    q += " ORDER BY i.sort, m.confidence DESC"
    with connect() as con:
        return [dict(r) for r in con.execute(q, args)]


def decide_match(cid: int, mid: int, status: str, by: str) -> None:
    if status not in ("confirmed", "rejected", "suggested"):
        raise EngineError("Decision must be confirm or reject")
    with connect() as con:
        cur = con.execute("UPDATE benchmark_matches SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND cycle_id = ?",
                          (status, by, now(), mid, cid))
        if not cur.rowcount:
            raise EngineError("No such match")


def confirmed_pairs() -> set[tuple[str, str]]:
    """(ERP code, benchmark key) pairs a person confirmed in any cycle: remembered next time."""
    with connect() as con:
        rows = con.execute(
            "SELECT i.erp_code, b.source, b.name, b.catalog_no FROM benchmark_matches m JOIN cycle_items i ON i.id = m.item_id "
            "JOIN benchmarks b ON b.id = m.benchmark_id WHERE m.status = 'confirmed' AND m.decided_by != 'auto'").fetchall()
    return {(str(r["erp_code"] or "").upper(), f"{r['source']}|{r['name']}|{r['catalog_no'] or ''}".lower()) for r in rows}


def best_benchmarks(cid: int) -> dict[int, dict]:
    """Lowest confirmed benchmark per item (price per piece incl. PPN)."""
    out: dict[int, dict] = {}
    for m in matches(cid, "confirmed"):
        if m["price_pp"] is None:
            continue
        cur = out.get(m["item_id"])
        if cur is None or m["price_pp"] < cur["price_pp"]:
            out[m["item_id"]] = {"price_pp": m["price_pp"], "source": m["source"], "name": m["bm_name"], "date": m["price_date"],
                                 "match_id": m["id"]}
    return out


# ---------------------------------------------------------------- documents (principal submission)

def add_document(cid: int, doc: dict) -> dict:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO cycle_documents (cycle_id, doc_type, filename, stored_name, content_type, size, sha256, uploaded_at, "
            "uploaded_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, doc["doc_type"], doc["filename"], doc["stored_name"], doc.get("content_type"), doc.get("size"),
             doc.get("sha256"), now(), doc["uploaded_by"]))
        _event(con, cid, doc["uploaded_by"], "document.upload", {"id": cur.lastrowid, "type": doc["doc_type"], "file": doc["filename"]})
        return _d(con.execute("SELECT * FROM cycle_documents WHERE id = ?", (cur.lastrowid,)).fetchone())


def documents(cid: int) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM cycle_documents WHERE cycle_id = ? AND deleted_at IS NULL ORDER BY id", (cid,))]


def get_document(cid: int, did: int) -> dict | None:
    with connect() as con:
        return _d(con.execute("SELECT * FROM cycle_documents WHERE cycle_id = ? AND id = ? AND deleted_at IS NULL",
                              (cid, did)).fetchone())


def delete_document(cid: int, did: int, by: str) -> dict | None:
    with connect() as con:
        d = _d(con.execute("SELECT * FROM cycle_documents WHERE cycle_id = ? AND id = ? AND deleted_at IS NULL", (cid, did)).fetchone())
        if d:
            con.execute("UPDATE cycle_documents SET deleted_at = ? WHERE id = ?", (now(), did))
            _event(con, cid, by, "document.delete", {"id": did, "file": d["filename"]})
        return d


def has_message(dedupe: str) -> bool:
    with connect() as con:
        return con.execute("SELECT 1 FROM outbox WHERE dedupe = ?", (dedupe,)).fetchone() is not None
