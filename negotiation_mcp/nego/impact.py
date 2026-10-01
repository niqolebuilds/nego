"""Per-piece prices, annual volume and cost impact for every item of a cycle.

Everything compares **per piece including PPN**, the template's unit-price basis, so a
box of 50 and a box of 100 are never compared directly. The baseline is the current
MOU price; an item with no MOU uses Siloam's latest PO price instead. Annual volume is
the last 12 months of POs, converted from PO units to pieces.

Cost impact = (step price − baseline) × annual pieces. Negative is a saving.
Margin impact needs Siloam's selling price per item and isn't computed yet.
"""

from __future__ import annotations

from . import model as M
from . import uom

STAGES = (("rfq", "RFQ"), ("co", "Counter offer"), ("fb1", "Feedback I"), ("on", "Online nego"))


def pack_size(item: dict) -> float | None:
    for f in ("rfq_qty", "mou_qty"):
        v = item.get(f)
        if v not in (None, "", 0):
            return float(v)
    return uom.parse(item.get("po_unit_text")).qty


def po_unit_qty(item: dict) -> float | None:
    """Pieces per PO unit as bought: the PO unit text, else the MOU pack."""
    parsed = uom.parse(item.get("po_unit_text"))
    if parsed.qty and parsed.confidence >= 0.6:
        return parsed.qty
    v = item.get("mou_qty") or item.get("rfq_qty")
    return float(v) if v else None


def enrich(item: dict, ppn: float = M.DEFAULT_PPN) -> dict:
    it = M.priced(item, ppn)
    per_po = po_unit_qty(item)
    it["po_unit_pcs"] = per_po
    it["annual_pcs"] = item["po_qty_12m"] * per_po if item.get("po_qty_12m") and per_po else None
    it["po_unit_price"] = (item["last_po_price"] / per_po * (1 + ppn)) if item.get("last_po_price") and per_po else None
    base, base_from = (it["mou_unit_price"], "mou") if it["mou_unit_price"] else (it["po_unit_price"], "po")
    it["base_price"], it["base_from"] = (base, base_from) if base else (None, None)
    stage, price = M.latest_price(it)
    it["latest_stage"], it["latest_price"] = stage, price
    it["change_pct"] = price / base - 1 if price is not None and base else None
    it["impact"] = (price - base) * it["annual_pcs"] if price is not None and base and it["annual_pcs"] else None
    return it


def summary(enriched: list[dict]) -> dict:
    """Totals per step for the cycle header."""
    out = {"items": len(enriched),
           "active": sum(1 for i in enriched if i.get("item_status") != "Discontinue"),
           "discontinued": sum(1 for i in enriched if i.get("item_status") == "Discontinue"),
           "with_mou": sum(1 for i in enriched if i.get("mou_unit_price")),
           "with_po": sum(1 for i in enriched if i.get("po_qty_12m")),
           "baseline_spend": sum(i["base_price"] * i["annual_pcs"] for i in enriched if i.get("base_price") and i.get("annual_pcs")),
           "po_value_12m": sum(i.get("po_value_12m") or 0.0 for i in enriched),
           "stages": []}
    for key, label in STAGES:
        rows = [i for i in enriched if i.get(f"{key}_unit_price") is not None and i.get("base_price")]
        delta = [(i[f"{key}_unit_price"] - i["base_price"]) * i["annual_pcs"] for i in rows if i.get("annual_pcs")]
        out["stages"].append({
            "key": key, "label": label, "items": len(rows),
            "increases": sum(1 for i in rows if i[f"{key}_unit_price"] > i["base_price"] * 1.0005),
            "decreases": sum(1 for i in rows if i[f"{key}_unit_price"] < i["base_price"] * 0.9995),
            "impact": sum(delta) if delta else None,
        })
    filled = [i for i in enriched if i.get("rfq_hna") is not None]
    out["rfq_filled"] = len(filled)
    latest = [i["impact"] for i in enriched if i.get("impact") is not None]
    out["latest_impact"] = sum(latest) if latest else None
    out["margin"] = None  # needs Siloam's selling price per item
    return out
