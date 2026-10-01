"""The anomaly scan: find what a person should look at before a price is accepted.

Each finding names an item, a rule, a severity, a plain message and, where there is an
obvious fix, the field and value to apply. The scan only *proposes*; an admin decides
"fix" or "keep as is" (with a reason), and decisions survive rescans (see
``store.sync_anomalies``). Prices compare per piece including PPN (``impact.enrich``).

Rules, from the team's own checklist:

* typing slips: a discount typed as 15 instead of 15%, a discount of 100% or more,
  an HNA without a Qty/PO unit, an HNA of 0;
* unit problems: pack size changed between MOU and RFQ, PO unit text that disagrees
  with the pack, a price off by a pack multiple (≈10×, 50×, 100×);
* rule 1, no increase if avoidable: any increase over the MOU gets the conversion
  discount that would keep the MOU net price;
* rule 2, the largest and smallest changes are always reviewed: robust outliers
  (median / MAD of the log price ratio) plus the top N each way;
* list problems: duplicate ERP codes, discontinued items with a price, active items
  with no RFQ price once the RFQ is back;
* Siloam's last PO price far from the MOU (invoice compliance or a unit mix-up).
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median

from . import model as M
from . import uom
from .impact import enrich

DISC_FIELDS = ("mou_disc", "rfq_disc", "co_disc", "fb1_disc", "on_disc")
AFTER_RFQ = {"counter_offer", "feedback1", "online_nego", "submission", "closed"}
ADMIN_DISC_FOR_STEP = {"online_nego": "on_disc", "submission": "on_disc", "closed": "on_disc"}

DEFAULTS = {"increase_tolerance": 0.005, "po_deviation": 0.10, "outlier_z": 3.5, "review_top_n": 3, "benchmark_deviation": 0.05}


def _f(label: str, value: float) -> str:
    return f"{label} {value:+.1%}"


def scan(items: list[dict], step: str = "prepare", ppn: float = M.DEFAULT_PPN, thresholds: dict | None = None,
         benchmarks: dict | None = None) -> list[dict]:
    th = {**DEFAULTS, **(thresholds or {})}
    out: list[dict] = []

    def add(item: dict, rule: str, severity: str, message: str, **extra) -> None:
        out.append({"item_id": item.get("id"), "rule": rule, "severity": severity, "message": message, **extra})

    enriched = [enrich(i, ppn) for i in items]
    pack_flagged: set = set()
    admin_disc = ADMIN_DISC_FOR_STEP.get(step, "co_disc")

    # duplicates
    codes: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        if it.get("erp_code"):
            codes[str(it["erp_code"]).strip().upper()].append(it)
    for code, group in codes.items():
        if len(group) > 1:
            for it in group:
                add(it, "duplicate_erp", "high", f"ERP code {code} appears {len(group)} times. The BAK can't have duplicates; "
                                                  "merge or remove the extra rows.")

    for raw, it in zip(items, enriched):
        # typing slips in discounts
        for f in DISC_FIELDS:
            v = raw.get(f)
            if v is None:
                continue
            hdr = M.BY_FIELD[f].header
            if 1 < v <= 100:
                add(raw, f"disc_whole:{f}", "high", f"{hdr} is {v:g}. That looks like {v:g}% typed as a whole number.",
                    field=f, suggestion=round(v / 100, 6), suggestion_text=f"Set to {v:g}%")
            elif v >= 1:
                add(raw, f"disc_range:{f}", "high", f"{hdr} is {v:g}, which is 100% or more. A discount must be under 100%.",
                    field=f)
        for pre, label in (("mou", "MOU"), ("rfq", "RFQ")):
            hna, qty = raw.get(f"{pre}_hna"), raw.get(f"{pre}_qty")
            if hna is not None and not qty:
                add(raw, f"missing_qty:{pre}", "high", f"{label} HNA is filled but Qty/PO unit is empty, so the price per "
                                                        "piece can't be worked out.", field=f"{pre}_qty")
            if hna == 0:
                add(raw, f"zero_hna:{pre}", "high", f"{label} HNA is 0.", field=f"{pre}_hna")

        # units
        mq, rq = raw.get("mou_qty"), raw.get("rfq_qty")
        if mq and rq and abs(mq - rq) > 1e-9:
            add(raw, "pack_changed", "medium",
                f"Pack size changed from {mq:g} (MOU) to {rq:g} (RFQ) per PO unit. Prices are compared per piece, but "
                "check that AX/D365 and the PO unit will change too.", detail={"mou_qty": mq, "rfq_qty": rq})
        parsed = uom.parse(raw.get("po_unit_text"))
        pack = rq or mq
        if parsed.confidence == 1.0 and parsed.qty and pack and abs(parsed.qty - pack) > 1e-9 and parsed.container:
            add(raw, "po_unit_mismatch", "medium",
                f"The PO unit '{raw['po_unit_text']}' holds {parsed.qty:g} but the {'RFQ' if rq else 'MOU'} says {pack:g} per PO unit.",
                detail={"po_unit": raw["po_unit_text"], "po_unit_qty": parsed.qty, "pack": pack})
        if raw.get("po_unit_text") and parsed.confidence == 0 and not pack:
            add(raw, "uom_unclear", "low", f"Can't tell how many pieces are in '{raw['po_unit_text']}'. Fill MOU Qty/PO unit.",
                field="mou_qty")

        mou, rfq = it["mou_unit_price"], it["rfq_unit_price"]
        mou = mou if mou and mou > 0 else None
        if mou and rfq and rfq > 0:
            m = uom.pack_multiple(rfq / mou)
            if m and m >= 5:
                pack_flagged.add(raw.get("id"))
                more = "higher" if rfq > mou else "lower"
                add(raw, "pack_multiple", "high",
                    f"RFQ price per piece is about {m}× {more} than the MOU. Usually the HNA is per box while Qty/PO unit is "
                    "per piece (or the reverse).", detail={"ratio": rfq / mou, "multiple": m})
        po = it["po_unit_price"]
        if po and mou:
            m = uom.pack_multiple(po / mou)
            if m and m >= 5:
                add(raw, "po_pack_multiple", "medium",
                    f"Siloam's last PO price per piece is about {m}× the MOU price. The PO unit or the MOU pack is probably wrong.",
                    detail={"ratio": po / mou, "multiple": m})
            elif abs(po / mou - 1) > th["po_deviation"]:
                add(raw, "po_vs_mou", "medium",
                    f"Last PO paid {po:,.0f} per piece vs MOU {mou:,.0f} ({po / mou - 1:+.1%}). Check invoice compliance or the PO unit.",
                    detail={"po_price": po, "mou_price": mou})

        # rule 1: no increase if avoidable
        stage, latest = it["latest_stage"], it["latest_price"]
        if mou and latest and latest > mou * (1 + th["increase_tolerance"]) and raw.get("id") not in pack_flagged:
            hna, qty = raw.get("rfq_hna"), raw.get("rfq_qty")
            gross = hna / qty * (1 + ppn) if hna and qty else None
            hold = 1 - mou / gross if gross else None
            stage_name = {"rfq": "RFQ", "co": "Counter offer", "fb1": "Feedback I", "on": "Online nego"}[stage]
            msg = f"{stage_name} is {latest / mou - 1:+.1%} vs MOU per piece. Rule 1: no increase if avoidable."
            extra = {}
            if hold is not None and 0 <= hold < 1:
                quoted = raw.get(f"{stage}_disc") or 0.0
                msg += f" A discount of {hold:.2%} on the new HNA (quoted {quoted * 100:g}%) keeps the MOU net price."
                extra = {"field": admin_disc, "suggestion": round(hold, 6),
                         "suggestion_text": f"Set {M.BY_FIELD[admin_disc].header} to {hold:.2%}"}
            if raw.get("price_reason"):
                msg += f" Principal's reason: {raw['price_reason']}"
            add(raw, "price_increase", "medium", msg, detail={"stage": stage, "change": latest / mou - 1}, **extra)

        # market benchmark (confirmed matches only), checked before anything is agreed
        bm = (benchmarks or {}).get(raw.get("id"))
        if bm and latest and latest > bm["price_pp"] * (1 + th["benchmark_deviation"]):
            add(raw, "above_benchmark", "medium",
                f"{ {'rfq': 'RFQ', 'co': 'Counter offer', 'fb1': 'Feedback I', 'on': 'Online nego'}[stage]} is "
                f"{latest / bm['price_pp'] - 1:+.1%} above the {bm['source']} benchmark ({bm['price_pp']:,.0f}/pc, {bm['name'][:60]}).",
                detail={"benchmark": bm["price_pp"], "source": bm["source"]})

        # list problems
        if raw.get("item_status") == "Discontinue" and (raw.get("rfq_hna") is not None or raw.get("on_disc") is not None):
            add(raw, "discontinued_priced", "low", "Marked Discontinue but has a price. It will be left out of the BAK unless "
                                                   "it's set back to Active.")
        if step in AFTER_RFQ and raw.get("item_status") != "Discontinue" and raw.get("rfq_hna") is None:
            add(raw, "rfq_missing", "medium", "Active item with no RFQ price from the principal.", field="rfq_hna")

    # rule 2: largest and smallest changes (RFQ vs MOU), robust outliers + top N each way
    pairs = [(raw, math.log(it["rfq_unit_price"] / it["mou_unit_price"]))
             for raw, it in zip(items, enriched)
             if (it["rfq_unit_price"] or 0) > 0 and (it["mou_unit_price"] or 0) > 0 and raw.get("id") not in pack_flagged]
    if len(pairs) >= 3:
        xs = [x for _, x in pairs]
        med = median(xs)
        mad = median(abs(x - med) for x in xs)
        ranked = sorted(pairs, key=lambda p: p[1])
        n = int(th["review_top_n"])
        top_up = {id(r) for r, x in ranked[::-1][:n] if x > 0}
        top_down = {id(r) for r, x in ranked[:n] if x < 0}
        for raw, x in pairs:
            z = 0.6745 * (x - med) / mad if mad > 0 else (0.0 if x == med else math.inf)
            outlier = abs(z) >= th["outlier_z"]
            if not (outlier or id(raw) in top_up or id(raw) in top_down):
                continue
            change = math.exp(x) - 1
            kind = "increase" if change > 0 else "decrease"
            why = "far outside the rest of this list" if outlier else f"one of the {n} largest {kind}s in this list"
            add(raw, "extreme_change", "medium" if outlier else "low",
                f"RFQ vs MOU {change:+.1%} per piece, {why}. Rule 2: always check the largest and smallest changes.",
                detail={"change": change, "z": None if math.isinf(z) else z, "median_change": math.exp(med) - 1})
    return out


RULE_LABELS = {
    "duplicate_erp": "Duplicate ERP code",
    "disc_whole": "Discount typed as a whole number",
    "disc_range": "Discount 100% or more",
    "missing_qty": "HNA without Qty/PO unit",
    "zero_hna": "HNA is 0",
    "pack_changed": "Pack size changed",
    "po_unit_mismatch": "PO unit disagrees with pack",
    "uom_unclear": "Unclear PO unit",
    "pack_multiple": "Price off by a pack multiple",
    "po_pack_multiple": "PO price off by a pack multiple",
    "po_vs_mou": "PO price far from MOU",
    "price_increase": "Price increase",
    "discontinued_priced": "Discontinued but priced",
    "rfq_missing": "No RFQ price",
    "extreme_change": "Largest / smallest change",
    "above_benchmark": "Above market benchmark",
}


def rule_label(rule: str) -> str:
    return RULE_LABELS.get(rule.split(":")[0], rule)
