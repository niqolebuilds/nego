"""Market benchmarks (INAPROC e-Katalog, SIMO Inhealth, others): import, then match to items.

The files come from the team's legitimate access (exports, saved catalogue pages, copies
from an authorised account). The app never logs in to those sites. What it does is the
tedious part:

1. **Read** the file whatever its headers, and turn every price into a price per piece
   including PPN (the unit text is parsed with ``uom.parse``).
2. **Match** each cycle item to benchmark rows: an exact catalogue number (REF) is
   certain; otherwise item name and brand words are compared. Strong matches are confirmed
   automatically, weaker ones wait for a person, and a person's decision is remembered for
   the same ERP code in later cycles.

Benchmarks are Siloam-internal: they never reach the principal (``portal.view_item``).
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..engine import EngineError
from . import store, tabular, uom

FIELDS = ("name", "brand", "catalog_no", "unit_text", "price", "price_date", "source")
SYN = {
    "name": ("nama produk", "nama barang", "product name", "item name", "nama", "deskripsi", "description", "produk", "name"),
    "brand": ("merek", "merk", "brand", "manufacturer", "pabrikan", "produsen"),
    "catalog_no": ("no katalog", "nomor katalog", "kode produk", "catalog no", "ref", "part number", "kode", "sku"),
    "unit_text": ("satuan", "kemasan", "unit", "uom", "packaging", "isi"),
    "price": ("harga", "harga satuan", "price", "unit price", "harga tayang", "harga produk", "harga katalog"),
    "price_date": ("tanggal", "date", "tgl", "periode", "updated"),
    "source": ("sumber", "source", "katalog"),
}
AUTO_CONFIRM = 0.85
SUGGEST_MIN = 0.5
STOP = {"dan", "the", "with", "untuk", "for", "isi", "box", "pcs", "steril", "sterile", "x", "of", "per"}


def read_file(filename: str, content: bytes, source: str, incl_ppn: bool, ppn: float, by: str) -> dict:
    headers, rows = tabular.read_table(filename, content)
    idx = tabular.map_headers(headers, FIELDS, SYN)
    if "name" not in idx or "price" not in idx:
        raise EngineError(f"Couldn't find the product name and price columns. Headers found: {', '.join(h for h in headers if h)[:300]}")
    out, skipped = [], 0
    for r in rows:
        get = lambda f: r[idx[f]] if f in idx and idx[f] < len(r) else None  # noqa: E731
        name = str(get("name") or "").strip()
        try:
            price = store._num(get("price"), "price")
        except EngineError:
            price = None
        if not name or not price or price <= 0:
            skipped += 1
            continue
        unit = str(get("unit_text") or "").strip() or None
        parsed = uom.parse(unit or name)
        pack = parsed.qty if parsed.qty and parsed.confidence >= 0.6 else None
        pp = price / (pack or 1) * (1 if incl_ppn else 1 + ppn) if pack or not unit else None
        out.append({"source": str(get("source") or source).strip()[:60] or source, "name": name[:300],
                    "brand": (str(get("brand") or "").strip() or None), "catalog_no": (str(get("catalog_no") or "").strip() or None),
                    "unit_text": unit, "pack_qty": pack, "price": price, "incl_ppn": incl_ppn, "price_pp": pp,
                    "price_date": str(get("price_date") or "")[:10] or None, "file": filename[:150]})
    if not out:
        raise EngineError("No rows with a product name and a price")
    store.add_benchmarks(out, by)
    return {"rows": len(out), "skipped": skipped, "without_pack": sum(1 for r in out if r["price_pp"] is None)}


def _norm_ref(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def tokens(*texts) -> set[str]:
    out = set()
    for t in texts:
        for w in re.findall(r"[a-z0-9]+(?:[.,][0-9]+)?", str(t or "").lower()):
            if w not in STOP and len(w) > 1:
                out.add(w)
    return out


def score(item: dict, bm: dict) -> tuple[float, str]:
    ir, br = _norm_ref(item.get("catalog_no")), _norm_ref(bm.get("catalog_no"))
    if ir and br and len(ir) >= 4 and ir == br:
        return 1.0, "ref"
    a = tokens(item.get("item_name"))
    b = tokens(bm.get("name"))
    if not a or not b:
        return 0.0, "name"
    brand_words = tokens(item.get("brand"), bm.get("brand"))
    core = {t for t in (a & b) if t not in brand_words and not re.fullmatch(r"v\d+", t)}
    if not core:  # sharing only a brand or a variant tag says nothing about the product
        return 0.0, "name"
    jac = len(a & b) / len(a | b)
    # numbers (sizes, strengths) must agree: 22G vs 24G, 500mg vs 1g are different products
    def sizes(ts: set[str]) -> set[str]:
        return {t for t in ts if re.search(r"\d", t) and not re.fullmatch(r"v\d+", t)}

    na, nb = sizes(a), sizes(b)
    if na and nb and not (na & nb):
        jac *= 0.3
    brand = str(item.get("brand") or "").lower().split()
    bm_text = f"{bm.get('brand') or ''} {bm.get('name')}".lower()
    if brand and brand[0] in bm_text:
        jac = min(1.0, jac + 0.2)
    return round(jac, 3), "name"


def match_cycle(cid: int) -> dict:
    items = store.items(cid)
    bms = store.benchmarks()
    if not bms:
        raise EngineError("Import a benchmark file first")
    remembered = store.confirmed_pairs()
    index: dict[str, list[int]] = defaultdict(list)
    for i, b in enumerate(bms):
        for t in tokens(b["name"]):
            index[t].append(i)
        if b.get("catalog_no"):
            index["ref:" + _norm_ref(b["catalog_no"])].append(i)
    found: list[dict] = []
    for it in items:
        cand = set(index.get("ref:" + _norm_ref(it.get("catalog_no")), []))
        for t in tokens(it.get("item_name")):
            if len(index.get(t, [])) < 500:  # very common words don't narrow anything
                cand.update(index.get(t, []))
        scored = sorted(((score(it, bms[i]), i) for i in cand), key=lambda x: -x[0][0])[:2]
        for (conf, method), i in scored:
            if conf < SUGGEST_MIN:
                continue
            b = bms[i]
            key = (str(it.get("erp_code") or "").upper(), f"{b['source']}|{b['name']}|{b['catalog_no'] or ''}".lower())
            status = "confirmed" if conf >= AUTO_CONFIRM or key in remembered else "suggested"
            found.append({"item_id": it["id"], "benchmark_id": b["id"], "confidence": conf,
                          "method": "remembered" if key in remembered and conf < AUTO_CONFIRM else method, "status": status})
    res = store.save_matches(cid, found)
    ms = store.matches(cid)
    store.log_event(cid, "system", "benchmark.match", {"found": len(found)})
    return {"candidates": len(found), "items_matched": len({m["item_id"] for m in ms if m["status"] == "confirmed"}),
            "to_review": sum(1 for m in ms if m["status"] == "suggested"), **res}


def item_benchmarks(cid: int) -> dict[int, dict]:
    return store.best_benchmarks(cid)

