"""The principal's side of a cycle: access links, what they may see and change, send.

Confidentiality is enforced here, in one place:

* a link gives access to **one cycle** of **one principal**, nothing else;
* a principal may change only the fields of their open step (``model.PRINCIPAL_STEP_FIELDS``);
* every item leaves through ``view_item``, an allowlist: no PO volumes or values, no last
  PO price, no cost impact, no Siloam findings, nothing about other principals.

Discounts come in from the form in percent (15 means 15%), so a principal can't type 15
and mean 0.15 by mistake.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .. import settings as S
from ..engine import EngineError
from . import checks as C
from . import model as M
from . import store, template_io, uom

PRINCIPAL_STEPS = tuple(M.PRINCIPAL_STEP_FIELDS)
STEP_NUMBER = {"identification": 1, "current_mou": 2, "rfq": 3, "counter_offer": 4, "feedback1": 5, "online_nego": 6,
               "submission": 7}
VIEW_FIELDS = ("id", "erp_code", "item_name", "brand", "catalog_no", "item_status", "remarks", "po_unit_text",
               "mou_qty", "mou_hna", "mou_disc", "rfq_qty", "rfq_hna", "rfq_disc", "price_reason", "principal_confirmed")
FB1_FIELDS = ("co_disc", "fb1_disc")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_link(cid: int, by: str, days: int | None = None) -> dict:
    c = store.require_cycle(cid)
    if c["status"] != "open":
        raise EngineError("This negotiation is closed")
    days = int(days or S.load()["principal_link_days"])
    if not 1 <= days <= 90:
        raise EngineError("A link can be valid for 1 to 90 days")
    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
    # The token is also kept encrypted, so reminders can resend the same link.
    from .vault import _fernet

    link = store.add_link(cid, _hash(token), expires, by, _fernet().encrypt(token.encode()).decode())
    return {**link, "path": f"/p/{token}"}


def current_link_path(cid: int) -> dict | None:
    """The live link for reminders: same token as sent before, or None if there isn't one."""
    from .vault import _fernet

    link = store.latest_active_link(cid)
    if not link:
        return None
    try:
        token = _fernet().decrypt(link["token_enc"].encode()).decode()
    except Exception:  # noqa: BLE001 - key rotated; a fresh link will be made
        return None
    return {**{k: v for k, v in link.items() if k not in ("token_enc", "token_hash")}, "path": f"/p/{token}"}


def resolve(token: str | None) -> dict | None:
    """The open cycle this token gives access to, or None if unknown, revoked or expired."""
    if not token or len(token) > 100:
        return None
    link = store.link_by_hash(_hash(token))
    if not link or link["revoked_at"] or link["expires_at"] < store.now():
        return None
    c = store.get_cycle(link["cycle_id"])
    if not c or c["status"] != "open":
        return None
    return {"link": link, "cycle": c}


def ppn() -> float:
    return float(S.load()["ppn_rate"])


def thresholds() -> dict:
    v = S.load()
    return {"increase_tolerance": v["anomaly_increase_tolerance"], "drop": v["check_drop"], "high_disc": v["check_high_disc"]}


def open_step(cycle: dict) -> str | None:
    """The step the principal can fill now, or None while it's Siloam's turn or already sent."""
    step = cycle["current_step"]
    if step in M.PRINCIPAL_STEP_FIELDS and step not in cycle["submitted_steps"]:
        return step
    return None


def view_item(it: dict, step: str, p: float, th: dict) -> dict:
    out = {k: it.get(k) for k in VIEW_FIELDS}
    out["mou_unit_price"] = M.unit_price(it.get("mou_hna"), it.get("mou_qty"), it.get("mou_disc"), p)
    out["rfq_unit_price"] = M.unit_price(it.get("rfq_hna"), it.get("rfq_qty"), it.get("rfq_disc"), p)
    parsed = uom.parse(it.get("po_unit_text"), it.get("mou_qty"))
    out["pack_name"] = parsed.container or "PCS"
    out["piece_name"] = parsed.base or "PCS"
    if step in ("feedback1",):
        for f in FB1_FIELDS:
            out[f] = it.get(f)
        out["co_unit_price"] = M.unit_price(it.get("rfq_hna"), it.get("rfq_qty"), it.get("co_disc"), p) if it.get("co_disc") is not None else None
        out["fb1_unit_price"] = M.unit_price(it.get("rfq_hna"), it.get("rfq_qty"), it.get("fb1_disc"), p) if it.get("fb1_disc") is not None else None
    issues = C.check_item(it, step, p, th)
    left = C.outstanding(it, issues)
    out["issues"] = [i.as_dict() for i in issues]
    out["outstanding"] = [i.code for i in left]
    out["state"] = ("missing" if any(i.level == "missing" for i in left) else
                    "check" if left else "done")
    return out


FILTERS = ("all", "todo", "check", "up", "done")


def items_page(cycle: dict, step: str, flt: str = "all", q: str = "", offset: int = 0, limit: int = 50) -> dict:
    if flt not in FILTERS:
        raise EngineError("Unknown filter")
    p, th = ppn(), thresholds()
    rows = [view_item(i, step, p, th) for i in store.items(cycle["id"])]
    counts = {"all": len(rows), "todo": sum(r["state"] == "missing" for r in rows),
              "check": sum(r["state"] == "check" for r in rows),
              "up": sum(any(i["code"] == "increase" for i in r["issues"]) for r in rows),
              "done": sum(r["state"] == "done" for r in rows)}
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in " ".join(str(r.get(f) or "") for f in ("erp_code", "item_name", "brand", "catalog_no")).lower()]
    if flt == "todo":
        rows = [r for r in rows if r["state"] == "missing"]
    elif flt == "check":
        rows = [r for r in rows if r["state"] == "check"]
    elif flt == "up":
        rows = [r for r in rows if any(i["code"] == "increase" for i in r["issues"])]
    elif flt == "done":
        rows = [r for r in rows if r["state"] == "done"]
    limit = max(1, min(limit, 200))
    return {"total": len(rows), "offset": offset, "items": rows[offset:offset + limit], "counts": counts}


def _from_form(field: str, value):
    """Form values: discounts in percent, numbers in Indonesian or plain style."""
    if field in M.PCT_FIELDS:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        x = store._num(str(value).replace("%", ""), M.BY_FIELD[field].header)
        return None if x is None else x / 100.0
    if field == "price_reason":
        return (str(value or "").strip()[:300]) or None
    return value


PRICE_FIELDS = {"rfq_qty", "rfq_hna", "rfq_disc", "fb1_disc", "item_status"}


def save(cycle: dict, item_id: int, changes: dict, by: str, confirm: str | None = None) -> dict:
    step = open_step(cycle)
    if not step:
        raise EngineError("Langkah ini sudah dikirim atau belum dibuka. / This step is closed or already sent.")
    allowed = M.PRINCIPAL_STEP_FIELDS[step]
    bad = [k for k in changes if k not in allowed]
    if bad:
        raise EngineError(f"Not editable at this step: {', '.join(sorted(bad))}")
    item = store.get_item(cycle["id"], item_id)
    if not item:
        raise EngineError("No such item")
    clean = {k: _from_form(k, v) for k, v in changes.items()}
    if PRICE_FIELDS & set(clean):
        clean["principal_confirmed"] = None  # a changed price must be confirmed again
    if confirm:
        codes = set(filter(None, (item.get("principal_confirmed") or "").split(",")))
        if "principal_confirmed" in clean:
            codes = set()
        codes.add(confirm[:40])
        clean["principal_confirmed"] = ",".join(sorted(codes))
    store.update_item(cycle["id"], item_id, clean, by, via="principal", allowed=set(allowed) | {"principal_confirmed"})
    return view_item(store.get_item(cycle["id"], item_id), step, ppn(), thresholds())


def fill_from_reference(cycle: dict, by: str, ids: list[int] | None = None) -> int:
    """RFQ: copy the MOU Qty/HNA/discount into blank RFQs. Feedback I: accept the counter offer."""
    step = open_step(cycle)
    if step not in ("rfq", "feedback1"):
        raise EngineError("Nothing to fill at this step")
    n = 0
    wanted = set(ids) if ids else None
    for it in store.items(cycle["id"]):
        if wanted is not None and it["id"] not in wanted:
            continue
        if it.get("item_status") == "Discontinue":
            continue
        if step == "rfq":
            if (it.get("rfq_hna") is not None and wanted is None) or it.get("mou_hna") is None:
                continue
            ch = {"rfq_qty": it.get("mou_qty"), "rfq_hna": it["mou_hna"], "rfq_disc": it.get("mou_disc") or 0.0,
                  "principal_confirmed": None}
            if not it.get("item_status"):
                ch["item_status"] = "Active"
        else:
            if it.get("co_disc") is None or (it.get("fb1_disc") is not None and wanted is None):
                continue
            ch = {"fb1_disc": it["co_disc"]}
        store.update_item(cycle["id"], it["id"], ch, by, via="principal:same-as-reference")
        n += 1
    return n


def summary(cycle: dict) -> dict:
    step = open_step(cycle) or cycle["current_step"]
    if step == "submission":
        dv = documents_view(cycle)
        left = [{"id": None, "erp_code": None, "item_name": t["id"], "issues": [{"code": "doc_missing", "level": "missing",
                 "id": f"Unggah {t['id']}.", "en": f"Upload the {t['en']}."}]} for t in dv["types"] if t["key"] in dv["missing_required"]]
        return {"step": step, "items": 0, "discontinued": 0, "done": len(dv["documents"]), "missing": len(left), "to_check": 0,
                "outstanding": left, "outstanding_count": len(left), "increases": [], "increase_count": 0,
                "documents": len(dv["documents"]), "can_send": not left}
    p, th = ppn(), thresholds()
    rows = [view_item(i, step, p, th) for i in store.items(cycle["id"])]
    left = [{"id": r["id"], "erp_code": r["erp_code"], "item_name": r["item_name"],
             "issues": [i for i in r["issues"] if i["code"] in r["outstanding"]]} for r in rows if r["outstanding"]]
    increases = [{"id": r["id"], "erp_code": r["erp_code"], "item_name": r["item_name"],
                  "mou_unit_price": r["mou_unit_price"], "rfq_unit_price": r["rfq_unit_price"], "reason": r["price_reason"]}
                 for r in rows if any(i["code"] in ("increase", "below_co") for i in r["issues"])]
    return {
        "step": step, "items": len(rows),
        "discontinued": sum(1 for r in rows if r["item_status"] == "Discontinue"),
        "done": sum(1 for r in rows if r["state"] == "done"),
        "missing": sum(1 for r in rows if r["state"] == "missing"),
        "to_check": sum(1 for r in rows if r["state"] == "check"),
        "outstanding": left[:100], "outstanding_count": len(left),
        "increases": increases[:100], "increase_count": len(increases),
        "can_send": not left,
    }


def documents_view(cycle: dict) -> dict:
    from . import vault

    docs = store.documents(cycle["id"])
    have = {d["doc_type"] for d in docs}
    return {"types": [{"key": k, "id": a, "en": b, "required": r, "uploaded": k in have} for k, a, b, r in vault.DOC_TYPES],
            "documents": [{"id": d["id"], "doc_type": d["doc_type"], "filename": d["filename"], "size": d["size"],
                           "uploaded_at": d["uploaded_at"]} for d in docs],
            "missing_required": sorted(vault.REQUIRED - have)}


def upload_document(cycle: dict, doc_type: str, filename: str, content: bytes, by: str) -> dict:
    from . import vault

    if open_step(cycle) != "submission":
        raise EngineError("Dokumen dikirim di langkah terakhir. / Documents are sent at the last step.")
    name, ctype = vault.check_upload(filename, content, doc_type)
    stored, digest = vault.save(cycle["id"], content)
    store.add_document(cycle["id"], {"doc_type": doc_type, "filename": name, "stored_name": stored, "content_type": ctype,
                                     "size": len(content), "sha256": digest, "uploaded_by": by})
    return documents_view(cycle)


def delete_document(cycle: dict, did: int, by: str) -> dict:
    from . import vault

    if open_step(cycle) != "submission":
        raise EngineError("Langkah ini sudah dikirim. / This step was already sent.")
    d = store.delete_document(cycle["id"], did, by)
    if not d:
        raise EngineError("No such document")
    vault.remove(cycle["id"], d["stored_name"])
    return documents_view(cycle)


def submit(cycle: dict, by: str) -> dict:
    step = open_step(cycle)
    if not step:
        raise EngineError("Langkah ini sudah dikirim. / This step was already sent.")
    s = summary(cycle)
    if not s["can_send"]:
        raise EngineError(f"Masih ada {s['outstanding_count']} hal yang perlu dilengkapi. / "
                          f"{s['outstanding_count']} things still need attention.")
    store.mark_submitted(cycle["id"], step, by, {"items": s["items"], "increases": s["increase_count"],
                                                 "discontinued": s["discontinued"]})
    from . import notify, service

    service.scan(cycle["id"])  # Siloam's own checks run straight away on what was sent
    try:
        notify.step_submitted(cycle["id"], step, s, by)
    except Exception:  # noqa: BLE001 - a failed notice must never undo the principal's submission
        pass
    return s


# ---------------------------------------------------------------- Excel

def export(cycle: dict) -> tuple[str, bytes]:
    step = open_step(cycle) or cycle["current_step"]
    data = template_io.export_cycle(cycle, store.items(cycle["id"]), ppn(),
                                    principal_step=step if step in M.PRINCIPAL_STEP_FIELDS else None,
                                    increase_tolerance=thresholds()["increase_tolerance"])
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in cycle["principal"]["name"]).strip().replace(" ", "_")
    return f"Siloam_{STEP_NUMBER.get(step, 0)}_{step}_{safe}.xlsx", data


def import_excel(cycle: dict, content: bytes, by: str, apply: bool) -> dict:
    """Read the principal's workbook. Rows with errors are reported and never saved."""
    step = open_step(cycle)
    if not step:
        raise EngineError("Langkah ini sudah dikirim atau belum dibuka. / This step is closed or already sent.")
    fields = M.PRINCIPAL_STEP_FIELDS[step]
    parsed = template_io.read_template(content)
    from .prepare import same_principal

    if parsed.get("principal") and not same_principal(parsed["principal"], cycle["principal"]["name"]):
        raise EngineError(f"File ini untuk '{parsed['principal']}'. / This file is for another principal.")
    notes = []
    for row in parsed["rows"]:
        for f in M.PCT_FIELDS:
            v = row.get(f)
            # A whole-number discount (15) in a % cell means 15%. Between 1 and 2 it's ambiguous
            # (150%? 1.5%?), so it stays as it is and the check rejects it.
            if isinstance(v, (int, float)) and 2 <= v <= 100:
                row[f] = v / 100.0
                notes.append({"row": row["_row"], "id": f"Diskon {v:g} dibaca sebagai {v:g}%.",
                              "en": f"Discount {v:g} read as {v:g}%."})
    plan = template_io.plan_import(store.items(cycle["id"]), parsed, fields)
    by_item: dict[int, dict] = {}
    rows_of: dict[int, int] = {}
    for ch in plan["changes"]:
        by_item.setdefault(ch["item_id"], {})[ch["field"]] = ch["new"]
        rows_of[ch["item_id"]] = ch.get("row")
    current = {i["id"]: i for i in store.items(cycle["id"])}
    rejected, accepted = [], []
    p, th = ppn(), thresholds()
    for item_id, ch in by_item.items():
        after = {**current[item_id], **ch}
        errs = [i for i in C.check_item(after, step, p, th) if i.level == "error"]
        entry = {"row": rows_of[item_id], "erp_code": after.get("erp_code"), "item_name": after.get("item_name")}
        if errs:
            rejected.append({**entry, "messages": [{"id": e.id, "en": e.en} for e in errs]})
        else:
            accepted.append({**entry, "item_id": item_id, "changes": ch})
    errors = [{"row": e["row"], "messages": [{"id": e["message"], "en": e["message"]}]} for e in plan["errors"]]
    res = {"rows_in_file": plan["rows_in_file"], "matched": plan["matched"], "accepted": len(accepted),
           "rejected": rejected[:200], "read_errors": errors[:200], "notes": notes[:200],
           "unmatched": plan["unmatched"][:50], "unmatched_count": plan["unmatched_count"]}
    if apply:
        for a in accepted:
            ch = dict(a["changes"])
            if PRICE_FIELDS & set(ch):
                ch["principal_confirmed"] = None
            store.update_item(cycle["id"], a["item_id"], ch, by, via="principal:excel",
                              allowed=set(fields) | {"principal_confirmed"})
        store.log_event(cycle["id"], by, "principal.excel", {"accepted": len(accepted), "rejected": len(rejected)})
        res["applied"] = len(accepted)
    return res
