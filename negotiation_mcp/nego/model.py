"""Steps, template columns and the unit-price formula, in one place.

The column letters and the formula mirror ``templates/Template_Nego.xlsx`` exactly, so
the app, the exported workbook and the principal's Excel always agree:

    Unit Price/pcs (incl. PPN) = HNA per PO unit / Qty per PO unit × (1 − Disc) × (1 + PPN)
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PPN = 0.11

# Steps in order. "who" is who acts at that step; the principal portal (phase 2)
# shows a principal only the step that is open for them.
STEPS: tuple[tuple[str, str, str], ...] = (
    ("prepare", "Prepare", "admin"),
    ("identification", "Item Identification", "principal"),
    ("current_mou", "Current MOU", "reference"),
    ("rfq", "RFQ", "principal"),
    ("counter_offer", "Counter Offer", "admin"),
    ("feedback1", "Feedback I", "principal"),
    ("online_nego", "Online Nego", "admin"),
    ("submission", "Submission", "principal"),
    ("closed", "Closed", "admin"),
)
STEP_KEYS = tuple(k for k, _, _ in STEPS)
STEP_LABEL = {k: label for k, label, _ in STEPS}


@dataclass(frozen=True)
class Column:
    letter: str
    field: str
    header: str
    section: str
    kind: str  # text | int | money | pct | formula
    who: str  # principal | optional | reference | admin


# Row 7 headers of the template, column by column.
COLUMNS: tuple[Column, ...] = (
    Column("A", "erp_code", "ERP Code", "identification", "text", "reference"),
    Column("B", "item_name", "Item Name", "identification", "text", "reference"),
    Column("C", "brand", "Brand / Manufacturer", "identification", "text", "principal"),
    Column("D", "catalog_no", "Catalog No. (REF)", "identification", "text", "principal"),
    Column("E", "item_status", "Item Status", "identification", "text", "principal"),
    Column("F", "remarks", "Remarks", "identification", "text", "optional"),
    Column("G", "mou_qty", "MOU_Qty/PO Unit", "current_mou", "int", "reference"),
    Column("H", "mou_hna", "MOU_HNA/PO Unit (excl. PPN)", "current_mou", "money", "reference"),
    Column("I", "mou_disc", "MOU_Disc%", "current_mou", "pct", "reference"),
    Column("J", "mou_unit_price", "MOU_Unit Price/pcs (incl. PPN)", "current_mou", "formula", "reference"),
    Column("K", "rfq_qty", "RFQ_Qty/PO Unit", "rfq", "int", "principal"),
    Column("L", "rfq_hna", "RFQ_HNA/PO Unit (excl. PPN)", "rfq", "money", "principal"),
    Column("M", "rfq_disc", "RFQ_Disc%", "rfq", "pct", "principal"),
    Column("N", "rfq_unit_price", "RFQ_Unit Price/PCS (incl. PPN)", "rfq", "formula", "reference"),
    Column("O", "co_disc", "CO_Disc%", "counter_offer", "pct", "admin"),
    Column("P", "co_unit_price", "CO_Unit Price/PCS (incl. PPN)", "counter_offer", "formula", "reference"),
    Column("Q", "fb1_disc", "FB1_Disc%", "feedback1", "pct", "principal"),
    Column("R", "fb1_unit_price", "FB1_Unit Price/PCS (incl. PPN)", "feedback1", "formula", "reference"),
    Column("S", "on_disc", "ON_Disc%", "online_nego", "pct", "admin"),
    Column("T", "on_unit_price", "ON_Unit Price/PCS (incl. PPN)", "online_nego", "formula", "reference"),
)
BY_FIELD = {c.field: c for c in COLUMNS}
INPUT_FIELDS = tuple(c.field for c in COLUMNS if c.kind != "formula")
SECTION_TITLES = {
    "identification": "ITEM IDENTIFICATION",
    "current_mou": "CURRENT MOU  (reference only)",
    "rfq": "RFQ (to be completed by Principal)",
    "counter_offer": "COUNTER OFFER",
    "feedback1": "FEEDBACK I",
    "online_nego": "ONLINE NEGO",
}
ITEM_STATUSES = ("Active", "Discontinue")


def unit_price(hna: float | None, qty: float | None, disc: float | None, ppn: float = DEFAULT_PPN) -> float | None:
    """Template formula. Returns None when the inputs are incomplete, as the IFERROR does."""
    if hna in (None, "") or qty in (None, "", 0):
        return None
    try:
        return float(hna) / float(qty) * (1.0 - float(disc or 0.0)) * (1.0 + ppn)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def priced(item: dict, ppn: float = DEFAULT_PPN) -> dict:
    """Add the computed unit-price columns to an item, exactly as the workbook does.

    MOU uses its own HNA and Qty; RFQ, CO, FB1 and ON all price the RFQ HNA and Qty
    with that step's discount (columns P, R and T reference $L and $K).
    """
    out = dict(item)
    out["mou_unit_price"] = unit_price(item.get("mou_hna"), item.get("mou_qty"), item.get("mou_disc"), ppn)
    out["rfq_unit_price"] = unit_price(item.get("rfq_hna"), item.get("rfq_qty"), item.get("rfq_disc"), ppn)
    for step, disc in (("co", "co_disc"), ("fb1", "fb1_disc"), ("on", "on_disc")):
        d = item.get(disc)
        out[f"{step}_unit_price"] = unit_price(item.get("rfq_hna"), item.get("rfq_qty"), d, ppn) if d not in (None, "") else None
    return out


def latest_price(item: dict) -> tuple[str | None, float | None]:
    """The most advanced negotiated price available: ON, then FB1, CO, RFQ."""
    for step in ("on", "fb1", "co", "rfq"):
        p = item.get(f"{step}_unit_price")
        if p is not None:
            return step, p
    return None, None


# What a principal may change at each of their steps. Everything else is read-only for them.
# ``price_reason`` (why a price went up, or why the counter offer isn't accepted) is not a
# template column; it travels in the extra "Alasan" column of the principal's workbook.
PRINCIPAL_STEP_FIELDS: dict[str, tuple[str, ...]] = {
    "identification": ("brand", "catalog_no", "item_status", "remarks"),
    "rfq": ("item_status", "rfq_qty", "rfq_hna", "rfq_disc", "price_reason", "remarks"),
    "feedback1": ("fb1_disc", "price_reason", "remarks"),
    "submission": (),  # documents only; see portal.upload_document
}
PCT_FIELDS = tuple(c.field for c in COLUMNS if c.kind == "pct")
