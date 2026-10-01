"""Step 0, Prepare: build a cycle's item list from what Siloam already has.

1. **Purchase orders** (PowerBI export): every item bought from the principal in the
   last 12 months, with its PO unit, quantity, value and latest price.
2. **Formulary**: items listed for the principal but not bought are added, so the
   principal confirms or discontinues them.
3. **Current MOU** (tracker, previous Template_Nego or trade agreement): fills the
   reference columns G–I and anything the principal confirmed last time.

Rows are matched by ERP code, then by item name. Items come out sorted A–Z by name,
as the BAK and Excel Confirmation need them.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from ..engine import EngineError
from . import tabular, uom

PO_FIELDS = ("principal", "erp_code", "item_name", "po_unit_text", "qty", "unit_price", "amount", "date", "brand", "catalog_no")
FORMULARY_FIELDS = ("principal", "erp_code", "item_name", "brand", "catalog_no", "item_status", "po_unit_text")
MOU_FIELDS = ("principal", "erp_code", "item_name", "brand", "catalog_no", "item_status", "mou_qty", "mou_hna", "mou_disc",
              "po_unit_text")


def norm_name(s: str | None) -> str:
    s = re.sub(r"\b(pt|tbk|cv|indonesia|persero)\b", " ", str(s or "").lower())
    return re.sub(r"[^a-z0-9]+", "", s)


def same_principal(a: str | None, b: str | None) -> bool:
    x, y = norm_name(a), norm_name(b)
    return bool(x and y) and (x == y or (min(len(x), len(y)) >= 4 and (x in y or y in x)))


def _key(row: dict) -> str | None:
    code = str(row.get("erp_code") or "").strip()
    if code:
        return "c:" + code.upper()
    name = norm_name(row.get("item_name"))
    return "n:" + name if name else None


def _f(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from .store import _num

    try:
        return _num(v, "number")
    except EngineError:
        return None


def _for_principal(rows: list[dict], principal: str, label: str) -> tuple[list[dict], int]:
    """Keep rows for this principal when the file has a principal column; else keep all."""
    if not rows or "principal" not in rows[0]:
        return rows, 0
    keep = [r for r in rows if same_principal(r.get("principal"), principal)]
    if not keep:
        names = Counter(str(r.get("principal") or "").strip() for r in rows)
        sample = ", ".join(n for n, _ in names.most_common(8) if n)
        raise EngineError(f"The {label} has no rows for '{principal}'. Principals in the file: {sample}")
    return keep, len(rows) - len(keep)


def _parse_date(v) -> date | None:
    if v in (None, ""):
        return None
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def summarise_po(rows: list[dict], months: int = 12) -> tuple[dict[str, dict], dict]:
    """Group PO lines by item over the last ``months`` months of the file."""
    dated = [(r, _parse_date(r.get("date"))) for r in rows]
    latest = max((d for _, d in dated if d), default=None)
    since = latest - timedelta(days=round(months * 30.44)) if latest else None
    groups: dict[str, list[tuple[dict, date | None]]] = defaultdict(list)
    skipped_old = skipped_blank = 0
    for r, d in dated:
        if since and d and d < since:
            skipped_old += 1
            continue
        k = _key(r)
        if not k:
            skipped_blank += 1
            continue
        groups[k].append((r, d))
    out = {}
    for k, lines in groups.items():
        lines.sort(key=lambda t: t[1] or date.min)
        last = lines[-1][0]
        qty = sum(_f(r.get("qty")) or 0.0 for r, _ in lines)
        value = 0.0
        for r, _ in lines:
            a = _f(r.get("amount"))
            if a is None:
                p, q = _f(r.get("unit_price")), _f(r.get("qty"))
                a = (p or 0.0) * (q or 0.0)
            value += a
        units = Counter(str(r.get("po_unit_text") or "").strip() for r, _ in lines if r.get("po_unit_text"))
        last_price = _f(last.get("unit_price"))
        if last_price is None and _f(last.get("qty")):
            amt = _f(last.get("amount"))
            last_price = amt / _f(last.get("qty")) if amt is not None else None
        out[k] = {
            "erp_code": str(last.get("erp_code") or "").strip() or None,
            "item_name": str(last.get("item_name") or "").strip() or None,
            "brand": last.get("brand") or None,
            "catalog_no": last.get("catalog_no") or None,
            "po_unit_text": units.most_common(1)[0][0] if units else None,
            "po_qty_12m": qty or None,
            "po_value_12m": value or None,
            "last_po_price": last_price,
            "po_lines": len(lines),
            "po_units_seen": len(units),
        }
    info = {"po_lines": len(rows), "po_lines_used": sum(len(v) for v in groups.values()), "skipped_older": skipped_old,
            "skipped_no_item": skipped_blank, "period_from": since.isoformat() if since else None,
            "period_to": latest.isoformat() if latest else None}
    return out, info


def build_items(principal: str, po_rows: list[dict] | None, formulary_rows: list[dict] | None = None,
                mou_rows: list[dict] | None = None, months: int = 12) -> tuple[list[dict], dict]:
    """Merge PO, formulary and MOU rows into Template_Nego items for one principal."""
    if not po_rows and not formulary_rows and not mou_rows:
        raise EngineError("Upload at least the purchase-order export, the formulary or the current MOU")
    summary: dict = {"principal": principal, "sources": {}}
    items: dict[str, dict] = {}
    multi_unit: list[str] = []

    if po_rows:
        po_rows, other = _for_principal(po_rows, principal, "PO export")
        grouped, info = summarise_po(po_rows, months)
        info["other_principals_rows"] = other
        summary["po"] = info
        for k, g in grouped.items():
            if g.pop("po_units_seen", 0) > 1:
                multi_unit.append(g["erp_code"] or g["item_name"])
            g.pop("po_lines", None)
            items[k] = {**g, "source": "po"}

    added_formulary = 0
    if formulary_rows:
        formulary_rows, other = _for_principal(formulary_rows, principal, "formulary")
        summary["formulary"] = {"rows": len(formulary_rows), "other_principals_rows": other}
        for r in formulary_rows:
            k = _key(r)
            if not k:
                continue
            it = items.get(k)
            if it is None:
                items[k] = {f: (r.get(f) or None) for f in ("erp_code", "item_name", "brand", "catalog_no", "item_status",
                                                             "po_unit_text")}
                items[k]["source"] = "formulary"
                added_formulary += 1
            else:
                for f in ("brand", "catalog_no", "item_status"):
                    if not it.get(f) and r.get(f):
                        it[f] = r[f]
        summary["formulary"]["added_not_bought"] = added_formulary

    if mou_rows:
        mou_rows, other = _for_principal(mou_rows, principal, "current MOU file")
        matched = added = 0
        for r in mou_rows:
            k = _key(r)
            if not k:
                continue
            it = items.get(k)
            if it is None:
                it = items[k] = {"erp_code": r.get("erp_code") or None, "item_name": r.get("item_name") or None,
                                 "source": "mou"}
                added += 1
            else:
                matched += 1
            for f in ("mou_qty", "mou_hna", "mou_disc"):
                v = _f(r.get(f))
                if v is not None:
                    it[f] = v
            for f in ("brand", "catalog_no", "item_status", "po_unit_text"):
                if not it.get(f) and r.get(f):
                    it[f] = r[f]
        summary["mou"] = {"rows": len(mou_rows), "matched": matched, "added_not_bought": added, "other_principals_rows": other}

    for it in items.values():
        parsed = uom.parse(it.get("po_unit_text"), _f(it.get("mou_qty")))
        it["base_unit"] = parsed.base
        if it.get("mou_qty") in (None, "") and parsed.qty and parsed.confidence >= 0.6 and it.get("mou_hna") not in (None, ""):
            it["mou_qty"] = parsed.qty
    rows = sorted(items.values(), key=lambda r: (str(r.get("item_name") or "").lower(), str(r.get("erp_code") or "")))
    for i, r in enumerate(rows, start=1):
        r["sort"] = i
    summary["items"] = len(rows)
    summary["by_source"] = dict(Counter(r["source"] for r in rows))
    summary["several_po_units"] = multi_unit[:50]
    return rows, summary


def read_upload(kind: str, filename: str, content: bytes) -> tuple[list[dict], dict]:
    fields, required = {
        "po": (PO_FIELDS, ("item_name",)),
        "formulary": (FORMULARY_FIELDS, ("item_name",)),
        "mou": (MOU_FIELDS, ("item_name",)),
    }[kind]
    rows, mapping = tabular.read_rows(filename, content, fields, required)
    if kind == "po" and not any(f in mapping for f in ("qty", "amount", "unit_price")):
        raise EngineError("The PO export needs a quantity, price or amount column")
    return rows, mapping
