"""Indonesian number display: Rp 9.905, Rp 5,36 M, 10,6%.

Display only. Calculations, JSON, CSV and the warehouse keep raw numbers. Standard
library only, so the calculation kernel can use it and stay dependency-free.

Conventions
-----------
* Dot groups thousands, comma marks decimals: ``Rp 107.157.799``.
* Whole rupiah, except amounts under Rp 100, which keep two decimals (``Rp 7,65``) so
  cheap per-unit prices do not lose the differences that matter.
* Compact scale words: ``rb`` (ribu, 10^3), ``jt`` (juta, 10^6), ``M`` (miliar, 10^9),
  ``T`` (triliun, 10^12).
* Any currency other than IDR falls back to the international style: ``USD 1,234.56``.
"""

from __future__ import annotations

DASH = "—"
SMALL_AMOUNT = 100.0
SCALES = ((1e12, "T", 2), (1e9, "M", 2), (1e6, "jt", 1), (1e3, "rb", 1))


def num(value: float, decimals: int = 0) -> str:
    """Indonesian grouping: 1234567.891 -> '1.234.567,89' (decimals=2)."""
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _is_idr(currency: str | None) -> bool:
    return (currency or "IDR").upper() in ("IDR", "RP")


def rp(value: float | None, currency: str | None = "IDR", decimals: int | None = None) -> str:
    """Money: 'Rp 9.905'. Under Rp 100 keeps two decimals unless ``decimals`` is given."""
    if value is None:
        return DASH
    if not _is_idr(currency):
        return f"{currency} {value:,.{2 if decimals is None else decimals}f}"
    if decimals is None:
        decimals = 2 if abs(value) < SMALL_AMOUNT and value != int(value) else 0
    sign = "-" if value < 0 else ""
    return f"{sign}Rp {num(abs(value), decimals)}"


def compact(value: float | None) -> str:
    """Compact number with an Indonesian scale word: 5.36e9 -> '5,36 M'."""
    if value is None:
        return DASH
    a = abs(value)
    for size, word, places in SCALES:
        if a >= size:
            return f"{num(value / size, places)} {word}"
    return num(value, 0)


def rp_compact(value: float | None, currency: str | None = "IDR") -> str:
    """Compact money: 5.36e9 -> 'Rp 5,36 M'."""
    if value is None:
        return DASH
    if not _is_idr(currency):
        return f"{currency} {value:,.0f}"
    sign = "-" if value < 0 else ""
    return f"{sign}Rp {compact(abs(value))}"


def pct(value: float | None, places: int = 2) -> str:
    """Percentage with a decimal comma: 0.106 -> '10,6%' (places=1)."""
    if value is None:
        return DASH
    return f"{num(value * 100, places)}%"
