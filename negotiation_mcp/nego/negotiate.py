"""Siloam's moves after the RFQ: counter offer, Online Nego, escalation, submission package.

**Counter offer (step 4).** For each item the engine proposes the CO discount that brings
the price per piece (incl. PPN) down to the lowest credible reference: the current MOU price
(rule 1, no increase if avoidable), Siloam's last PO price, or a confirmed market benchmark.
It never asks for less than the principal already quoted, and never more than a set number
of extra discount points, so the counter stays credible. Every suggestion carries its reason.

**Online Nego (step 6).** The agreed discount per item: by default what the principal
offered in Feedback I, else the counter offer; the admin edits what was agreed in the meeting.
If the result still costs Siloam more than the escalation threshold, it needs sign-off.

**Submission package.** The agreed prices as an Excel workbook with three sheets:
Excel Confirmation, BAK draft (by binding: Nett or Disc) and Checks. Active items only,
A–Z, no duplicate ERP codes. Siloam's own templates for both can replace these layouts
once they're provided.
"""

from __future__ import annotations

import io
import math
from collections import Counter

from .. import settings as S
from ..engine import EngineError
from . import store
from .impact import enrich, summary
from .template_io import tanggal


def _rp(v: float) -> str:
    return "Rp " + f"{v:,.0f}".replace(",", ".")


def suggest_co(item: dict, bench: dict | None, ppn: float, max_extra: float) -> dict | None:
    if item.get("item_status") == "Discontinue" or item.get("rfq_hna") is None or not item.get("rfq_qty"):
        return None
    it = enrich(item, ppn)
    gross = item["rfq_hna"] / item["rfq_qty"] * (1 + ppn)
    quoted = item.get("rfq_disc") or 0.0
    rfq_pp = gross * (1 - quoted)
    refs = [(label, v) for label, v in (("the current MOU price", it["mou_unit_price"]), ("Siloam's last PO price", it["po_unit_price"]),
                                        (f"the {bench['source']} benchmark" if bench else "", bench["price_pp"] if bench else None))
            if v and v > 0]
    if not refs:
        return {"co_disc": quoted, "note": "No MOU, PO or benchmark price to compare with: counter at the quoted discount."}
    label, target = min(refs, key=lambda r: r[1])
    if rfq_pp <= target * (1 + 1e-9):
        return {"co_disc": quoted, "note": f"The quote ({_rp(rfq_pp)}/pc) is already at or below {label} ({_rp(target)}/pc): accept it."}
    need = 1 - target / gross
    co = math.ceil(need * 1000 - 1e-9) / 1000
    cap = min(0.95, quoted + max_extra)
    note = f"Bring the price to {label}: {_rp(target)}/pc (quoted {_rp(rfq_pp)}/pc, {rfq_pp / target - 1:+.1%})."
    if co > cap:
        co = cap
        note += f" Capped at {quoted + max_extra:.1%} ({max_extra:.0%} points over the quote); the rest goes to Online Nego."
    return {"co_disc": round(co, 4), "note": note}


def plan_co(cid: int, overwrite: bool = False) -> dict:
    from .benchmark import item_benchmarks

    v = S.load()
    ppn, extra = float(v["ppn_rate"]), float(v["co_max_extra"])
    bench = item_benchmarks(cid)
    out = []
    for it in store.items(cid):
        if it.get("co_disc") is not None and not overwrite:
            continue
        s = suggest_co(it, bench.get(it["id"]), ppn, extra)
        if s:
            out.append({"item_id": it["id"], **s, "before": it.get("co_disc")})
    # impact if applied
    items = {i["id"]: dict(i) for i in store.items(cid)}
    for s in out:
        items[s["item_id"]]["co_disc"] = s["co_disc"]
    co_stage = next(st for st in summary([enrich(i, ppn) for i in items.values()])["stages"] if st["key"] == "co")
    return {"suggestions": out, "count": len(out), "co_impact": co_stage["impact"], "co_increases": co_stage["increases"]}


def apply_co(cid: int, by: str, overwrite: bool = False) -> dict:
    c = store.require_cycle(cid)
    if c["status"] != "open":
        raise EngineError("This negotiation is closed")
    plan = plan_co(cid, overwrite)
    for s in plan["suggestions"]:
        store.update_item(cid, s["item_id"], {"co_disc": s["co_disc"], "co_note": s["note"]}, by, via="engine:counter-offer")
    store.log_event(cid, by, "co.apply", {"items": plan["count"], "overwrite": overwrite})
    return {"applied": plan["count"], "co_impact": plan["co_impact"]}


def fill_on(cid: int, by: str, overwrite: bool = False) -> dict:
    """Online Nego starting point: the principal's Feedback I discount, else the counter offer."""
    n = 0
    for it in store.items(cid):
        if it.get("item_status") == "Discontinue" or (it.get("on_disc") is not None and not overwrite):
            continue
        d = it.get("fb1_disc") if it.get("fb1_disc") is not None else it.get("co_disc")
        if d is None:
            continue
        store.update_item(cid, it["id"], {"on_disc": d}, by, via="engine:online-nego")
        n += 1
    store.log_event(cid, by, "on.fill", {"items": n, "overwrite": overwrite})
    return {"filled": n}


def escalation(cid: int) -> dict:
    v = S.load()
    ppn, limit = float(v["ppn_rate"]), float(v["escalation_impact"])
    rows = [enrich(i, ppn) for i in store.items(cid)]
    on = [r for r in rows if r.get("on_unit_price") is not None and r.get("base_price")]
    impact = sum((r["on_unit_price"] - r["base_price"]) * r["annual_pcs"] for r in on if r.get("annual_pcs"))
    ups = [r for r in on if r["on_unit_price"] > r["base_price"] * (1 + float(v["anomaly_increase_tolerance"]))]
    need = bool(on) and (impact > limit or bool(ups))
    reasons = []
    if impact > limit:
        reasons.append(f"Agreed prices cost {_rp(impact)} a year more than the MOU (limit {_rp(limit)}).")
    if ups:
        reasons.append(f"{len(ups)} items end above the MOU price per piece.")
    return {"items_agreed": len(on), "impact": impact if on else None, "increases": len(ups), "needed": need, "reasons": reasons,
            "limit": limit}


# ---------------------------------------------------------------- submission package

def package_checks(cid: int) -> dict:
    ppn = float(S.load()["ppn_rate"])
    items = [enrich(i, ppn) for i in store.items(cid)]
    active = [i for i in items if i.get("item_status") != "Discontinue"]
    codes = Counter(str(i.get("erp_code") or "").upper() for i in active if i.get("erp_code"))
    dup = sorted(c for c, n in codes.items() if n > 1)
    missing_on = [i for i in active if i.get("on_disc") is None]
    no_code = [i for i in active if not i.get("erp_code")]
    up_no_reason = [i for i in active if i.get("on_unit_price") and i.get("base_price") and i["on_unit_price"] > i["base_price"] * 1.005
                    and not i.get("price_reason")]
    problems = []
    if missing_on:
        problems.append(f"{len(missing_on)} active items have no agreed (Online Nego) discount.")
    if dup:
        problems.append(f"Duplicate ERP codes: {', '.join(dup[:10])}.")
    if no_code:
        problems.append(f"{len(no_code)} active items have no ERP code.")
    if up_no_reason:
        problems.append(f"{len(up_no_reason)} items end above the MOU with no reason recorded.")
    agreed = sorted([i for i in active if i.get("on_disc") is not None and i.get("erp_code") and str(i["erp_code"]).upper() not in dup],
                    key=lambda i: str(i.get("item_name") or "").lower())
    return {"agreed": agreed, "problems": problems, "ready": not problems, "active": len(active),
            "discontinued": len(items) - len(active), "missing_on": [i["erp_code"] or i["item_name"] for i in missing_on][:50]}


def package_xlsx(cid: int) -> tuple[str, bytes, dict]:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    c = store.require_cycle(cid)
    chk = package_checks(cid)
    if not chk["agreed"]:
        raise EngineError("No agreed prices yet: fill the Online Nego discounts first")
    p = c["principal"]
    binding = c.get("binding") or "Nett"
    wb = openpyxl.Workbook()
    head = PatternFill("solid", fgColor="0C21A4")
    bold = Font(bold=True)

    def header(ws, title: str, cols: list[str]) -> None:
        ws["A1"] = title
        ws["A1"].font = Font(bold=True, size=14)
        meta = [("Principal", p["name"]), ("Distributor", p.get("distributor") or "—"),
                ("Contract period", f"{tanggal(c['contract_start'])} - {tanggal(c['contract_end'])}"),
                ("Binding", "Nett price" if binding == "Nett" else "Discount"),
                ("Online Nego", c.get("meeting_at") or "—")]
        for i, (k, v) in enumerate(meta, start=2):
            ws.cell(i, 1, k).font = bold
            ws.cell(i, 2, v)
        if not chk["ready"]:
            ws.cell(2, 5, "DRAFT — see the Checks sheet").font = Font(bold=True, color="C62B2B")
        r = len(meta) + 3
        for j, h in enumerate(cols, start=1):
            cell = ws.cell(r, j, h)
            cell.font, cell.fill = Font(bold=True, color="FFFFFF"), head
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.freeze_panes = ws.cell(r + 1, 3)
        return r + 1

    ws = wb.active
    ws.title = "Excel Confirmation"
    cols = ["No", "ERP Code", "Item Name", "Brand", "Catalog No. (REF)", "Qty/PO Unit", "HNA/PO Unit (excl. PPN)", "Agreed Disc%",
            "Nett/PO Unit (excl. PPN)", "Unit Price/pcs (incl. PPN)", "MOU Unit Price/pcs (incl. PPN)", "Change vs MOU"]
    r = header(ws, "EXCEL CONFIRMATION — AGREED PRICES", cols)
    for n, i in enumerate(chk["agreed"], start=1):
        nett = i["rfq_hna"] * (1 - i["on_disc"]) if i.get("rfq_hna") is not None else None
        ch = i["on_unit_price"] / i["mou_unit_price"] - 1 if i.get("on_unit_price") and i.get("mou_unit_price") else None
        vals = [n, i["erp_code"], i["item_name"], i.get("brand"), i.get("catalog_no"), i.get("rfq_qty"), i.get("rfq_hna"),
                i["on_disc"], nett, i.get("on_unit_price"), i.get("mou_unit_price"), ch]
        for j, v in enumerate(vals, start=1):
            ws.cell(r, j, v)
        ws.cell(r, 8).number_format = "0.0%"
        ws.cell(r, 12).number_format = "+0.0%;-0.0%;0.0%"
        for j in (7, 9, 10, 11):
            ws.cell(r, j).number_format = "#,##0.00"
        r += 1
    for col, w in zip("ABCDEFGHIJKL", (5, 14, 44, 16, 18, 10, 16, 10, 16, 16, 16, 11)):
        ws.column_dimensions[col].width = w

    bak = wb.create_sheet("BAK Draft")
    if binding == "Nett":
        cols = ["No", "ERP Code", "Item Name", "Satuan PO", "Isi/PO Unit", "Harga Nett/PO Unit (excl. PPN)", "Harga/pcs (incl. PPN)"]
    else:
        cols = ["No", "ERP Code", "Item Name", "Satuan PO", "Isi/PO Unit", "HNA/PO Unit (excl. PPN)", "Diskon%"]
    r = header(bak, f"BERITA ACARA KESEPAKATAN (DRAFT) — BINDING {'NETT' if binding == 'Nett' else 'DISKON'}", cols)
    for n, i in enumerate(chk["agreed"], start=1):
        unit = i.get("po_unit_text") or ""
        if binding == "Nett":
            vals = [n, i["erp_code"], i["item_name"], unit, i.get("rfq_qty"),
                    i["rfq_hna"] * (1 - i["on_disc"]) if i.get("rfq_hna") is not None else None, i.get("on_unit_price")]
        else:
            vals = [n, i["erp_code"], i["item_name"], unit, i.get("rfq_qty"), i.get("rfq_hna"), i["on_disc"]]
        for j, v in enumerate(vals, start=1):
            bak.cell(r, j, v)
        bak.cell(r, 6).number_format = "#,##0.00"
        bak.cell(r, 7).number_format = "0.0%" if binding != "Nett" else "#,##0.00"
        r += 1
    for col, w in zip("ABCDEFG", (5, 14, 44, 14, 10, 20, 16)):
        bak.column_dimensions[col].width = w

    ck = wb.create_sheet("Checks")
    ck["A1"] = "Checks before the BAK"
    ck["A1"].font = Font(bold=True, size=14)
    lines = chk["problems"] or ["All active items have an agreed price; no duplicate ERP codes."]
    for k, line in enumerate(lines, start=3):
        ck.cell(k, 1, line)
    ck.cell(len(lines) + 4, 1, f"Agreed items: {len(chk['agreed'])} · Active: {chk['active']} · Discontinued (left out): {chk['discontinued']}")
    ck.column_dimensions["A"].width = 100
    buf = io.BytesIO()
    wb.save(buf)
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in p["name"]).strip().replace(" ", "_")
    store.log_event(cid, "system", "package.export", {"items": len(chk["agreed"]), "ready": chk["ready"]})
    return f"BAK_Confirmation_{safe}_{c['contract_start'][:4]}.xlsx", buf.getvalue(), {k: v for k, v in chk.items() if k != "agreed"}

