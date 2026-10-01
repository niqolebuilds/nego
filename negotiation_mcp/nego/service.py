"""What the web app and MCP call: one function per action on a cycle.

Keeps the order of operations in one place (change data, rescan, log), so a route
handler never forgets to rescan after an import or an edit.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .. import settings as S
from ..engine import EngineError
from . import anomalies as A
from . import model as M
from . import prepare as P
from . import store, template_io
from .impact import enrich, summary


def ppn() -> float:
    return float(S.load()["ppn_rate"])


def thresholds() -> dict:
    v = S.load()
    return {"increase_tolerance": v["anomaly_increase_tolerance"], "po_deviation": v["anomaly_po_deviation"],
            "outlier_z": v["anomaly_outlier_z"], "review_top_n": v["anomaly_review_top_n"],
            "benchmark_deviation": v["anomaly_benchmark_deviation"]}


def months_between(today: date, end: date) -> float:
    return (end - today).days / 30.44


def principals(today: date | None = None) -> dict:
    today = today or date.today()
    alert_months = int(S.load()["mou_alert_months"])
    rows = store.list_principals()
    for p in rows:
        end = date.fromisoformat(p["mou_end"]) if p.get("mou_end") else None
        p["mou_days_left"] = (end - today).days if end else None
        open_cycle = p["cycle"] and p["cycle"]["status"] == "open"
        p["mou_alert"] = bool(end and months_between(today, end) <= alert_months and not open_cycle)
    return {"principals": rows, "alert_months": alert_months, "steps": steps()}


def steps() -> list[dict]:
    return [{"key": k, "label": label, "who": who} for k, label, who in M.STEPS]


def scan(cid: int) -> dict:
    c = store.require_cycle(cid)
    findings = A.scan(store.items(cid), c["current_step"], ppn(), thresholds(), store.best_benchmarks(cid))
    res = store.sync_anomalies(cid, findings)
    return res


def overview(cid: int) -> dict:
    c = store.require_cycle(cid)
    p = ppn()
    enriched = [enrich(i, p) for i in store.items(cid)]
    return {"cycle": c, "steps": steps(), "kpis": summary(enriched), "anomalies": store.anomaly_counts(cid),
            "events": store.cycle_events(cid, 30), "ppn": p,
            "columns": [{"letter": col.letter, "field": col.field, "header": col.header, "section": col.section,
                         "kind": col.kind, "who": col.who} for col in M.COLUMNS],
            "sections": M.SECTION_TITLES, "escalation": _escalation(cid), "documents": len(store.documents(cid)),
            "benchmark_review": sum(1 for m in store.matches(cid, "suggested"))}


def _escalation(cid: int) -> dict:
    from .negotiate import escalation

    return escalation(cid)


FILTERS = ("all", "anomalies", "increase", "decrease", "no_mou", "no_rfq", "discontinued")


def item_page(cid: int, q: str = "", flt: str = "all", offset: int = 0, limit: int = 50, sort: str = "sort") -> dict:
    store.require_cycle(cid)
    if flt not in FILTERS:
        raise EngineError("Unknown filter")
    p = ppn()
    open_by_item: dict[int, list[dict]] = {}
    for a in store.anomalies(cid, status="open"):
        if a["item_id"] is not None:
            open_by_item.setdefault(a["item_id"], []).append(
                {"id": a["id"], "rule": a["rule"], "label": A.rule_label(a["rule"]), "severity": a["severity"]})
    rows = [enrich(i, p) for i in store.items(cid)]
    bench = store.best_benchmarks(cid)
    for r in rows:
        b = bench.get(r["id"])
        r["bench_pp"], r["bench_source"] = (b["price_pp"], b["source"]) if b else (None, None)
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in " ".join(str(r.get(f) or "") for f in ("erp_code", "item_name", "brand", "catalog_no")).lower()]
    if flt == "anomalies":
        rows = [r for r in rows if r["id"] in open_by_item]
    elif flt == "increase":
        rows = [r for r in rows if (r.get("change_pct") or 0) > 0.0005]
    elif flt == "decrease":
        rows = [r for r in rows if (r.get("change_pct") or 0) < -0.0005]
    elif flt == "no_mou":
        rows = [r for r in rows if not r.get("mou_unit_price")]
    elif flt == "no_rfq":
        rows = [r for r in rows if r.get("rfq_hna") is None and r.get("item_status") != "Discontinue"]
    elif flt == "discontinued":
        rows = [r for r in rows if r.get("item_status") == "Discontinue"]
    keyf = {
        "sort": lambda r: r["sort"],
        "change": lambda r: -(r.get("change_pct") if r.get("change_pct") is not None else -1e9),
        "impact": lambda r: -(r.get("impact") if r.get("impact") is not None else -1e18),
        "spend": lambda r: -(r.get("po_value_12m") or 0.0),
        "name": lambda r: str(r.get("item_name") or "").lower(),
    }.get(sort, lambda r: r["sort"])
    rows.sort(key=keyf)
    total = len(rows)
    page = rows[offset:offset + max(1, min(limit, 500))]
    for r in page:
        r["flags"] = open_by_item.get(r["id"], [])
    return {"total": total, "offset": offset, "items": page}


def item_detail(cid: int, item_id: int) -> dict:
    it = store.get_item(cid, item_id)
    if not it:
        raise EngineError("No such item in this negotiation")
    out = enrich(it, ppn())
    out["anomalies"] = [{**a, "label": A.rule_label(a["rule"])} for a in store.anomalies(cid, item_id=item_id)]
    out["history"] = store.item_history(item_id)
    out["benchmarks"] = [m for m in store.matches(cid) if m["item_id"] == item_id and m["status"] != "rejected"]
    return out


def anomaly_list(cid: int, status: str | None = "open") -> dict:
    store.require_cycle(cid)
    rows = store.anomalies(cid, status=status or None)
    for r in rows:
        r["label"] = A.rule_label(r["rule"])
    by_rule: dict[str, int] = {}
    for r in rows:
        by_rule[r["label"]] = by_rule.get(r["label"], 0) + 1
    return {"anomalies": rows, "by_rule": by_rule, "counts": store.anomaly_counts(cid)}


def edit_item(cid: int, item_id: int, changes: dict, by: str) -> dict:
    store.require_cycle(cid)
    _, diff = store.update_item(cid, item_id, changes, by, via="admin")
    rescan = scan(cid) if diff else None
    return {"item": item_detail(cid, item_id), "changed": [d["field"] for d in diff], "scan": rescan}


def decide(cid: int, aid: int, decision: str, reason: str, by: str) -> dict:
    """fix: apply the suggested value (if any) and mark fixed; keep: accept with a reason; reopen."""
    a = store.get_anomaly(cid, aid)
    if not a:
        raise EngineError("No such finding")
    if decision == "fix":
        if a.get("field") and a.get("suggestion") is not None and a.get("item_id"):
            store.update_item(cid, a["item_id"], {a["field"]: a["suggestion"]}, by, via=f"anomaly:{a['rule']}")
        res = store.decide_anomaly(cid, aid, "fixed", reason, by)
        scan(cid)
        return {"anomaly": store.get_anomaly(cid, aid) or res, "counts": store.anomaly_counts(cid)}
    if decision == "keep":
        res = store.decide_anomaly(cid, aid, "kept", reason, by)
    elif decision == "reopen":
        res = store.decide_anomaly(cid, aid, "open", reason, by)
    else:
        raise EngineError("Decision must be fix, keep or reopen")
    return {"anomaly": res, "counts": store.anomaly_counts(cid)}


def prepare(cid: int, uploads: dict[str, tuple[str, bytes]], by: str, replace: bool = True) -> dict:
    """Build items from PO / formulary / MOU uploads, then scan."""
    c = store.require_cycle(cid)
    if c["status"] != "open":
        raise EngineError("This negotiation is closed")
    rows: dict[str, list[dict] | None] = {"po": None, "formulary": None, "mou": None}
    mappings = {}
    for kind, (filename, content) in uploads.items():
        if kind not in rows:
            continue
        if kind == "mou" and filename.lower().endswith((".xlsx", ".xlsm")) and _looks_like_template(content):
            parsed = template_io.read_template(content)
            rows[kind] = [mou_from_template_row(r) for r in parsed["rows"]]
            mappings[kind] = {"format": "Template_Nego"}
            continue
        rows[kind], mappings[kind] = P.read_upload(kind, filename, content)
    items, summ = P.build_items(c["principal"]["name"], rows["po"], rows["formulary"], rows["mou"])
    summ["columns_used"] = mappings
    if not replace and store.items(cid):
        raise EngineError("This negotiation already has items. Prepare again to replace them")
    store.replace_items(cid, items, by)
    summ["scan"] = scan(cid)
    store.mark_prepared(cid, summ, by)
    return summ


def mou_from_template_row(r: dict) -> dict:
    """Last cycle's Template_Nego becomes this cycle's MOU: the agreed price is the RFQ
    HNA and Qty with the last discount negotiated (ON, else FB1, CO, RFQ)."""
    out = {k: v for k, v in r.items() if not k.startswith("_") and k in ("erp_code", "item_name", "brand", "catalog_no",
                                                                         "item_status", "mou_qty", "mou_hna", "mou_disc")}
    if r.get("rfq_hna") is not None and r.get("rfq_qty"):
        disc = next((r[f] for f in ("on_disc", "fb1_disc", "co_disc", "rfq_disc") if r.get(f) is not None), 0.0)
        out.update(mou_qty=r["rfq_qty"], mou_hna=r["rfq_hna"], mou_disc=disc)
    return out


def _looks_like_template(content: bytes) -> bool:
    try:
        template_io.read_template(content)
        return True
    except EngineError:
        return False


def export_xlsx(cid: int) -> tuple[str, bytes]:
    c = store.require_cycle(cid)
    data = template_io.export_cycle(c, store.items(cid), ppn())
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in c["principal"]["name"]).strip().replace(" ", "_")
    return f"Template_Nego_{safe}_{c['contract_start'][:4]}.xlsx", data


IMPORT_FIELDS = tuple(f for f in M.INPUT_FIELDS if f not in ("erp_code", "item_name"))


def import_template(cid: int, content: bytes, by: str, apply: bool, fields: tuple[str, ...] = IMPORT_FIELDS) -> dict:
    """Preview (apply=False) or apply a filled Template_Nego. Applying rescans."""
    c = store.require_cycle(cid)
    parsed = template_io.read_template(content)
    plan = template_io.plan_import(store.items(cid), parsed, fields)
    if parsed.get("principal") and not P.same_principal(parsed["principal"], c["principal"]["name"]):
        plan["warning"] = f"The file is for '{parsed['principal']}', not {c['principal']['name']}."
    if apply:
        if plan.get("warning"):
            raise EngineError(plan["warning"] + " Nothing was imported.")
        plan["applied_items"] = template_io.apply_plan(cid, plan, by, via="template")
        store.log_event(cid, by, "template.import", {"items": plan["applied_items"], "changes": len(plan["changes"])})
        plan["scan"] = scan(cid)
    plan["changes"] = plan["changes"][:500]
    return plan


def set_step(cid: int, step: str, by: str, note: str = "", send_link: bool = False) -> dict:
    """Move to a step. With ``send_link``, a principal step also sends the principal their
    link through the automation webhook (see notify.py)."""
    c = store.set_step(cid, step, by, note)
    scan(cid)  # some rules depend on the step (e.g. missing RFQ prices)
    if send_link and step in M.PRINCIPAL_STEP_FIELDS:
        from . import notify

        c["message"] = notify.step_opened(cid, by)
    return c


def create_cycle(principal_id: int, data: dict, by: str) -> dict[str, Any]:
    return store.create_cycle(principal_id, data, by)
