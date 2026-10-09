"""Equivalent products compared across brands.

Items that share a generic name or group (Admin > Master data) are the same thing to buy,
so their prices per piece, including PPN, can be set side by side: paracetamol 500 mg from
three brands, three ERP codes. Which one is cheapest, how far each of the others is above
it, and what that gap costs over a year of purchases.

The comparison assumes the admin put only interchangeable pieces in a group (same strength
and form). It uses the latest price in the negotiation (RFQ, counter offer, Feedback I,
Online nego), else the MOU or last PO price, and skips discontinued items.
"""

from __future__ import annotations

from . import master
from . import model as M
from .impact import enrich


def _price(it: dict) -> tuple[float | None, str | None]:
    if it.get("latest_price"):
        return it["latest_price"], it.get("latest_stage")
    if it.get("base_price"):
        return it["base_price"], it.get("base_from")
    return None, None


def analyse(items: list[dict], ppn: float = M.DEFAULT_PPN, spread: float = 0.20) -> dict:
    """Groups with two or more priced items, biggest saving first, plus how much is grouped."""
    gmap = master.group_map()
    by_group: dict[str, dict] = {}
    grouped = ungrouped = 0
    for raw in items:
        code = str(raw.get("erp_code") or "").strip().lower()
        if raw.get("item_status") == "Discontinue":
            continue
        if code not in gmap:
            ungrouped += 1
            continue
        grouped += 1
        key, label = gmap[code]
        it = enrich(raw, ppn)
        price, stage = _price(it)
        if not price:
            continue
        g = by_group.setdefault(key, {"key": key, "label": label, "members": []})
        g["members"].append({"item_id": raw.get("id"), "erp_code": raw.get("erp_code"), "item_name": raw.get("item_name"),
                             "brand": raw.get("brand"), "price": price, "stage": stage, "annual_pcs": it.get("annual_pcs")})
    groups = []
    for g in by_group.values():
        members = sorted(g["members"], key=lambda m: m["price"])
        if len(members) < 2:
            continue
        low = members[0]["price"]
        for m in members:
            m["gap"] = m["price"] / low - 1
            m["extra_cost"] = (m["price"] - low) * m["annual_pcs"] if m["annual_pcs"] else None
        top = members[-1]["gap"]
        groups.append({"key": g["key"], "label": g["label"], "members": members, "cheapest": members[0], "spread": top,
                       "flagged": top >= spread,
                       "potential_saving": sum(m["extra_cost"] or 0.0 for m in members[1:]) or None})
    groups.sort(key=lambda g: (not g["flagged"], -(g["potential_saving"] or 0.0), -g["spread"]))
    return {"groups": groups, "flagged": sum(1 for g in groups if g["flagged"]), "grouped_items": grouped,
            "ungrouped_items": ungrouped, "spread_threshold": spread,
            "potential_saving": sum(g["potential_saving"] or 0.0 for g in groups if g["flagged"]) or None}


def expensive_items(items: list[dict], ppn: float, spread: float) -> dict[int, dict]:
    """item id -> the cheapest equivalent, for items priced above it by more than ``spread``."""
    out: dict[int, dict] = {}
    for g in analyse(items, ppn, spread)["groups"]:
        low = g["cheapest"]
        for m in g["members"][1:]:
            if m["gap"] >= spread and m["item_id"] is not None:
                out[m["item_id"]] = {"label": g["label"], "price": m["price"], "cheapest_price": low["price"], "gap": m["gap"],
                                     "cheapest_name": low["item_name"], "cheapest_brand": low["brand"], "cheapest_code": low["erp_code"]}
    return out
