"""Read messy purchase-unit text into a pack size, so every price compares per piece.

Vendors, POs, the formulary and benchmarks write the same unit in many ways:
"BOX@50", "BX 10 VIAL", "Box isi 100", "1 BOX = 100 PCS", "STRIP 10 TAB",
"PACK/12", "PCS", "AMP". ``parse`` returns the container, the number of base pieces
in it and how sure it is. Nothing here guesses silently: an unreadable unit comes
back with confidence 0 so the anomaly scan can ask a person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONTAINERS = {
    "box": "BOX", "bx": "BOX", "bok": "BOX", "dus": "BOX", "dos": "BOX", "ktk": "BOX", "kotak": "BOX",
    "pack": "PACK", "pak": "PACK", "pck": "PACK", "pk": "PACK", "pax": "PACK",
    "strip": "STRIP", "str": "STRIP", "blister": "STRIP", "bls": "STRIP",
    "carton": "CARTON", "ctn": "CARTON", "karton": "CARTON",
    "set": "SET", "kit": "KIT", "roll": "ROLL", "rol": "ROLL", "bag": "BAG", "sachet": "SACHET",
    "botol": "BOTTLE", "btl": "BOTTLE", "bottle": "BOTTLE", "fl": "BOTTLE", "flash": "BOTTLE",
}
PIECES = {
    "pcs": "PCS", "pc": "PCS", "piece": "PCS", "pieces": "PCS", "ea": "PCS", "each": "PCS", "buah": "PCS", "bh": "PCS",
    "unit": "PCS", "unt": "PCS", "lbr": "PCS", "lembar": "PCS", "sheet": "PCS",
    "amp": "AMP", "ampul": "AMP", "ampoule": "AMP", "vial": "VIAL", "vl": "VIAL", "via": "VIAL",
    "tab": "TAB", "tablet": "TAB", "kaps": "CAP", "cap": "CAP", "kapsul": "CAP", "capsule": "CAP",
    "test": "TEST", "tes": "TEST", "tst": "TEST", "pasang": "PAIR", "pair": "PAIR", "psg": "PAIR",
    "syringe": "PCS", "tube": "TUBE", "tb": "TUBE", "inf": "BOTTLE", "flakon": "VIAL",
}


@dataclass(frozen=True)
class Uom:
    raw: str
    container: str | None  # BOX, PACK, ... or None for a single piece
    qty: float | None  # base pieces per purchase unit
    base: str | None  # PCS, VIAL, TAB, ...
    confidence: float  # 1.0 read clearly, 0.6 inferred, 0.0 unreadable

    @property
    def label(self) -> str:
        if self.qty is None:
            return self.raw or "?"
        if self.container is None or self.qty == 1:
            return self.base or "PCS"
        return f"{self.container}@{self.qty:g} {self.base or 'PCS'}"


def _word(token: str) -> tuple[str | None, str | None]:
    t = token.lower().strip(".")
    return CONTAINERS.get(t), PIECES.get(t)


def parse(text: str | None, qty_hint: float | None = None) -> Uom:
    """Parse a purchase-unit string. ``qty_hint`` (e.g. the template's Qty/PO Unit) wins over guesses."""
    raw = (text or "").strip()
    t = raw.lower()
    t = re.sub(r"(\d)[.,](?=\d{3}\b)", r"\1", t)  # 1.000 -> 1000
    tokens = re.findall(r"\d+(?:[.,]\d+)?|[a-z]+", t)
    container = base = None
    numbers: list[float] = []
    for tok in tokens:
        if tok[0].isdigit():
            numbers.append(float(tok.replace(",", ".")))
            continue
        c, p = _word(tok)
        if c and not container:
            container = c
        elif p and not base:
            base = p
    # "1 BOX = 100 PCS" or "BOX isi 100": a leading 1 is the container count, not the pack size
    if len(numbers) >= 2 and numbers[0] == 1:
        numbers = numbers[1:]
    qty = numbers[0] if numbers else None

    if qty_hint not in (None, "", 0):
        hint = float(qty_hint)
        conf = 1.0 if qty in (None, hint) else 0.6
        return Uom(raw, container if hint != 1 else None, hint, base or "PCS", conf)
    if container and qty:
        return Uom(raw, container, qty, base or "PCS", 1.0)
    if container and not qty:
        return Uom(raw, container, None, base, 0.0)  # "BOX" with no size: ask
    if base and not container:
        return Uom(raw, None, qty or 1.0, base, 1.0 if not qty or qty == 1 else 0.6)
    if qty and not container and not base:
        return Uom(raw, None, qty, "PCS", 0.6)
    return Uom(raw, None, None, None, 0.0)


# Common pack sizes: a price ratio near one of these means a unit mix-up, not a real change.
PACK_MULTIPLES = (2, 4, 5, 6, 10, 12, 20, 24, 25, 30, 48, 50, 60, 100, 120, 144, 200, 250, 500, 1000)


def pack_multiple(ratio: float, tolerance: float = 0.08) -> int | None:
    """If ``ratio`` (or its inverse) sits within tolerance of a pack size, return it."""
    if not ratio or ratio <= 0:
        return None
    r = ratio if ratio >= 1 else 1.0 / ratio
    for m in PACK_MULTIPLES:
        if abs(r - m) / m <= tolerance:
            return m
    return None
