"""Item master data: one row per ERP code, with the labels that make comparison possible.

Every item that enters a negotiation is registered here (``store._insert_items``), so new
codes show up for an admin to label. The labels are:

* ``generic_name``: the active ingredient, so the same medicine from different brands can
  be compared (paracetamol 500 mg from brand A, B and C);
* ``group_key``: an admin-chosen group that overrides the generic name when two products
  count as equivalent for a different reason;
* ``tags``: free labels (category, formulary status, anything the team uses).

Admin only. Negotiators read the results through the analysis that uses the labels.
"""

from __future__ import annotations

import io
import re
from typing import Any

from ..engine import EngineError
from . import store, tabular

STATUSES = ("new", "reviewed")
LABEL_FIELDS = ("item_name", "generic_name", "group_key", "brand", "catalog_no", "uom", "tags")
IMPORT_FIELDS = ("erp_code", "item_name", "generic_name", "group_key", "brand", "catalog_no", "po_unit_text", "tags")
FILTERS = ("all", "new", "unlabelled")
MAX_LEN = {"item_name": 200, "generic_name": 120, "group_key": 120, "brand": 120, "catalog_no": 80, "uom": 40, "tags": 300}
EXPORT_HEADERS = ("ERP Code", "Item Name", "Generic Name", "Group", "Brand", "Catalog No (REF)", "Unit", "Tags", "Status")


def clean_tags(value: Any) -> str | None:
    """'a; B,a' -> 'a, B': separators unified, blanks and case-insensitive duplicates dropped."""
    seen: dict[str, str] = {}
    for part in re.split(r"[;,|\n]", str(value or "")):
        t = " ".join(part.split())
        if t:
            seen.setdefault(t.lower(), t)
    return ", ".join(seen.values())[: MAX_LEN["tags"]] or None


def _text(field: str, value: Any) -> str | None:
    if value is None:
        return None
    if field == "tags":
        return clean_tags(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    t = " ".join(str(value).split())
    if len(t) > MAX_LEN[field]:
        raise EngineError(f"{field.replace('_', ' ').capitalize()} is longer than {MAX_LEN[field]} characters")
    return t or None


def _code(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # Excel stores 1001 as 1001.0
    return str(value or "").strip()


def _where(filter_: str, q: str) -> tuple[str, list]:
    if filter_ not in FILTERS:
        raise EngineError("Unknown filter")
    parts, args = [], []
    if filter_ == "new":
        parts.append("status = 'new'")
    elif filter_ == "unlabelled":
        parts.append("TRIM(COALESCE(generic_name, '')) = ''")
    for word in q.lower().split()[:6]:
        like = f"%{word}%"
        parts.append("(LOWER(erp_code) LIKE ? OR LOWER(COALESCE(item_name,'')) LIKE ? OR LOWER(COALESCE(generic_name,'')) LIKE ? "
                     "OR LOWER(COALESCE(group_key,'')) LIKE ? OR LOWER(COALESCE(brand,'')) LIKE ? OR LOWER(COALESCE(tags,'')) LIKE ?)")
        args += [like] * 6
    return (" WHERE " + " AND ".join(parts)) if parts else "", args


def counts() -> dict:
    with store.connect() as con:
        r = con.execute("SELECT COUNT(*), SUM(status = 'new'), SUM(TRIM(COALESCE(generic_name, '')) = ''), "
                        "SUM(TRIM(COALESCE(generic_name, '')) != ''), COUNT(DISTINCT LOWER(COALESCE(NULLIF(TRIM(group_key), ''), NULLIF(TRIM(generic_name), '')))) "
                        "FROM item_master").fetchone()
    return {"all": r[0] or 0, "new": r[1] or 0, "unlabelled": r[2] or 0, "labelled": r[3] or 0, "groups": r[4] or 0}


def list_items(filter_: str = "all", q: str = "", offset: int = 0, limit: int = 50) -> dict:
    where, args = _where(filter_, q)
    limit = max(1, min(int(limit), 200))
    with store.connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM item_master{where}", args).fetchone()[0]
        rows = con.execute(f"SELECT * FROM item_master{where} ORDER BY (status = 'new') DESC, LOWER(COALESCE(item_name, erp_code)), erp_code "
                           "LIMIT ? OFFSET ?", [*args, limit, max(0, int(offset))]).fetchall()
    return {"total": total, "items": [dict(r) for r in rows], "counts": counts()}


def group_map() -> dict[str, tuple[str, str]]:
    """ERP code (lower case) -> (group key, label), for every item with a group or a generic name.
    The group wins over the generic name; the key ignores case and extra spaces."""
    out: dict[str, tuple[str, str]] = {}
    with store.connect() as con:
        for r in con.execute("SELECT erp_code, group_key, generic_name FROM item_master"):
            label = " ".join(str(r["group_key"] or r["generic_name"] or "").split())
            if label:
                out[r["erp_code"].lower()] = (label.casefold(), label)
    return out


def get(erp_code: str) -> dict | None:
    with store.connect() as con:
        r = con.execute("SELECT * FROM item_master WHERE erp_code = ?", (_code(erp_code),)).fetchone()
    return dict(r) if r else None


def update(erp_code: str, changes: dict, by: str) -> dict:
    """Edit labels of one item. Giving it a generic name or group counts as reviewing it."""
    bad = [k for k in changes if k not in (*LABEL_FIELDS, "status")]
    if bad:
        raise EngineError(f"These fields can't be changed here: {', '.join(sorted(bad))}")
    cur = get(erp_code)
    if not cur:
        raise EngineError("No such item")
    new = {k: _text(k, v) for k, v in changes.items() if k != "status"}
    if "status" in changes:
        if changes["status"] not in STATUSES:
            raise EngineError("Status must be new or reviewed")
        new["status"] = changes["status"]
    elif (new.get("generic_name") or new.get("group_key")) and cur["status"] == "new":
        new["status"] = "reviewed"
    diff = {k: v for k, v in new.items() if cur[k] != v}
    if diff:
        with store.connect() as con:
            con.execute(f"UPDATE item_master SET {', '.join(f'{k} = ?' for k in diff)}, updated_at = ?, updated_by = ? WHERE erp_code = ?",
                        [*diff.values(), store.now(), by, cur["erp_code"]])
    return {"item": get(cur["erp_code"]), "changed": {k: {"old": cur[k], "new": v} for k, v in diff.items()}}


def bulk_update(erp_codes: list[str], by: str, group_key: str | None = None, generic_name: str | None = None,
                tags_add: str | None = None, status: str | None = None) -> dict:
    """Apply the same label to many items. Only the fields given are touched."""
    codes = [_code(c) for c in erp_codes if _code(c)]
    if not codes:
        raise EngineError("Select at least one item")
    if len(codes) > 2000:
        raise EngineError("Select at most 2,000 items at a time")
    if status is not None and status not in STATUSES:
        raise EngineError("Status must be new or reviewed")
    group, generic, add = _text("group_key", group_key), _text("generic_name", generic_name), clean_tags(tags_add)
    if not (group or generic or add or status):
        raise EngineError("Choose what to set: a group, a generic name, tags or a status")
    done = 0
    with store.connect() as con:
        for code in codes:
            cur = con.execute("SELECT * FROM item_master WHERE erp_code = ?", (code,)).fetchone()
            if not cur:
                continue
            sets: dict[str, Any] = {}
            if group:
                sets["group_key"] = group
            if generic:
                sets["generic_name"] = generic
            if add:
                sets["tags"] = clean_tags(f"{cur['tags'] or ''}, {add}")
            if status:
                sets["status"] = status
            elif (group or generic) and cur["status"] == "new":
                sets["status"] = "reviewed"
            sets = {k: v for k, v in sets.items() if cur[k] != v}
            if sets:
                con.execute(f"UPDATE item_master SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ?, updated_by = ? WHERE erp_code = ?",
                            [*sets.values(), store.now(), by, cur["erp_code"]])
                done += 1
    return {"updated": done, "selected": len(codes)}


def import_file(filename: str, content: bytes, apply: bool, by: str) -> dict:
    """Read a master-data sheet with whatever headers it has. Blank cells never erase a label."""
    rows, mapping = tabular.read_rows(filename, content, IMPORT_FIELDS, required=("erp_code",))
    existing = {}
    with store.connect() as con:
        for r in con.execute("SELECT * FROM item_master"):
            existing[r["erp_code"].lower()] = dict(r)
    new, changed, same, skipped = [], [], 0, 0
    seen: set[str] = set()
    for i, raw in enumerate(rows, start=1):
        code = _code(raw.get("erp_code"))
        if not code or code.lower() in seen:
            skipped += 1
            continue
        seen.add(code.lower())
        vals = {}
        for src, dst in (("item_name", "item_name"), ("generic_name", "generic_name"), ("group_key", "group_key"), ("brand", "brand"),
                         ("catalog_no", "catalog_no"), ("po_unit_text", "uom"), ("tags", "tags")):
            v = _text(dst, raw.get(src))
            if v is not None:
                vals[dst] = v
        cur = existing.get(code.lower())
        if cur is None:
            new.append((code, vals))
            continue
        diff = {k: v for k, v in vals.items() if cur[k] != v}
        if diff:
            changed.append((cur["erp_code"], diff, {k: cur[k] for k in diff}))
        else:
            same += 1
    if apply:
        now = store.now()
        with store.connect() as con:
            for code, vals in new:
                con.execute("INSERT INTO item_master (erp_code, item_name, generic_name, group_key, brand, catalog_no, uom, tags, status, source, "
                            "first_seen, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?, 'import', ?, ?, ?)",
                            (code, vals.get("item_name"), vals.get("generic_name"), vals.get("group_key"), vals.get("brand"),
                             vals.get("catalog_no"), vals.get("uom"), vals.get("tags"),
                             "reviewed" if vals.get("generic_name") or vals.get("group_key") else "new", now, now, by))
            for code, diff, _old in changed:
                sets = dict(diff)
                if (diff.get("generic_name") or diff.get("group_key")) and existing[code.lower()]["status"] == "new":
                    sets["status"] = "reviewed"
                con.execute(f"UPDATE item_master SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ?, updated_by = ? WHERE erp_code = ?",
                            [*sets.values(), now, by, code])
    return {"applied": apply, "mapping": mapping, "rows_read": len(rows), "new": len(new), "changed": len(changed),
            "unchanged": same, "skipped": skipped,
            "sample": [{"erp_code": c, "item_name": v.get("item_name"), "kind": "new"} for c, v in new[:8]]
                      + [{"erp_code": c, "kind": "changed", "fields": {k: {"old": o.get(k), "new": d[k]} for k in d}} for c, d, o in changed[:12]]}


def export_xlsx() -> bytes:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Item master"
    ws.append(list(EXPORT_HEADERS))
    for c in ws[1]:
        c.font = Font(bold=True)
    with store.connect() as con:
        for r in con.execute("SELECT * FROM item_master ORDER BY LOWER(COALESCE(item_name, erp_code)), erp_code"):
            ws.append([r["erp_code"], r["item_name"], r["generic_name"], r["group_key"], r["brand"], r["catalog_no"], r["uom"], r["tags"], r["status"]])
    for col, width in zip("ABCDEFGHI", (14, 42, 26, 22, 20, 18, 10, 30, 10)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def overview() -> list[dict]:
    """One line per master-data set: how much is there, when it last changed, what needs a look."""
    from .. import appdb

    c = counts()
    with store.connect() as con:
        principals = con.execute("SELECT COUNT(*), MAX(COALESCE(updated_at, created_at)) FROM principals").fetchone()
        bench = con.execute("SELECT COUNT(*), COUNT(DISTINCT source), MAX(uploaded_at) FROM benchmarks").fetchone()
        cycles = con.execute("SELECT COUNT(*), SUM(status = 'open') FROM cycles").fetchone()
        item_last = con.execute("SELECT MAX(COALESCE(updated_at, first_seen)) FROM item_master").fetchone()[0]
    versions = appdb.list_versions()
    return [
        {"key": "items", "label": "Items", "count": c["all"], "note": f"{c['labelled']} with a generic name, {c['groups']} groups",
         "updated": item_last, "attention": c["new"], "attention_label": "new, not reviewed"},
        {"key": "principals", "label": "Principals", "count": principals[0], "updated": principals[1]},
        {"key": "negotiations", "label": "Negotiations", "count": cycles[0], "note": f"{cycles[1] or 0} open", "updated": None},
        {"key": "benchmarks", "label": "Benchmark prices", "count": bench[0], "note": f"{bench[1]} sources", "updated": bench[2]},
        {"key": "price_data", "label": "Price data versions", "count": len(versions),
         "note": (versions[0]["note"] or versions[0]["id"]) if versions else "none uploaded", "updated": versions[0]["created_at"] if versions else None},
    ]
