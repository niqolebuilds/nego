"""Read CSV or Excel exports whose headers vary, into rows with our field names.

PowerBI, AX, D365, the formulary and the trackers all name the same column
differently ("Item Number", "Kode Barang", "ERP Code"...). ``read_rows`` loads the first
sheet (or a CSV), finds the header row, and ``map_headers`` matches each wanted field
to a header through a synonym list, case and punctuation ignored.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from typing import Any

from ..engine import EngineError

SYNONYMS: dict[str, tuple[str, ...]] = {
    "principal": ("principal", "principal name", "manufacturer group", "prinsipal", "vendor", "vendor name", "supplier",
                  "supplier name", "nama vendor", "nama supplier", "pemasok"),
    "distributor": ("distributor", "distributor name", "pbf", "nama distributor"),
    "erp_code": ("erp code", "erp", "item code", "item number", "item no", "item id", "kode barang", "kode item",
                 "product code", "material code", "sku", "kode"),
    "item_name": ("item name", "item description", "description", "product name", "nama barang", "nama item",
                  "product", "item", "material description"),
    "brand": ("brand", "brand manufacturer", "manufacturer", "merk", "merek", "pabrikan"),
    "catalog_no": ("catalog no ref", "catalog no", "catalogue no", "catalog number", "ref", "ref no", "no katalog",
                   "part number", "part no"),
    "item_status": ("item status", "status", "status item"),
    "po_unit_text": ("unit", "uom", "purchase unit", "po unit", "satuan", "satuan beli", "unit of measure", "purch unit"),
    "qty": ("qty", "quantity", "po qty", "jumlah", "qty po", "ordered qty", "quantity ordered"),
    "unit_price": ("unit price", "price", "harga", "harga satuan", "net price", "po price", "purchase price", "harga beli"),
    "amount": ("amount", "value", "total", "line amount", "net amount", "po value", "nilai", "total amount"),
    "date": ("date", "po date", "order date", "tanggal", "tgl po", "document date", "created date"),
    "mou_qty": ("mou qty po unit", "qty po unit", "qty per po unit", "isi", "pack size", "conversion", "konversi"),
    "mou_hna": ("mou hna po unit excl ppn", "hna", "hna po unit", "hna excl ppn", "list price", "harga hna"),
    "mou_disc": ("mou disc", "disc", "discount", "diskon", "disc pct"),
    "binding": ("binding", "tipe binding", "type"),
    "mou_start": ("mou start", "start date", "contract start", "periode awal", "valid from"),
    "mou_end": ("mou end", "end date", "contract end", "periode akhir", "valid to", "expiry", "expired"),
    "contact_name": ("contact name", "pic", "pic name", "nama pic", "contact"),
    "contact_email": ("contact email", "email", "pic email", "email pic"),
    "contact_phone": ("contact phone", "phone", "whatsapp", "wa", "no hp", "no wa", "pic phone", "telepon"),
    "category": ("category", "kategori", "item category", "class"),
    "name": ("name", "principal", "principal name", "prinsipal", "vendor", "vendor name"),
}


def _norm(h: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).strip()


def map_headers(headers: list[str], wanted: tuple[str, ...], synonyms: dict | None = None) -> dict[str, int]:
    """field -> column index. Exact synonym matches first, then 'header starts with synonym'."""
    normed = [_norm(h) for h in headers]
    out: dict[str, int] = {}
    taken: set[int] = set()
    for exact in (True, False):
        for field in wanted:
            if field in out:
                continue
            for syn in (synonyms or SYNONYMS).get(field, (field.replace("_", " "),)):
                hit = next((i for i, h in enumerate(normed) if i not in taken and
                            (h == syn if exact else (h.startswith(syn + " ") or h.endswith(" " + syn)))), None)
                if hit is not None:
                    out[field] = hit
                    taken.add(hit)
                    break
    return out


def _cell(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return v.strip()
    return v


def read_table(filename: str, content: bytes, max_rows: int = 200_000) -> tuple[list[str], list[list[Any]]]:
    """Headers and data rows from a CSV or the first sheet of an .xlsx."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001 - openpyxl raises many types for a bad file
            raise EngineError(f"Can't read that Excel file: {e}") from None
        ws = wb.worksheets[0]
        raw = [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
    elif name.endswith((".csv", ".txt")):
        text = content.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        raw = [[_cell(v) for v in r] for r in csv.reader(io.StringIO(text), dialect)]
    else:
        raise EngineError("Upload a .csv or .xlsx file")
    # Header row: the row in the top 15 with the most text cells (at least two), so title
    # rows above a table (as in Template_Nego) are skipped.
    counts = [sum(1 for v in r if isinstance(v, str) and v) for r in raw[:15]]
    hdr_i = max(range(len(counts)), key=lambda i: (counts[i], -i)) if counts else None
    if hdr_i is None or counts[hdr_i] < 2:
        raise EngineError("Couldn't find a header row in the first 15 rows")
    headers = [str(v or "").strip() for v in raw[hdr_i]]
    rows = [r for r in raw[hdr_i + 1:] if any(v not in (None, "") for v in r)]
    if len(rows) > max_rows:
        raise EngineError(f"The file has more than {max_rows:,} rows")
    return headers, rows


def read_rows(filename: str, content: bytes, wanted: tuple[str, ...], required: tuple[str, ...] = ()) -> tuple[list[dict], dict]:
    """Rows as dicts of our field names, plus a mapping report {field: header}."""
    headers, rows = read_table(filename, content)
    idx = map_headers(headers, wanted)
    missing = [f for f in required if f not in idx]
    if missing:
        nice = ", ".join(f.replace("_", " ") for f in missing)
        raise EngineError(f"Couldn't find these columns: {nice}. Headers found: {', '.join(h for h in headers if h)[:300]}")
    out = []
    for r in rows:
        out.append({f: (r[i] if i < len(r) else None) for f, i in idx.items()})
    return out, {f: headers[i] for f, i in idx.items()}
