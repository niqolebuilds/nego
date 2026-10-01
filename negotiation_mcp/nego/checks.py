"""Checks a principal sees while filling a step, in Bahasa Indonesia and English.

One source of truth for the online form and the Excel upload. Four levels:

* ``missing``  – the SKU isn't filled yet (blocks sending);
* ``error``    – a value that can't be right (blocks sending, never saved from Excel);
* ``reason``   – allowed, but needs a reason (a price above the MOU, a counter offer not taken);
* ``confirm``  – unusual, the principal confirms it once ("Ya, sudah benar").

Prices compare per piece including PPN with the template formula (``model.unit_price``).
Siloam's own findings (PO history, volumes, outliers across the list) stay in
``anomalies.py`` and are never shown to the principal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import model as M
from . import uom

LEVELS = ("missing", "error", "reason", "confirm")
BLOCKING = {"missing", "error"}
DEFAULTS = {"increase_tolerance": 0.005, "drop": 0.40, "high_disc": 0.60}


@dataclass(frozen=True)
class Issue:
    level: str
    code: str
    field: str | None
    id: str
    en: str

    def as_dict(self) -> dict:
        return asdict(self)


def pct_text(v: float) -> str:
    s = f"{v * 100:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",") + "%"


def rp(v: float) -> str:
    return "Rp " + f"{v:,.0f}".replace(",", ".")


def check_item(item: dict, step: str, ppn: float = M.DEFAULT_PPN, thresholds: dict | None = None) -> list[Issue]:
    th = {**DEFAULTS, **(thresholds or {})}
    out: list[Issue] = []
    add = lambda *a: out.append(Issue(*a))  # noqa: E731
    active = item.get("item_status") != "Discontinue"

    if step == "identification":
        if not item.get("item_status"):
            add("missing", "status_missing", "item_status", "Pilih status: Aktif atau Tidak dijual lagi.",
                "Choose a status: Active or Discontinued.")
        if active and not item.get("brand"):
            add("missing", "brand_missing", "brand", "Isi brand / pabrikan.", "Fill in the brand / manufacturer.")
        if active and not item.get("catalog_no"):
            add("missing", "ref_missing", "catalog_no", "Isi nomor katalog (REF).", "Fill in the catalogue number (REF).")
        return out

    if step == "rfq":
        if not active:
            return out
        hna, qty, disc = item.get("rfq_hna"), item.get("rfq_qty"), item.get("rfq_disc")
        if hna is None and qty is None and disc is None:
            add("missing", "rfq_missing", "rfq_hna", "Harga belum diisi.", "No price yet.")
            return out
        if hna is None:
            add("missing", "hna_missing", "rfq_hna", "Isi harga HNA.", "Fill in the HNA.")
        elif hna <= 0:
            add("error", "hna_zero", "rfq_hna", "HNA harus lebih dari 0.", "HNA must be more than 0.")
        if qty is None:
            add("missing", "qty_missing", "rfq_qty", "Isi jumlah pcs per kemasan.", "Fill in pieces per pack.")
        elif qty < 1 or qty != int(qty):
            add("error", "qty_whole", "rfq_qty", "Isi per kemasan harus bilangan bulat, minimal 1.",
                "Pieces per pack must be a whole number of at least 1.")
        if disc is not None and not 0 <= disc < 1:
            add("error", "disc_range", "rfq_disc", f"Diskon {pct_text(disc)} tidak mungkin. Maksimal 99%.",
                f"A {pct_text(disc)} discount isn't possible. The most is 99%.")
        if any(i.level == "error" for i in out) or hna is None or not qty:
            return out
        if disc is not None and disc > th["high_disc"]:
            add("confirm", "disc_high", "rfq_disc", f"Diskon {pct_text(disc)} sangat besar. Apakah benar?",
                f"A {pct_text(disc)} discount is very high. Is that right?")
        new = M.unit_price(hna, qty, disc, ppn)
        mou = M.unit_price(item.get("mou_hna"), item.get("mou_qty"), item.get("mou_disc"), ppn)
        mq = item.get("mou_qty")
        if mq and qty != mq:
            add("confirm", "pack_changed", "rfq_qty",
                f"Isi per kemasan berubah dari {mq:g} menjadi {qty:g} pcs. Apakah kemasan memang berubah?",
                f"Pieces per pack changed from {mq:g} to {qty:g}. Has the pack really changed?")
        if new and mou:
            ratio = new / mou
            m = uom.pack_multiple(ratio)
            if m and m >= 5:
                more = "lebih mahal" if ratio > 1 else "lebih murah"
                add("confirm", "pack_multiple", "rfq_hna",
                    f"Harga per pcs sekitar {m}× {more} dari MOU ({rp(new)} vs {rp(mou)}). Apakah HNA diisi per kemasan "
                    f"dan isi per kemasan sudah benar?",
                    f"The price per piece is about {m}× {'higher' if ratio > 1 else 'lower'} than the MOU "
                    f"({rp(new)} vs {rp(mou)}). Is the HNA per pack and the pieces per pack right?")
            elif ratio > 3 or ratio < 1 / 3:
                add("confirm", "huge_change", "rfq_hna",
                    f"Harga per pcs {rp(new)}, sekitar {f'{ratio:.1f}'.replace('.', ',')}× harga MOU {rp(mou)}. Periksa HNA dan isi per kemasan.",
                    f"The price per piece {rp(new)} is about {ratio:.1f}× the MOU price {rp(mou)}. Check the HNA and pieces per pack.")
                if ratio > 1:
                    add("reason", "increase", "price_reason",
                        f"Harga per pcs naik dari MOU ({rp(mou)} → {rp(new)}). Mohon isi alasan kenaikan.",
                        f"The price per piece is up on the MOU ({rp(mou)} → {rp(new)}). Please give the reason.")
            elif ratio > 1 + th["increase_tolerance"]:
                add("reason", "increase", "price_reason",
                    f"Harga per pcs naik {pct_text(ratio - 1)} dari MOU ({rp(mou)} → {rp(new)}). Mohon isi alasan kenaikan.",
                    f"The price per piece is up {pct_text(ratio - 1)} on the MOU ({rp(mou)} → {rp(new)}). "
                    "Please give the reason.")
            elif ratio < 1 - th["drop"]:
                add("confirm", "big_drop", "rfq_hna",
                    f"Harga per pcs turun {pct_text(1 - ratio)} dari MOU. Apakah benar?",
                    f"The price per piece is down {pct_text(1 - ratio)} on the MOU. Is that right?")
        return out

    if step == "feedback1":
        if not active or item.get("co_disc") is None:
            return out
        fb, co = item.get("fb1_disc"), item.get("co_disc")
        if fb is None:
            add("missing", "fb1_missing", "fb1_disc", "Terima counter offer atau isi diskon Anda.",
                "Accept the counter offer or enter your discount.")
        elif not 0 <= fb < 1:
            add("error", "disc_range", "fb1_disc", f"Diskon {pct_text(fb)} tidak mungkin. Maksimal 99%.",
                f"A {pct_text(fb)} discount isn't possible. The most is 99%.")
        elif fb < co - 1e-9:
            add("reason", "below_co", "price_reason",
                f"Diskon Anda {pct_text(fb)}, di bawah counter offer Siloam {pct_text(co)}. Mohon isi alasan.",
                f"Your discount {pct_text(fb)} is below Siloam's counter offer of {pct_text(co)}. Please give the reason.")
        return out
    return out


def outstanding(item: dict, issues: list[Issue]) -> list[Issue]:
    """Issues that still stop sending: missing, errors, reasons not given, confirms not ticked."""
    confirmed = set(filter(None, (item.get("principal_confirmed") or "").split(",")))
    left = []
    for i in issues:
        if i.level in BLOCKING:
            left.append(i)
        elif i.level == "reason" and not (item.get("price_reason") or "").strip():
            left.append(i)
        elif i.level == "confirm" and i.code not in confirmed:
            left.append(i)
    return left


REASONS = (
    ("bahan_baku", "Kenaikan bahan baku", "Raw material cost"),
    ("kurs", "Kurs / nilai tukar", "Exchange rate"),
    ("kemasan", "Perubahan kemasan", "Pack change"),
    ("regulasi", "Regulasi / izin edar", "Regulation / registration"),
    ("logistik", "Biaya logistik", "Logistics cost"),
    ("lainnya", "Lainnya", "Other"),
)
